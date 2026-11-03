"""Reading the movie's subtitles during the scan.

* English subtitle tracks are searched for filtered words. Those words are starred out
  in the clean copy (the audio is left alone when the dialogue isn't English).
* In English-language movies the subtitles also catch words speech recognition missed:
  each subtitle match is paired with what was heard at that moment ("ship" for "shit")
  and muted; if nothing similar was heard, it's listed in Review (unticked) to check.
* Picture-based subtitles are read with text recognition (see ocr.py)."""

import difflib
import json
import uuid
from pathlib import Path

from .media import TEXT_SUB_CODECS
from .subtitles import extract_text_sub, read_srt
from .words import find_in_text, normalize

IMAGE_SUB_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}
ENGLISH = {"eng", "en", "english", "und", ""}


def is_english_track(st):
    return (st.get("tags", {}).get("language") or "").lower() in ENGLISH


def read_tracks(info, work_dir: Path, progress=None, cancel=None, log=print, ocr=True):
    """[{index, language, title, forced, default, image, cues}] for English subtitle tracks.
    Picture-based tracks are read with text recognition once and cached."""
    cache_path = Path(work_dir) / "subtitles.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    tracks = []
    subs = [s for s in info.subtitles if is_english_track(s)]
    for n, s in enumerate(subs):
        tags, disp = s.get("tags", {}), s.get("disposition", {})
        codec = s.get("codec_name") or ""
        entry = {"index": s["index"], "language": tags.get("language") or "und", "title": tags.get("title", ""),
                 "forced": bool(disp.get("forced")), "default": bool(disp.get("default")),
                 "image": codec in IMAGE_SUB_CODECS, "codec": codec, "cues": []}
        key = f"{s['index']}:{codec}"
        if key in cache:
            entry["cues"] = cache[key]
        elif codec in TEXT_SUB_CODECS:
            tmp = Path(work_dir) / f"sub{s['index']}.srt"
            if extract_text_sub(info.path, s["index"], tmp):
                entry["cues"] = read_srt(tmp)
                tmp.unlink(missing_ok=True)
        elif codec in IMAGE_SUB_CODECS and ocr:
            try:
                from . import ocr as ocr_mod
                def p(f, m):
                    if progress:
                        progress((n + f) / max(1, len(subs)), m)
                entry["cues"] = ocr_mod.read_image_subtitles(info, s, Path(work_dir), p, cancel, log)
            except ImportError as e:
                log(f"Text recognition isn't available ({e}); picture subtitles can't be read.")
            except Exception as e:
                if cancel is not None and cancel.is_set():
                    raise
                log(f"Couldn't read picture subtitles in track {s['index']}: {e}")
        if entry["cues"]:
            cache[key] = entry["cues"]
        tracks.append(entry)
        if progress:
            progress((n + 1) / max(1, len(subs)), f"Reading subtitles ({n + 1} of {len(subs)})")
    Path(cache_path).write_text(json.dumps(cache), encoding="utf-8")
    return tracks


def _similar(a, b):
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    if a[0] == b[0]:
        r += 0.15
    return r


def subtitle_findings(tracks, patterns, words, mutes, dialogue_english, pad=0.15):
    """Returns (subtitle_items, extra_mutes).
    subtitle_items: one per filtered word in the subtitles (deduplicated across tracks) -
      ticking/unticking decides whether it's starred out.
    extra_mutes: (English movies only) words the subtitles show but speech recognition missed."""
    items, extra = [], []
    seen = {}
    heard = list(words or [])
    for t in tracks:
        for start, end, text in t["cues"]:
            for shown, norm, cat in find_in_text(text, patterns):
                key = (round(float(start), 0), norm)
                if key in seen:
                    seen[key]["tracks"].append(t["index"])
                    continue
                item = {"id": uuid.uuid4().hex[:12], "kind": "subtitle", "start": round(float(start), 2),
                        "end": round(float(end), 2), "words": shown, "norm": norm, "category": cat,
                        "context": text.replace("\n", " "), "tracks": [t["index"]], "on": True,
                        "origin": "subtitles", "cue_key": round(float(start), 1)}
                seen[key] = item
                items.append(item)
                if not dialogue_english:
                    continue
                if _already(norm, mutes, start, end):      # heard and muted already
                    item["heard"] = True
                    continue
                # Find what was heard instead, inside the subtitle's time.
                cands = [w for w in heard if float(start) - 0.6 <= w["s"] <= float(end) + 0.6]
                first = norm.split()[0] if norm else ""
                best = max(cands, key=lambda w: _similar(w["w"], first), default=None)
                if best is not None and _similar(best["w"], first) >= 0.6:
                    extra.append({"id": uuid.uuid4().hex[:12], "start": max(0.0, round(best["s"] - pad, 2)),
                                  "end": round(best["e"] + pad, 2), "category": cat, "on": True,
                                  "words": f"{shown} (heard as \"{best['w'].strip()}\")",
                                  "context": text.replace("\n", " "), "origin": "subtitle-check"})
                else:
                    extra.append({"id": uuid.uuid4().hex[:12], "start": round(float(start), 2),
                                  "end": round(float(end), 2), "category": cat, "on": False,
                                  "words": f"{shown} (in subtitles, not heard clearly)",
                                  "context": text.replace("\n", " "), "origin": "subtitle-check",
                                  "unsure": True})
    return items, extra


def _already(norm, mutes, start, end):
    for m in mutes or []:
        if m["end"] >= float(start) - 0.6 and m["start"] <= float(end) + 0.6:
            mw = " ".join(normalize(x) for x in str(m.get("words", "")).replace("/", " ").split())
            if norm.split()[0] in mw or _similar(mw.split()[0] if mw else "", norm.split()[0]) >= 0.6:
                return True
    return False


def kept_keys(items):
    """Subtitle words the user unticked -> left as they are."""
    return {(i["cue_key"], i["norm"]) for i in items or [] if not i.get("on", True)}
