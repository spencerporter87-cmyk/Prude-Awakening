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
from ..scenes import SCENE_GROUPS, SCENE_TYPES
from ..settings import FilterOptions
from ..words import CATEGORIES, CATEGORY_NAMES
from ..render import merge_ranges, scene_ranges

INTIMATE = {"nudity", "sexual", "sex_nude", "sex_implied", "suggestive", "swimwear", "kissing"}
from ..subtitles import TimeMap
from .player import HAVE_MM, Player
from .scenepicker import SceneGroup, action_combo
from .wordpicker import WordCategory
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
    openFiles = Signal(list)
    browse = Signal()
    openSettings = Signal()
    openBatch = Signal()

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
        self.batch_btn = button("☰  Batch", None, self.openBatch.emit,
                                tip="Several movies or a whole TV season, one after another")
        top.addWidget(self.batch_btn, 0, Qt.AlignTop)
        top.addWidget(button("⚙  Settings", None, self.openSettings.emit), 0, Qt.AlignTop)
        lay.addLayout(top)
        self.drop = DropZone()
        self.drop.fileDropped.connect(self.openFile.emit)
        self.drop.filesDropped.connect(self.openFiles.emit)
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
        info.addSpacing(16)
        info.addWidget(label("Dialogue:", "Muted"))
        self.dialogue = QComboBox()
        for text, key in (("Detect automatically", "auto"), ("English", "en"),
                          ("Another language (clean the subtitles only)", "foreign")):
            self.dialogue.addItem(text, key)
        self.dialogue.setToolTip("Movies like Apocalypto have no English speech, only English subtitles.\n"
                                 "Then nothing is silenced; swear words in the subtitles are starred out\n"
                                 "and the subtitles are set to show automatically.")
        info.addWidget(self.dialogue)
        info.addStretch()
        outer.addLayout(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # ---- language card
        lang = Card()
        lh = QHBoxLayout()
        lh.addWidget(label("Language", "H2"))
        lh.addStretch()
        lh.addWidget(button("Select all", "Link", lambda: self._all_words(True)))
        lh.addWidget(label("·", "Muted"))
        lh.addWidget(button("Select none", "Link", lambda: self._all_words(False)))
        lang.lay.addLayout(lh)
        lang.lay.addWidget(label("Words to silence. Click ▸ Words to pick individual words.",
                                 "Muted", wrap=True))
        srow = QHBoxLayout()
        srow.addWidget(label("In subtitles, show them as", None))
        self.star = QComboBox()
        for text, key in (("****", "stars"), ("f***  (first letter)", "first"),
                          ("a cleaner word (freaking, crap…)", "word")):
            self.star.addItem(text, key)
        self.star.currentIndexChanged.connect(self._changed)
        srow.addWidget(self.star)
        srow.addStretch()
        lang.lay.addLayout(srow)
        self.cats = {}
        for key, name, desc in CATEGORIES:
            wc = WordCategory(key, name, desc, masked=(key == "slurs"))
            wc.changed.connect(self._changed)
            self.cats[key] = wc
            lang.lay.addWidget(wc)
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
        sc = Card("Scenes", "Parts of the movie to remove. You'll review every one before anything is changed, "
                            "and can change what happens to each one.")
        self.groups = {}
        for g, gname, gdesc in SCENE_GROUPS:
            types = [(k, n, d) for k, tg, n, d in SCENE_TYPES if tg == g]
            sg = SceneGroup(g, gname, gdesc if g != "custom" else "Anything else to find", types,
                            text_box=(g == "custom"))
            sg.changed.connect(self._changed)
            self.groups[g] = sg
            sc.lay.addWidget(sg)
        self.scene_note = label("", "Small", wrap=True)
        sc.lay.addWidget(self.scene_note)
        sc.lay.addStretch()
        grid.addWidget(sc, 1, 0)          # stacked: each card gets the full width
        grid.setColumnStretch(0, 1)
        grid.setVerticalSpacing(16)

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
        self.dialogue.setCurrentIndex(max(0, self.dialogue.findData(project.dialogue or "auto")))
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
        excluded = set(f.excluded_words or [])
        for k, wc in self.cats.items():
            wc.set_state(k in f.words, excluded)
        self.custom.setPlainText(f.custom_words)
        self.allow.setText(f.allow_words)
        self.pad.setValue(int(round(f.padding * 100)))
        self.pad_val.setText(f"{f.padding:.2f} s")
        (self.mute_center if f.mute_mode == "center" else self.mute_all).setChecked(True)
        self.star.setCurrentIndex(max(0, self.star.findData(f.star_style or "stars")))
        chosen = set(f.scenes)
        for g, sg in self.groups.items():
            sg.set_state(chosen, (f.scene_sensitivity or {}).get(g, f.sensitivity),
                         (f.scene_actions or {}).get(g, f.scene_action),
                         f.scene_descriptions if g == "custom" else "")
        self._loading = was
        self._update_notes()

    def filters(self) -> FilterOptions:
        return FilterOptions(
            words=[k for k, wc in self.cats.items() if wc.enabled()],
            excluded_words=[x for wc in self.cats.values() for x in wc.excluded()],
            custom_words=self.custom.toPlainText().strip(),
            allow_words=self.allow.text().strip(),
            padding=self.pad.value() / 100,
            star_style=self.star.currentData() or "stars",
            mute_mode="center" if self.mute_center.isChecked() else "all",
            scenes=[k for sg in self.groups.values() for k in sg.selected()],
            scene_descriptions=self.groups["custom"].text_value(),
            sensitivity=3,
            scene_action="cut",
            scene_sensitivity={g: sg.sensitivity() for g, sg in self.groups.items()},
            scene_actions={g: sg.action_value() for g, sg in self.groups.items()})

    def _all_words(self, on):
        was = self._loading
        self._loading = True
        for wc in self.cats.values():
            wc.set_all(on)
        self._loading = was
        self._changed()

    def dialogue_value(self):
        return self.dialogue.currentData() or "auto"

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
            self.scene_note.setText("Cutting, blacking out or blurring scenes means the video is re-encoded "
                                    "when saving (very high quality; faster with an NVIDIA card).")
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
    def __init__(self, scene, source, blurred, on_change, on_play=None):
        super().__init__()
        self.s, self.source, self.on_change = scene, source, on_change
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.pic = QLabel()
        self.pic.setFixedSize(112, 63)
        self.pic.setStyleSheet("background: #222; border-radius: 8px;")
        self.pic.setAlignment(Qt.AlignCenter)
        top.addWidget(self.pic, 0, Qt.AlignTop)
        self.set_blur(blurred)
        info = QVBoxLayout()
        info.setSpacing(2)
        t = label(scene.get("label", "Scene"), "H2")
        t.setWordWrap(True)
        info.addWidget(t)
        bits = []
        if scene.get("detail"):
            bits.append(scene["detail"].capitalize())
        if scene.get("score") is not None and not scene.get("manual"):
            bits.append(f"{int(float(scene['score']) * 100)}% sure")
        if bits:
            info.addWidget(label(" · ".join(bits), "Small", wrap=True))
        times = QHBoxLayout()
        self.a, self.b = TimeEdit(scene["start"]), TimeEdit(scene["end"])
        self.a.changed.connect(self._times)
        self.b.changed.connect(self._times)
        times.addWidget(self.a)
        times.addWidget(label("–"))
        times.addWidget(self.b)
        self.len = label("", "Muted")
        times.addWidget(self.len)
        times.addStretch()
        info.addLayout(times)
        top.addLayout(info, 1)
        outer.addLayout(top)
        btns = QHBoxLayout()
        btns.addWidget(button("▶ Play", None, lambda: on_play(self.s) if on_play else
                              play_clip(source, self.s["start"], self.s["end"], scene.get("label", "Scene"))))
        self.chk = QCheckBox("Change it")
        self.chk.setToolTip("Untick to leave this scene as it is")
        self.chk.setChecked(scene.get("on", True))
        self.chk.toggled.connect(self._toggle)
        btns.addWidget(self.chk)
        self.act = action_combo(scene.get("action") or "cut")
        self.act.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.act.setMinimumWidth(110)
        self.act.setEnabled(scene.get("on", True))
        self.act.currentIndexChanged.connect(self._action)
        btns.addWidget(self.act, 1)
        outer.addLayout(btns)
        self._times()

    def set_blur(self, blurred):
        path = self.s.get("thumb_blur" if blurred else "thumb") or self.s.get("thumb")
        pm = QPixmap(path) if path and Path(path).exists() else QPixmap()
        if pm.isNull():
            self.pic.setText("No picture")
            self.pic.setStyleSheet("background: #EFE6D9; color: #6B566E; border-radius: 8px;")
        else:
            self.pic.setPixmap(pm.scaled(112, 63, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))

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
        self.act.setEnabled(on)
        self.on_change()

    def _action(self, _i):
        self.s["action"], self.s["action_set"] = self.act.currentData(), True
        self.on_change()


class ReviewPage(QWidget):
    back = Signal()
    render = Signal()
    changed = Signal()

    COLS = ["On", "Time", "Words", "What was said", ""]

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
        self.sub = label("Untick anything you want to keep. Click ▶ Play, or anywhere on the timeline, "
                         "to watch that moment. Turn on “Preview with filters on” to see the result before saving.",
                         "Muted", wrap=True)
        lay.addWidget(self.sub)

        main = QHBoxLayout()
        main.setSpacing(16)
        self.player = Player(live_toggle=True, timeline_inside=False)
        main.addWidget(self.player, 1)
        self.tabs = QTabWidget()
        main.addWidget(self.tabs, 1)
        lay.addLayout(main, 1)
        tl_card = QFrame()
        tl_card.setObjectName("Card")
        tl_lay = QVBoxLayout(tl_card)
        tl_lay.setContentsMargins(10, 10, 14, 8)
        tl_lay.setSpacing(2)
        tl_lay.addWidget(self.player.timeline)
        tl_lay.addLayout(self.player.legend_box)
        lay.addWidget(tl_card)
        self.player.timeline.itemClicked.connect(self._timeline_item)
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
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
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
        self.scene_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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
        self.player.set_legend("plan", filters.scene_action)
        self.player.load(project.source)
        self._fill_words()
        self._fill_scenes()
        self._update()
        if not project.mutes and project.scenes:
            self.tabs.setCurrentIndex(1)
        else:
            self.tabs.setCurrentIndex(0)

    def _sub_rows(self):
        """Subtitle-only rows: shown when the dialogue isn't English (nothing to silence, only stars)."""
        from ..pipeline import dialogue_is_english
        if dialogue_is_english(self.project):
            return []
        return list(getattr(self.project, "subtitle_edits", []) or [])

    def _fill_words(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.rows = [("mute", m) for m in self.project.mutes] + [("sub", x) for x in self._sub_rows()]
        self.rows.sort(key=lambda r: r[1]["start"])
        for r, (kind, m) in enumerate(self.rows):
            self.table.insertRow(r)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Checked if m.get("on", True) else Qt.Unchecked)
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, QTableWidgetItem(fmt_time(m["start"])))
            cat = "Added by you" if m.get("manual") else CATEGORY_NAMES.get(m.get("category"), "")
            if kind == "sub":
                cat += "\nIn the subtitles only: starred out when ticked (the dialogue isn't English)."
                chk.setToolTip("Star this word out in the subtitles")
            elif m.get("unsure"):
                cat += "\nFound in the subtitles but not heard clearly: play it to check."
            wi = QTableWidgetItem(("💬 " if kind == "sub" else "") + m.get("words", ""))
            wi.setToolTip(cat)
            self.table.setItem(r, 2, wi)
            ci = QTableWidgetItem(m.get("context", ""))
            ci.setToolTip(f"{cat}\n{m.get('context', '')}")
            self.table.setItem(r, 3, ci)
            b = button("▶ Play", "Link", lambda _=False, m=m: self._play(m), tip="See and hear it in the original")
            self.table.setCellWidget(r, 4, b)
        self.table.blockSignals(False)
        self.tabs.setTabText(0, f"Words ({len(self.rows)})")

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
            row = SceneRow(s, self.project.source, self.blurred and s.get("kind") in INTIMATE,
                           self._scene_changed, self._play)
            self.scene_rows.append(row)
            self.scene_list.addWidget(row)
        self.scene_list.addStretch()
        self.tabs.setTabText(1, f"Scenes ({len(self.project.scenes)})")

    def _toggle_blur(self, show):
        self.blurred = not show
        for row in getattr(self, "scene_rows", []):
            row.set_blur(self.blurred and row.s.get("kind") in INTIMATE)

    def _item_changed(self, item):
        if item.column() == 0:
            self.rows[item.row()][1]["on"] = item.checkState() == Qt.Checked
            self._update()
            self.changed.emit()

    def _set_all(self, on):
        for _k, m in self.rows:
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

    def _play(self, item):
        a, b = float(item["start"]), float(item["end"])
        if not (HAVE_MM and self.player.play_range(max(0.0, a - 2.0), b)):
            play_clip(self.project.source, a, b, item.get("words") or item.get("label", ""))

    def _timeline_item(self, kind, i):
        if kind == "mute":
            self.tabs.setCurrentIndex(0)
            if 0 <= i < len(self.project.mutes):
                target = self.project.mutes[i]
                i = next((r for r, (_k, m) in enumerate(self.rows) if m is target), i)
            self.table.selectRow(i)
            self.table.scrollToItem(self.table.item(i, 0))
        else:
            self.tabs.setCurrentIndex(1)
            rows = getattr(self, "scene_rows", [])
            if 0 <= i < len(rows):
                self.scene_scroll.ensureWidgetVisible(rows[i])

    def leave(self):
        self.player.stop()

    def _refresh_timeline(self):
        p = self.project
        r = scene_ranges(p, self.filters.scene_action)
        mutes = merge_ranges([(m["start"], m["end"]) for m in p.mutes if m.get("on", True)] + r["mute"])
        self.player.set_filters(mutes, r["cut"], r["black"], r["blur"], r["black_keep"], r["blur_parts"])
        dur = self.info.duration if self.info else max([1.0] + [x["end"] for x in p.mutes + p.scenes])
        self.player.timeline.set_plan(dur, p.mutes, p.scenes, self.filters.scene_action)

    def _update(self):
        self._refresh_timeline()
        mutes = [m for m in self.project.mutes if m.get("on", True)]
        scenes = [s for s in self.project.scenes if s.get("on", True)]
        subs = self._sub_rows()
        if subs and not self.project.mutes:
            parts = [f"{sum(1 for x in subs if x.get('on', True))} of {len(subs)} words starred out "
                     "in the subtitles (the dialogue isn't English)"]
        else:
            parts = [f"{len(mutes)} of {len(self.project.mutes)} words will be silenced"]
        if self.project.scenes:
            by = {}
            for s in scenes:
                a = s.get("action") or self.filters.scene_action
                by[a] = by.get(a, 0) + 1
            names = {"cut": "cut", "black": "blacked out", "black_keep": "blacked out (sound kept)",
                     "blur": "blurred", "blur_parts": "private parts blurred", "mute": "silenced"}
            what = ", ".join(f"{n} {names[a]}" for a, n in by.items()) or "none changed"
            parts.append(f"{len(scenes)} of {len(self.project.scenes)} scenes ({what})")
        if any((s.get("action") or self.filters.scene_action) != "mute" for s in scenes):
            parts.append("video will be re-encoded at very high quality")
        else:
            parts.append("picture copied untouched")
        self.summary.setText(" · ".join(parts) + ".")
        self.go.setEnabled(bool(mutes or scenes or subs))


