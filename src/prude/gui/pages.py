"""The app's screens."""

import os
import subprocess
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QPlainTextEdit, QProgressBar,
                               QRadioButton, QScrollArea, QSizePolicy, QSlider, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import models
from ..paths import NO_WINDOW, find_tool
from ..pipeline import STAGES
from ..scenes import SCENE_CATEGORIES, SCENE_NAMES
from ..settings import FilterOptions
from ..words import CATEGORIES, CATEGORY_NAMES
from .widgets import (Card, DropZone, StageRow, TimeEdit, button, fmt_time, icon_pixmap, label)


def page():
    w = QWidget()
    w.setObjectName("Page")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(36, 28, 36, 24)
    lay.setSpacing(16)
    return w, lay


def play_clip(source, start, end, title="Preview"):
    """Play a few seconds around a spot with ffplay (handles every format)."""
    start = max(0.0, float(start) - 2.0)
    dur = max(2.0, float(end) - float(start) + 2.0)
    try:
        exe = find_tool("ffplay")
    except FileNotFoundError:
        if hasattr(os, "startfile"):
            os.startfile(str(source))
        return
    subprocess.Popen([exe, "-hide_banner", "-loglevel", "quiet", "-autoexit", "-ss", f"{start:.2f}",
                      "-t", f"{dur:.2f}", "-window_title", title, "-x", "960", "-y", "540", str(source)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=NO_WINDOW)


# =========================================================================== Home

class HomePage(QWidget):
    openFile = Signal(str)
    browse = Signal()
    openSettings = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(48, 32, 48, 32)
        lay.setSpacing(18)
        top = QHBoxLayout()
        pic = QLabel()
        pic.setPixmap(icon_pixmap(56))
        top.addWidget(pic)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label("Prude Awakening", "H1"))
        col.addWidget(label("Mutes bad language and removes scenes you'd rather not see.", "Muted"))
        top.addLayout(col, 1)
        top.addWidget(button("⚙  Settings", None, self.openSettings.emit), 0, Qt.AlignTop)
        lay.addLayout(top)
        self.drop = DropZone()
        self.drop.fileDropped.connect(self.openFile.emit)
        self.drop.browse.connect(self.browse.emit)
        lay.addWidget(self.drop, 1)
        self.recent_box = QVBoxLayout()
        lay.addLayout(self.recent_box)
        steps = QHBoxLayout()
        for n, (t, d) in enumerate([("Choose", "what to filter"), ("Scan", "the movie listens & watches"),
                                    ("Review", "every mute and scene"), ("Save", "a clean copy")], 1):
            c = Card()
            c.lay.setContentsMargins(16, 12, 16, 12)
            c.lay.addWidget(label(f"{n}. {t}", "H2"))
            c.lay.addWidget(label(d, "Muted"))
            steps.addWidget(c)
        lay.addLayout(steps)

    def set_recent(self, paths):
        while self.recent_box.count():
            it = self.recent_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        paths = [p for p in paths if Path(p).exists()][:3]
        if not paths:
            return
        row = QHBoxLayout()
        row.addWidget(label("Recent:", "Muted"))
        for p in paths:
            b = button(Path(p).name, "Link", lambda _=False, p=p: self.openFile.emit(p), tip=p)
            row.addWidget(b)
        row.addStretch()
        w = QWidget()
        w.setLayout(row)
        self.recent_box.addWidget(w)


# =========================================================================== Setup

