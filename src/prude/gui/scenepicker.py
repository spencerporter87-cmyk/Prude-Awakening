"""Scene options, grouped: a compact row per group with its details behind a toggle."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QPlainTextEdit, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from .widgets import button, label
from .wordpicker import CategoryBox

ACTION_CHOICES = [("cut", "Cut it out"), ("black", "Black screen + silence"),
                  ("black_keep", "Black screen (keep sound)"),
                  ("blur_parts", "Blur just the private parts (keep sound)"),
                  ("blur", "Blur the whole picture (keep sound)"),
                  ("mute", "Silence the sound only")]
ACTION_SHORT = {"cut": "Cut it out", "black": "Black screen + silence",
                "black_keep": "Black screen (keep sound)", "blur_parts": "Blur private parts",
                "blur": "Blur picture", "mute": "Silence sound"}
PICKY = {1: "Only obvious", 2: "Fewer flags", 3: "Balanced", 4: "More flags", 5: "Flag anything suspicious"}


def action_combo(current="cut"):
    c = QComboBox()
    for v, t in ACTION_CHOICES:
        c.addItem(t, v)
    c.setCurrentIndex(max(0, c.findData(current)))
    return c


class SceneGroup(QFrame):
    changed = Signal()
    detailsToggled = Signal(str, bool)

    def __init__(self, group, name, desc, types, text_box=False, parent=None):
        super().__init__(parent)
        self.group = group
        self.name = name
        self.desc = desc
        self._loading = False
        self.expanded = False
        self._details_badge = ""
        self.setObjectName("SceneGroup")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.box = None
        self.boxes = {}
        if types:
            self.box = CategoryBox(name)
            self.box.setTristate(True)
            self.box.setStyleSheet("font-weight: 600;")
            self.box.clicked.connect(self._group_clicked)
            head.addWidget(self.box)
        else:
            t = label(name)
            t.setStyleSheet("font-weight: 600;")
            head.addWidget(t)
        self.chip = label("", "Muted")
        self.chip.setStyleSheet("background:#F1E8DB; border-radius:9px; padding:2px 8px;")
        head.addWidget(self.chip)
        head.addStretch()
        self.details_btn = button("▸ Details", "Link", self.toggle_details)
        self.details_btn.setAccessibleName(f"{name} details, collapsed")
        head.addWidget(self.details_btn)
        lay.addLayout(head)

        self.details = QWidget()
        dl = QVBoxLayout(self.details)
        dl.setContentsMargins(24, 0, 0, 0)
        dl.setSpacing(8)
        if desc:
            dl.addWidget(label(desc, "Muted", wrap=True))
        if types:
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(0)
            for i, (key, tname, tdesc) in enumerate(types):
                cb = QCheckBox(tname)
                cb.setToolTip(tdesc)
                cb.setAccessibleName(tname)
                cb.toggled.connect(self._toggled)
                self.boxes[key] = cb
                grid.addWidget(cb, i // 3, i % 3)
            for c in range(3):
                grid.setColumnStretch(c, 1)
            dl.addLayout(grid)
        self.text = None
        if text_box:
            self.text = QPlainTextEdit()
            self.text.setPlaceholderText("One per line, in plain English, e.g.\nsmoking a cigarette\n"
                                         "a person vomiting\na large spider")
            self.text.setFixedHeight(84)
            self.text.textChanged.connect(self._toggled)
            dl.addWidget(self.text)

        row = QHBoxLayout()
        row.addWidget(label("How picky", "Small"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(1, 5)
        self.slider.setPageStep(1)
        self.slider.setMinimumWidth(90)
        self.slider.setAccessibleName(f"{name} how picky")
        row.addWidget(self.slider, 1)
        self.picky = label("", "Small")
        self.picky.setMinimumWidth(118)
        row.addWidget(self.picky)
        dl.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(label("What to do with them", "Small"))
        self.action = action_combo()
        self.action.setMinimumWidth(120)
        self.action.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.action.setAccessibleName(f"{name} action")
        self.action.currentIndexChanged.connect(self._toggled)
        row2.addWidget(self.action, 1)
        dl.addLayout(row2)
        self.details.setVisible(False)
        lay.addWidget(self.details)
        self.slider.valueChanged.connect(lambda v: (self.picky.setText(PICKY[v]), self._toggled()))
        self.slider.setValue(3)
        self._sync()

    # ------------------------------------------------------------ details
    def toggle_details(self):
        self.set_details_expanded(not self.expanded)

    def set_details_expanded(self, on, persist=True):
        on = bool(on)
        if on == self.expanded:
            return
        self.expanded = on
        self.details.setVisible(on)
        self._render_details_button()
        if persist:
            self.detailsToggled.emit(self.group, on)

    def set_details_badge(self, badge):
        badge = badge or ""
        if badge == self._details_badge:
            return
        self._details_badge = badge
        self._render_details_button()

    def _render_details_button(self):
        arrow = "▾" if self.expanded else "▸"
        suffix = f" · {self._details_badge}" if self._details_badge else ""
        self.details_btn.setText(f"{arrow} Details{suffix}")
        state = "expanded" if self.expanded else "collapsed"
        self.details_btn.setAccessibleName(f"{self.name} details, {state}")
        self.details_btn.setAccessibleDescription(f"{self.name} details, {state}")

    # ------------------------------------------------------------ state
    def set_state(self, selected, sensitivity, action, text=""):
        self._loading = True
        for k, cb in self.boxes.items():
            cb.setChecked(k in selected)
        self.slider.setValue(int(sensitivity))
        self.picky.setText(PICKY[int(sensitivity)])
        self.action.setCurrentIndex(max(0, self.action.findData(action)))
        if self.text is not None:
            self.text.setPlainText(text or "")
        self._loading = False
        self._sync()

    def selected(self):
        return [k for k, cb in self.boxes.items() if cb.isChecked()]

    def sensitivity(self):
        return self.slider.value()

    def action_value(self):
        return self.action.currentData()

    def text_value(self):
        return self.text.toPlainText().strip() if self.text is not None else ""

    # ------------------------------------------------------------ events
    def _group_clicked(self):
        on = self.box.checkState() == Qt.Checked
        self._loading = True
        for cb in self.boxes.values():
            cb.setChecked(on)
        self._loading = False
        self._sync()
        self.changed.emit()

    def _toggled(self, *_):
        if self._loading:
            return
        self._sync()
        self.changed.emit()

    def _sync(self):
        active = bool(self.selected()) or bool(self.text_value())
        for w in (self.slider, self.picky, self.action):
            w.setEnabled(active)
        if self.boxes:
            n, on = len(self.boxes), len(self.selected())
            self.box.blockSignals(True)
            self.box.setCheckState(Qt.Checked if on == n else Qt.Unchecked if on == 0 else Qt.PartiallyChecked)
            self.box.blockSignals(False)
        action = ACTION_SHORT.get(self.action_value() or "", self.action.currentText())
        if self.boxes:
            on = len(self.selected())
            self.chip.setText("Off" if on == 0 else f"{on} of {len(self.boxes)} · {action}")
        else:
            self.chip.setText(f"Custom · {action}" if self.text_value() else "Off")
        self.chip.setAccessibleName(self.chip.text())
