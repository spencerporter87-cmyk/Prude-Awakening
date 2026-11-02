"""Language categories that open up (▸) to show every word, each with its own tick box."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from ..words import category_groups, group_key
from .widgets import button, label


def _friendly(pattern):
    p = pattern.strip()
    if p.startswith("*"):
        p = "…" + p[1:]
    if p.endswith("*"):
        p = p[:-1] + "…"
    return p.replace("*", "…")


class CategoryBox(QCheckBox):
    """Shows ☑ / ▣ (some) / ☐, but a click only ever toggles between all and none."""

    def nextCheckState(self):
        self.setCheckState(Qt.Unchecked if self.checkState() == Qt.Checked else Qt.Checked)


class WordCategory(QWidget):
    changed = Signal()
    COLS = 3

    def __init__(self, key, name, desc, masked=False, parent=None):
        super().__init__(parent)
        self.key = key
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.box = CategoryBox(name)
        self.box.setTristate(True)
        self.box.clicked.connect(self._box_clicked)
        head.addWidget(self.box)
        d = label(desc, "Small")
        d.setToolTip(desc)
        head.addWidget(d, 1)
        self.count = label("", "Small")
        head.addWidget(self.count)
        self.chev = button("▸ Words", "Link", self.toggle, tip="Choose individual words")
        head.addWidget(self.chev)
        lay.addLayout(head)

        self.panel = QFrame()
        self.panel.setObjectName("WordPanel")
        self.panel.setStyleSheet("#WordPanel { background: #FBF7F1; border: 1px solid #EDE3D5; "
                                 "border-radius: 10px; }")
        pl = QVBoxLayout(self.panel)
        pl.setContentsMargins(12, 8, 12, 10)
        pl.setSpacing(4)
        links = QHBoxLayout()
        links.addWidget(label("Tick the words to silence. Hover a word to see every form it catches.", "Small"), 1)
        links.addWidget(button("All", "Link", lambda: self.set_all(True)))
        links.addWidget(button("None", "Link", lambda: self.set_all(False)))
        pl.addLayout(links)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(0)
        self.words = {}
        for i, (lab, pats) in enumerate(category_groups(key)):
            cb = QCheckBox(lab)
            forms = ", ".join(_friendly(p) for p in pats)
            cb.setToolTip("Catches: " + (forms if not masked else f"{len(pats)} spelling(s)"))
            cb.toggled.connect(self._word_toggled)
            self.words[group_key(key, lab)] = cb
            grid.addWidget(cb, i // self.COLS, i % self.COLS)
        for c in range(self.COLS):
            grid.setColumnStretch(c, 1)
        pl.addLayout(grid)
        self.panel.setVisible(False)
        lay.addWidget(self.panel)
        self._sync()

    # ------------------------------------------------------------ state
    def set_state(self, enabled, excluded):
        self._loading = True
        for k, cb in self.words.items():
            cb.setChecked(bool(enabled) and k not in excluded)
        self._loading = False
        self._sync()

    def enabled(self):
        return any(cb.isChecked() for cb in self.words.values())

    def excluded(self):
        if not self.enabled():
            return []
        return [k for k, cb in self.words.items() if not cb.isChecked()]

    def set_all(self, on):
        self._loading = True
        for cb in self.words.values():
            cb.setChecked(on)
        self._loading = False
        self._sync()
        self.changed.emit()

    def toggle(self):
        show = not self.panel.isVisible()
        self.panel.setVisible(show)
        self.chev.setText("▾ Words" if show else "▸ Words")

    # ------------------------------------------------------------ events
    def _box_clicked(self):
        self.set_all(self.box.checkState() == Qt.Checked)

    def _word_toggled(self, _on):
        if self._loading:
            return
        self._sync()
        self.changed.emit()

    def _sync(self):
        n = len(self.words)
        on = sum(cb.isChecked() for cb in self.words.values())
        self.box.blockSignals(True)
        self.box.setCheckState(Qt.Checked if on == n else Qt.Unchecked if on == 0 else Qt.PartiallyChecked)
        self.box.blockSignals(False)
        self.count.setText(f"{on} of {n}" if 0 < on < n else ("all" if on == n else ""))
