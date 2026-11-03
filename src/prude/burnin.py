"""Subtitles burned into the picture (part of the image, can't be switched off).

The lower part of the picture is read with text recognition twice a second. When a line
contains a filtered word, that word's spot is blurred for as long as the line is on screen.
This is slow (it reads the whole movie), so it runs only when asked, or automatically for
foreign-language films that have no subtitle tracks and show English text in a quick check."""

import json
import subprocess
import uuid
from pathlib import Path

import numpy as np

from . import ocr
from .media import Cancelled, ffmpeg
from .paths import NO_WINDOW
from .words import find_in_text

FPS = 2
BAND = 0.40                 # bottom 40% of the picture
WIDTH = 960                 # read at this width


def _geometry(info):
    w, h = info.width or 1920, info.height or 1080
    band_src = int(h * BAND) // 2 * 2
    out_h = max(32, int(round(WIDTH * band_src / w / 2)) * 2)
    return w, h, band_src, out_h


def _frames(info, times=None, cancel=None):
    """Yield (time, gray band) - every 1/FPS s, or at the given times only."""
    w, h, band_src, out_h = _geometry(info)
    vf = f"crop=iw:{band_src}:0:ih-{band_src},scale={WIDTH}:{out_h},format=gray"
    size = WIDTH * out_h
    if times is not None:
        for t in times:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            r = subprocess.run([ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", str(info.path),
                                "-map", "0:v:0", "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-"],
                               capture_output=True, creationflags=NO_WINDOW)
            if len(r.stdout) >= size:
                yield t, np.frombuffer(r.stdout[:size], np.uint8).reshape(out_h, WIDTH)
        return
    cmd = [ffmpeg(), "-v", "error", "-nostdin", "-hwaccel", "auto", "-i", str(info.path), "-map", "0:v:0",
           "-vf", f"fps={FPS},{vf}", "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    try:
        i = 0
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            buf = proc.stdout.read(size)
            if not buf or len(buf) < size:
                break
            yield i / FPS, np.frombuffer(buf, np.uint8).reshape(out_h, WIDTH)
            i += 1
    finally:
        proc.kill()
        proc.wait()


def _lines(band):
    """[(text, x0, y0, x1, y1)] in band pixels."""
    import cv2
    img = cv2.cvtColor(band, cv2.COLOR_GRAY2BGR)
    try:
        result, _ = ocr.engine()(img, use_cls=False)
    except TypeError:
        result, _ = ocr.engine()(img)
    out = []
    for box, text, score in result or []:
        if float(score) < 0.6 or not str(text).strip():
            continue
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        out.append((ocr.tidy(str(text)), min(xs), min(ys), max(xs), max(ys)))
    return out


def _word_box(line, start, end):
    """Spot of characters start..end inside a line box (characters are about the same width)."""
    text, x0, y0, x1, y1 = line
    n = max(1, len(text))
    cw = (x1 - x0) / n
    a = x0 + cw * start - cw * 0.6
    b = x0 + cw * end + cw * 0.6
    pad = (y1 - y0) * 0.15
    return max(0.0, a), max(0.0, y0 - pad), b, y1 + pad


def _find(text, norm_words):
    """Character positions of each filtered word in the text."""
    low = text.lower()
    spots = []
    for shown, norm, cat in norm_words:
        k = low.find(shown.lower())
        if k >= 0:
            spots.append((shown, norm, cat, k, k + len(shown)))
    return spots


def has_text(info, cancel=None, samples=40):
    """Quick check: does English-looking text show up in the lower picture?"""
    dur = info.duration or 0
    if dur < 60:
        times = [dur * (i + 0.5) / 10 for i in range(10)]
    else:
        times = [dur * (i + 0.5) / samples for i in range(samples)]
    hits = 0
    for _t, band in _frames(info, times, cancel):
        words = " ".join(t for t, *_ in _lines(band)).split()
        if sum(1 for w in words if w.isalpha() and len(w) > 2) >= 3:
            hits += 1
    return hits >= 3


def scan(info, patterns, work_dir: Path, progress=None, cancel=None, log=print):
    """Burned-in subtitle words: [{id, start, end, words, norm, category, context, box, on, origin}].
    Cached in the project folder (burnin.json)."""
    cache = Path(work_dir) / "burnin.json"
    try:
        seen_lines = json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        seen_lines = []            # [[t, text, x0, y0, x1, y1]]
    if not seen_lines:
        w, h, band_src, out_h = _geometry(info)
        prev, prev_lines = None, []
        total = max(1, int((info.duration or 1) * FPS))
        for n, (t, band) in enumerate(_frames(info, cancel=cancel)):
            if prev is not None and float(np.mean(np.abs(band.astype(np.int16) - prev))) < 2.0:
                lines = prev_lines
            else:
                lines = _lines(band) if (band > 190).mean() > 0.002 else []
            prev, prev_lines = band.astype(np.int16), lines
            for text, x0, y0, x1, y1 in lines:
                seen_lines.append([round(t, 2), text, x0, y0, x1, y1])
            if progress and n % 20 == 0:
                progress(min(1.0, n / total), f"Reading text in the picture ({int(t // 60)} of "
                                              f"{int((info.duration or 0) // 60)} min)")
        cache.write_text(json.dumps(seen_lines), encoding="utf-8")
    return words_from_lines(info, seen_lines, patterns)


def words_from_lines(info, seen_lines, patterns):
    """Group sightings of the same word at the same spot into blur boxes (frame pixels)."""
    w, h, band_src, out_h = _geometry(info)
    scale = w / WIDTH
    top = h - band_src
    hits = []                              # (t, shown, norm, cat, x0, y0, x1, y1, text)
    for t, text, *box in seen_lines:
        found = find_in_text(text, patterns)
        if not found:
            continue
        line = (text, *box)
        for shown, norm, cat, a, b in _find(text, found):
            x0, y0, x1, y1 = _word_box(line, a, b)
            hits.append((t, shown, norm, cat, x0 * scale, top + y0 * scale, x1 * scale, top + y1 * scale, text))
    hits.sort()
    items = []
    for t, shown, norm, cat, x0, y0, x1, y1, text in hits:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        for it in reversed(items):
            if it["norm"] == norm and t - it["_last"] <= 1.5 / FPS + 0.01 and \
                    abs(it["_cx"] - cx) < (x1 - x0) and abs(it["_cy"] - cy) < (y1 - y0):
                it["_last"] = t
                b = it["box"]
                it["box"] = [min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1)]
                break
        else:
            items.append({"id": uuid.uuid4().hex[:12], "kind": "burned", "_first": t, "_last": t,
                          "_cx": cx, "_cy": cy, "words": shown, "norm": norm, "category": cat,
                          "context": text, "box": [x0, y0, x1, y1], "on": True, "origin": "burned-in"})
    out = []
    for it in items:
        x0, y0, x1, y1 = it.pop("box")
        it["start"] = round(max(0.0, it.pop("_first") - 0.6), 2)
        it["end"] = round(it.pop("_last") + 0.6, 2)
        it.pop("_cx"), it.pop("_cy")
        x0, y0 = max(0, int(x0) // 2 * 2), max(0, int(y0) // 2 * 2)
        x1, y1 = min(w, int(x1 + 1.999)), min(h, int(y1 + 1.999))
        it["box"] = [x0, y0, max(2, (x1 - x0) // 2 * 2), max(2, (y1 - y0) // 2 * 2)]
        out.append(it)
    return out


def filter_graph(boxes, src):
    """Blur each box during its time. boxes: [(start, end, x, y, w, h)]. Returns (graph, label)."""
    graph, cur = [], src
    for i, (a, b, x, y, w, h) in enumerate(boxes[:300]):
        en = f"between(t,{a:.3f},{b:.3f})"
        graph += [f"{cur}split=2[bm{i}][bc{i}]",
                  f"[bc{i}]crop={w}:{h}:{x}:{y},boxblur=luma_radius='min(w,h)/3':luma_power=3:"
                  f"chroma_radius='min(cw,ch)/3':chroma_power=3:enable='{en}'[bb{i}]",
                  f"[bm{i}][bb{i}]overlay={x}:{y}:enable='{en}'[bo{i}]"]
        cur = f"[bo{i}]"
    return graph, cur
