"""Sound for the built-in player.

The player's video component can't decode the sound formats movies use (Dolby Digital, DTS,
TrueHD...), so it plays pictures silently. Instead the movie's sound is converted once into a
small stereo AAC file (a couple of minutes for a long movie, then remembered) and played next to the
picture. The converted file is made from the same audio track the clean copy has, so what you
hear is what the file contains."""

import hashlib
import os
import time
from pathlib import Path

from .media import MediaError, audio_map_filter, ffmpeg, run
from .paths import app_data_dir


def _dir():
    d = app_data_dir() / "previews"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tidy(folder, days=14):
    cut = time.time() - days * 86400
    for f in folder.glob("*.m4a"):
        try:
            if f.stat().st_mtime < cut:
                f.unlink()
        except OSError:
            pass


def audio_proxy(path, track=0):
    """Stereo AAC copy of one audio track (cached). Returns its path."""
    path = Path(path)
    st = path.stat()
    key = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}|{track}".encode()).hexdigest()[:20]
    folder = _dir()
    out = folder / f"{key}.m4a"
    if out.exists() and out.stat().st_size > 1000:
        return out
    _tidy(folder)
    tmp = folder / f"{key}.part.m4a"
    fx = audio_map_filter(path, track)
    r = run([ffmpeg(), "-v", "error", "-nostdin", "-y", "-i", str(path), "-map", f"0:a:{track}", "-vn", "-sn",
             *(["-af", fx] if fx else []), "-ac", "2", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(tmp)])
    if r.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise MediaError(f"Couldn't prepare the sound for preview: {(r.stderr or '')[-200:]}")
    os.replace(tmp, out)
    return out


# ---------------------------------------------------------------- "See it edited"

def nearest_edit(project, t):
    """The ticked word or scene closest to time t: (start, end, label) or None."""
    best = None
    for kind, items in (("word", project.active_mutes()), ("scene", project.active_scenes())):
        for it in items:
            a, b = float(it["start"]), float(it["end"])
            d = 0.0 if a <= t <= b else min(abs(a - t), abs(b - t))
            if best is None or d < best[0]:
                label = it.get("words") if kind == "word" else (it.get("label") or "Scene")
                best = (d, a, b, f"{'🔇' if kind == 'word' else '🎬'} {label}")
    from . import paint
    for pt in paint.active(getattr(project, "paint", None)):
        a, b = paint.span(pt)
        d = 0.0 if a <= t <= b else min(abs(a - t), abs(b - t))
        if best is None or d < best[0]:
            best = (d, a, b, f"🖌 {pt.get('label') or 'Painted blur'}")
    return None if best is None else best[1:]


def _shift(items, w0, w1):
    out = []
    for it in items:
        a, b = float(it["start"]), float(it["end"])
        if b > w0 and a < w1:
            out.append(dict(it, start=a - w0, end=b - w0))
    return out