class SetupPage(QWidget):
    back = Signal()
    scan = Signal()
    savePreset = Signal(str, dict)
    trackChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        self._loading = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 22, 36, 20)
        outer.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(button("←  Choose another movie", "Link", self.back.emit))
        head.addStretch()
        outer.addLayout(head)
        self.title = label("", "H1")
        outer.addWidget(self.title)
        info = QHBoxLayout()
        self.summary = label("", "Muted")
        info.addWidget(self.summary)
        info.addSpacing(16)
        info.addWidget(label("Audio:", "Muted"))
        self.track = QComboBox()
        self.track.setMinimumWidth(320)
        self.track.currentIndexChanged.connect(lambda i: (not self._loading) and self.trackChanged.emit(i))
        info.addWidget(self.track)
        info.addStretch()
        outer.addLayout(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # ---- language card
        lang = Card("Language", "Words to silence. In subtitles they become ****.")
        self.word_boxes = {}
        for key, name, desc in CATEGORIES:
            cb = QCheckBox(name)
            cb.setToolTip(desc)
            cb.toggled.connect(self._changed)
            self.word_boxes[key] = cb
            row = QHBoxLayout()
            row.addWidget(cb)
            row.addWidget(label(desc, "Small"), 1)
            lang.lay.addLayout(row)
        lang.lay.addSpacing(6)
        lang.lay.addWidget(label("Your own words to silence (one per line)", None))
        self.custom = QPlainTextEdit()
        self.custom.setPlaceholderText("e.g.\nidiot\nshut up\nstupid")
        self.custom.setFixedHeight(76)
        self.custom.textChanged.connect(self._changed)
        lang.lay.addWidget(self.custom)
        lang.lay.addWidget(label("Never silence these words (comma separated)", None))
        self.allow = QLineEdit()
        self.allow.setPlaceholderText("e.g. jesus, god")
        self.allow.textChanged.connect(self._changed)
        lang.lay.addWidget(self.allow)
        lang.lay.addSpacing(6)
        prow = QHBoxLayout()
        prow.addWidget(label("Extra silence around each word", None))
        self.pad_val = label("", "Muted")
        prow.addStretch()
        prow.addWidget(self.pad_val)
        lang.lay.addLayout(prow)
        self.pad = QSlider(Qt.Horizontal)
        self.pad.setRange(0, 50)
        self.pad.valueChanged.connect(lambda v: (self.pad_val.setText(f"{v / 100:.2f} s"), self._changed()))
        lang.lay.addWidget(self.pad)
        self.mute_all = QRadioButton("Silence all sound during the word (recommended)")
        self.mute_center = QRadioButton("Silence only the dialogue speaker on surround sound (music keeps playing)")
        g = QButtonGroup(self)
        for r in (self.mute_all, self.mute_center):
            g.addButton(r)
            r.toggled.connect(self._changed)
            lang.lay.addWidget(r)
        lang.lay.addStretch()
        grid.addWidget(lang, 0, 0)

        # ---- scenes card
        sc = Card("Scenes", "Parts of the movie to remove. You'll review every one before anything is cut.")
        self.scene_boxes = {}
        for key, name, desc in SCENE_CATEGORIES:
            cb = QCheckBox(name)
            cb.toggled.connect(self._changed)
            self.scene_boxes[key] = cb
            row = QHBoxLayout()
            row.addWidget(cb)
            row.addWidget(label(desc, "Small"), 1)
            sc.lay.addLayout(row)
        sc.lay.addSpacing(6)
        sc.lay.addWidget(label("Describe anything else to find (one per line)", None))
        self.describe = QPlainTextEdit()
        self.describe.setPlaceholderText("e.g.\nsmoking a cigarette\na person vomiting\na large spider")
        self.describe.setFixedHeight(76)
        self.describe.textChanged.connect(self._changed)
        sc.lay.addWidget(self.describe)
        srow = QHBoxLayout()
        srow.addWidget(label("How picky", None))
        srow.addStretch()
        self.sens_val = label("", "Muted")
        srow.addWidget(self.sens_val)
        sc.lay.addLayout(srow)
        self.sens = QSlider(Qt.Horizontal)
        self.sens.setRange(1, 5)
        self.sens.setPageStep(1)
        self.sens.setTickPosition(QSlider.TicksBelow)
        names = {1: "Only obvious scenes", 2: "Fewer flags", 3: "Balanced", 4: "More flags",
                 5: "Flag anything suspicious"}
        self.sens.valueChanged.connect(lambda v: (self.sens_val.setText(names[v]), self._changed()))
        sc.lay.addWidget(self.sens)
        sc.lay.addSpacing(6)
        sc.lay.addWidget(label("What happens to a removed scene", None))
        self.act_cut = QRadioButton("Cut it out (the movie gets a little shorter)")
        self.act_black = QRadioButton("Black screen and silence (same length)")
        g2 = QButtonGroup(self)
        for r in (self.act_cut, self.act_black):
            g2.addButton(r)
            r.toggled.connect(self._changed)
            sc.lay.addWidget(r)
        self.scene_note = label("", "Small", wrap=True)
        sc.lay.addWidget(self.scene_note)
        sc.lay.addStretch()
        grid.addWidget(sc, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        # ---- footer
        foot = QHBoxLayout()
        foot.addWidget(label("Preset:", "Muted"))
        self.preset = QComboBox()
        self.preset.setMinimumWidth(180)
        self.preset.activated.connect(self._preset_chosen)
        foot.addWidget(self.preset)
        foot.addWidget(button("Save as preset…", None, self._save_preset))
        foot.addStretch()
        self.note = label("", "Small", wrap=True)
        self.note.setMaximumWidth(420)
        self.note.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        foot.addWidget(self.note)
        self.scan_btn = button("Scan movie  →", "Primary", self.scan.emit)
        foot.addWidget(self.scan_btn)
        outer.addLayout(foot)
        self._presets = {}

    # ---- data in/out
    def set_movie(self, info, project, settings, notes=""):
        self._loading = True
        self.title.setText(Path(project.source).name)
        self.summary.setText(info.summary())
        self.track.clear()
        for t in info.audio:
            self.track.addItem(t.describe())
        self.track.setCurrentIndex(min(project.audio_track, max(0, len(info.audio) - 1)))
        self._presets = settings.all_presets()
        self.preset.clear()
        self.preset.addItems(list(self._presets) + ["Custom"])
        self.apply(FilterOptions.from_dict(settings.filters))
        name = settings.last_preset if settings.last_preset in self._presets else "Custom"
        self.preset.setCurrentText(name)
        self._loading = False
        self.note.setText(notes)
        self._update_notes()

    def apply(self, f: FilterOptions):
        was = self._loading
        self._loading = True
        for k, cb in self.word_boxes.items():
            cb.setChecked(k in f.words)
        self.custom.setPlainText(f.custom_words)
        self.allow.setText(f.allow_words)
        self.pad.setValue(int(round(f.padding * 100)))
        self.pad_val.setText(f"{f.padding:.2f} s")
        (self.mute_center if f.mute_mode == "center" else self.mute_all).setChecked(True)
        for k, cb in self.scene_boxes.items():
            cb.setChecked(k in f.scenes)
        self.describe.setPlainText(f.scene_descriptions)
        self.sens.setValue(int(f.sensitivity))
        self.sens.valueChanged.emit(self.sens.value())
        (self.act_black if f.scene_action == "black" else self.act_cut).setChecked(True)
        self._loading = was
        self._update_notes()

    def filters(self) -> FilterOptions:
        return FilterOptions(
            words=[k for k, cb in self.word_boxes.items() if cb.isChecked()],
            custom_words=self.custom.toPlainText().strip(),
            allow_words=self.allow.text().strip(),
            padding=self.pad.value() / 100,
            mute_mode="center" if self.mute_center.isChecked() else "all",
            scenes=[k for k, cb in self.scene_boxes.items() if cb.isChecked()],
            scene_descriptions=self.describe.toPlainText().strip(),
            sensitivity=self.sens.value(),
            scene_action="black" if self.act_black.isChecked() else "cut")

    def preset_name(self):
        return self.preset.currentText()

    def _changed(self, *_):
        if self._loading:
            return
        cur = asdict(self.filters())
        match = next((n for n, d in self._presets.items() if FilterOptions.from_dict(d).__dict__ == cur), None)
        self.preset.setCurrentText(match or "Custom")
        self._update_notes()

    def _update_notes(self):
        f = self.filters()
        any_scene = bool(f.scenes or f.scene_descriptions)
        if any_scene:
            self.scene_note.setText("Removing scenes means the video is re-encoded when saving "
                                    "(very high quality; faster with an NVIDIA card).")
        else:
            self.scene_note.setText("No scenes selected: the picture is copied untouched — no quality change.")
        self.scan_btn.setEnabled(bool(f.words or f.custom_words or any_scene))

    def _preset_chosen(self, _i):
        name = self.preset.currentText()
        if name in self._presets:
            self.apply(FilterOptions.from_dict(self._presets[name]))
            self.preset.setCurrentText(name)

    def _save_preset(self):
        name, ok = QInputDialog.getText(self, "Save preset", "Name for these settings:")
        if ok and name.strip():
            name = name.strip()
            d = asdict(self.filters())
            self._presets[name] = d
            if self.preset.findText(name) < 0:
                self.preset.insertItem(self.preset.count() - 1, name)
            self.preset.setCurrentText(name)
            self.savePreset.emit(name, d)


# =========================================================================== Scan

class ScanPage(QWidget):
    cancel = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(60, 40, 60, 30)
        lay.setSpacing(14)
        self.title = label("Scanning", "H1")
        lay.addWidget(self.title)
        lay.addWidget(label("You can leave this running and come back. Nothing is changed in your movie.",
                            "Muted", wrap=True))
        self.card = Card()
        lay.addWidget(self.card)
        self.rows = {}
        for key, title in STAGES:
            r = StageRow(title)
            self.rows[key] = r
            self.card.lay.addWidget(r)
        self.log = label("", "Small", wrap=True)
        lay.addWidget(self.log)
        lay.addStretch()
        foot = QHBoxLayout()
        foot.addStretch()
        self.cancel_btn = button("Cancel", "Danger", self.cancel.emit)
        foot.addWidget(self.cancel_btn)
        lay.addLayout(foot)
        self.order, self.current = [], None

    def start(self, name, stages, skipped=()):
        self.title.setText(f"Scanning {name}")
        self.order = list(stages)
        self.current = None
        self.log.setText("")
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("Cancel")
        for key, row in self.rows.items():
            row.setVisible(key in stages)
            row.bar.setValue(0)
            row.msg.setText("Waiting")
            row.set_state("skipped" if key in skipped else "pending")

    def on_progress(self, stage, frac, msg):
        if stage not in self.rows:
            return
        if stage != self.current:
            if self.current in self.rows:
                self.rows[self.current].set_state("done")
            if stage in self.order:
                for k in self.order[:self.order.index(stage)]:
                    if self.rows[k].dot.text() == "○":
                        self.rows[k].set_state("skipped")
            self.current = stage
            self.rows[stage].setVisible(True)
            self.rows[stage].set_state("active")
        self.rows[stage].set_progress(frac, msg)

    def finish(self):
        for k in self.order:
            if self.rows[k].dot.text() in ("○", "●"):
                self.rows[k].set_state("done")


# =========================================================================== Review

class TimesDialog(QDialog):
    def __init__(self, title, start=0.0, end=1.0, with_label=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QFormLayout(self)
        self.a, self.b = TimeEdit(start), TimeEdit(end)
        form.addRow("Starts at (h:mm:ss)", self.a)
        form.addRow("Ends at (h:mm:ss)", self.b)
        self.text = QLineEdit()
        if with_label:
            self.text.setPlaceholderText("What is it? (optional)")
            form.addRow("Note", self.text)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)


class SceneRow(QFrame):
    def __init__(self, scene, source, blurred, on_change):
        super().__init__()
        self.s, self.source, self.on_change = scene, source, on_change
        self.setObjectName("Card")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(14)
        self.pic = QLabel()
        self.pic.setFixedSize(192, 108)
        self.pic.setStyleSheet("background: #222; border-radius: 8px;")
        self.pic.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.pic)
        self.set_blur(blurred)
        col = QVBoxLayout()
        head = QHBoxLayout()
        t = label(scene.get("label", "Scene"), "H2")
        head.addWidget(t)
        if scene.get("score") is not None and not scene.get("manual"):
            conf = int(float(scene["score"]) * 100)
            head.addWidget(label(f"{conf}% sure", "Muted"))
        head.addStretch()
        col.addLayout(head)
        times = QHBoxLayout()
        self.a, self.b = TimeEdit(scene["start"]), TimeEdit(scene["end"])
        self.a.changed.connect(self._times)
        self.b.changed.connect(self._times)
        times.addWidget(self.a)
        times.addWidget(label("to"))
        times.addWidget(self.b)
        self.len = label("", "Muted")
        times.addWidget(self.len)
        times.addStretch()
        col.addLayout(times)
        btns = QHBoxLayout()
        btns.addWidget(button("▶  Play", None, lambda: play_clip(source, self.s["start"], self.s["end"],
                                                                  scene.get("label", "Scene"))))
        self.chk = QCheckBox("Remove this scene")
        self.chk.setChecked(scene.get("on", True))
        self.chk.toggled.connect(self._toggle)
        btns.addWidget(self.chk)
        btns.addStretch()
        col.addLayout(btns)
        lay.addLayout(col, 1)
        self._times()

    def set_blur(self, blurred):
        path = self.s.get("thumb_blur" if blurred else "thumb") or self.s.get("thumb")
        pm = QPixmap(path) if path and Path(path).exists() else QPixmap()
        if pm.isNull():
            self.pic.setText("No picture")
            self.pic.setStyleSheet("background: #EFE6D9; color: #6B566E; border-radius: 8px;")
        else:
            self.pic.setPixmap(pm.scaled(192, 108, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))

    def _times(self, *_):
        a, b = self.a.seconds(), self.b.seconds()
        if b <= a:
            b = a + 1
            self.b.set_seconds(b)
        if (a, b) != (self.s["start"], self.s["end"]):
            self.s["start"], self.s["end"], self.s["edited"] = round(a, 2), round(b, 2), True
            self.on_change()
        self.len.setText(f"({b - a:.0f} s)")

    def _toggle(self, on):
        self.s["on"] = on
        self.on_change()


class ReviewPage(QWidget):
    back = Signal()
    render = Signal()
    changed = Signal()

    COLS = ["Mute", "Time", "Words", "Type", "What was said", ""]

    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        self.project = None
        self.blurred = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 22, 36, 20)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(button("←  Change settings", "Link", self.back.emit))
        head.addStretch()
        lay.addLayout(head)
        self.title = label("Review", "H1")
        lay.addWidget(self.title)
        self.sub = label("Untick anything you want to keep. Use ▶ to hear or see it first.", "Muted", wrap=True)
        lay.addWidget(self.sub)

        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        # words
        wt = QWidget()
        wl = QVBoxLayout(wt)
        wl.setContentsMargins(12, 12, 12, 12)
        bar = QHBoxLayout()
        bar.addWidget(button("Select all", None, lambda: self._set_all(True)))
        bar.addWidget(button("Select none", None, lambda: self._set_all(False)))
        bar.addStretch()
        bar.addWidget(button("+  Add a mute…", None, self._add_mute))
        wl.addLayout(bar)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(40)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(self._item_changed)
        wl.addWidget(self.table)
        self.tabs.addTab(wt, "Words")
        # scenes
        st = QWidget()
        sl = QVBoxLayout(st)
        sl.setContentsMargins(12, 12, 12, 12)
        sbar = QHBoxLayout()
        self.show_pics = QCheckBox("Show pictures unblurred")
        self.show_pics.toggled.connect(self._toggle_blur)
        sbar.addWidget(self.show_pics)
        sbar.addStretch()
        sbar.addWidget(button("+  Add a scene to remove…", None, self._add_scene))
        sl.addLayout(sbar)
        self.scene_scroll = QScrollArea()
        self.scene_scroll.setWidgetResizable(True)
        self.scene_host = QWidget()
        self.scene_list = QVBoxLayout(self.scene_host)
        self.scene_list.setSpacing(10)
        self.scene_scroll.setWidget(self.scene_host)
        sl.addWidget(self.scene_scroll)
        self.tabs.addTab(st, "Scenes")

        foot = QHBoxLayout()
        self.summary = label("", "Muted", wrap=True)
        foot.addWidget(self.summary, 1)
        self.go = button("Make clean copy  →", "Primary", self.render.emit)
        foot.addWidget(self.go)
        lay.addLayout(foot)

    def set_project(self, project, info, settings, filters):
        self.project, self.info, self.filters = project, info, filters
        self.blurred = settings.blur_thumbnails
        self.show_pics.blockSignals(True)
        self.show_pics.setChecked(not self.blurred)
        self.show_pics.blockSignals(False)
        self.title.setText(f"Review: {Path(project.source).name}")
        self._fill_words()
        self._fill_scenes()
        self._update()
        if not project.mutes and project.scenes:
            self.tabs.setCurrentIndex(1)
        else:
            self.tabs.setCurrentIndex(0)

    def _fill_words(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for r, m in enumerate(self.project.mutes):
            self.table.insertRow(r)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Checked if m.get("on", True) else Qt.Unchecked)
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, QTableWidgetItem(fmt_time(m["start"])))
            self.table.setItem(r, 2, QTableWidgetItem(m.get("words", "")))
            cat = "Added by you" if m.get("manual") else CATEGORY_NAMES.get(m.get("category"), "")
            self.table.setItem(r, 3, QTableWidgetItem(cat))
            self.table.setItem(r, 4, QTableWidgetItem(m.get("context", "")))
            b = button("▶ Play", "Link", lambda _=False, m=m: play_clip(self.project.source, m["start"], m["end"],
                                                                   m.get("words", "")), tip="Hear it in the original")
            self.table.setCellWidget(r, 5, b)
        self.table.blockSignals(False)
        self.tabs.setTabText(0, f"Words ({len(self.project.mutes)})")

    def _fill_scenes(self):
        while self.scene_list.count():
            it = self.scene_list.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.scene_rows = []
        if not self.project.scenes:
            self.scene_list.addWidget(label(
                "No scenes to remove. (Turn on scene options in settings to look for some.)", "Muted"))
        for s in self.project.scenes:
            row = SceneRow(s, self.project.source, self.blurred and s.get("kind") in ("nudity", "sexual"),
                           self._scene_changed)
            self.scene_rows.append(row)
            self.scene_list.addWidget(row)
        self.scene_list.addStretch()
        self.tabs.setTabText(1, f"Scenes ({len(self.project.scenes)})")

    def _toggle_blur(self, show):
        self.blurred = not show
        for row in getattr(self, "scene_rows", []):
            row.set_blur(self.blurred and row.s.get("kind") in ("nudity", "sexual"))

    def _item_changed(self, item):
        if item.column() == 0:
            self.project.mutes[item.row()]["on"] = item.checkState() == Qt.Checked
            self._update()
            self.changed.emit()

    def _set_all(self, on):
        for m in self.project.mutes:
            m["on"] = on
        self._fill_words()
        self._update()
        self.changed.emit()

    def _scene_changed(self):
        self._update()
        self.changed.emit()

    def _add_mute(self):
        d = TimesDialog("Add a mute", 0, 1, parent=self)
        if d.exec():
            a, b = d.a.seconds(), d.b.seconds()
            if b > a:
                import uuid
                self.project.mutes.append({"id": uuid.uuid4().hex[:8], "start": a, "end": b,
                                           "words": "(added by you)", "context": "", "category": "custom",
                                           "on": True, "manual": True})
                self.project.mutes.sort(key=lambda m: m["start"])
                self._fill_words()
                self._update()
                self.changed.emit()

    def _add_scene(self):
        d = TimesDialog("Add a scene to remove", 0, 10, with_label=True, parent=self)
        if d.exec():
            a, b = d.a.seconds(), d.b.seconds()
            if b > a:
                import uuid
                self.project.scenes.append({"id": uuid.uuid4().hex[:8], "start": a, "end": b,
                                            "kind": "manual", "label": d.text.text().strip() or "Added by you",
                                            "on": True, "manual": True})
                self.project.scenes.sort(key=lambda s: s["start"])
                self._fill_scenes()
                self._update()
                self.changed.emit()

    def _update(self):
        mutes = [m for m in self.project.mutes if m.get("on", True)]
        scenes = [s for s in self.project.scenes if s.get("on", True)]
        secs = sum(s["end"] - s["start"] for s in scenes)
        parts = [f"{len(mutes)} of {len(self.project.mutes)} words will be silenced"]
        if self.project.scenes:
            verb = "cut out" if self.filters.scene_action == "cut" else "blacked out"
            parts.append(f"{len(scenes)} of {len(self.project.scenes)} scenes {verb} ({fmt_time(secs, False)})")
        if scenes:
            parts.append("video will be re-encoded at very high quality")
        else:
            parts.append("picture copied untouched")
        self.summary.setText(" · ".join(parts) + ".")
        self.go.setEnabled(bool(mutes or scenes))


