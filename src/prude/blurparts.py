"""Blur only the exposed body parts, not the whole picture.

For each scene marked "blur the private parts", frames are sampled several times a
second, the nudity detector finds each exposed area, and FFmpeg moves blur patches
over those areas while the movie is re-encoded. The rest of the picture and the sound
are untouched."""

import shutil
from pathlib import Path

from .paths import seq_pattern
from .media import Cancelled, ffmpeg, run

SAMPLE_FPS = 10          # detections per second inside blurred scenes
BIG_EVERY = 3            # the sharper (slower) model looks at every 3rd sample as well
SLOTS = 8                # most areas blurred at once
GROW = {"tight": 0.2, "normal": 0.5, "large": 0.85}    # how far each box reaches beyond the detected area
MIN_SCORE = 0.2          # inside a scene you already flagged, a missed flash costs more than a spare blur
HOLD_AFTER = 0.6         # keep blurring this long after the detector loses the area (it blinks in motion)
LEAD_BEFORE = 0.3        # ...and start this early when the area is found from the next sample
BRIDGE = 1.0             # a gap shorter than this between two sightings is blurred through
FRAME_W = 1280          # width of the pictures the detectors look at (up from 640: small parts were lost)
TILE_ASPECT = 1.45       # a picture wider than this is also looked at in two overlapping halves...
TILE_FRAC = 0.6          # ...each this share of the width, so a part the model would see as a speck is twice as big
OCC_MIN = 0.6            # the whole-picture checker is this sure it's explicit but no body part was found:
OCC_EVERY = 2            # ...find where by covering parts of the picture (every this many samples)
OCC_DROP = 0.12          # a covered patch has to lower the checker's score by at least this much to count
DEFAULT_CLASSES = ["FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
                   "BUTTOCKS_EXPOSED", "ANUS_EXPOSED"]
TYPE_CLASSES = {
    "nude_f_breasts": ["FEMALE_BREAST_EXPOSED"],
    "nude_f_genitals": ["FEMALE_GENITALIA_EXPOSED"],
    "nude_m_genitals": ["MALE_GENITALIA_EXPOSED"],
    "nude_buttocks": ["BUTTOCKS_EXPOSED", "ANUS_EXPOSED"],
    "nude_m_chest": ["MALE_BREAST_EXPOSED"],
}


def classes_for(scene_types):
    out = []
    for t in scene_types or []:
        for c in TYPE_CLASSES.get(t, []):
            if c not in out:
                out.append(c)
    return out or list(DEFAULT_CLASSES)


class Detector:
    """Wraps NudeNet; returns [(x, y, w, h)] boxes in the image's pixels."""

    def __init__(self, classes, model=None):
        from nudenet import NudeDetector
        self.det = None
        if model:                              # the larger, sharper NudeNet model when it's been downloaded
            try:
                self.det = NudeDetector(model_path=str(model), inference_resolution=640)
            except Exception:                  # noqa: BLE001 - fall back to the built-in one
                self.det = None
        self.det = self.det or NudeDetector()
        self.classes = set(classes)

    def boxes(self, image_path):
        out = []
        for d in self.det.detect(str(image_path)) or []:
            if d.get("class") in self.classes and float(d.get("score", 0)) >= MIN_SCORE:
                x, y, w, h = [float(v) for v in d["box"]]
                out.append((x, y, w, h))
        return out


def content_rect(imgs, dark=24, min_bar=0.03):
    """(x0, y0, x1, y1): the picture without black bars (widescreen films are letterboxed), judged over several
    frames so a dark scene isn't mistaken for a bar. Falls back to the whole frame when unsure."""
    import numpy as np
    h, w = imgs[0].shape[:2]
    rows, cols = np.zeros(h), np.zeros(w)
    for im in imgs:
        g = im.max(axis=2) if im.ndim == 3 else im
        rows, cols = np.maximum(rows, g.max(axis=1)), np.maximum(cols, g.max(axis=0))
    live_r, live_c = np.where(rows > dark)[0], np.where(cols > dark)[0]
    if not live_r.size or not live_c.size:
        return 0, 0, w, h
    x0, x1, y0, y1 = int(live_c[0]), int(live_c[-1]) + 1, int(live_r[0]), int(live_r[-1]) + 1
    if y0 < h * min_bar:
        y0 = 0
    if h - y1 < h * min_bar:
        y1 = h
    if x0 < w * min_bar:
        x0 = 0
    if w - x1 < w * min_bar:
        x1 = w
    if (y1 - y0) < h * 0.4 or (x1 - x0) < w * 0.4:
        return 0, 0, w, h
    return x0, y0, x1, y1


