"""Hand-painted blurs: you paint over something on screen, move the painted shape along as it moves,
and the blur follows it from frame to frame.

A "patch" is a shape made of brush strokes plus a few keyframes saying where the shape is (and how
big) at certain moments; in between, it glides from one keyframe to the next. Everything is stored
as fractions of the picture's width and height, so it works at any resolution (the preview clips
and the finished copy use the same patch).

    patch = {"id", "on", "label", "start", "end", "pad_before", "pad_after",
             "anchor": [x, y],                           # where the shape sits when it hasn't been moved
             "strokes": [{"r": radius / picture width, "pts": [[x, y], ...]}],   # relative to the anchor
             "keys": [{"t": seconds, "dx": .., "dy": .., "sc": ..}]}              # offset and size over time
"""

import math
import subprocess
import uuid
from pathlib import Path

import numpy as np

from .paths import seq_pattern
from .media import Cancelled, MediaError, ffmpeg
from .paths import NO_WINDOW

MASK_WIDTH = 640         # the blur mask is made this wide, then stretched to the picture
DEFAULT_RADIUS = 0.03    # brush radius as a fraction of the picture's width


# ------------------------------------------------------------------ the shape and where it is
def new_patch(start, end, label="Painted blur"):
    return {"id": uuid.uuid4().hex[:10], "on": True, "label": label, "start": float(start), "end": float(end),
            "pad_before": 0.0, "pad_after": 0.0, "anchor": [0.5, 0.5], "strokes": [], "keys": []}


def span(patch):
    """Start and end including your own nudges."""
    return (float(patch["start"]) - float(patch.get("pad_before") or 0.0),
            float(patch["end"]) + float(patch.get("pad_after") or 0.0))


def transform(patch, t):
    """(dx, dy, scale) of the shape at time t: the keyframes, with a smooth glide in between."""
    keys = sorted(patch.get("keys") or [], key=lambda k: k["t"])
    if not keys:
        return 0.0, 0.0, 1.0
    if t <= keys[0]["t"]:
        k = keys[0]
        return k["dx"], k["dy"], k["sc"]
    if t >= keys[-1]["t"]:
        k = keys[-1]
        return k["dx"], k["dy"], k["sc"]
    for a, b in zip(keys, keys[1:]):
        if a["t"] <= t <= b["t"]:
            u = (t - a["t"]) / max(1e-9, b["t"] - a["t"])
            return (a["dx"] + u * (b["dx"] - a["dx"]), a["dy"] + u * (b["dy"] - a["dy"]),
                    a["sc"] + u * (b["sc"] - a["sc"]))
    return 0.0, 0.0, 1.0


def shape_at(patch, t):
    """[(radius, [(x, y), ...])] in picture fractions at time t."""
    dx, dy, sc = transform(patch, t)
    ax, ay = patch.get("anchor", [0.5, 0.5])
    out = []
    for s in patch.get("strokes") or []:
        pts = [(ax + dx + sc * float(p[0]), ay + dy + sc * float(p[1])) for p in s["pts"]]
        out.append((float(s["r"]) * sc, pts))
    return out


def _to_local(patch, t, x, y):
    dx, dy, sc = transform(patch, t)
    ax, ay = patch["anchor"]
    return [(x - ax - dx) / sc, (y - ay - dy) / sc]


def add_stroke(patch, t, pts, radius=DEFAULT_RADIUS):
    """Paint a stroke (points as picture fractions) while the shape is where it is at time t."""
    if not pts:
        return
    if not patch["strokes"]:
        patch["anchor"] = [float(pts[0][0]), float(pts[0][1])]
        patch["keys"] = []
    dx, dy, sc = transform(patch, t)
    patch["strokes"].append({"r": float(radius) / sc, "pts": [_to_local(patch, t, float(x), float(y)) for x, y in pts]})
    if t < patch["start"]:
        patch["start"] = float(t)
    if t > patch["end"]:
        patch["end"] = float(t)


def erase_near(patch, t, x, y, radius, aspect):
    """Remove the strokes that pass within `radius` (fraction of width) of the point. Returns how many."""
    keep, gone = [], 0
    for s, (r, pts) in zip(patch["strokes"], shape_at(patch, t)):
        hit = any(((px - x) ** 2 + ((py - y) / aspect) ** 2) ** 0.5 <= radius + r for px, py in pts)
        if hit:
            gone += 1
        else:
            keep.append(s)
    patch["strokes"] = keep
    return gone


