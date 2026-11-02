"""Command-line version (used for automated tests and power users).

  prude movie.mkv                              scan + render with saved settings
  prude movie.mkv --words strong,mild --scenes nudity,violence --describe "smoking"
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .media import Cancelled, probe, pick_audio_track
from .pipeline import render_project, scan
from .project import Project
from .settings import FilterOptions, Settings


def main(argv=None):
    p = argparse.ArgumentParser(prog="prude", description="Prude Awakening (command line)")
    p.add_argument("video")
    p.add_argument("--words", help="comma list: strong,blasphemy,sexual,slurs,mild")
    p.add_argument("--custom", default=None, help="extra words, comma separated")
    p.add_argument("--scenes", default=None, help="comma list: nudity,sexual,violence,gore")
    p.add_argument("--describe", action="append", default=None, help="scene description (repeatable)")
    p.add_argument("--sensitivity", type=int)
    p.add_argument("--action", choices=["cut", "black"])
    p.add_argument("--padding", type=float)
    p.add_argument("--mute-mode", choices=["all", "center"])
    p.add_argument("--audio-track", type=int)
    p.add_argument("--model", help="speech model, e.g. tiny.en, small.en, large-v3")
    p.add_argument("--device", choices=["auto", "cuda", "cpu"])
    p.add_argument("--models-dir")
    p.add_argument("--out")
    p.add_argument("--scan-only", action="store_true")
    p.add_argument("--rescan", action="store_true", help="forget the saved transcript and scenes")
    p.add_argument("--summary-json", help="write a JSON summary here")
    p.add_argument("--version", action="version", version=__version__)
    a = p.parse_args(argv)

    settings = Settings.load()
    if a.model:
        settings.speech_model = a.model
    if a.device:
        settings.device = a.device
    if a.models_dir:
        settings.models_dir = a.models_dir
    f = FilterOptions.from_dict(settings.filters)
    if a.words is not None:
        f.words = [w for w in a.words.split(",") if w]
    if a.custom is not None:
        f.custom_words = a.custom
    if a.scenes is not None:
        f.scenes = [s for s in a.scenes.split(",") if s]
    if a.describe is not None:
        f.scene_descriptions = "\n".join(a.describe)
    for k in ("sensitivity", "padding"):
        if getattr(a, k) is not None:
            setattr(f, k, getattr(a, k))
    if a.action:
        f.scene_action = a.action
    if a.mute_mode:
        f.mute_mode = a.mute_mode

    src = Path(a.video).resolve()
    project = Project.open(src)
    if a.rescan:
        project.clear_cache()
        project = Project(source=str(src))
    info = probe(src)
    project.audio_track = a.audio_track if a.audio_track is not None else (
        project.audio_track if project.transcript else pick_audio_track(info, project.language))
    print(f"Prude Awakening {__version__} - {src.name} ({info.summary()})")
    print(f"Audio track {project.audio_track}: {info.audio[project.audio_track].describe()}")

    last = {}

    def progress(stage, frac, msg):
        pct = int(frac * 100)
        if last.get(stage) != pct // 10:
            last[stage] = pct // 10
            print(f"  [{stage}] {pct:3d}%  {msg}", flush=True)

    try:
        scan(project, f, settings, progress)
        print(f"Found {len(project.mutes)} mutes and {len(project.scenes)} scenes.")
        for m in project.mutes:
            print(f"  mute  {m['start']:8.2f}-{m['end']:8.2f}  [{m.get('category')}] {m['words']}")
        for s in project.scenes:
            print(f"  scene {s['start']:8.2f}-{s['end']:8.2f}  {s['label']} ({s.get('score')})")
        result = None
        if not a.scan_only and (project.active_mutes() or project.active_scenes()):
            result = render_project(project, f, settings, a.out, progress)
            print(f"Done: {result['output']} ({result['how']})")
            for n in result["notes"]:
                print("  " + n)
        if a.summary_json:
            Path(a.summary_json).write_text(json.dumps({
                "mutes": project.mutes, "scenes": project.scenes, "result": result,
                "transcript_words": len(project.transcript.get("words") or []),
                "device": project.transcript.get("device"), "filters": asdict(f)}, indent=1))
    except Cancelled:
        print("Cancelled.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