def edited_clip(project, filters, settings, start, end, pad=8.0, progress=None, cancel=None, log=print):
    """Make a short low-resolution copy of the movie around start..end twice: untouched and with
    the real filters applied (the same code that makes the clean copy), so what you see and hear is
    what the clean copy will contain there. Returns {"original", "edited", "start", "end", "summary"}.
    Spoken replacements are left out (they could cost money); everything else is real."""
    import copy
    import shutil
    from .export import ExportOptions
    from .media import probe
    from .pipeline import patterns_for
    from .render import render

    src = Path(project.source)
    info = probe(src)
    if not info.video:
        raise MediaError("This file has no picture to preview.")
    if not info.audio:
        raise MediaError("This file has no audio track, so there is nothing to filter.")
    w0 = max(0.0, float(start) - pad)
    w1 = min(float(info.duration) or (float(end) + pad), float(end) + pad)
    if w1 - w0 > 75:                                   # keep it quick: one long scene is cut down to its start
        w1 = w0 + 75
    st = src.stat()
    track = project.audio_track if 0 <= project.audio_track < len(info.audio) else 0
    key = hashlib.sha1(f"{src.resolve()}|{st.st_size}|{int(st.st_mtime)}|{track}|{w0:.2f}|{w1:.2f}".encode()).hexdigest()[:16]
    folder = _dir() / f"clip-{key}"
    folder.mkdir(parents=True, exist_ok=True)
    orig, edited = folder / "original.mp4", folder / "edited.mp4"
    for old in _dir().glob("clip-*"):                  # keep the folder small
        if old != folder and old.stat().st_mtime < time.time() - 86400:
            shutil.rmtree(old, ignore_errors=True)
    if not (orig.exists() and orig.stat().st_size > 1000):
        r = run([ffmpeg(), "-v", "error", "-nostdin", "-y", "-ss", f"{w0:.3f}", "-t", f"{w1 - w0:.3f}", "-i", str(src),
                 "-map", "0:v:0", "-map", f"0:a:{track}", "-sn", "-dn",
                 *(["-af", audio_map_filter(src, track)] if audio_map_filter(src, track) else []),
                 "-vf", "scale=-2:'min(720,ih)',format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
                 "-crf", "22", "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-movflags", "+faststart",
                 str(folder / "original.part.mp4")])
        part = folder / "original.part.mp4"
        if r.returncode != 0 or not part.exists():
            raise MediaError(f"Couldn't cut the preview clip: {(r.stderr or '')[-200:]}")
        os.replace(part, orig)
    if progress:
        progress(0.2, "Applying the filters…")

    p = copy.deepcopy(project)
    p.source, p.audio_track = str(orig), 0
    p.mutes = _shift(project.mutes, w0, w1)
    p.scenes = _shift(project.scenes, w0, w1)
    from . import paint
    p.paint = [paint.shifted(x, w0) for x in paint.active(project.paint)
               if paint.span(x)[1] > w0 and paint.span(x)[0] < w1]
    p.subtitle_edits, p.burned_edits, p.transcript = [], [], {"track": 0, "words": []}
    f = copy.copy(filters)
    f.advanced = {"enabled": False, "mode": "mute", "engine": "windows"}
    s = copy.copy(settings)
    s.keep_subtitles = s.make_subtitles = False
    s.device = "cpu"
    ex = ExportOptions(container="mp4", resolution="source", quality="high", fast_cuts=False, other_audio=False)
    try:
        res = render(probe(orig), p, f, s, patterns_for(f), edited,
                     (lambda fr, m: progress(0.2 + 0.8 * fr, m)) if progress else None, cancel, log, ex)
    finally:
        try:
            shutil.rmtree(p.dir, ignore_errors=True)   # the stand-in project's scratch folder
        except Exception:                              # noqa: BLE001
            pass
    words = [m for m in p.active_mutes()]
    scenes = [x for x in p.active_scenes()]
    bits = []
    if words:
        bits.append(f"🔇 {len(words)} word{'s' if len(words) != 1 else ''} silenced")
    npaint = len(paint.active(p.paint))
    if npaint:
        bits.append(f"🖌 {npaint} painted blur{'s' if npaint != 1 else ''}")
    for act, name in (("cut", "✂ cut out"), ("black", "⬛ blacked out"), ("black_keep", "⬛ black screen, sound kept"),
                      ("blur", "◌ blurred"), ("blur_parts", "◌ private parts blurred"), ("mute", "🔇 silenced")):
        n = sum(1 for x in scenes if (x.get("action") or filters.scene_action) == act)
        if n:
            bits.append(f"{name}: {n} scene{'s' if n != 1 else ''}")
    return {"original": str(orig), "edited": str(edited), "start": w0, "end": w1,
            "summary": " · ".join(bits) or "no edits in this stretch", "valid": res.get("valid", True),
            "problems": res.get("problems", [])}
