"""Scan and render, step by step, with progress for the app window."""

import uuid
from pathlib import Path

from . import models, scenes as scene_mod
from .media import probe
from .render import output_path, render
from .transcribe import extract_dialogue_wav, transcribe
from .words import build_patterns, find_matches, merge_intervals, normalize

STAGES = [
    ("gpu", "Getting the graphics-card speed-up pack"),
    ("audio", "Pulling out the dialogue"),
    ("speech", "Listening for words"),
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


def find_word_mutes(project, filters):
    words = project.transcript.get("words") or []
    hits = find_matches(words, patterns_for(filters), filters.padding, filters.padding,
                        filters.allow_words)
    merged = merge_intervals(hits)
    for m in merged:
        m["start"], m["end"] = round(m["start"], 2), round(m["end"], 2)
    project.mutes = _carry_over(merged, project.mutes)
    return project.mutes


def scan(project, filters, settings, progress=None, cancel=None, log=print, get_gpu_pack=False):
    """progress(stage, fraction, message)."""
    def p(stage):
        return (lambda f, m: progress(stage, f, m)) if progress else None

    info = probe(project.source)
    if project.audio_track >= len(info.audio):
        project.audio_track = 0
    mdir = settings.models_path
    mdir.mkdir(parents=True, exist_ok=True)

    if not project.has_transcript(project.audio_track):
        if get_gpu_pack and not models.gpu_pack_ready(mdir):
            try:
                models.ensure_gpu_pack(mdir, p("gpu"), cancel)
            except models.DownloadError as e:
                log(f"GPU pack not installed: {e}")
        wav = project.dir / "dialogue.wav"
        try:
            extract_dialogue_wav(info, project.audio_track, wav, p("audio"), cancel)
            words, model, device = transcribe(
                wav, info.duration, mdir, project.language, settings.speech_model,
                settings.device, p("speech"), cancel, log)
        finally:
            wav.unlink(missing_ok=True)
        offset = float(info.audio[project.audio_track].raw.get("start_time") or 0) - info.format_start
        if abs(offset) > 0.001:
            for w in words:
                w["s"], w["e"] = round(w["s"] + offset, 3), round(w["e"] + offset, 3)
        project.transcript = {"track": project.audio_track, "model": model, "device": device,
                              "words": words}
        project.save()
    if progress:
        progress("words", 1.0, "Matching your word list")
    find_word_mutes(project, filters)

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
