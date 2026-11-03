"""Scan and render, step by step, with progress for the app window."""

import uuid
from pathlib import Path

from . import models, scenes as scene_mod
from .media import Cancelled, probe
from .render import output_path, render
from .subscan import read_tracks, subtitle_findings
from .transcribe import detect_language, extract_dialogue_wav, transcribe
from .words import build_patterns, find_matches, merge_intervals, normalize

STAGES = [
    ("gpu", "Getting the graphics-card speed-up pack"),
    ("audio", "Pulling out the dialogue"),
    ("speech", "Listening for words"),
    ("subs", "Reading the subtitles"),
    ("words", "Matching your word list"),
    ("models", "Getting the scene model"),
    ("frames", "Watching the movie"),
    ("analyze", "Looking for scenes"),
]


def _overlap(a, b):
    """Overlap of two time ranges as a share of their combined span (0..1)."""
    a0, a1, b0, b1 = float(a["start"]), float(a["end"]), float(b["start"]), float(b["end"])
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    union = max(a1, b1) - min(a0, b0)
    return inter / union if union > 0 else 0.0


def _text(item):
    return " ".join(normalize(w) for w in str(item.get("words", "")).replace("/", " ").split())


def _match_score(n, o):
    """How likely a new finding is the same edit as an old one (0 = different)."""
    if n.get("kind") == "subtitle" or o.get("kind") == "subtitle":
        same = n.get("norm") == o.get("norm") and abs(float(n["start"]) - float(o["start"])) <= 1.0
        return 2.0 if same else 0.0
    if "kind" in n or "kind" in o:                       # scenes
        same = n.get("kind") == o.get("kind") or (n.get("group") and n.get("group") == o.get("group"))
        ov = _overlap(n, o)
        return ov + 0.5 if same and ov >= 0.3 else 0.0
    # words: same words said at (nearly) the same moment
    close = abs(float(n["start"]) - float(o["start"])) <= 1.5
    if close and _text(n) and _text(n) == _text(o):
        return 2.0 - abs(float(n["start"]) - float(o["start"]))
    if _text(n) and _text(o) and _text(n) != _text(o):
        return 0.0                                       # different words are different edits
    ov = _overlap(n, o)
    return ov if ov >= 0.5 and n.get("category") == o.get("category") else 0.0


def _carry_over(new, old):
    """Keep IDs, on/off choices, chosen actions, adjusted times and hand-made entries
    across re-scans. Findings are matched by what they are and how much they overlap,
    not by rounded start times."""
    pool = [o for o in old if not o.get("manual")]
    taken = set()
    for n in new:
        best, best_score = None, 0.0
        for i, o in enumerate(pool):
            if i in taken:
                continue
            sc = _match_score(n, o)
            if sc > best_score:
                best, best_score = i, sc
        o = pool[best] if best is not None else None
        if best is not None:
            taken.add(best)
        n["id"] = (o or {}).get("id") or uuid.uuid4().hex[:12]
        n.setdefault("origin", "auto")
        if o is not None:
            n["on"] = o.get("on", True)
            for k in ("action", "action_set", "replacement"):
                if o.get("action_set" if k != "replacement" else k):
                    n[k] = o.get(k)
            if o.get("edited"):
                n["start"], n["end"], n["edited"] = o["start"], o["end"], True
        else:
            n.setdefault("on", True)
    manual = [o for o in old if o.get("manual")]
    return sorted(new + manual, key=lambda x: float(x["start"]))


def patterns_for(filters):
    return build_patterns(filters.words, filters.custom_words, filters.allow_words,
                          getattr(filters, "excluded_words", ()))


def find_word_mutes(project, filters, extra=()):
    """extra: mutes found through the subtitles (words speech recognition missed)."""
    words = (project.transcript.get("words") or []) if dialogue_is_english(project) else []
    hits = find_matches(words, patterns_for(filters), filters.padding, filters.padding,
                        filters.allow_words)
    merged = merge_intervals(hits)
    for m in merged:
        m["start"], m["end"] = round(m["start"], 2), round(m["end"], 2)
    project.mutes = _carry_over(merged + [dict(e) for e in extra], project.mutes)
    return project.mutes


def dialogue_is_english(project):
    if project.dialogue == "en":
        return True
    if project.dialogue == "foreign":
        return False
    return project.english_share >= 0.25