def set_key(patch, t, dx, dy, sc):
    keys = [k for k in patch.get("keys") or [] if abs(k["t"] - t) > 1e-3]
    keys.append({"t": float(t), "dx": float(dx), "dy": float(dy), "sc": max(0.05, float(sc))})
    patch["keys"] = sorted(keys, key=lambda k: k["t"])


def move_at(patch, t, ddx, ddy):
    """Slide the shape by (ddx, ddy) at time t; it keeps that spot there and glides to the neighbouring keyframes."""
    if not patch.get("keys"):
        set_key(patch, patch["start"], 0.0, 0.0, 1.0)      # the starting position is the first keyframe
    dx, dy, sc = transform(patch, t)
    set_key(patch, t, dx + ddx, dy + ddy, sc)


def scale_at(patch, t, factor, pivot):
    """Grow or shrink the shape at time t around a point (picture fractions), keeping that point still."""
    if not patch.get("keys"):
        set_key(patch, patch["start"], 0.0, 0.0, 1.0)
    dx, dy, sc = transform(patch, t)
    ax, ay = patch["anchor"]
    px, py = pivot
    nx, ny = px + factor * (ax + dx - px), py + factor * (ay + dy - py)
    set_key(patch, t, nx - ax, ny - ay, sc * factor)


def remove_key(patch, t):
    patch["keys"] = [k for k in patch.get("keys") or [] if abs(k["t"] - t) > 1e-3]


def shifted(patch, delta):
    """A copy with every time moved by -delta (used for the short preview clips)."""
    p = dict(patch)
    p["start"], p["end"] = float(patch["start"]) - delta, float(patch["end"]) - delta
    p["keys"] = [dict(k, t=k["t"] - delta) for k in patch.get("keys") or []]
    return p


def active(patches):
    return [p for p in patches or [] if p.get("on", True) and p.get("strokes")]


# ------------------------------------------------------------------ the mask
def mask_at(patches, t, w, h, grow_px=3.0, feather=0.4):
    """Grayscale picture (255 = blur here) for time t from every patch that is on at that moment."""
    import cv2
    m = np.zeros((h, w), np.uint8)
    for p in patches:
        a, b = span(p)
        if not (a - 1e-3 <= t < b - 1e-3):          # the end is exclusive: a painted span ends before frame b
            continue
        for r, pts in shape_at(p, t):
            rad = max(1, int(round(r * w + grow_px)))
            px = [(int(round(x * w)), int(round(y * h))) for x, y in pts]
            if len(px) == 1:
                cv2.circle(m, px[0], rad, 255, -1, cv2.LINE_AA)
            else:
                cv2.polylines(m, [np.array(px, np.int32)], False, 255, rad * 2, cv2.LINE_AA)
                cv2.circle(m, px[0], rad, 255, -1, cv2.LINE_AA)
                cv2.circle(m, px[-1], rad, 255, -1, cv2.LINE_AA)
    if feather and m.any():
        k = max(3, int(round(feather * min(w, h) / 40)) * 2 + 1)
        m = cv2.GaussianBlur(m, (k, k), 0)
    return m


def _rate(info):
    r = info.video.get("avg_frame_rate") or info.video.get("r_frame_rate") or "24/1"
    return "24/1" if r in ("0/0", "0/1", "0") else r


def groups(patches, duration):
    """Patches that overlap in time (or sit within a second of each other) share one mask clip:
    [(start, end, [patch, ...])], sorted, kept inside the movie."""
    spans = []
    for p in active(patches):
        a, b = span(p)
        a, b = max(0.0, a), min(float(duration), b) if duration else b
        if b - a > 0.02:
            spans.append((a, b, p))
    spans.sort(key=lambda x: x[0])
    out = []
    for a, b, p in spans:
        if out and a <= out[-1][1] + 1.0:
            out[-1][1] = max(out[-1][1], b)
            out[-1][2].append(p)
        else:
            out.append([a, b, [p]])
    return [(a, b, ps) for a, b, ps in out]


