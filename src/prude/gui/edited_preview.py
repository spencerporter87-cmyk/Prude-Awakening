"""'See it edited': a short clip made with the real filters, next to the untouched original."""

import threading

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QDialog, QHBoxLayout, QMenu, QPushButton, QVBoxLayout

from ..preview import clip_to_movie
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
        self.shown = "edited"
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
        # fix what the filters got wrong right here: pause on the frame, then paint a blur on, or take one off
        fix = QHBoxLayout()
        self.project = project
        self.b_paint = button("🖌  Paint a blur here", None, lambda: self._edit_here("blur"),
                              tip="Pause on the frame first. Opens the paint window at this moment so you can blur "
                                  "something the filters missed.")
        self.b_unblur = button("🧽  Remove blur here", None, lambda: self._edit_here("clear"),
                               tip="Pause on the frame first. Opens the paint window so you can take the "
                                   "automatic blur off something that shouldn't be blurred.")
        self.b_change = button("✎  Change a painted area…", None, self._change_menu,
                               tip="Re-open a blur you painted, or an area where you removed the blur")
        for b in (self.b_paint, self.b_unblur, self.b_change):
            b.setEnabled(False)
            fix.addWidget(b)
        fix.addStretch()
        lay.addLayout(fix)
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
        for b in (self.b_paint, self.b_unblur, self.b_change):
            b.setEnabled(True)
        self.b_edit.setChecked(True)
        self._show("edited")

    def _show(self, which):
        if not (self.res and HAVE_MM):
            return
        self.b_edit.setText(("●" if which == "edited" else "○") + "  Edited: what the clean copy will contain")
        self.b_orig.setText(("●" if which == "original" else "○") + "  Original")
        self.shown = which
        self.player.load(self.res[which], proxy=False)
        self.player.player.play()

    def _here(self):
        """The movie time of the frame showing now (the clip starts a few seconds before the edit). The edited clip
        has the cut scenes taken out, so its clock runs behind the movie's by the length of every cut before it."""
        pos = float(self.player.position())
        if self.shown == "edited":
            pos = clip_to_movie(pos, self.res.get("cuts") or [])
        return float(self.res["start"]) + pos

    def _edit_here(self, mode, patch=None):
        page = self.parent()
        if not (self.res and hasattr(page, "_paint")):
            return
        if HAVE_MM:
            self.player.player.pause()
        at = float(patch["start"]) if patch else self._here()
        if page._paint(patch, at=at, mode=mode):       # saved: the page opens a fresh preview with the change in it
            self.close()

    def _change_menu(self):
        from .. import paint
        a, b = float(self.res["start"]), float(self.res["end"])
        found = [x for x in (self.project.paint or []) if x.get("strokes")
                 and paint.span(x)[1] > a and paint.span(x)[0] < b]
        menu = QMenu(self)
        if not found:
            menu.addAction("Nothing painted in this stretch yet").setEnabled(False)
        for x in found:
            lo, hi = paint.span(x)
            icon = "🧽" if paint.is_clear(x) else "🖌"
            act = menu.addAction(f"{icon}  {x.get('label') or 'Painted blur'}  ·  {fmt_time(lo)} – {fmt_time(hi)}")
            act.triggered.connect(lambda _=False, x=x: self._edit_here("clear" if paint.is_clear(x) else "blur", x))
        menu.exec(self.b_change.mapToGlobal(QPoint(0, self.b_change.height())))

    def done(self, r):
        self._cancel.set()
        self.player.stop()
        super().done(r)