def views(cw, ch):
    """The windows (x, y, w, h) of a cropped picture that get looked at: the whole thing, and for wide pictures
    two overlapping halves, so a small part is a bigger target."""
    out = [(0, 0, cw, ch)]
    if ch and cw / ch > TILE_ASPECT:
        tw = int(cw * TILE_FRAC)
        out += [(0, 0, tw, ch), (cw - tw, 0, tw, ch)]
    return out


def locate_with_checker(clf, img, p0=None):
    """The third opinion. The whole-picture checker says this frame is explicit but no body part was found, so
    cover each part of the picture in turn and see where its score collapses. Returns [(x, y, w, h)]."""
    import numpy as np
    h, w = img.shape[:2]
    if p0 is None:
        p0 = float(clf.probs([img])[0])
    if p0 < OCC_MIN:
        return []
    ww, hh = max(8, w // 3), max(8, h // 3)
    xs = [int(round(i * (w - ww) / 4)) for i in range(5)]
    ys = [int(round(i * (h - hh) / 4)) for i in range(5)]
    grey = np.full((hh, ww, 3), 127, np.uint8)
    cells, batch = [], []
    for y in ys:
        for x in xs:
            im = img.copy()
            im[y:y + hh, x:x + ww] = grey
            cells.append((x, y))
            batch.append(im)
    drops = p0 - np.asarray(clf.probs(batch), np.float32)
    top = float(drops.max())
    if top < OCC_DROP:
        return []
    keep = [(x, y, ww, hh) for (x, y), d in zip(cells, drops) if d >= max(OCC_DROP, 0.6 * top)]
    # the part is where the covered patches overlap, not the whole patch
    x0, y0 = max(c[0] for c in keep), max(c[1] for c in keep)
    x1, y1 = min(c[0] + ww for c in keep), min(c[1] + hh for c in keep)
    if x1 - x0 >= 8 and y1 - y0 >= 8 and len(keep) > 1:
        return [(x0, y0, x1 - x0, y1 - y0)]
    return merge_boxes(keep)


def _tonemap(info):
    """Filters that bring an HDR picture to normal brightness so the detectors see what a person would."""
    if not info.is_hdr:
        return ""
    from .scenes import _filters
    f = _filters()
    if " zscale " in f and " tonemap " in f:
        return ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
                "zscale=t=bt709:m=bt709:r=tv,")
    return ""


