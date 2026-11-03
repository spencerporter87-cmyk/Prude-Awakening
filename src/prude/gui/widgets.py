"""Small reusable widgets."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)

from ..paths import DATA_DIR

from ..media import VIDEO_EXTS  # noqa: F401  (re-exported for the GUI)


def app_icon() -> QIcon:
    ico = DATA_DIR / "icon.ico"
    png = DATA_DIR / "icon.png"
    return QIcon(str(ico if ico.exists() else png))


def icon_pixmap(size) -> QPixmap:
    png = DATA_DIR / "icon.png"
    pm = QPixmap(str(png))
    return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation) if not pm.isNull() else pm


def label(text, name=None, wrap=False):
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def button(text, name=None, slot=None, tip=None):
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    if slot:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


class Card(QFrame):
    def __init__(self, title=None, subtitle=None, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(20, 18, 20, 18)
        self.lay.setSpacing(8)
        if title:
            self.lay.addWidget(label(title, "H2"))
        if subtitle:
            self.lay.addWidget(label(subtitle, "Muted", wrap=True))


class DropZone(QFrame):
    fileDropped = Signal(str)
    filesDropped = Signal(list)      # several files, or folders -> batch
    browse = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Drop")
        self.setAcceptDrops(True)
        self.setMinimumHeight(280)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(10)
        pic = QLabel()
        pic.setPixmap(icon_pixmap(96))
        pic.setAlignment(Qt.AlignCenter)
        lay.addWidget(pic)
        t = label("Drop a movie or TV episode here", "H2")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        s = label("MKV, MP4, AVI, MOV, WMV, TS and more · drop several files or a folder for a batch", "Muted")
        s.setAlignment(Qt.AlignCenter)
        lay.addWidget(s)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button("Browse…", "Primary", self.browse.emit))
        row.addStretch()
        lay.addSpacing(6)
        lay.addLayout(row)

    def _set_hover(self, on):
        self.setProperty("hover", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._set_hover(True)

    def dragLeaveEvent(self, e):
        self._set_hover(False)

    def dropEvent(self, e):
        self._set_hover(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
        if len(paths) > 1 or any(Path(p).is_dir() for p in paths):
            self.filesDropped.emit(paths)
            return
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p and Path(p).suffix.lower() in VIDEO_EXTS:
                self.fileDropped.emit(p)
                return
        if e.mimeData().urls():
            self.fileDropped.emit(e.mimeData().urls()[0].toLocalFile())


class StageRow(QWidget):
    """One step of the scan: status dot, title, progress bar, detail text."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        self.dot = QLabel("○")
        self.dot.setFixedWidth(26)
        self.dot.setStyleSheet("font-size: 15pt; color: #B8A99A;")
        lay.addWidget(self.dot)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.title = label(title)
        self.title.setStyleSheet("font-weight: 600;")
        col.addWidget(self.title)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFixedHeight(10)
        col.addWidget(self.bar)
        self.msg = label("Waiting", "Small")
        col.addWidget(self.msg)
        lay.addLayout(col, 1)
        self.set_state("pending")

    def set_state(self, state):
        colors = {"pending": ("○", "#B8A99A"), "active": ("●", "#2E7D8C"),
                  "done": ("✓", "#5DAA4A"), "skipped": ("–", "#B8A99A")}
        ch, col = colors[state]
        self.dot.setText(ch)
        self.dot.setStyleSheet(f"font-size: 15pt; color: {col}; font-weight: 700;")
        if state == "done":
            self.bar.setValue(1000)
            if self.msg.text() in ("Waiting", ""):
                self.msg.setText("Done")
        if state == "skipped":
            self.msg.setText("Already done earlier — skipped")
            self.bar.setValue(1000)

    def set_progress(self, frac, msg):
        self.bar.setValue(int(max(0.0, min(1.0, frac)) * 1000))
        if msg:
            self.msg.setText(msg)


def fmt_time(sec, tenths=True):
    sec = max(0.0, float(sec))
    h, m = int(sec // 3600), int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:04.1f}" if tenths else f"{h}:{m:02d}:{int(s):02d}"


def parse_time(text):
    text = text.strip()
    if not text:
        raise ValueError("empty")
    total = 0.0
    for part in text.split(":"):
        total = total * 60 + float(part)
    return total


class TimeEdit(QLineEdit):
    """h:mm:ss.s text box."""
    changed = Signal(float)

    def __init__(self, seconds=0.0, parent=None):
        super().__init__(parent)
        self.setFixedWidth(96)
        self.setAlignment(Qt.AlignCenter)
        self.set_seconds(seconds)
        self.editingFinished.connect(self._done)

    def set_seconds(self, s):
        self._value = float(s)
        self.setText(fmt_time(s))

    def seconds(self):
        return self._value

    def _done(self):
        try:
            v = parse_time(self.text())
        except ValueError:
            self.set_seconds(self._value)
            return
        self.set_seconds(v)
        self.changed.emit(v)
