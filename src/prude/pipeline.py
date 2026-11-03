"""Scan and render, step by step, with progress for the app window."""

import uuid
from pathlib import Path

from . import models, scenes as scene_mod
from .media import probe
from .render import output_path, render
from .transcribe import extract_dialogue_wav, transcribe
from .words import build_patterns, find_matches, merge_intervals

STAGES = [
    ("gpu", "Getting the graphics-card speed-up pack"),
    ("audio", "Pulling out the dialogue"),
    ("speech", "Listening for words"),
    ("words", "Matching your word list"),
    ("models", "Getting the scene model"),
    ("frames", "Watching the movie"),
    ("analyze", "Looking for scenes"),
]


def _key(item):
    return (item.get("kind") or item.get("category"), round(float(item["start"]), 0))


def _carry_over(new, old):
    """Keep the user's on/off choices and hand-made entries across re-scans."""
    prev = {_key(o): o for o in old}
    for n in new:
        o = prev.get(_key(n))
        n["id"] = (o or {}).get("id") or uuid.uuid4().hex[:8]
        if o is not None:
            n["on"] = o.get("on", True)
            if o.get("action_set"):
                n["action"], n["action_set"] = o.get("action"), True
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


def render_project(project, filters, settings, out=None, progress=None, cancel=None, log=print):
    info = probe(project.source)
    out = Path(out) if out else output_path(Path(project.source), settings.output_dir)
    result = render(info, project, filters, settings, patterns_for(filters), out,
                    (lambda f, m: progress("render", f, m)) if progress else None, cancel, log)
    project.last_output = result["output"]
    project.save()
    return result