def detect(info, ranges, classes, work_dir: Path, progress=None, cancel=None, detector=None, model=None,
           checker=None):
    """Sample each range and find the areas to blur. Returns [(time, [(x, y, w, h)])] in
    the movie's own pixel size, sorted by time. `checker` (an NsfwClassifier) is the third opinion that locates
    explicit areas the body-part detectors missed."""
    import cv2
    det = detector or Detector(classes)
    big = None if detector else (Detector(classes, model) if model else None)
    W, H = info.width, info.height
    samples = []
    total = sum(b - a for a, b in ranges) or 1.0
    done = 0.0
    tone = _tonemap(info)
    for n, (a, b) in enumerate(ranges):
        d = Path(work_dir) / f"blur_{n}"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        r = run([ffmpeg(), "-y", "-loglevel", "error", "-ss", f"{a:.3f}", "-i", str(info.path),
                 "-t", f"{b - a:.3f}", "-map", "0:v:0", "-an", "-sn", "-vf",
                 f"{tone}fps={SAMPLE_FPS},scale='min({FRAME_W},iw)':-2,format=yuvj420p", "-q:v", "3",
                 seq_pattern(d, "%06d.jpg")])
        if r.returncode != 0:
            continue
        frames = sorted(d.glob("*.jpg"))
        if not frames:
            continue
        look = [cv2.imread(str(frames[i])) for i in sorted({int(i * (len(frames) - 1) / 11) for i in range(12)})]
        look = [im for im in look if im is not None]
        if not look:
            continue
        cx0, cy0, cx1, cy1 = content_rect(look)
        wins = views(cx1 - cx0, cy1 - cy0)
        for w in range(len(wins)):
            (d / f"v{w}").mkdir(exist_ok=True)
        for k, f in enumerate(frames):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            img = cv2.imread(str(f))
            if img is None:
                continue
            sx, sy = W / img.shape[1], H / img.shape[0]
            crop = img[cy0:cy1, cx0:cx1]
            use_big = big is not None and k % BIG_EVERY == 0
            found = []
            for w, (vx, vy, vw, vh) in enumerate(wins):
                vp = d / f"v{w}" / f.name
                cv2.imwrite(str(vp), crop[vy:vy + vh, vx:vx + vw], [cv2.IMWRITE_JPEG_QUALITY, 93])
                got = list(det.boxes(vp))
                if use_big:
                    try:
                        got += list(big.boxes(vp))
                    except Exception:          # noqa: BLE001 - the fast model alone still tracks
                        big, use_big = None, False
                found += [(x + vx + cx0, y + vy + cy0, bw, bh) for x, y, bw, bh in got]
                vp.unlink(missing_ok=True)
            boxes = merge_boxes(found)
            if not boxes and checker is not None and k % OCC_EVERY == 0:
                try:
                    boxes = locate_with_checker(checker, img)
                except Exception:              # noqa: BLE001 - the third opinion is a bonus
                    checker = None
            boxes = [(x * sx, y * sy, bw * sx, bh * sy) for x, y, bw, bh in boxes]
            samples.append((a + k / SAMPLE_FPS, boxes))
            if progress and k % 10 == 0:
                done_now = done + k / SAMPLE_FPS
                progress(min(1.0, done_now / total), f"Finding the areas to blur ({int(done_now)} of {int(total)} s)")
        done += b - a
        shutil.rmtree(d, ignore_errors=True)
    samples.sort(key=lambda s: s[0])
    return samples


def merge_boxes(boxes, gap=0.1):
    """Boxes that touch or overlap become one box covering both (a body seen by two detectors, or an
    area seen in two neighbouring moments while it moves)."""
    out = [list(b) for b in boxes]
    changed = True
    while changed and len(out) > 1:
        changed = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                x1, y1, w1, h1 = out[i]
                x2, y2, w2, h2 = out[j]
                gx, gy = gap * max(w1, w2), gap * max(h1, h2)
                if x1 - gx <= x2 + w2 and x2 - gx <= x1 + w1 and y1 - gy <= y2 + h2 and y2 - gy <= y1 + h1:
                    x, y = min(x1, x2), min(y1, y2)
                    out[i] = [x, y, max(x1 + w1, x2 + w2) - x, max(y1 + h1, y2 + h2) - y]
                    del out[j]
                    changed = True
                    break
            if changed:
                break
    return [tuple(b) for b in out]


def _pad_box(x, y, w, h, W, H, grow=GROW["normal"]):
    px, py = max(w * grow / 2, 6.0), max(h * grow / 2, 6.0)
    x0, y0 = max(0.0, x - px), max(0.0, y - py)
    x1, y1 = min(float(W), x + w + px), min(float(H), y + h + py)
    return int(x0), int(y0), max(2, int(x1 - x0)), max(2, int(y1 - y0))


def track(samples):
    """Fill the holes a detector leaves while people move: [(time, boxes)] in, same out.
    A gap between two sightings is blurred through; the area is kept for a moment after it's lost
    and picked up a moment before it's found."""
    n = len(samples)
    times = [t for t, _ in samples]
    base = [list(b) for _, b in samples]
    last, nxt_at = [-1] * n, [n] * n                  # nearest sighting at or before / at or after each sample
    for i in range(n):
        last[i] = i if base[i] else (last[i - 1] if i else -1)
    for i in range(n - 1, -1, -1):
        nxt_at[i] = i if base[i] else (nxt_at[i + 1] if i + 1 < n else n)
    out = [list(b) for b in base]
    for i in range(n):
        if base[i]:
            continue
        j, k = last[i], nxt_at[i]
        prev = base[j] if j >= 0 and times[i] - times[j] <= BRIDGE else None
        nxt = base[k] if k < n and times[k] - times[i] <= BRIDGE else None
        if prev and nxt:
            out[i] = prev + nxt
        elif prev and times[i] - times[j] <= HOLD_AFTER:
            out[i] = prev
        elif nxt and times[k] - times[i] <= LEAD_BEFORE:
            out[i] = nxt
    return [(t, b) for t, b in zip(times, out)]


