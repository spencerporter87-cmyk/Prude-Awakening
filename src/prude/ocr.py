"""Reading text from pictures: picture-based subtitles (Blu-ray PGS, DVD, DVB) and, later,
subtitles burned into the picture.

Picture subtitles are drawn by FFmpeg onto a black canvas a few times a second; only the
frames where something changes are kept (usually two per subtitle: on and off). Each one is
read with RapidOCR (small ONNX models that ship with the app, offline)."""

import re
import shutil
from pathlib import Path

import numpy as np

from .media import Cancelled, ffmpeg, run_ffmpeg

FPS = 8                      # how often the subtitles are checked (1/8 s timing)
_engine = None


class OcrUnavailable(ImportError):
    pass


def engine():
    global _engine
    if _engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as e:                       # noqa: BLE001 - any import problem
            raise OcrUnavailable(f"RapidOCR isn't installed ({e})") from e
        _engine = RapidOCR()
    return _engine


def prepare(gray):
    """Crop to the text, make it dark-on-white with a margin, upscale small text."""
    import cv2
    mask = gray > 100
    if not mask.any():
        return None
    ys, xs = np.where(mask)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    crop = gray[max(0, y0 - 6):y1 + 6, max(0, x0 - 6):x1 + 6]
    img = 255 - crop
    h = img.shape[0]
    if h < 60:
        f = 60 / max(1, h) if h < 30 else 1.6
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    img = cv2.copyMakeBorder(img, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def read_text(img_bgr):
    """Text in the picture, top to bottom, one line per row of text."""
    result, _ = engine()(img_bgr)
    if not result:
        return ""
    boxes = []
    for box, text, score in result:
        if float(score) < 0.5 or not str(text).strip():
            continue
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        boxes.append((min(ys), max(ys), min(xs), str(text).strip()))
    boxes.sort()
    lines = []
    for top, bottom, left, text in boxes:
        if lines and top < lines[-1]["bottom"] - 0.5 * (lines[-1]["bottom"] - lines[-1]["top"]):
            lines[-1]["parts"].append((left, text))
            lines[-1]["bottom"] = max(lines[-1]["bottom"], bottom)
        else:
            lines.append({"top": top, "bottom": bottom, "parts": [(left, text)]})
    return "\n".join(" ".join(t for _x, t in sorted(l["parts"])) for l in lines)


def tidy(text):
    """Common OCR slips in subtitles."""
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"(?<=[a-z])0(?=[a-z])", "o", text)
    text = re.sub(r"(?<![A-Za-z])l(?=[A-Z])", "I", text)
    text = text.replace("|", "I")
    return re.sub(r"[ \t]+", " ", text).strip()


def subtitle_frames(info, stream, out_dir: Path, progress=None, cancel=None):
    """[(time, png_path)] at every change of the picture subtitles (blank frames included)."""
    w = int(stream.get("width") or info.width or 1920)
    h = int(stream.get("height") or info.height or 1080)
    out_dir.mkdir(parents=True, exist_ok=True)
    graph = (f"[0:v][1:{stream['index']}]overlay=eof_action=pass,format=gray,"
             f"mpdecimate=hi=768:lo=320:frac=0.1[v]")
    cmd = [ffmpeg(), "-hide_banner", "-y", "-f", "lavfi", "-i",
           f"color=c=black:s={w}x{h}:r={FPS}:d={info.duration:.3f}", "-i", str(info.path),
           "-filter_complex", graph, "-map", "[v]", "-fps_mode", "passthrough", "-frame_pts", "1",
           str(out_dir / "%d.png")]
    run_ffmpeg(cmd, info.duration, progress, cancel, "Finding the subtitles")
    frames = []
    for p in out_dir.glob("*.png"):
        try:
            frames.append((int(p.stem) / FPS, p))
        except ValueError:
            pass
    return sorted(frames)


def read_image_subtitles(info, stream, work_dir: Path, progress=None, cancel=None, log=print):
    """Picture subtitles -> [[start, end, text]]."""
    import cv2
    engine()                                  # fail early (and clearly) if OCR is missing
    tmp = Path(work_dir) / f"ocr-{stream['index']}"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        frames = subtitle_frames(info, stream, tmp,
                                 (lambda f, m: progress(0.3 * f, m)) if progress else None, cancel)
        cues, seen = [], {}
        lag = 0.5 / FPS                        # changes are seen up to one check late
        for i, (t, path) in enumerate(frames):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            img = prepare(gray) if gray is not None else None
            if img is None:
                continue
            end = frames[i + 1][0] if i + 1 < len(frames) else info.duration
            key = hash(img.tobytes())
            text = seen.get(key)
            if text is None:
                text = tidy(read_text(img))
                seen[key] = text
            if not text:
                continue
            start = max(0.0, round(t - lag, 3))
            end = round(max(start + 0.3, end - lag), 3)
            if cues and cues[-1][2] == text and start - cues[-1][1] < 0.3:
                cues[-1][1] = end
            else:
                cues.append([start, end, text])
            if progress and i % 10 == 0:
                progress(0.3 + 0.7 * i / max(1, len(frames)),
                         f"Reading picture subtitles ({len(cues)} lines so far)")
        log(f"Read {len(cues)} subtitle lines from pictures (track {stream['index']}).")
        return cues
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
