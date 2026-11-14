"""A second look at the finished copy: anything that slipped through is listed, with a way to fix it.

Four checks, each independent (one failing never hides the others):
  words     every silenced stretch really is silent in the finished file (measured, not assumed)
  subtitles no listed word is left in the finished copy's subtitles
  picture   a stricter nudity pass over the finished picture, looking at different instants than the first scan
  listen    (slower, optional) the finished sound is transcribed again and the word list applied to it

Times: every finding has `out_start/out_end` (in the finished copy) and `start/end` (in the original movie,
which is what the paint tool and the review list use)."""

import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from .media import Cancelled, audio_map_filter, ffmpeg, probe
from .paths import NO_WINDOW

SILENT_DB = -48.0          # a silenced stretch measuring louder than this was not silenced
EDGE = 0.06                # ignore the first and last moments of a silence (the fade)
PHASE = 0.5                # the picture check looks at the half-seconds between the first scan's samples
RATE = 8000                # audio rate used for measuring

KINDS = {"mute": "A silenced word is still audible", "heard": "A listed word was heard again",
         "subtitle": "A listed word is in the subtitles", "picture": "Possible exposed body"}


# ------------------------------------------------------------------ time mapping
def movie_to_out(t, cuts):
    """A time in the original movie -> the same moment in the finished copy (cuts are taken out)."""
    removed = 0.0
    for a, b in sorted((float(a), float(b)) for a, b in cuts):
        if t >= b:
            removed += b - a
        elif t > a:
            removed += t - a
    return t - removed


def out_to_movie(t, cuts):
    """The reverse: a time in the finished copy -> the original movie's time."""
    for a, b in sorted((float(a), float(b)) for a, b in cuts):
        if t >= a - 1e-6:
            t += b - a
    return t


def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def _finding(kind, out_a, out_b, cuts, text="", detail="", score=None, thumb="", thumb_blur=""):
    return {"id": uuid.uuid4().hex[:10], "kind": kind, "text": text, "detail": detail,
            "out_start": round(float(out_a), 2), "out_end": round(float(out_b), 2),
            "start": round(out_to_movie(float(out_a), cuts), 2), "end": round(out_to_movie(float(out_b), cuts), 2),
            "score": None if score is None else round(float(score), 2), "thumb": thumb, "thumb_blur": thumb_blur}


def key_of(f):
    """Stable identity of a finding, so a dismissed one stays dismissed after the next check."""
    return f"{f['kind']}:{int(round(float(f['start'])))}:{(f.get('text') or '')[:20].lower()}"