def write_commands(path: Path, samples, ranges, W, H, grow=GROW["normal"]):
    """FFmpeg 'sendcmd' file that moves SLOTS white boxes on the blur mask over time.
    Each sample's boxes are merged with its neighbours', so a moving area stays covered."""
    lines = []

    def place(t, boxes):
        cmds = []
        for i in range(SLOTS):
            if i < len(boxes):
                x, y, w, h = _pad_box(*boxes[i], W, H, grow)
            else:
                x, y, w, h = 0, 0, 1, 1      # parked: a single pixel in the corner
            cmds += [f"drawbox@s{i} x {x}", f"drawbox@s{i} y {y}", f"drawbox@s{i} w {w}", f"drawbox@s{i} h {h}"]
        lines.append(f"{max(0.0, t):.3f} " + ", ".join(cmds) + ";")

    samples = track(samples)
    place(0.0, [])
    for i, (t, boxes) in enumerate(samples):
        nxt = samples[i + 1][1] if i + 1 < len(samples) and samples[i + 1][0] - t <= 1.5 / SAMPLE_FPS else []
        prv = samples[i - 1][1] if i > 0 and t - samples[i - 1][0] <= 1.5 / SAMPLE_FPS else []
        merged = sorted(merge_boxes(boxes + nxt + prv), key=lambda b: -b[2] * b[3])[:SLOTS]
        place(t - 0.5 / SAMPLE_FPS, merged)
    ends = sorted(ranges)
    for n, (a, b) in enumerate(ends):     # nothing outside the chosen scenes (unless the next one starts right away)
        if n + 1 < len(ends) and ends[n + 1][0] <= b + 0.35:
            continue
        place(b + 0.05, [])
    lines.sort(key=lambda ln: float(ln.split(" ", 1)[0]))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def filter_parts(info, ranges, cmd_file_name, enable_expr, keep_original=None):
    """Filtergraph pieces: returns (pre, label) where `pre` turns [0:v:0] into [vparts].
    keep_original="[label]" also leaves an untouched copy of the picture under that label (used to take the blur
    off areas the person marked)."""
    W, H = info.width, info.height
    rate = info.video.get("avg_frame_rate") or info.video.get("r_frame_rate") or "24"
    if rate in ("0/0", "0"):
        rate = "24"
    sar = (info.video.get("sample_aspect_ratio") or "1:1").replace(":", "/")
    if sar in ("0/1", "N/A"):
        sar = "1"
    ten = info.is_10bit
    alpha_fmt = "yuva420p10le" if ten else "yuva420p"
    mask_fmt = "gray10le" if ten else "gray"
    slots = ",".join(f"drawbox@s{i}=x=0:y=0:w=1:h=1:color=white:t=fill" for i in range(SLOTS))
    dur = info.duration + 2
    graph = [
        f"color=c=black:s={W}x{H}:r={rate}:d={dur:.3f},format=gray,setsar={sar},"
        f"sendcmd=f={cmd_file_name},{slots},format={mask_fmt}[pmask]",
        f"[0:v:0]split={3 if keep_original else 2}[pmain][pcopy]{keep_original or ''}",
        f"[pcopy]boxblur=luma_radius='min(w,h)/18':luma_power=4:chroma_radius='min(cw,ch)/18':"
        f"chroma_power=4,format={alpha_fmt}[pblur]",
        "[pblur][pmask]alphamerge[pblurred]",
        f"[pmain][pblurred]overlay=0:0:shortest=1:format={'yuv420p10' if ten else 'yuv420'}:"
        f"enable='{enable_expr}'[vparts]",
    ]
    return graph, "[vparts]"
