"""FFmpeg helpers: probing files, audio tracks, running ffmpeg with progress."""

import json
import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .paths import NO_WINDOW, find_tool


class Cancelled(Exception):
    """The user pressed Cancel."""


class MediaError(Exception):
    pass


def ffmpeg() -> str:
    return find_tool("ffmpeg")


def ffprobe() -> str:
    return find_tool("ffprobe")


def run(cmd, **kw):
    cmd = list(cmd)
    if Path(cmd[0]).stem.lower() == "ffmpeg" and "-nostdin" not in cmd:
        cmd.insert(1, "-nostdin")
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, **kw)


def run_ffmpeg(cmd, duration, progress=None, cancel=None, label="Working"):
    """Run an ffmpeg command (without -progress) and report 0..1 progress.
    Raises MediaError with ffmpeg's message on failure, Cancelled on cancel."""
    full = [cmd[0], "-nostdin", "-hide_banner", "-loglevel", "error", "-nostats",
            "-progress", "pipe:1"] + list(cmd[1:])
    proc = subprocess.Popen(full, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=NO_WINDOW)
    try:
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise Cancelled()
            if line.startswith("out_time_us=") and duration and progress:
                try:
                    pos = int(line.split("=", 1)[1]) / 1e6
                    progress(min(1.0, max(0.0, pos / duration)), label)
                except ValueError:
                    pass
        err = proc.stderr.read()
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    if proc.returncode != 0:
        raise MediaError(err.strip()[-3000:] or f"ffmpeg exited with code {proc.returncode}")
    if progress:
        progress(1.0, label)


@lru_cache(maxsize=1)
def encoders() -> frozenset:
    out = run([ffmpeg(), "-hide_banner", "-encoders"]).stdout
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "AVS":
            names.add(parts[1])
    return frozenset(names)


@lru_cache(maxsize=4)
def encoder_works(name: str) -> bool:
    """NVENC is compiled into ffmpeg but only works with an NVIDIA card + driver."""
    if name not in encoders():
        return False
    r = run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
             "color=c=black:s=320x240:d=0.2", "-c:v", name, "-f", "null", "-"])
    return r.returncode == 0


@lru_cache(maxsize=1)
def ffmpeg_major_version() -> int:
    m = re.search(r"version n?(\d+)", run([ffmpeg(), "-version"]).stdout)
    return int(m.group(1)) if m else 7


@lru_cache(maxsize=1)
def channel_layouts() -> dict:
    out = run([ffmpeg(), "-hide_banner", "-layouts"]).stdout
    layouts, in_std = {}, False
    for line in out.splitlines():
        if line.startswith("Standard channel layouts"):
            in_std = True
            continue
        if in_std:
            parts = line.split()
            if len(parts) == 2 and parts[0] != "NAME":
                layouts[parts[0]] = parts[1].split("+")
    return layouts


LANG_NAMES = {"eng": "English", "en": "English", "spa": "Spanish", "es": "Spanish",
              "fre": "French", "fra": "French", "fr": "French", "ger": "German",
              "deu": "German", "de": "German", "ita": "Italian", "it": "Italian",
              "por": "Portuguese", "pt": "Portuguese", "jpn": "Japanese",
              "rus": "Russian", "chi": "Chinese", "zho": "Chinese", "kor": "Korean"}
LANG_CODES = {"en": {"en", "eng", "english"}, "es": {"es", "spa", "spanish"},
              "fr": {"fr", "fre", "fra", "french"}, "de": {"de", "ger", "deu", "german"},
              "it": {"it", "ita", "italian"}, "pt": {"pt", "por", "portuguese"}}
LANG3 = {"en": "eng", "es": "spa", "fr": "fre", "de": "ger", "it": "ita", "pt": "por"}
TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}


def stream_bitrate(st):
    tags = st.get("tags", {})
    for val in (st.get("bit_rate"), tags.get("BPS"), tags.get("BPS-eng")):
        try:
            if val:
                return int(val)
        except ValueError:
            pass
    return None


@dataclass
class AudioTrack:
    number: int            # 0-based among audio streams
    index: int             # absolute stream index
    codec: str
    layout: str
    channels: int
    language: str
    title: str
    default: bool
    extra: bool            # commentary / audio description
    raw: dict = field(repr=False, default_factory=dict)

    def describe(self) -> str:
        lang = LANG_NAMES.get(self.language, self.language or "Unknown language")
        bits = [lang, self.layout or f"{self.channels} ch", self.codec.upper()]
        if self.title:
            bits.append(f'"{self.title}"')
        return " · ".join(bits)


@dataclass
class MediaInfo:
    path: Path
    duration: float
    format_start: float
    streams: list
    video: dict
    audio: list
    subtitles: list

    @property
    def width(self):
        return int(self.video.get("width") or 0)

    @property
    def height(self):
        return int(self.video.get("height") or 0)

    @property
    def is_10bit(self):
        return "10" in (self.video.get("pix_fmt") or "") or "12" in (self.video.get("pix_fmt") or "")

    @property
    def is_hdr(self):
        return self.video.get("color_transfer") in ("smpte2084", "arib-std-b67")

    @property
    def fps(self):
        r = self.video.get("avg_frame_rate") or self.video.get("r_frame_rate") or "24/1"
        try:
            a, b = r.split("/")
            v = float(a) / float(b)
            return v if v > 0 else 24.0
        except (ValueError, ZeroDivisionError):
            return 24.0

    def summary(self) -> str:
        h, m = int(self.duration // 3600), int(self.duration % 3600 // 60)
        res = f"{self.width}×{self.height}" if self.width else "audio only"
        bits = [f"{h}h {m:02d}m" if h else f"{m} min", res,
                (self.video.get("codec_name") or "").upper()]
        if self.is_hdr:
            bits.append("HDR")
        return " · ".join(b for b in bits if b)


def probe(path) -> MediaInfo:
    path = Path(path)
    r = run([ffprobe(), "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", str(path)])
    if r.returncode != 0:
        raise MediaError(f"This file couldn't be read as a video:\n{r.stderr.strip()}")
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    videos = [s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")]
    audio = []
    for n, s in enumerate(st for st in streams if st.get("codec_type") == "audio"):
        tags = s.get("tags", {})
        disp = s.get("disposition", {})
        title = tags.get("title", "")
        audio.append(AudioTrack(
            number=n, index=s["index"], codec=s.get("codec_name", "?"),
            layout=s.get("channel_layout", ""), channels=int(s.get("channels") or 2),
            language=(tags.get("language") or "").lower(), title=title,
            default=bool(disp.get("default")),
            extra=("commentary" in title.lower() or "description" in title.lower()
                   or bool(disp.get("comment")) or bool(disp.get("visual_impaired"))),
            raw=s))
    duration = float(fmt.get("duration") or 0)
    if not duration:
        for s in streams:
            try:
                duration = max(duration, float(s.get("duration") or 0))
            except ValueError:
                pass
    return MediaInfo(path=path, duration=duration,
                     format_start=float(fmt.get("start_time") or 0),
                     streams=streams, video=videos[0] if videos else {},
                     audio=audio,
                     subtitles=[s for s in streams if s.get("codec_type") == "subtitle"])


def pick_audio_track(info: MediaInfo, language="en") -> int:
    wanted = LANG_CODES.get(language, {language})
    cands = [t for t in info.audio if t.language in wanted and not t.extra]
    if cands:
        defaults = [t for t in cands if t.default]
        return (defaults or cands)[0].number
    for t in info.audio:
        if t.default:
            return t.number
    return 0