def scan(project, filters, settings, progress=None, cancel=None, log=print, get_gpu_pack=False):
    """progress(stage, fraction, message)."""
    def p(stage):
        return (lambda f, m: progress(stage, f, m)) if progress else None

    info = probe(project.source)
    if project.audio_track >= len(info.audio):
        project.audio_track = 0
    mdir = settings.models_path
    mdir.mkdir(parents=True, exist_ok=True)

    need_listen = not project.has_transcript(project.audio_track) or (
        project.transcript.get("skipped") and dialogue_is_english(project))
    if need_listen:
        if get_gpu_pack and not models.gpu_pack_ready(mdir):
            try:
                models.ensure_gpu_pack(mdir, p("gpu"), cancel)
            except models.DownloadError as e:
                log(f"GPU pack not installed: {e}")
        wav = project.dir / "dialogue.wav"
        try:
            extract_dialogue_wav(info, project.audio_track, wav, p("audio"), cancel)
            if project.dialogue == "auto" and (not project.spoken_language
                                               or project.lang_track != project.audio_track):
                try:
                    lang, share, samples = detect_language(wav, info.duration, mdir, settings.device,
                                                           progress=p("speech"), cancel=cancel, log=log)
                    project.spoken_language, project.english_share = lang, round(share, 2)
                    project.lang_track = project.audio_track
                    log(f"Spoken language: {lang} ({int(share * 100)}% of samples English: {samples})")
                except Cancelled:
                    raise
                except Exception as e:                       # noqa: BLE001 - assume English
                    log(f"Couldn't tell the spoken language ({e}); treating it as English.")
                    project.english_share = 1.0
            if dialogue_is_english(project):
                words, model, device = transcribe(
                    wav, info.duration, mdir, project.language, settings.speech_model,
                    settings.device, p("speech"), cancel, log)
            else:
                words, model, device = [], "", ""
                log("The dialogue isn't English, so listening for English words was skipped; "
                    "the English subtitles will be cleaned instead.")
        finally:
            wav.unlink(missing_ok=True)
        offset = float(info.audio[project.audio_track].raw.get("start_time") or 0) - info.format_start
        if abs(offset) > 0.001:
            for w in words:
                w["s"], w["e"] = round(w["s"] + offset, 3), round(w["e"] + offset, 3)
        project.transcript = {"track": project.audio_track, "model": model, "device": device,
                              "words": words, "skipped": "" if words or dialogue_is_english(project)
                              else "not English"}
        project.save()
    if progress:
        progress("words", 1.0, "Matching your word list")
    find_word_mutes(project, filters)

    # ---- subtitles: star out words; in English movies also catch words that weren't heard
    english = dialogue_is_english(project)
    tracks = read_tracks(info, project.dir, p("subs"), cancel, log)
    items, extra = subtitle_findings(tracks, patterns_for(filters), project.transcript.get("words"),
                                     project.mutes, english, filters.padding)
    project.subtitle_edits = _carry_over(items, project.subtitle_edits)
    if extra:
        find_word_mutes(project, filters, extra)
        log(f"Subtitles caught {sum(1 for e in extra if not e.get('unsure'))} missed word(s); "
            f"{sum(1 for e in extra if e.get('unsure'))} more to check in Review.")

    descriptions = [d.strip() for d in (filters.scene_descriptions or "").splitlines() if d.strip()]
    if filters.scenes or descriptions:
        def sp(stage, f, m):
            if progress:
                progress(stage, f, m)
        found = scene_mod.watch(info, project.dir, mdir, filters, sp, cancel, log)
        project.scenes = _carry_over(found, project.scenes)
    else:
        project.scenes = [s for s in project.scenes if s.get("manual")]
    project.scan_filters = dict(filters.__dict__)
    project.save()
    return project


def render_project(project, filters, settings, out=None, progress=None, cancel=None, log=print,
                   export=None):
    """export: ExportOptions (folder, format, size, quality, naming). Without it the old
    defaults are used: '<name> (clean)' next to the movie (or in Settings' output folder)."""
    import time
    from .export import output_path as export_path
    info = probe(project.source)
    if out is None:
        out = export_path(Path(project.source), export) if export else \
            output_path(Path(project.source), settings.output_dir)
    out = Path(out)
    if out.resolve() == Path(project.source).resolve():
        raise ValueError("The clean copy can't replace the original movie. Choose another name or folder.")
    result = render(info, project, filters, settings, patterns_for(filters), out,
                    (lambda f, m: progress("render", f, m)) if progress else None, cancel, log, export)
    result["finished_at"] = time.strftime("%Y-%m-%d %H:%M")
    result["edition"] = export.edition if export and export.naming == "plex_edition" else ""
    project.last_output = result["output"]
    project.exports = (project.exports or [])[-19:] + [{k: v for k, v in result.items() if k != "notes"}]
    project.save()
    return result
