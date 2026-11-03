"""Prude Awakening - main window."""

import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QFileDialog, QMainWindow, QMessageBox, QStackedWidget)

from .. import APP_NAME, __version__, models
from ..media import MediaError, pick_audio_track, probe
from ..paths import app_data_dir
from ..pipeline import render_project, scan
from ..project import Project
from ..render import output_path
from ..scenes import CLIP_FOR
from ..settings import FilterOptions, Settings
from ..jobs import Queue
from .batch import BatchPage
from .pages import HomePage, RenderPage, ReviewPage, ScanPage, SetupPage
from .settings_dialog import SettingsDialog
from .style import QSS
from .widgets import VIDEO_EXTS, app_icon
from .worker import Worker

log = logging.getLogger("prude")


def setup_logging():
    d = app_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(d / "app.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        if not settings.models_dir:
            try:
                from ..paths import roomiest_models_dir
                better = roomiest_models_dir()
                if better is not None:
                    settings.models_dir = str(better)
                    settings.save()
                    log.info("Models folder set to %s (more free space than C:)", better)
            except Exception:                                # noqa: BLE001
                log.exception("Couldn't choose a models drive")
        self.project = None
        self.info = None
        self.worker = None
        self._gpu_present = None
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1240, 820)
        if settings.window and len(settings.window) == 4:
            x, y, w, h = settings.window
            self.setGeometry(x, y, max(900, w), max(640, h))
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.home, self.setup, self.scanp = HomePage(), SetupPage(), ScanPage()
        self.review, self.renderp = ReviewPage(), RenderPage()
        self.queue = Queue()
        self.batch = BatchPage(self.queue, settings, is_busy=lambda: self.worker is not None and self.worker.isRunning())
        for w in (self.home, self.setup, self.scanp, self.review, self.renderp, self.batch):
            self.stack.addWidget(w)
        self.batch.back.connect(self.go_home)
        self.batch.openReview.connect(self.open_review)
        self.home.openBatch.connect(self.show_batch)
        self.home.openFiles.connect(self.add_to_batch)
        self._update_batch_button()
        self.home.openFile.connect(self.open_file)
        self.home.browse.connect(self.browse)
        self.home.openSettings.connect(self.open_settings)
        self.home.set_recent(settings.recent)
        self.setup.back.connect(self.go_home)
        self.setup.scan.connect(self.start_scan)
        self.setup.savePreset.connect(self._save_preset)
        self.setup.trackChanged.connect(self._track_changed)
        self.scanp.cancel.connect(self.cancel_work)
        self.review.back.connect(lambda: (self.review.leave(), self.stack.setCurrentWidget(self.setup)))
        self.review.render.connect(self.start_render)
        self.review.changed.connect(lambda: self.project and self.project.save())
        self.renderp.cancel.connect(self.cancel_work)
        self.renderp.again.connect(lambda: (self.renderp.leave(), self.go_home()))
        self.renderp.backToReview.connect(self._back_to_review)
        self.setAcceptDrops(True)

    # ------------------------------------------------------------ helpers
    def gpu_present(self):
        if self._gpu_present is None:
            self._gpu_present = models.nvidia_gpu_present()
        return self._gpu_present

    def error(self, title, msg, details=""):
        log.error("%s: %s\n%s", title, msg, details)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(msg)
        if details:
            box.setDetailedText(details)
        box.exec()

    def busy(self):
        if self.batch.running():
            QMessageBox.information(self, "The batch is running",
                                    "The batch is using the graphics card. Pause it (Batch → Pause) first.")
            return True
        return self.worker is not None and self.worker.isRunning()

    # ------------------------------------------------------------ batch
    def _update_batch_button(self):
        waiting = sum(1 for j in self.queue.jobs if j.status == "queued")
        self.home.batch_btn.setText(f"☰  Batch ({waiting} waiting)" if waiting else "☰  Batch")

    def show_batch(self):
        self.batch.refresh()
        self.stack.setCurrentWidget(self.batch)

    def add_to_batch(self, paths):
        self.show_batch()
        self.batch.add_dialog(paths)
        self._update_batch_button()

    def open_review(self, source, filters):
        try:
            info = probe(source)
        except Exception as e:
            self.error("Can't open this file", str(e))
            return
        self.info, self.project = info, Project.open(source)
        f = FilterOptions.from_dict(filters)
        self._filters = f
        self.setup.set_movie(info, self.project, self.settings, self._scan_note())
        self.setup.apply(f)
        self.review.set_project(self.project, info, self.settings, f)
        self.stack.setCurrentWidget(self.review)

    # ------------------------------------------------------------ files
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and self.stack.currentWidget() in (self.home, self.setup):
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
        if len(paths) > 1 or any(Path(p).is_dir() for p in paths):
            self.add_to_batch(paths)
        elif paths:
            self.open_file(paths[0])

    def browse(self):
        exts = " ".join(f"*{x}" for x in sorted(VIDEO_EXTS))
        start = str(Path(self.settings.recent[0]).parent) if self.settings.recent else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Choose a movie", start,
                                              f"Videos ({exts});;All files (*)")
        if path:
            self.open_file(path)

    def open_file(self, path):
        if self.busy():
            return
        path = str(Path(path))
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            info = probe(path)
        except (MediaError, FileNotFoundError, OSError) as e:
            QApplication.restoreOverrideCursor()
            self.error("Can't open this file", str(e))
            return
        QApplication.restoreOverrideCursor()
        if not info.audio:
            self.error("No sound", "This file has no audio track, so there's nothing to listen to.")
            return
        self.info = info
        self.project = Project.open(path)
        if not self.project.transcript:
            self.project.audio_track = pick_audio_track(info, self.project.language)
        self.settings.recent = [path] + [p for p in self.settings.recent if p != path][:7]
        self.settings.save()
        self.setup.set_movie(info, self.project, self.settings, self._scan_note())
        self.stack.setCurrentWidget(self.setup)

    def _scan_note(self):
        if self.project.has_transcript(self.project.audio_track):
            return "Already listened to — a new scan only takes moments (unless scenes are new)."
        mdir = self.settings.models_path
        name = self.settings.speech_model or ("large-v3" if self.gpu_present() else "small.en")
        if not models.whisper_ready(name, mdir):
            gb = models.WHISPER_SIZES_GB.get(name, 1.5)
            return f"First scan downloads the speech model (about {gb:.1f} GB, one time only)."
        return ""

    def _track_changed(self, i):
        if self.project:
            self.project.audio_track = i
            self.setup.note.setText(self._scan_note())

    def go_home(self):
        if self.worker is not None and self.worker.isRunning():
            return
        self._update_batch_button()
        self.home.set_recent(self.settings.recent)
        self.stack.setCurrentWidget(self.home)

    def _save_preset(self, name, d):
        self.settings.presets[name] = d
        self.settings.last_preset = name
        self.settings.save()

    def open_settings(self):
        SettingsDialog(self.settings, self, self.gpu_present(), self._download_gpu_only).exec()

    def _download_gpu_only(self):
        self.settings.gpu_pack_declined = False
        self.settings.save()
        QMessageBox.information(self, "GPU pack", "The speed-up pack will be downloaded at the start of "
                                                  "your next scan.")

    # ------------------------------------------------------------ scanning
    def _want_gpu_pack(self):
        frozen = getattr(sys, "frozen", False)
        mdir = self.settings.models_path
        if not frozen or self.settings.device == "cpu" or models.gpu_pack_ready(mdir):
            return False
        if not self.gpu_present() or self.settings.gpu_pack_declined:
            return False
        r = QMessageBox.question(
            self, "Use your NVIDIA graphics card?",
            "Your computer has an NVIDIA graphics card. With a one-time download of NVIDIA's "
            "speed-up pack (about 1.2 GB), scanning is roughly 10 times faster.\n\nDownload it now?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r == QMessageBox.No:
            self.settings.gpu_pack_declined = True
            self.settings.save()
            return False
        return True

    def _check_space(self, need_gb):
        from ..paths import free_space_gb
        mdir = self.settings.models_path
        if free_space_gb(mdir) >= need_gb:
            return True
        r = QMessageBox.question(
            self, "Not enough room for the AI models",
            f"The models need about {need_gb:.0f} GB, but there's only {free_space_gb(mdir):.1f} GB free in\n"
            f"{mdir}\n\nChoose a different folder (for example on another drive)?")
        if r != QMessageBox.Yes:
            return False
        d = QFileDialog.getExistingDirectory(self, "Folder for the AI models")
        if not d:
            return False
        self.settings.models_dir = str(Path(d) / "Prude Awakening Models")
        self.settings.save()
        return self._check_space(need_gb)

    def start_scan(self):
        if self.busy() or not self.project:
            return
        f = self.setup.filters()
        dialogue = self.setup.dialogue_value()
        if dialogue != self.project.dialogue:
            self.project.dialogue = dialogue
            if dialogue == "auto":
                self.project.spoken_language = ""
        self.settings.filters = asdict(f)
        self.settings.last_preset = self.setup.preset_name()
        self.settings.save()
        mdir = self.settings.models_path
        have_words = self.project.has_transcript(self.project.audio_track) and not (
            self.project.transcript.get("skipped") and dialogue == "en")
        want_scenes = bool(f.scenes or f.scene_descriptions)
        want_clip = any(k in CLIP_FOR for k in f.scenes) or bool(f.scene_descriptions.strip())
        need = 0.0
        name = self.settings.speech_model or ("large-v3" if self.gpu_present() else "small.en")
        if not have_words and not models.whisper_ready(name, mdir):
            need += models.WHISPER_SIZES_GB.get(name, 1.5) + 0.3
        if want_clip and not models.clip_ready(mdir):
            need += 0.7
        get_gpu = (not have_words) and self._want_gpu_pack()
        if get_gpu:
            need += 2.5
        if need and not self._check_space(need):
            return
        stages = []
        if get_gpu:
            stages.append("gpu")
        stages += ["audio", "speech", "words", "subs"]
        if f.burned_subs == "on" or (f.burned_subs == "auto" and dialogue != "en"):
            stages.append("burned")
        if want_scenes:
            if want_clip and not models.clip_ready(self.settings.models_path):
                stages.append("models")
            stages += ["frames", "analyze"]
        skipped = ("audio", "speech") if have_words else ()
        self.scanp.start(Path(self.project.source).name, stages, skipped)
        self.stack.setCurrentWidget(self.scanp)
        self._filters = f
        self.review.leave()
        self.worker = Worker(scan, self.project, f, self.settings, get_gpu_pack=get_gpu)
        self.worker.progress.connect(self.scanp.on_progress)
        self.worker.log.connect(self._log)
        self.worker.finished_ok.connect(self._scan_done)
        self.worker.failed.connect(self._failed)
        self.worker.cancelled.connect(self._cancelled)
        log.info("Scan start %s filters=%s", self.project.source, asdict(f))
        self.worker.start()

    def _log(self, msg):
        log.info(msg)
        self.scanp.log.setText(msg)

    def _scan_done(self, project):
        self.scanp.finish()
        self.worker = None
        self.review.set_project(self.project, self.info, self.settings, self._filters)
        self.stack.setCurrentWidget(self.review)

    def _failed(self, msg, details):
        self.worker = None
        back = self.review if self.stack.currentWidget() is self.renderp else self.setup
        self.stack.setCurrentWidget(back)
        self.error("Something went wrong", msg, details)

    def _cancelled(self):
        self.worker = None
        back = self.review if self.stack.currentWidget() is self.renderp else self.setup
        self.stack.setCurrentWidget(back)

    def cancel_work(self):
        if self.busy():
            self.worker.cancel()
            self.scanp.cancel_btn.setEnabled(False)
            self.scanp.cancel_btn.setText("Stopping…")
            self.renderp.cancel_btn.setEnabled(False)
            self.renderp.ticker.stop()

    # ------------------------------------------------------------ rendering
    def start_render(self):
        if self.busy():
            return
        from ..export import ExportOptions
        from ..render import scene_ranges
        from .export_dialog import ExportDialog
        f = getattr(self, "_filters", None) or FilterOptions.from_dict(self.settings.filters)
        opts = ExportOptions.from_dict(self.settings.export) if self.settings.export else \
            ExportOptions(folder=self.settings.output_dir, quality=self.settings.video_quality,
                          naming="plex_edition")
        r = scene_ranges(self.project, f.scene_action)
        reencode = any(r[a] for a in ("cut", "black", "black_keep", "blur", "blur_parts"))
        removed = sum(b - a for a, b in r["cut"])
        preset = self.settings.last_preset if self.settings.last_preset != "Custom" else ""
        dlg = ExportDialog(self.project.source, self.info, opts, reencode, removed,
                           f"{preset} Edit" if preset else "", self)
        if not dlg.exec():
            return
        opts, out = dlg.options(), dlg.output()
        self.settings.export = opts.to_dict()
        self.settings.save()
        if Path(out).resolve() == Path(self.project.source).resolve():
            self.error("Can't overwrite the original", "Choose a different name or folder.")
            return
        if out.exists():
            q = QMessageBox.question(self, "Replace?", f"{out.name} already exists. Replace it?")
            if q != QMessageBox.Yes:
                return
        resized = opts.resolution != "source" and self.info.height and self.info.height > int(opts.resolution) + 8
        self._filters_used = f
        self.review.leave()
        self.renderp.start(Path(self.project.source).name, out, reencode or resized, self.project, f,
                           self.info.duration)
        self.stack.setCurrentWidget(self.renderp)
        self.worker = Worker(render_project, self.project, f, self.settings, out, export=opts)
        self.worker.progress.connect(self.renderp.on_progress)
        self.worker.log.connect(lambda m: log.info(m))
        self.worker.finished_ok.connect(self._render_done)
        self.worker.failed.connect(self._failed)
        self.worker.cancelled.connect(self._cancelled)
        self.worker.start()

    def _back_to_review(self):
        self.renderp.leave()
        self.review.player.load(self.project.source)
        self.stack.setCurrentWidget(self.review)

    def _render_done(self, result):
        self.worker = None
        log.info("Render done %s", result)
        self.renderp.show_result(result, self.project, self._filters_used, self.info.duration)

    # ------------------------------------------------------------ window
    def closeEvent(self, e):
        if self.batch.running():
            r = QMessageBox.question(self, "Stop the batch and quit?",
                                     "The batch is running. Quit anyway? It will pick up where it left off "
                                     "next time you press Start.")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            current = [j for j in self.queue.jobs if j.status == "running"]
            self.batch.runner.pause_after_current()
            self.batch.runner.cancel_current()
            self.batch.runner.wait(8000)
            for j in current:            # resume it next time instead of leaving it "cancelled"
                self.queue.retry(j)
        if self.worker is not None and self.worker.isRunning():
            r = QMessageBox.question(self, "Stop and quit?", "A scan or save is still running. Stop it and quit?")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            self.worker.cancel()
            self.worker.wait(5000)
        g = self.geometry()
        self.settings.window = [g.x(), g.y(), g.width(), g.height()]
        self.settings.save()
        e.accept()


def self_test(out_path):
    """Used by the build pipeline: check that everything the app needs is inside the package."""
    report, ok = {"version": __version__}, True
    import importlib
    for mod in ("faster_whisper", "ctranslate2", "onnxruntime", "nudenet", "cv2", "tokenizers",
                "numpy", "requests", "PySide6", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"):
        try:
            m = importlib.import_module(mod)
            report[mod] = getattr(m, "__version__", "ok")
        except Exception as e:
            report[mod] = f"FAILED: {e}"
            ok = False
    from ..paths import find_tool
    for tool in ("ffmpeg", "ffprobe", "ffplay"):
        try:
            report[tool] = find_tool(tool)
        except Exception as e:
            report[tool] = f"FAILED: {e}"
            ok = False
    try:
        from nudenet import NudeDetector
        NudeDetector()
        report["nudenet_model"] = "ok"
    except Exception as e:
        report["nudenet_model"] = f"FAILED: {e}"
        ok = False
    try:
        import numpy as np
        from .. import ocr
        img = np.full((90, 600, 3), 255, np.uint8)
        import cv2
        cv2.putText(img, "Hello there", (20, 60), cv2.FONT_HERSHEY_DUPLEX, 1.6, (0, 0, 0), 3)
        report["ocr"] = ocr.read_text(img) or "FAILED: read nothing"
        ok = ok and "hello" in report["ocr"].lower()
    except Exception as e:
        report["ocr"] = f"FAILED: {e}"
        ok = False
    report["ok"] = ok
    Path(out_path).write_text(json.dumps(report, indent=1))
    return 0 if ok else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in argv:
        i = argv.index("--self-test")
        sys.exit(self_test(argv[i + 1] if i + 1 < len(argv) else "selftest.json"))
    setup_logging()
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    app.setStyleSheet(QSS)
    if "--tour" in argv:
        from .tour import run_tour
        i = argv.index("--tour")
        video = argv[argv.index("--video") + 1] if "--video" in argv else None
        return run_tour(app, Path(argv[i + 1]), video)
    w = MainWindow(Settings.load())
    w.show()
    files = [a for a in argv if not a.startswith("--") and Path(a).exists()]
    if files:
        QTimer.singleShot(200, lambda: w.open_file(files[0]))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
