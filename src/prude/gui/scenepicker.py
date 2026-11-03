"""Scene options, grouped: each group has tick boxes, its own "how picky" slider and
what to do with what it finds."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QPlainTextEdit, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from .widgets import label
from .wordpicker import CategoryBox

ACTION_CHOICES = [("cut", "Cut it out"), ("black", "Black screen + silence"),
                  ("black_keep", "Black screen (keep sound)"),
                  ("blur_parts", "Blur just the private parts (keep sound)"),
                  ("blur", "Blur the whole picture (keep sound)"),
                  ("mute", "Silence the sound only")]
PICKY = {1: "Only obvious", 2: "Fewer flags", 3: "Balanced", 4: "More flags", 5: "Flag anything suspicious"}


def action_combo(current="cut"):
    c = QComboBox()
    for v, t in ACTION_CHOICES:
        c.addItem(t, v)
    c.setCurrentIndex(max(0, c.findData(current)))
    return c


class SceneGroup(QFrame):
    changed = Signal()

    def __init__(self, group, name, desc, types, text_box=False, parent=None):
        super().__init__(parent)
        self.group = group
        self._loading = False
        self.setObjectName("SceneGroup")
        self.setStyleSheet("#SceneGroup { background: #FBF7F1; border: 1px solid #EDE3D5; border-radius: 10px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
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
        head.addStretch()
        lay.addLayout(head)
        if desc:
            lay.addWidget(label(desc, "Small", wrap=True))

        if types:
            grid = QGridLayout()
            grid.setContentsMargins(22, 0, 0, 0)
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(0)
            for i, (key, tname, tdesc) in enumerate(types):
                cb = QCheckBox(tname)
                cb.setToolTip(tdesc)
                cb.toggled.connect(self._toggled)
                self.boxes[key] = cb
                grid.addWidget(cb, i // 2, i % 2)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            lay.addLayout(grid)
        self.text = None
        if text_box:
            self.text = QPlainTextEdit()
            self.text.setPlaceholderText("One per line, in plain English, e.g.\nsmoking a cigarette\n"
                                         "a person vomiting\na large spider")
            self.text.setFixedHeight(84)
            self.text.textChanged.connect(self._toggled)
            lay.addWidget(self.text)

        row = QHBoxLayout()
        row.addWidget(label("How picky", "Small"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(1, 5)
        self.slider.setPageStep(1)
        self.slider.setMinimumWidth(90)
        row.addWidget(self.slider, 1)
        self.picky = label("", "Small")
        self.picky.setMinimumWidth(118)
        row.addWidget(self.picky)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(label("What to do with them", "Small"))
        self.action = action_combo()
        self.action.setMinimumWidth(120)
        self.action.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.action.currentIndexChanged.connect(self._toggled)
        row2.addWidget(self.action, 1)
        lay.addLayout(row2)
        self.slider.valueChanged.connect(lambda v: (self.picky.setText(PICKY[v]), self._toggled()))
        self.slider.setValue(3)
        self._sync()

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
        if self.box is None:
            return
        n, on = len(self.boxes), len(self.selected())
        self.box.blockSignals(True)
        self.box.setCheckState(Qt.Checked if on == n else Qt.Unchecked if on == 0 else Qt.PartiallyChecked)
        self.box.blockSignals(False)