# =========================================================================== Render / done

class RenderPage(QWidget):
    cancel = Signal()
    again = Signal()
    backToReview = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(60, 50, 60, 30)
        lay.setSpacing(14)
        self.title = label("Making the clean copy", "H1")
        lay.addWidget(self.title)
        self.sub = label("", "Muted", wrap=True)
        lay.addWidget(self.sub)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFixedHeight(16)
        lay.addWidget(self.bar)
        self.msg = label("", "Small")
        lay.addWidget(self.msg)
        self.notes = label("", "Muted", wrap=True)
        lay.addWidget(self.notes)
        lay.addStretch()
        self.btns = QHBoxLayout()
        self.cancel_btn = button("Cancel", "Danger", self.cancel.emit)
        self.play_btn = button("▶  Play clean copy", "Primary", self._play)
        self.folder_btn = button("Open folder", None, self._folder)
        self.review_btn = button("Back to review", None, self.backToReview.emit)
        self.again_btn = button("Clean another movie", None, self.again.emit)
        self.btns.addStretch()
        for b in (self.review_btn, self.again_btn, self.folder_btn, self.play_btn, self.cancel_btn):
            self.btns.addWidget(b)
        lay.addLayout(self.btns)
        self.output = None

    def start(self, name, out, reencode):
        self.output = None
        self.title.setText("Making the clean copy")
        self.sub.setText(f"Saving to {out}" + ("\nThe video is being re-encoded, so this takes a while — "
                                               "you can leave it running." if reencode else ""))
        self.bar.setValue(0)
        self.msg.setText("")
        self.notes.setText("")
        self.cancel_btn.setVisible(True)
        for b in (self.play_btn, self.folder_btn, self.again_btn, self.review_btn):
            b.setVisible(False)

    def on_progress(self, _stage, frac, msg):
        self.bar.setValue(int(frac * 1000))
        self.msg.setText(f"{int(frac * 100)}%  ·  {msg}")

    def done(self, result):
        self.output = result["output"]
        self.title.setText("✓  Your clean copy is ready")
        self.sub.setText(self.output)
        self.bar.setValue(1000)
        bits = [f"{result['mutes']} words silenced"]
        if result["cuts"]:
            bits.append(f"{result['cuts']} scenes cut ({fmt_time(result['removed_seconds'], False)} removed)")
        if result["blackouts"]:
            bits.append(f"{result['blackouts']} scenes blacked out")
        bits.append(result["how"])
        self.msg.setText(" · ".join(bits))
        self.notes.setText("\n".join(result.get("notes", [])))
        self.cancel_btn.setVisible(False)
        for b in (self.play_btn, self.folder_btn, self.again_btn, self.review_btn):
            b.setVisible(True)

    def _play(self):
        if self.output and hasattr(os, "startfile"):
            os.startfile(self.output)

    def _folder(self):
        if self.output and os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(Path(self.output))])