def write_masks(patches, info, work_dir, progress=None, cancel=None):
    """Make one small lossless mask clip per group of patches. Returns
    [{"path", "start_frame", "frames", "start", "end"}] (frames at the movie's own frame rate)."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    W, H = info.width, info.height
    mw = min(MASK_WIDTH, W) // 2 * 2
    mh = max(2, int(round(mw * H / W / 2)) * 2)
    fps = info.fps
    segs = []
    total = sum(b - a for a, b, _ in groups(patches, info.duration)) or 1.0
    done = 0.0
    for n, (a, b, ps) in enumerate(groups(patches, info.duration)):
        first = int(math.ceil(a * fps - 1e-3 * fps))
        count = max(1, int(math.ceil(b * fps - 1e-3 * fps)) - first) + 2     # + two spare empty frames so it ends clean
        path = work_dir / f"paint_{n}.mkv"
        cmd = [ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{mw}x{mh}",
               "-framerate", _rate(info), "-i", "-", "-c:v", "ffv1", "-pix_fmt", "gray", str(path)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        try:
            for i in range(count):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                t = (first + i) / fps
                frame = mask_at(ps, t, mw, mh) if i < count - 2 else np.zeros((mh, mw), np.uint8)
                proc.stdin.write(frame.tobytes())
                if progress and i % 24 == 0:
                    progress(min(1.0, (done + i / fps) / total), "Painting the blur mask")
            proc.stdin.close()
            err = proc.stderr.read().decode("utf-8", "replace")
            if proc.wait() != 0:
                raise MediaError(f"Couldn't make the painted-blur mask: {err[-200:]}")
        except BaseException:
            try:
                proc.kill()
            except Exception:                                 # noqa: BLE001
                pass
            raise
        done += b - a
        segs.append({"path": str(path), "start_frame": first, "frames": count, "start": a, "end": b})
    return segs


def filter_graph(info, segs, first_input, src):
    """Filtergraph pieces that blur the painted areas of `src`. Returns (graph, label).
    Each mask clip is an extra input (numbered from first_input); black gaps join them into one
    mask as long as the movie, which then blurs a copy of the picture only where it's white."""
    W, H = info.width, info.height
    rate = _rate(info)
    sar = (info.video.get("sample_aspect_ratio") or "1:1").replace(":", "/")
    if sar in ("0/1", "N/A"):
        sar = "1"
    ten = info.is_10bit
    alpha_fmt = "yuva420p10le" if ten else "yuva420p"
    mask_fmt = "gray10le" if ten else "gray"
    fps = info.fps
    # Frame timestamps in a file are rounded (to the millisecond in .mkv), so the picture and the mask could
    # disagree by a frame about which frame is which. Snapping both to the exact frame grid makes the blur land on
    # the frame you picked. (Not for variable-frame-rate files, where there is no grid.)
    cfr = (info.video.get("r_frame_rate") or rate) == (info.video.get("avg_frame_rate") or rate)
    # one clock for both: a timebase of exactly one frame, so frame number N is pts N for the picture and the
    # mask alike (no container rounding, no float truncation)
    num, _, den = str(rate).partition("/")
    snap = f"settb={den or 1}/{num},setpts=N," if cfr else ""
    norm = snap
    pieces, parts, at = [], [], 0          # at = next frame number the mask should be on
    for j, s in enumerate(segs):
        gap = s["start_frame"] - at
        if gap > 0:
            pieces.append(f"color=c=black:s={W}x{H}:r={rate}:d={(gap - 0.5) / fps:.5f},format=gray,setsar={sar}[pg{j}]")
            parts.append(f"[pg{j}]")
        pieces.append(f"[{first_input + j}:v:0]{snap or 'setpts=PTS-STARTPTS,'}format=gray,"
                      f"scale={W}:{H}:flags=bilinear,setsar={sar},format=gray[pm{j}]")
        parts.append(f"[pm{j}]")
        at = s["start_frame"] + s["frames"]
    tail = max(1, int(round((info.duration + 2) * fps)) - at)
    pieces.append(f"color=c=black:s={W}x{H}:r={rate}:d={(tail - 0.5) / fps:.5f},format=gray,setsar={sar}[pgt]")
    parts.append("[pgt]")
    enable = "+".join(f"between(t,{max(0.0, s['start'] - 0.2):.3f},{s['end'] + 0.2:.3f})" for s in segs)
    graph = pieces + [
        f"{''.join(parts)}concat=n={len(parts)}:v=1:a=0,{snap}format={mask_fmt}[ppmask]",
        f"{src}{norm}split=2[ppmain][ppcopy]",
        f"[ppcopy]boxblur=luma_radius='min(w,h)/18':luma_power=4:chroma_radius='min(cw,ch)/18':"
        f"chroma_power=4:enable='{enable}',format={alpha_fmt}[ppblur]",
        "[ppblur][ppmask]alphamerge[ppblurred]",
        f"[ppmain][ppblurred]overlay=0:0:shortest=1:format={'yuv420p10' if ten else 'yuv420'}:"
        f"enable='{enable}'[vpaint]",
    ]
    return graph, "[vpaint]"


