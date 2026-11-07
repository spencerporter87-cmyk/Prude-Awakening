"""'See it edited': a short clip made with the real filters, next to the untouched original."""

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QDialog, QHBoxLayout, QPushButton, QVBoxLayout

from .player import HAVE_MM, Player
from .widgets import button, fmt_time, label


class EditedPreview(QDialog):
    _built = Signal(object, str)
    _progress = Signal(float, str)

    def __init__(self, project, filters, settings, start, end, what="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("See it edited")
        self.resize(900, 640)
        self.res = None
        self._cancel = threading.Event()
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(label(f"Around {fmt_time(start, False)}" + (f" · {what}" if what else ""), "H2"))
        self.note = label("Making a short copy with your filters applied… (a few seconds)", "Muted", wrap=True)
        lay.addWidget(self.note)
        self.player = Player(live_toggle=False, timeline_inside=False)
        lay.addWidget(self.player, 1)
        row = QHBoxLayout()
        self.b_edit = QPushButton("●  Edited: what the clean copy will contain")
        self.b_orig = QPushButton("○  Original")
        for b in (self.b_edit, self.b_orig):
            b.setCheckable(True)
            b.setEnabled(False)
            row.addWidget(b)
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        grp.addButton(self.b_edit)
        grp.addButton(self.b_orig)
        self.b_edit.clicked.connect(lambda: self._show("edited"))
        self.b_orig.clicked.connect(lambda: self._show("original"))
        row.addStretch()
        row.addWidget(button("Close", None, self.close))
        lay.addLayout(row)
        self._built.connect(self._ready)
        self._progress.connect(lambda f, m: self.note.setText(f"{m} ({int(f * 100)}%)"))

        def work():
            from ..preview import edited_clip
            try:
                self._built.emit(edited_clip(project, filters, settings, start, end,
                                             progress=lambda f, m: self._progress.emit(f, m),
                                             cancel=self._cancel), "")
            except Exception as e:                               # noqa: BLE001 - shown to the person
                self._built.emit(None, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _ready(self, res, err):
        if not res:
            self.note.setText("Couldn't make the preview: " + (err or "unknown problem")[-300:])
            return
        self.res = res
        self.note.setText(f"{res['summary']} in this stretch. The first clip shows the clean copy "
                          "(blurs, black screens and silences are real). Switch to the original to compare. "
                          "Preview quality is lower than the finished copy."
                          + ("" if res.get("valid", True) else "  ⚠ " + "; ".join(res.get("problems") or [])))
        self.b_edit.setEnabled(True)
        self.b_orig.setEnabled(True)
        self.b_edit.setChecked(True)
        self._show("edited")

    def _show(self, which):
        if not (self.res and HAVE_MM):
            return
        self.b_edit.setText(("●" if which == "edited" else "○") + "  Edited: what the clean copy will contain")
        self.b_orig.setText(("●" if which == "original" else "○") + "  Original")
        self.player.load(self.res[which], proxy=False)
        self.player.player.play()

    def done(self, r):
        self._cancel.set()
        self.player.stop()
        super().done(r)
