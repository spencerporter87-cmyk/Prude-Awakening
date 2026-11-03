from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLineEdit, QMessageBox, QVBoxLayout, QWidget)

from .. import models
from ..paths import free_space_gb, projects_dir
from .widgets import button, label

SPEECH_MODELS = [("", "Automatic (best for your computer)"),
                 ("large-v3", "Most accurate (large-v3, 3 GB, best with NVIDIA card)"),
                 ("large-v3-turbo", "Accurate and faster (large-v3-turbo, 1.6 GB)"),
                 ("medium.en", "Medium (1.5 GB)"),
                 ("small.en", "Fast (small, 0.5 GB)")]


def folder_size_gb(p: Path):
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total / 1e9


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None, gpu_present=False, on_gpu_pack=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(820)
        self.s = settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(label("Settings", "H1"))
        form = QFormLayout()
        form.setVerticalSpacing(12)
        lay.addLayout(form)

        self.out = QLineEdit(settings.output_dir)
        self.out.setPlaceholderText("Same folder as the movie")
        form.addRow("Save clean copies to", self._with_browse(self.out))

        self.mdir = QLineEdit(settings.models_dir)
        self.mdir.setPlaceholderText(str(settings.models_path))
        self.space = label("", "Small")
        self.mdir.textChanged.connect(self._space)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(self._with_browse(self.mdir))
        col.addWidget(self.space)
        w = QWidget()
        w.setLayout(col)
        form.addRow("Keep AI models in", w)
        self._space()

        self.device = QComboBox()
        for v, t in (("auto", "Automatic"), ("cuda", "NVIDIA graphics card"), ("cpu", "Processor only")):
            self.device.addItem(t, v)
        self.device.setCurrentIndex(max(0, self.device.findData(settings.device)))
        form.addRow("Run speech recognition on", self.device)

        self.model = QComboBox()
        for v, t in SPEECH_MODELS:
            self.model.addItem(t, v)
        self.model.setCurrentIndex(max(0, self.model.findData(settings.speech_model)))
        form.addRow("Speech model", self.model)

        self.quality = QComboBox()
        self.quality.addItem("Very high (recommended)", "high")
        self.quality.addItem("Maximum (bigger files, slower)", "max")
        self.quality.setCurrentIndex(max(0, self.quality.findData(settings.video_quality)))
        form.addRow("Video quality when re-encoding", self.quality)

        self.keep_subs = QCheckBox("Keep the movie's subtitles (bad words become ****)")
        self.keep_subs.setChecked(settings.keep_subtitles)
        self.make_subs = QCheckBox("Add subtitles written from the dialogue")
        self.make_subs.setChecked(settings.make_subtitles)
        self.blur = QCheckBox("Blur pictures of nudity in the review list")
        self.blur.setChecked(settings.blur_thumbnails)
        for c in (self.keep_subs, self.make_subs, self.blur):
            form.addRow("", c)

        gpu = QHBoxLayout()
        gpu.setContentsMargins(0, 0, 0, 0)
        if not gpu_present:
            gpu.addWidget(label("No NVIDIA graphics card found — everything runs on the processor.", "Muted"))
        elif models.gpu_pack_ready(settings.models_path):
            gpu.addWidget(label("✓ Installed — speech recognition uses your graphics card.", "Muted"))
        else:
            gpu.addWidget(label("Not installed (about 1.2 GB, makes scanning ~10× faster).", "Muted"))
            if on_gpu_pack:
                gpu.addWidget(button("Download now", None, lambda: (self.accept(), on_gpu_pack())))
        gpu.addStretch()
        gw = QWidget()
        gw.setLayout(gpu)
        form.addRow("Graphics card speed-up pack", gw)

        cache = QHBoxLayout()
        cache.setContentsMargins(0, 0, 0, 0)
        self.cache_lbl = label(f"{folder_size_gb(projects_dir()):.2f} GB used by saved scans", "Muted")
        cache.addWidget(self.cache_lbl)
        cache.addWidget(button("Clear saved scans", None, self._clear_cache))
        cache.addStretch()
        cw = QWidget()
        cw.setLayout(cache)
        form.addRow("Saved scans", cw)

        from .widgets import beta_badge
        hdr = QHBoxLayout()
        hdr.addWidget(label("Advanced Editing", "H2"))
        hdr.addWidget(beta_badge())
        hdr.addStretch()
        lay.addLayout(hdr)
        form2 = QFormLayout()
        form2.setVerticalSpacing(10)
        lay.addLayout(form2)
        self.eleven = QLineEdit(settings.elevenlabs_key)
        self.eleven.setEchoMode(QLineEdit.Password)
        self.eleven.setPlaceholderText("Optional. Stays on this PC; only used if you pick ElevenLabs.")
        form2.addRow("ElevenLabs key", self.eleven)
        self.mt_dir = QLineEdit(settings.musetalk_dir)
        self.mt_dir.setPlaceholderText("Optional, experimental: folder of your own MuseTalk install")
        form2.addRow("MuseTalk folder", self._with_browse(self.mt_dir))
        self.mt_py = QLineEdit(settings.musetalk_python)
        self.mt_py.setPlaceholderText("python (or the python.exe inside MuseTalk's environment)")
        form2.addRow("MuseTalk Python", self.mt_py)
        lay.addWidget(label("MuseTalk (lip-sync) is free to use and runs on your NVIDIA card, but it is a "
                            "separate install of several GB that this app can't bundle.", "Small", wrap=True))

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _with_browse(self, edit):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        row.addWidget(button("Browse…", None, lambda: self._browse(edit)))
        row.addWidget(button("Default", "Link", lambda: edit.setText("")))
        w = QWidget()
        w.setLayout(row)
        return w

    def _browse(self, edit):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder", edit.text() or str(Path.home()))
        if d:
            edit.setText(d)

    def _space(self):
        p = Path(self.mdir.text()) if self.mdir.text() else self.s.models_path
        self.space.setText(f"{free_space_gb(p):.1f} GB free there. The models need about 4 GB.")

    def _clear_cache(self):
        import shutil
        if QMessageBox.question(self, "Clear saved scans",
                                "Forget every saved scan? Movies will need to be scanned again.") \
                == QMessageBox.Yes:
            shutil.rmtree(projects_dir(), ignore_errors=True)
            projects_dir()
            self.cache_lbl.setText("0.00 GB used by saved scans")

    def _save(self):
        s = self.s
        s.output_dir = self.out.text().strip()
        s.models_dir = self.mdir.text().strip()
        s.device = self.device.currentData()
        s.speech_model = self.model.currentData()
        s.video_quality = self.quality.currentData()
        s.keep_subtitles = self.keep_subs.isChecked()
        s.make_subtitles = self.make_subs.isChecked()
        s.blur_thumbnails = self.blur.isChecked()
        s.elevenlabs_key = self.eleven.text().strip()
        s.musetalk_dir = self.mt_dir.text().strip()
        s.musetalk_python = self.mt_py.text().strip()
        s.save()
        self.accept()