# ------------------------------------------------------------------ frames for the editor
def _tonemap(info):
    from .blurparts import _tonemap as tm        # HDR pictures are brought to normal brightness first
    return tm(info)


def extract_frames(info, t0, t1, out_dir, width=960, cancel=None):
    """Every frame between t0 and t1 as small JPEGs (frame i is at t0 + i / fps). Returns the file list."""
    from .media import run
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jpg"):
        old.unlink(missing_ok=True)
    r = run([ffmpeg(), "-y", "-loglevel", "error", "-ss", f"{max(0.0, t0):.3f}", "-t", f"{max(0.1, t1 - t0):.3f}",
             "-i", str(info.path), "-map", "0:v:0", "-an", "-sn", "-vf", f"{_tonemap(info)}fps={_rate(info)},scale={width}:-2,format=yuvj420p",
             "-q:v", "3", seq_pattern(out_dir, "%05d.jpg")])
    if r.returncode != 0:
        raise MediaError(f"Couldn't read the frames: {(r.stderr or '')[-200:]}")
    return sorted(out_dir.glob("*.jpg"))


# ------------------------------------------------------------------ what the editor shows
def preview_frame(path, patch, t, cache=None, show_blur=True):
    """The still at `path` as an RGB array with the painted blur applied the way the real render will
    (a blurred copy shown only where the mask is white)."""
    import cv2
    img = cv2.imread(str(path))
    if img is None:
        raise MediaError(f"Couldn't read a frame: {path}")
    if not show_blur or not patch.get("strokes"):
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    h, w = img.shape[:2]
    m = mask_at([patch], t, w, h)
    if not m.any():
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    key = str(path)
    blur = cache.get(key) if cache is not None else None
    if blur is None:
        k = max(3, (min(w, h) // 18) | 1)
        blur = img
        for _ in range(3):                               # three soft passes look like the real heavy blur
            blur = cv2.blur(blur, (k, k))
        if cache is not None:
            if len(cache) > 40:
                cache.clear()
            cache[key] = blur
    a = (m.astype(np.float32) / 255.0)[:, :, None]
    out = img.astype(np.float32) * (1 - a) + blur.astype(np.float32) * a
    return cv2.cvtColor(out.astype(np.uint8), cv2.COLOR_BGR2RGB)


def frame_time(t0, i, fps):
    return t0 + i / fps


def key_times(patch):
    return [k["t"] for k in sorted(patch.get("keys") or [], key=lambda k: k["t"])]


def include_frame(patch, t, fps):
    """Make sure the frame at time t is inside the painted span (the end is exclusive, so the frame counts)."""
    if t < patch["start"]:
        patch["start"] = float(t)
    if t + 1.0 / fps > patch["end"] + 1e-6:
        patch["end"] = float(t + 1.0 / fps)


def aligned(t, fps):
    """The time of the frame at or just before t."""
    return max(0.0, int(float(t) * fps + 1e-6) / fps)


def follow_to(patch, t, x, y, grab):
    """Put the shape so the point you grabbed (picture fractions, offset `grab` from the shape's anchor spot
    when you pressed) is at (x, y) at time t. Used for 'follow my cursor' while the frames play."""
    if not patch.get("keys"):
        set_key(patch, patch["start"], 0.0, 0.0, 1.0)
    _dx, _dy, sc = transform(patch, t)
    ax, ay = patch["anchor"]
    set_key(patch, t, x - grab[0] - ax, y - grab[1] - ay, sc)


def grab_offset(patch, t, x, y):
    dx, dy, _sc = transform(patch, t)
    ax, ay = patch["anchor"]
    return (x - (ax + dx), y - (ay + dy))


def frame_count(patch, fps):
    a, b = span(patch)
    return max(1, int(round((b - a) * fps)))
