"""Fast cuts: remove scenes without re-encoding the picture.

A compressed video can only start playing cleanly at a keyframe, so each part that's kept
starts at the first keyframe after a cut (the cut runs on a little, usually under a second
or two). The picture is copied bit-for-bit; the sound is cut to match exactly.
Anything unexpected -> returns None and the caller re-encodes as usual."""

import json
import shutil
from pathlib import Path

from .media import MediaError, ffmpeg, ffprobe, run

CODECS = {"h264", "hevc", "av1"}
MAX_EXTRA = 5.0              # give up if one cut would run on longer than this
END_MARGIN = 0.25            # parts end this much before a cut starts


def keyframes(info):
    r = run([ffprobe(), "-v", "error", "-select_streams", f"{info.video['index']}",
             "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(info.path)])
    out = []
    for line in (r.stdout or "").splitlines():
        t, _, flags = line.partition(",")
        if "K" in flags:
            try:
                out.append(float(t) - info.format_start)
            except ValueError:
                pass
    return sorted(out)


def plan(info, cuts, kfs=None):
    """(new_cuts, kept_parts, extra_seconds) or None when fast cuts don't fit."""
    if not info.video or info.video.get("codec_name") not in CODECS or not cuts:
        return None
    kfs = kfs if kfs is not None else keyframes(info)
    if len(kfs) < 2:
        return None
    new, extra = [], 0.0
    for a, b in cuts:
        nxt = next((k for k in kfs if k >= b - 0.002), None)
        b2 = info.duration if nxt is None else nxt
        if b2 - b > MAX_EXTRA:
            return None
        extra += max(0.0, b2 - b)
        if new and a <= new[-1][1]:
            new[-1] = (new[-1][0], max(new[-1][1], b2))
        else:
            new.append((a, b2))
    kept, pos = [], 0.0
    for a, b in new:
        if a - pos > 0.05:
            kept.append((pos, a))
        pos = b
    if info.duration - pos > 0.05:
        kept.append((pos, info.duration))
    if not kept:
        return None
    return new, kept, round(extra, 2)


def _packets(path):
    r = run([ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries",
             "packet=pts_time,duration_time,flags", "-of", "json", str(path)])
    return json.loads(r.stdout or "{}").get("packets", [])


def copy_parts(info, kept, work: Path, cancel=None):
    """Copy each kept part's picture. Returns (concat list file, [(start, end)] actually kept)."""
    work.mkdir(parents=True, exist_ok=True)
    real, lines = [], []
    for i, (s, e) in enumerate(kept):
        if cancel is not None and cancel.is_set():
            from .media import Cancelled
            raise Cancelled()
        seg = work / f"part{i:04d}.mkv"
        start = s                  # FFmpeg's -ss counts from the start of the file's own timeline already
        cmd = [ffmpeg(), "-v", "error", "-nostdin", "-y"]
        if s > 0:
            # FFmpeg seeks a little before the time asked for, to the keyframe before; asking for
            # a moment after the keyframe lands exactly on it
            cmd += ["-ss", f"{start + 0.15:.6f}"]
        # stop a little early: in coding order, a frame from just after the cut can come before
        # the last frames ahead of it
        stop = e if e >= info.duration - 0.05 else e - END_MARGIN
        lead = 0.15 if s > 0 else 0.0
        cmd += ["-i", str(info.path), "-t", f"{stop - s - lead:.6f}", "-map", f"0:{info.video['index']}",
                "-c", "copy", "-bsf:v", "noise=amount=0:drop=lt(pts\\,startpts)",   # leading frames
                "-avoid_negative_ts", "make_zero", str(seg)]
        r = run(cmd)
        if r.returncode != 0 or not seg.exists():
            raise MediaError(f"Fast cut failed: {r.stderr[-400:]}")
        pk = _packets(seg)
        if not pk or "K" not in pk[0].get("flags", ""):
            return None                                   # didn't start on a keyframe
        times = [float(p["pts_time"]) for p in pk if p.get("pts_time") not in (None, "N/A")]
        if not times:
            return None
        fdur = float(pk[-1].get("duration_time") or 0) or 1.0 / info.fps
        if min(times) < times[0] - 0.001:
            return None                                   # pictures from before the keyframe got in
        length = max(times) + fdur - min(times)
        if length > (e - s) + 0.001 or length < (stop - s) - 1.0:
            return None                                   # not what was planned: re-encode instead
        real.append((s, s + length))
        lines.append(f"file '{seg.name}'")
    lst = work / "parts.txt"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lst, real


def cuts_from_kept(kept, duration):
    """The removed time ranges that match exactly what the copied parts kept."""
    out, pos = [], 0.0
    for s, e in kept:
        if s - pos > 0.0005:
            out.append((pos, s))
        pos = e
    if duration - pos > 0.0005:
        out.append((pos, duration))
    return out


def cleanup(work: Path):
    shutil.rmtree(work, ignore_errors=True)