def clean_timeline(project, filters, duration):
    """(new_duration, mutes, other_scenes, cut_points) for the clean copy's timeline."""
    r = scene_ranges(project, filters.scene_action)
    labels = {round(s["start"], 2): s.get("label", "Scene") for s in project.active_scenes()}
    cuts = r["cut"]
    tm = TimeMap(cuts)
    mutes = []
    for m in project.active_mutes():
        a, b = tm(m["start"]), tm(m["end"])
        if a is not None and b is not None:
            mutes.append({"start": a, "end": b, "words": m.get("words", "")})
    others = []
    for action in ("black", "black_keep", "blur", "blur_parts", "mute"):
        for a, b in r[action]:
            ma, mb = tm(a), tm(b)
            if ma is not None and mb is not None:
                others.append({"start": ma, "end": mb, "action": action,
                               "label": labels.get(round(a, 2), "Scene")})
    cut_points = [(tm(a), b - a, labels.get(round(a, 2), "Scene")) for a, b in cuts]
    return max(1.0, duration - sum(b - a for a, b in cuts)), mutes, others, cut_points


# =========================================================================== Render / done

class RenderPage(QWidget):
    cancel = Signal()
    again = Signal()
    backToReview = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 28, 40, 22)
        lay.setSpacing(10)
        self.title = label("Making the clean copy", "H1")
        lay.addWidget(self.title)
        self.sub = label("", "Muted", wrap=True)
        lay.addWidget(self.sub)
        self.stats = QWidget()
        st = QHBoxLayout(self.stats)
        st.setContentsMargins(0, 8, 0, 0)
        self.pct = QLabel("0%")
        self.pct.setStyleSheet("font-size: 34pt; font-weight: 700; color: #2E7D8C;")
        st.addWidget(self.pct)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.where = label("Starting…", "H2")
        col.addWidget(self.where)
        self.eta = label("", "Muted")
        col.addWidget(self.eta)
        st.addLayout(col, 1)
        self.elapsed = label("", "Muted")
        st.addWidget(self.elapsed, 0, Qt.AlignBottom)
        lay.addWidget(self.stats)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFixedHeight(16)
        lay.addWidget(self.bar)
        self.msg = label("", "Small")
        lay.addWidget(self.msg)
        self.tl_card = QFrame()
        self.tl_card.setObjectName("Card")
        tcl = QVBoxLayout(self.tl_card)
        tcl.setContentsMargins(10, 10, 14, 8)
        tcl.addWidget(label("The clean copy so far", "H2"))
        from .timeline import Timeline, legend
        self.progress_tl = Timeline()
        tcl.addWidget(self.progress_tl)
        tcl.addWidget(legend("progress"))
        lay.addWidget(self.tl_card)
        self.notes = label("", "Muted", wrap=True)
        lay.addWidget(self.notes)
        from PySide6.QtCore import QElapsedTimer, QTimer
        self.clock = QElapsedTimer()
        self.ticker = QTimer(self)
        self.ticker.setInterval(1000)
        self.ticker.timeout.connect(self._tick)
        self.total = 1.0
        self.player = Player(live_toggle=False, timeline_inside=True)
        self.player.setVisible(False)
        lay.addWidget(self.player, 1)
        self.spacer = QWidget()
        lay.addWidget(self.spacer, 1)
        self.btns = QHBoxLayout()
        self.cancel_btn = button("Cancel", "Danger", self.cancel.emit)
        self.play_btn = button("▶  Play / pause", "Primary", self._play)
        self.ext_btn = button("Open in my video player", None,
                              lambda: self.output and hasattr(os, "startfile") and os.startfile(self.output))
        self.folder_btn = button("Open folder", None, self._folder)
        self.review_btn = button("Back to review", None, self.backToReview.emit)
        self.again_btn = button("Clean another movie", None, self.again.emit)
        self.btns.addStretch()
        for b in (self.review_btn, self.again_btn, self.folder_btn, self.ext_btn, self.play_btn, self.cancel_btn):
            self.btns.addWidget(b)
        lay.addLayout(self.btns)
        self.output = None

    def start(self, name, out, reencode, project=None, filters=None, duration=None):
        self.output = None
        self.title.setText("Making the clean copy")
        self.sub.setText(f"Saving to {out}" + ("\nThe video is being re-encoded, so this takes a while — "
                                               "you can leave it running." if reencode else ""))
        self.bar.setRange(0, 0)      # "busy" animation until the first numbers arrive
        self.msg.setText("")
        self.notes.setText("")
        self.pct.setText("0%")
        self.where.setText("Starting…")
        self.eta.setText("Getting the encoder ready")
        self.stats.setVisible(True)
        if project is not None and filters is not None and duration:
            self.total, mutes, blacks, cuts = clean_timeline(project, filters, duration)
            self.progress_tl.set_result(self.total, mutes, blacks, cuts)
            self.progress_tl.set_fill(0)
            self.progress_tl.set_position(0)
            self.tl_card.setVisible(True)
        else:
            self.tl_card.setVisible(False)
        self.clock.start()
        self.ticker.start()
        self._tick()
        self.cancel_btn.setVisible(True)
        self.cancel_btn.setEnabled(True)
        self.player.stop()
        self.player.setVisible(False)
        self.spacer.setVisible(True)
        self.bar.setVisible(True)
        for b in (self.play_btn, self.folder_btn, self.again_btn, self.review_btn, self.ext_btn):
            b.setVisible(False)

    def _tick(self):
        secs = self.clock.elapsed() / 1000 if self.clock.isValid() else 0
        self.elapsed.setText(f"Running for {fmt_time(secs, False)}")

    def on_progress(self, _stage, frac, msg):
        if frac > 0 and self.bar.maximum() == 0:
            self.bar.setRange(0, 1000)
        if self.bar.maximum():
            self.bar.setValue(int(frac * 1000))
        self.pct.setText(f"{int(frac * 100)}%")
        pos = frac * self.total
        self.where.setText(f"{fmt_time(pos, False)} of {fmt_time(self.total, False)} saved")
        parts = [p for p in (msg or "").split(" · ")[1:]]
        self.eta.setText(" · ".join(parts) if parts else (msg or ""))
        self.progress_tl.set_fill(pos)
        self.progress_tl.set_position(pos)

    def done(self, result):
        self.ticker.stop()
        self.stats.setVisible(False)
        self.tl_card.setVisible(False)
        self.bar.setRange(0, 1000)
        self.output = result["output"]
        self.title.setText("✓  Your clean copy is ready")
        self.sub.setText(self.output)
        self.bar.setValue(1000)
        def n(k, word):
            return f"{k} {word}{'' if k == 1 else 's'}"
        bits = [n(result["mutes"], "word") + " silenced"]
        if result["cuts"]:
            bits.append(n(result["cuts"], "scene") + f" cut ({fmt_time(result['removed_seconds'], False)} removed)")
        if result["blackouts"]:
            bits.append(n(result["blackouts"], "scene") + " blacked out")
        if result.get("parts_blurred"):
            bits.append(n(result["parts_blurred"], "scene") + " with private parts blurred")
        if result.get("blurs"):
            bits.append(n(result["blurs"], "scene") + " blurred")
        if result.get("muted_scenes"):
            bits.append(n(result["muted_scenes"], "scene") + " silenced")
        self.msg.setText(" · ".join(bits))
        from ..export import human_size
        rows = []
        if result.get("original_duration"):
            rows.append(f"<b>Length:</b> {fmt_time(result['original_duration'], False)} original → "
                        f"{fmt_time(result.get('final_duration', 0), False)} clean copy")
        if result.get("size_bytes"):
            res = f" · {result['resolution']}" if result.get("resolution") else ""
            rows.append(f"<b>File:</b> {human_size(result['size_bytes'])}{res}")
        if result.get("valid") is False:
            self.title.setText("⚠  Saved, but please check it")
            rows += [f"<span style='color:#C8412B'>⚠ {p}</span>" for p in result.get("problems", [])]
        elif "valid" in result:
            rows.append("<span style='color:#3C7A30'>✓ Checked: the finished file opens, and its length "
                        "and streams are as expected.</span>")
        rows += [f"• {w}" for w in result.get("warnings", result.get("notes", []))]
        self.notes.setTextFormat(Qt.RichText)
        self.notes.setText("<br>".join(rows))
        self.cancel_btn.setVisible(False)
        for b in (self.play_btn, self.folder_btn, self.again_btn, self.review_btn, self.ext_btn):
            b.setVisible(True)

    def show_result(self, result, project, filters, duration):
        """Player for the clean copy + timeline of what changed (in the clean copy's time)."""
        self.done(result)
        new_dur, mutes, blackouts, cut_points = clean_timeline(project, filters, duration)
        self.player.timeline.set_result(new_dur, mutes, blackouts, cut_points)
        self.player.set_legend("result")
        self.player.setVisible(True)
        self.spacer.setVisible(False)
        self.bar.setVisible(False)
        self.player.load(self.output)

    def leave(self):
        self.player.stop()

    def _play(self):
        if self.output and HAVE_MM and self.player.isVisible():
            self.player.toggle()
        elif self.output and hasattr(os, "startfile"):
            os.startfile(self.output)

    def _folder(self):
        if self.output and os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(Path(self.output))])
