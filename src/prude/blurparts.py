"""Blur only the exposed body parts, not the whole picture.

For each scene marked "blur the private parts", frames are sampled several times a
second, the nudity detector finds each exposed area, and FFmpeg moves blur patches
over those areas while the movie is re-encoded. The rest of the picture and the sound
are untouched."""

import shutil
from pathlib import Path

from .media import Cancelled, ffmpeg, run

SAMPLE_FPS = 5           # detections per second inside blurred scenes
SLOTS = 6                # most areas blurred at once
PAD = 0.35               # grow each box by 35% so nothing peeks out as people move
MIN_SCORE = 0.3
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

    def __init__(self, classes):
        from nudenet import NudeDetector
        self.det = NudeDetector()
        self.classes = set(classes)

    def boxes(self, image_path):
        out = []
        for d in self.det.detect(str(image_path)) or []:
            if d.get("class") in self.classes and float(d.get("score", 0)) >= MIN_SCORE:
                x, y, w, h = [float(v) for v in d["box"]]
                out.append((x, y, w, h))
        return out


def detect(info, ranges, classes, work_dir: Path, progress=None, cancel=None, detector=None):
    """Sample each range and find the areas to blur. Returns [(time, [(x, y, w, h)])] in
    the movie's own pixel size, sorted by time."""
    det = detector or Detector(classes)
    W, H = info.width, info.height
    samples = []
    total = sum(b - a for a, b in ranges) or 1.0
    done = 0.0
    for n, (a, b) in enumerate(ranges):
        d = Path(work_dir) / f"blur_{n}"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        r = run([ffmpeg(), "-y", "-loglevel", "error", "-ss", f"{a:.3f}", "-i", str(info.path),
                 "-t", f"{b - a:.3f}", "-map", "0:v:0", "-an", "-sn", "-vf",
                 f"fps={SAMPLE_FPS},scale=640:-2", "-q:v", "4", str(d / "%06d.jpg")])
        if r.returncode != 0:
            continue
        frames = sorted(d.glob("*.jpg"))
        for k, f in enumerate(frames):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            import cv2
            img = cv2.imread(str(f))
            if img is None:
                continue
            sx, sy = W / img.shape[1], H / img.shape[0]
            boxes = [(x * sx, y * sy, w * sx, h * sy) for x, y, w, h in det.boxes(f)]
            samples.append((a + k / SAMPLE_FPS, boxes))
            if progress and k % 10 == 0:
                done_now = done + k / SAMPLE_FPS
                progress(min(1.0, done_now / total), f"Finding the areas to blur ({int(done_now)} of {int(total)} s)")
        done += b - a
        shutil.rmtree(d, ignore_errors=True)
    samples.sort(key=lambda s: s[0])
    return samples


def _pad_box(x, y, w, h, W, H):
    px, py = w * PAD / 2, h * PAD / 2
    x0, y0 = max(0.0, x - px), max(0.0, y - py)
    x1, y1 = min(float(W), x + w + px), min(float(H), y + h + py)
    return int(x0), int(y0), max(2, int(x1 - x0)), max(2, int(y1 - y0))


def write_commands(path: Path, samples, ranges, W, H):
    """FFmpeg 'sendcmd' file that moves SLOTS white boxes on the blur mask over time.
    Each sample's boxes are merged with the next sample's, so a moving area stays covered."""
    lines = []

    def place(t, boxes):
        cmds = []
        for i in range(SLOTS):
            if i < len(boxes):
                x, y, w, h = _pad_box(*boxes[i], W, H)
            else:
                x, y, w, h = 0, 0, 1, 1      # parked: a single pixel in the corner
            cmds += [f"drawbox@s{i} x {x}", f"drawbox@s{i} y {y}", f"drawbox@s{i} w {w}", f"drawbox@s{i} h {h}"]
        lines.append(f"{max(0.0, t):.3f} " + ", ".join(cmds) + ";")

    place(0.0, [])
    for i, (t, boxes) in enumerate(samples):
        nxt = samples[i + 1][1] if i + 1 < len(samples) and samples[i + 1][0] - t <= 1.5 / SAMPLE_FPS else []
        prv = samples[i - 1][1] if i > 0 and t - samples[i - 1][0] <= 1.5 / SAMPLE_FPS else []
        merged = sorted(boxes + nxt + prv, key=lambda b: -b[2] * b[3])[:SLOTS]
        place(t - 0.5 / SAMPLE_FPS, merged)
    for a, b in ranges:     # nothing outside the chosen scenes
        place(b + 0.05, [])
    lines.sort(key=lambda ln: float(ln.split(" ", 1)[0]))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def filter_parts(info, ranges, cmd_file_name, enable_expr):
    """Filtergraph pieces: returns (pre, label) where `pre` turns [0:v:0] into [vparts]."""
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
        "[0:v:0]split=2[pmain][pcopy]",
        f"[pcopy]boxblur=luma_radius='min(w,h)/18':luma_power=4:chroma_radius='min(cw,ch)/18':"
        f"chroma_power=4,format={alpha_fmt}[pblur]",
        "[pblur][pmask]alphamerge[pblurred]",
        f"[pmain][pblurred]overlay=0:0:shortest=1:format={'yuv420p10' if ten else 'yuv420'}:"
        f"enable='{enable_expr}'[vparts]",
    ]
    return graph, "[vparts]"
