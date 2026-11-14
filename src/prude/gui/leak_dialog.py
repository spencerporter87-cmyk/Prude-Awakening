"""'What slipped through': the findings of the check of the finished copy, each with a way to fix it."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget)

from .. import leakcheck
from .widgets import button, fmt_time, label

ICON = {"picture": "🖼", "mute": "🔊", "heard": "👂", "subtitle": "💬"}


class LeakRow(QFrame):
    def __init__(self, f, blurred, dlg):
        super().__init__()
        self.setObjectName("Card")
        self.f = f
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)
        path = f.get("thumb_blur" if blurred else "thumb") or f.get("thumb")
        if path and Path(path).exists():
            pic = QLabel()
            pm = QPixmap(path)
            if not pm.isNull():
                pic.setPixmap(pm.scaledToWidth(150, Qt.SmoothTransformation))
                lay.addWidget(pic)
        col = QVBoxLayout()
        col.setSpacing(3)
        head = f"{ICON.get(f['kind'], '•')}  {leakcheck.KINDS.get(f['kind'], f['kind'])}"
        col.addWidget(label(f"<b>{head}</b>  ·  at {fmt_time(f['start'], False)} in the movie", None))
        what = (f.get("text") or "").strip()
        bits = [x for x in (what and f"“{what}”", f.get("detail"),
                            f.get("score") is not None and f["kind"] == "picture" and f"{int(f['score'] * 100)}% sure")
                if x]
        col.addWidget(label("  ·  ".join(bits), "Muted", wrap=True))
        lay.addLayout(col, 1)
        btns = QVBoxLayout()
        btns.setSpacing(4)
        if f["kind"] == "picture":
            btns.addWidget(button("🖌  Paint a blur here", "Primary", lambda: dlg.paint_at(f)))
        if f["kind"] == "heard":
            btns.addWidget(button("🔇  Mute this word", "Primary", lambda: dlg.mute(f)))
        btns.addWidget(button("Not a problem", None, lambda: dlg.dismiss(f),
                              tip="Don't list this again for this movie"))
        lay.addLayout(btns)


class LeakDialog(QDialog):
    paintRequested = Signal(float)      # open the paint window at this movie time
    changed = Signal()                  # the project was changed (a word was muted)
    backToReview = Signal()

    def __init__(self, project, settings, parent=None):
        super().__init__(parent)
        self.project = project
        self.blurred = settings.blur_thumbnails
        self.setWindowTitle("What slipped through")
        self.resize(820, 600)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        self.head = label("", "H2", wrap=True)
        lay.addWidget(self.head)
        self.note = label("", "Muted", wrap=True)
        lay.addWidget(self.note)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.NoFrame)
        self.inner = QWidget()
        self.rows = QVBoxLayout(self.inner)
        self.rows.setContentsMargins(0, 0, 8, 0)
        self.rows.setSpacing(8)
        self.area.setWidget(self.inner)
        lay.addWidget(self.area, 1)
        self.help = label("After you fix things, go back to the review and save the clean copy again.", "Muted",
                          wrap=True)
        lay.addWidget(self.help)
        foot = QHBoxLayout()
        foot.addStretch()
        foot.addWidget(button("Close", None, self.close))
        foot.addWidget(button("Back to review", "Primary", self._back))
        lay.addLayout(foot)
        self.fill()

    @property
    def leaks(self):
        return self.project.leaks or {}

    def fill(self):
        while self.rows.count():
            it = self.rows.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        lk = self.leaks
        self.head.setText(lk.get("summary") or "No check has been run yet.")
        notes = []
        if lk.get("skipped"):
            notes.append("Not checked: " + "; ".join(lk["skipped"]) + ".")
        if lk.get("approximate"):
            notes.append("This copy was made before the check existed, so it was compared with your current choices.")
        notes.append("The check is a safety net, not a guarantee: it can miss things and it can flag things that are fine.")
        self.note.setText("  ".join(notes))
        for f in lk.get("findings", []):
            self.rows.addWidget(LeakRow(f, self.blurred, self))
        self.rows.addStretch()

    # --- actions
    def paint_at(self, f):
        span = max(0.0, float(f["end"]) - float(f["start"]))
        self.paintRequested.emit(float(f["start"]) + min(1.0, span / 2))

    def mute(self, f):
        if leakcheck.mute_it(self.project, f["id"]):
            self.changed.emit()
        self.fill()

    def dismiss(self, f):
        leakcheck.dismiss(self.project, f["id"])
        self.fill()

    def _back(self):
        self.backToReview.emit()
        self.close()
