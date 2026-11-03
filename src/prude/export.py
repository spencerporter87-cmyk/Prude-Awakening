"""Where and how the clean copy is saved: folder, format, size, quality and Plex naming."""

import re
from dataclasses import asdict, dataclass
from pathlib import Path

NAMING = {
    "plex_edition": "Plex edition (shows up next to the original as an edition)",
    "plex_library": "Plex, separate library (plain \"Title (Year)\")",
    "simple": "Simple (\"Movie (clean)\")",
}
RESOLUTIONS = {"source": None, "2160": 2160, "1080": 1080, "720": 720}
_BAD = r'[<>:"/\\|?*{}]'


@dataclass
class ExportOptions:
    folder: str = ""                 # "" = same folder as the movie
    container: str = "mkv"           # mkv | mp4
    resolution: str = "source"       # source | 2160 | 1080 | 720
    quality: str = "high"            # high | max
    naming: str = "plex_edition"
    edition: str = "Family Edit"
    own_folder: bool = True          # put it in a "Title (Year)" folder when saving elsewhere

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})

    def to_dict(self):
        return asdict(self)


def clean_name(text):
    text = re.sub(_BAD, "", str(text)).strip().strip(".")
    return re.sub(r"\s+", " ", text)


def parse_title(src: Path):
    """("Pretty Woman", "1990") from 'Pretty Woman (1990).mkv', 'Pretty.Woman.1990.1080p.mkv'
    or a 'Pretty Woman (1990)' folder."""
    for name in (src.stem, src.parent.name):
        n = re.sub(r"\{edition-[^}]*\}", "", name)
        m = re.match(r"^(.*?)[\s._\-\[(]*((?:19|20)\d{2})(?:[\s._\-\])]|$)", n)
        if m and m.group(1).strip(" ._-"):
            title = re.sub(r"[._]+", " ", m.group(1)).strip(" -")
            return clean_name(title), m.group(2)
    return clean_name(re.sub(r"[._]+", " ", src.stem)), ""


def output_path(src: Path, opts: ExportOptions) -> Path:
    src = Path(src)
    ext = ".mp4" if opts.container == "mp4" else ".mkv"
    title, year = parse_title(src)
    base = f"{title} ({year})" if year else title
    if opts.naming == "plex_edition":
        edition = clean_name(opts.edition) or "Family Edit"
        name = f"{base} {{edition-{edition}}}"
    elif opts.naming == "plex_library":
        name = base
    else:
        name = f"{src.stem} (clean)"
    folder = Path(opts.folder) if opts.folder else src.parent
    if opts.folder and opts.own_folder and opts.naming != "simple" and folder.name != base:
        folder = folder / base
    out = folder / f"{name}{ext}"
    if out.resolve() == src.resolve():                    # never overwrite the original
        out = folder / f"{name} (clean){ext}"
    return out


def target_height(info, opts: ExportOptions):
    """Height to scale down to, or None to keep the original size."""
    h = RESOLUTIONS.get(opts.resolution)
    if not h or not info.height or info.height <= h + 8:
        return None
    return h


def estimate_size(info, needs_reencode, opts: ExportOptions, removed_seconds=0.0):
    """Very rough size in bytes, for showing "about 4.2 GB" before saving."""
    dur = max(1.0, info.duration - removed_seconds)
    try:
        total = Path(info.path).stat().st_size
    except OSError:
        total = 0
    if not needs_reencode:
        return int(total * dur / max(1.0, info.duration))
    h = target_height(info, opts) or info.height or 1080
    mbps = 24 if h >= 2000 else 9 if h >= 1000 else 4.5 if h >= 700 else 2.5
    if opts.quality == "max":
        mbps *= 1.5
    audio = sum((int(t.raw.get("bit_rate") or 640000) for t in info.audio[:1]))
    return int(dur * (mbps * 1e6 + audio) / 8)


def human_size(n):
    n = float(n or 0)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("bytes", "KB") else f"{n:.1f} {unit}"
        n /= 1024