# ------------------------------------------------------------------ 1. silenced words
def decode_audio(path, track=0, cancel=None, rate=RATE):
    """The finished file's sound as mono floats (-1..1)."""
    import numpy as np
    mf = audio_map_filter(path, track)
    cmd = [ffmpeg(), "-v", "error", "-i", str(path), "-map", f"0:a:{track}"]
    if mf:
        cmd += ["-af", mf]
    cmd += ["-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
    chunks = []
    try:
        while True:
            buf = p.stdout.read(1 << 20)
            if not buf:
                break
            chunks.append(buf)
            if cancel is not None and cancel.is_set():
                raise Cancelled()
    finally:
        if p.poll() is None:
            p.kill()
        p.stdout.close()
        p.wait()
    raw = b"".join(chunks)
    return np.frombuffer(raw[:len(raw) // 2 * 2], dtype="<i2").astype("float32") / 32768.0


def level_db(samples, a, b, rate=RATE):
    """RMS level in dB (full scale = 0) of the stretch a..b seconds."""
    import numpy as np
    i, j = int(a * rate), int(b * rate)
    seg = samples[max(0, i):max(0, j)]
    if len(seg) < 8:
        return None
    rms = float(np.sqrt(np.mean(np.square(seg, dtype="float64"))))
    return -200.0 if rms <= 1e-9 else 20.0 * float(np.log10(rms))


def silenced_words(samples, spans, cuts, names, rate=RATE):
    """spans: stretches (original time) that were silenced. Returns findings for those that aren't silent."""
    out, n = [], len(samples) / rate
    for a, b in spans:
        oa, ob = movie_to_out(a, cuts), movie_to_out(b, cuts)
        if ob - oa < 2 * EDGE + 0.04 or oa >= n:
            continue                                        # cut away, or too short to measure
        db = level_db(samples, oa + EDGE, min(ob, n) - EDGE, rate)
        if db is not None and db > SILENT_DB:
            words = names(a, b)
            out.append(_finding("mute", oa, ob, cuts, words,
                                f"Should be silent, but it measures {db:.0f} dB.", score=min(1.0, (db + 60) / 40)))
    return out


# ------------------------------------------------------------------ 2. subtitles
def subtitle_leaks(out_path, info, patterns, cuts, work, log=print):
    from .subscan import read_tracks
    from .subtitles import read_srt
    from .words import censor_text
    found, groups = [], []
    try:
        for t in read_tracks(info, work, None, None, log, ocr=False):
            groups.append((t.get("title") or t.get("language") or "subtitles", t["cues"]))
    except Cancelled:
        raise
    except Exception as e:                                  # noqa: BLE001 - embedded tracks are optional
        log(f"Couldn't read the subtitles inside the file ({e}).")
    out = Path(out_path)
    for side in sorted(out.parent.glob(f"{out.stem}.*.srt")):
        try:
            groups.append((side.name, read_srt(side)))
        except Exception as e:                              # noqa: BLE001
            log(f"Couldn't read {side.name} ({e}).")
    for label, cues in groups:
        for a, b, text in cues:
            _, n = censor_text(text, patterns, "stars")
            if n:
                snippet = " ".join(str(text).split())[:90]
                found.append(_finding("subtitle", a, b, cuts, snippet, f"In {label}.", score=1.0))
    return found


# ------------------------------------------------------------------ 3. picture
def nudity_types(filters):
    from .scenes import NUDE_TYPES, expand_types
    return [t for t in expand_types(filters.scenes) if t in NUDE_TYPES]


def strict_filters(filters):
    """The same nudity choices, one notch more sensitive."""
    from .settings import FilterOptions
    base = int((filters.scene_sensitivity or {}).get("nudity", filters.sensitivity))
    return FilterOptions(scenes=nudity_types(filters), sensitivity=min(5, base + 1),
                         scene_sensitivity={"nudity": min(5, base + 1)}, scene_action="cut",
                         scene_actions={}, scene_descriptions="")


def picture_leaks(info, project, filters, settings, cuts, work, thumbs, progress=None, cancel=None, log=print):
    from . import paint, scenes as scene_mod
    types = nudity_types(filters)
    if not types:
        return None
    found = scene_mod.watch(info, work, settings.models_path, strict_filters(filters), progress, cancel, log,
                            phase=PHASE)
    cleared = [paint.span(p) for p in paint.clear_patches(getattr(project, "paint", None))]
    left_in = [(float(s["start"]), float(s["end"])) for s in project.scenes if not s.get("on", True)]
    thumbs.mkdir(parents=True, exist_ok=True)
    out = []
    for s in found:
        f = _finding("picture", s["start"], s["end"], cuts, s.get("detail") or s.get("label", "nudity"),
                     s.get("why", ""), score=s.get("score"))
        length = max(0.5, f["end"] - f["start"])
        if any(_overlap(f["start"], f["end"], a, b) > 0 for a, b in cleared):
            continue                                        # you took the blur off there yourself
        if any(_overlap(f["start"], f["end"], a, b) >= 0.5 * length for a, b in left_in):
            continue                                        # a scene you chose to leave in
        for key, dest in (("thumb", f"{f['id']}.jpg"), ("thumb_blur", f"{f['id']}_blur.jpg")):
            src = s.get(key)
            if src and Path(src).exists():
                shutil.copyfile(src, thumbs / dest)
                f[key] = str(thumbs / dest)
        out.append(f)
    return out


# ------------------------------------------------------------------ 4. listen again
def listen_again(info, project, filters, settings, cuts, work, progress=None, cancel=None, log=print):
    from .pipeline import dialogue_is_english, patterns_for
    from .transcribe import extract_dialogue_wav, transcribe
    from .words import find_matches
    if not dialogue_is_english(project):
        return None
    wav = Path(work) / "listen.wav"
    try:
        extract_dialogue_wav(info, 0, wav, None, cancel)
        words, _m, _d = transcribe(wav, info.duration, settings.models_path, project.language,
                                   settings.speech_model, settings.device, progress, cancel, log)
    finally:
        wav.unlink(missing_ok=True)
    hits = find_matches(words, patterns_for(filters), 0.05, 0.05, filters.allow_words)
    return [_finding("heard", h["start"], h["end"], cuts, h.get("words", ""),
                     "Heard again in the finished copy.", score=0.9) for h in hits]


# ------------------------------------------------------------------ the whole check
def _ranges_from_project(project, filters, duration):
    from .render import clamp_ranges, merge_ranges, scene_ranges
    mutes = merge_ranges(clamp_ranges(((m["start"], m["end"]) for m in project.active_mutes()), duration))
    r = scene_ranges(project, filters.scene_action, duration, float(getattr(filters, "blur_margin", 0.0) or 0.0))
    return {"cut_ranges": r["cut"], "mute_ranges": mutes,
            "silent_ranges": merge_ranges(r["black"] + r["mute"]), "mute_mode": filters.mute_mode}


def last_result(project, output):
    for e in reversed(project.exports or []):
        if str(e.get("output")) == str(output):
            return e
    return None


def summary(findings, skipped, ran):
    """One sentence for the finished-copy screen."""
    if not ran:
        return "The check couldn't run."
    if not findings:
        what = ", ".join(ran)
        return f"✓ Nothing slipped through (checked: {what})."
    n = len(findings)
    parts = []
    for k, one, many in (("picture", "a moment in the picture", "moments in the picture"),
                         ("mute", "a silence that isn't", "silences that aren't"),
                         ("heard", "a word heard again", "words heard again"),
                         ("subtitle", "a line in the subtitles", "lines in the subtitles")):
        c = sum(1 for f in findings if f["kind"] == k)
        if c:
            parts.append(one if c == 1 else f"{c} {many}")
    return f"⚠ {n} thing{'s' if n != 1 else ''} to look at: " + ", ".join(parts) + "."


def run(project, filters, settings, result=None, output=None, picture=True, listen=False,
        progress=None, cancel=None, log=print):
    """Check the finished copy and remember what was found in project.leaks."""
    from .pipeline import patterns_for
    out = Path(output or (result or {}).get("output") or project.last_output or "")
    if not out.is_file():
        raise FileNotFoundError(f"The finished copy isn't there: {out}")
    result = result or last_result(project, str(out)) or {}
    approx = "mute_ranges" not in result
    if approx:
        result = dict(result, **_ranges_from_project(project, filters, probe(project.source).duration))
    cuts = [(float(a), float(b)) for a, b in result.get("cut_ranges", [])]
    info = probe(out)

    def say(stage, lo, hi):
        return (lambda f, m: progress(stage, lo + (hi - lo) * max(0.0, min(1.0, f)), m)) if progress else None

    work = Path(tempfile.mkdtemp(prefix="prude-leak-", dir=str(project.dir)))
    thumbs = project.dir / "leak-thumbs"
    shutil.rmtree(thumbs, ignore_errors=True)
    findings, ran, skipped = [], [], []
    t0 = time.time()
    try:
        # ---- words
        if progress:
            progress("check", 0.0, "Measuring every silenced word")
        if result.get("voices_replaced"):
            skipped.append("silences (words were replaced with other voices)")
        elif result.get("mute_mode") == "center":
            skipped.append("silences (only the centre speaker was muted)")
        elif not info.audio:
            skipped.append("silences (the finished copy has no sound)")
        else:
            try:
                samples = decode_audio(out, 0, cancel)
                spans = [tuple(x) for x in result.get("mute_ranges", [])] + \
                        [tuple(x) for x in result.get("silent_ranges", [])]

                def names(a, b):
                    hit = [m.get("words", "") for m in project.mutes if m.get("on", True) and
                           _overlap(float(m["start"]), float(m["end"]), a, b) > 0]
                    return " / ".join(dict.fromkeys(h for h in hit if h))[:60]
                findings += silenced_words(samples, spans, cuts, names)
                ran.append("silenced words")
            except Cancelled:
                raise
            except Exception as e:                          # noqa: BLE001
                skipped.append(f"silences ({str(e)[:80]})")
        # ---- subtitles
        if progress:
            progress("check", 0.05, "Reading the finished subtitles")
        try:
            findings += subtitle_leaks(out, info, patterns_for(filters), cuts, work, log)
            ran.append("subtitles")
        except Cancelled:
            raise
        except Exception as e:                              # noqa: BLE001
            skipped.append(f"subtitles ({str(e)[:80]})")
        # ---- picture
        hi = 0.9 if listen else 1.0
        if picture:
            try:
                got = picture_leaks(info, project, filters, settings, cuts, work / "picture", thumbs,
                                    say("check", 0.08, hi), cancel, log)
                if got is None:
                    skipped.append("picture (no nudity filter was on)")
                else:
                    findings += got
                    ran.append("picture")
            except Cancelled:
                raise
            except Exception as e:                          # noqa: BLE001
                log(f"The picture check failed: {e}")
                skipped.append(f"picture ({str(e)[:80]})")
        # ---- listen again
        if listen:
            try:
                got = listen_again(info, project, filters, settings, cuts, work, say("check", hi, 1.0), cancel, log)
                if got is None:
                    skipped.append("listening again (the dialogue isn't English)")
                else:
                    findings += got
                    ran.append("listening again")
            except Cancelled:
                raise
            except Exception as e:                          # noqa: BLE001
                log(f"Listening again failed: {e}")
                skipped.append(f"listening again ({str(e)[:80]})")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    dismissed = set((getattr(project, "leaks", None) or {}).get("dismissed", []))
    findings = [f for f in findings if key_of(f) not in dismissed]
    findings.sort(key=lambda f: f["start"])
    project.leaks = {"output": str(out), "checked_at": time.strftime("%Y-%m-%d %H:%M"), "ran": ran,
                     "skipped": skipped, "findings": findings, "dismissed": sorted(dismissed),
                     "approximate": approx, "seconds": round(time.time() - t0, 1),
                     "summary": summary(findings, skipped, ran)}
    project.save()
    log(project.leaks["summary"])
    return project.leaks


# ------------------------------------------------------------------ fixing what was found
def dismiss(project, finding_id):
    """Not a real problem: don't list it again."""
    lk = getattr(project, "leaks", None) or {}
    f = next((x for x in lk.get("findings", []) if x["id"] == finding_id), None)
    if not f:
        return
    lk["findings"] = [x for x in lk["findings"] if x["id"] != finding_id]
    lk["dismissed"] = sorted(set(lk.get("dismissed", [])) | {key_of(f)})
    lk["summary"] = summary(lk["findings"], lk.get("skipped", []), lk.get("ran", []))
    project.leaks = lk
    project.save()


def mute_it(project, finding_id, pad=0.15):
    """Silence a word the check heard again in the finished copy."""
    lk = getattr(project, "leaks", None) or {}
    f = next((x for x in lk.get("findings", []) if x["id"] == finding_id), None)
    if not f or f["kind"] != "heard":
        return None
    m = {"id": uuid.uuid4().hex[:8], "start": round(max(0.0, f["start"] - pad), 2), "end": round(f["end"] + pad, 2),
         "words": f.get("text") or "(added from the check)", "context": "", "category": "custom",
         "on": True, "manual": True, "origin": "manual"}
    project.mutes.append(m)
    project.mutes.sort(key=lambda x: x["start"])
    lk["findings"] = [x for x in lk["findings"] if x["id"] != finding_id]
    lk["summary"] = summary(lk["findings"], lk.get("skipped", []), lk.get("ran", []))
    project.leaks = lk
    project.save()
    return m
