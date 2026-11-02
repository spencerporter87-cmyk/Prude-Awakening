"""Automated screenshot tour, used by the build pipeline to check every screen."""

import subprocess
from pathlib import Path

from PySide6.QtCore import QTimer

from ..media import ffmpeg
from ..settings import BUILTIN_PRESETS, Settings
from .main import MainWindow
from .settings_dialog import SettingsDialog

SAMPLE_WORDS = [("Well,", 1.0, 1.3), (" what", 4.8, 5.0), (" the", 5.0, 5.1), (" hell", 5.1, 5.5),
                (" are", 5.5, 5.7), (" you", 5.7, 5.9), (" doing?", 5.9, 6.4), (" God", 9.8, 10.1),
                (" damn", 10.1, 10.4), (" it!", 10.4, 10.6), (" Oh", 17.0, 17.1), (" my", 17.2, 17.3),
                (" god.", 17.4, 17.7)]


def run_tour(app, out_dir: Path, video=None):
    out_dir.mkdir(parents=True, exist_ok=True)
    s = Settings()
    s.filters = BUILTIN_PRESETS["Family night"].__dict__.copy()
    w = MainWindow(s)
    w.resize(1280, 820)
    w.show()
    steps = []

    def shot(name, widget=None):
        (widget or w).grab().save(str(out_dir / f"{name}.png"))

    def add(fn, delay=400):
        steps.append((fn, delay))

    add(lambda: shot("1-home"))
    if video:
        add(lambda: w.open_file(video))
        add(lambda: (w.setup.apply(BUILTIN_PRESETS["Kids"]),
                     w.setup.describe.setPlainText("smoking a cigarette\na person vomiting")))
        add(lambda: shot("2-setup"))

        def fake_scan():
            w.scanp.start(Path(video).name, ["audio", "speech", "words", "frames", "analyze"])
            w.stack.setCurrentWidget(w.scanp)
            w.scanp.on_progress("audio", 1.0, "Done")
            w.scanp.on_progress("speech", 0.42, "Listening… about 4 min left")
        add(fake_scan)
        add(lambda: shot("3-scanning"))

        def fake_review():
            p = w.project
            p.transcript = {"track": 0, "model": "demo", "words": [
                {"w": a, "s": b, "e": c, "p": 0.9} for a, b, c in SAMPLE_WORDS]}
            from ..pipeline import find_word_mutes
            find_word_mutes(p, w.setup.filters())
            thumbs = []
            for t in (3, 12):
                f = p.dir / f"demo_{t}.jpg"
                subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-ss", str(t), "-i", video,
                                "-frames:v", "1", "-vf", "scale=384:-2", str(f)])
                thumbs.append(str(f))
            p.mutes.append({"id": "k", "start": 21.0, "end": 21.6, "words": "crap", "category": "mild",
                            "context": "oh crap", "on": False})
            p.scenes = [
                {"id": "a", "kind": "violence", "label": "Violence", "start": 2.0, "end": 6.5,
                 "score": 0.71, "thumb": thumbs[0], "thumb_blur": thumbs[0], "on": True},
                {"id": "b", "kind": "custom:smoking a cigarette", "label": "“smoking a cigarette”",
                 "start": 11.0, "end": 14.0, "score": 0.52, "thumb": thumbs[1], "thumb_blur": thumbs[1],
                 "on": False}]
            w._filters = w.setup.filters()
            w.review.set_project(p, w.info, w.settings, w._filters)
            w.stack.setCurrentWidget(w.review)
        add(fake_review, 2500)

        def media_check():
            import json
            from .player import HAVE_MM
            w.review.player.seek_to(8.0)
            (out_dir / "media.json").write_text(json.dumps({
                "have_multimedia": HAVE_MM, "review_duration": w.review.player.duration()}))
        add(media_check, 1500)
        add(lambda: shot("4-review-words"))
        add(lambda: w.review.tabs.setCurrentIndex(1))
        add(lambda: shot("5-review-scenes"))

        def fake_render():
            w.renderp.start(Path(video).name, Path(video).with_name("demo (clean).mkv"), True)
            w.stack.setCurrentWidget(w.renderp)
            w.renderp.on_progress("render", 0.37, "Making the clean copy (re-encoding video)")
        add(fake_render)
        add(lambda: shot("6-rendering"))
        add(lambda: w.renderp.show_result(
            {"output": str(video), "mutes": 3, "cuts": 1, "blackouts": 0, "removed_seconds": 4.5,
             "how": "video re-encoded on the graphics card (NVENC HEVC)",
             "notes": ["Made subtitles from the dialogue: demo (clean).en.srt"]},
            w.project, w._filters, w.info.duration), 2500)
        add(lambda: w.renderp.player.seek_to(6.0), 1200)
        add(lambda: shot("7-done"))
        add(lambda: w.renderp.leave())

    def settings_shot():
        d = SettingsDialog(w.settings, w, gpu_present=True)
        d.show()
        QTimer.singleShot(400, lambda: (shot("8-settings", d), d.close()))
    add(settings_shot, 900)
    add(lambda: app.quit(), 200)

    def run(i=0):
        if i >= len(steps):
            return
        fn, delay = steps[i]
        try:
            fn()
        except Exception as e:  # keep going so we still get the other screenshots
            (out_dir / f"error-step-{i}.txt").write_text(repr(e))
            import traceback
            (out_dir / f"error-step-{i}.txt").write_text(traceback.format_exc())
        QTimer.singleShot(delay, lambda: run(i + 1))

    QTimer.singleShot(600, run)
    return app.exec()
