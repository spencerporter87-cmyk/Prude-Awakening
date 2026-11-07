"""🖌 Paint a blur: step through the movie frame by frame, paint over what should be blurred, move the
painted shape along as it moves, and say exactly which frames it covers.

It works on still frames (so it's exact and never skips), shows the blur the way the real render makes it,
and saves into the project as a "painted blur" that is rendered with everything else."""

import copy
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLineEdit, QPushButton,
                               QSlider, QVBoxLayout, QWidget)

from .. import paint
from .widgets import button, fmt_time, label

WINDOW = 6.0            # seconds of frames loaded at a time


class FrameBar(QWidget):
    """Where you are among the loaded frames, which frames are blurred, and where your key positions are."""
    seek = Signal(int)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(34)
        self.setCursor(Qt.PointingHandCursor)
        self.n, self.i, self.a, self.b, self.keys = 1, 0, 0, 0, []

    def set_state(self, n, i, a, b, keys):
        self.n, self.i, self.a, self.b, self.keys = max(1, n), i, a, b, keys
        self.update()

    def _x(self, i):
        return 6 + (self.width() - 12) * (i + 0.5) / self.n

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(6, 12, self.width() - 12, 10)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#d9dde3"))
        p.drawRoundedRect(r, 4, 4)
        if self.b > self.a:                               # the frames that get blurred
            x0, x1 = 6 + (self.width() - 12) * self.a / self.n, 6 + (self.width() - 12) * self.b / self.n
            p.setBrush(QColor("#8b5cf6"))
            p.drawRoundedRect(QRectF(max(6, x0), 12, max(2, min(x1, self.width() - 6) - max(6, x0)), 10), 4, 4)
        p.setBrush(QColor("#f59e0b"))                    # key positions
        for k in self.keys:
            if 0 <= k < self.n:
                p.drawEllipse(QPointF(self._x(k), 17), 4.5, 4.5)
        p.setPen(QPen(QColor("#111827"), 2))
        p.drawLine(int(self._x(self.i)), 4, int(self._x(self.i)), 30)
        p.end()

    def mousePressEvent(self, e):
        self._go(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._go(e)

    def _go(self, e):
        f = (e.position().x() - 6) / max(1, self.width() - 12)
        self.seek.emit(max(0, min(self.n - 1, int(f * self.n))))


class Canvas(QWidget):
    """The frame, the painted shape's outline and the brush."""
    stroke = Signal(list)
    moved = Signal(float, float)
    erased = Signal(float, float)
    scaled = Signal(float, float, float)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(480, 270)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.image = None
        self.aspect = 16 / 9
        self.tool, self.radius = "paint", 0.03
        self.outline = []
        self.live = []
        self.cursor = None
        self.last = None
        self.note = ""

    def set_image(self, qimg, outline, note=""):
        self.image, self.outline, self.note = qimg, outline, note
        if qimg is not None and qimg.height():
            self.aspect = qimg.width() / qimg.height()
        self.update()

    def _rect(self):
        w, h = self.width(), self.height()
        rw = min(w, h * self.aspect)
        rh = rw / self.aspect
        return QRectF((w - rw) / 2, (h - rh) / 2, rw, rh)

    def _frac(self, pos):
        r = self._rect()
        return ((pos.x() - r.x()) / r.width(), (pos.y() - r.y()) / r.height())

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#0b0f19"))
        r = self._rect()
        if self.image is not None:
            p.drawImage(r, self.image)
        pen = QPen(QColor(255, 255, 255, 230), 1.6)
        pen.setStyle(Qt.DashLine)
        p.setBrush(Qt.NoBrush)
        for rad, pts in self.outline:                     # where the painted shape is on this frame
            p.setPen(pen)
            for x, y in pts:
                c = QPointF(r.x() + x * r.width(), r.y() + y * r.height())
                p.drawEllipse(c, rad * r.width(), rad * r.width())
        if self.live:
            p.setPen(QPen(QColor(139, 92, 246, 200), max(2.0, 2 * self.radius * r.width()),
                          Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            pts = [QPointF(r.x() + x * r.width(), r.y() + y * r.height()) for x, y in self.live]
            if len(pts) == 1:
                p.drawPoint(pts[0])
            for a, b in zip(pts, pts[1:]):
                p.drawLine(a, b)
        if self.cursor is not None and self.tool in ("paint", "erase"):
            p.setPen(QPen(QColor("#f59e0b"), 1.5))
            p.drawEllipse(self.cursor, self.radius * r.width(), self.radius * r.width())
        if self.note:
            p.setPen(QColor("#f9fafb"))
            p.fillRect(QRectF(r.x() + 8, r.y() + 8, 330, 24), QColor(0, 0, 0, 150))
            p.drawText(QRectF(r.x() + 14, r.y() + 8, 320, 24), Qt.AlignVCenter | Qt.AlignLeft, self.note)
        p.end()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or self.image is None:
            return
        self.last = self._frac(e.position())
        if self.tool == "paint":
            self.live = [self.last]
        elif self.tool == "erase":
            self.erased.emit(*self.last)
        self.update()

    def mouseMoveEvent(self, e):
        self.cursor = e.position()
        if e.buttons() & Qt.LeftButton and self.last is not None:
            cur = self._frac(e.position())
            if self.tool == "paint":
                lx, ly = self.live[-1]
                if abs(cur[0] - lx) + abs(cur[1] - ly) > 0.004:
                    self.live.append(cur)
            elif self.tool == "move":
                self.moved.emit(cur[0] - self.last[0], cur[1] - self.last[1])
            elif self.tool == "erase":
                self.erased.emit(*cur)
            self.last = cur
        self.update()

    def mouseReleaseEvent(self, e):
        if self.tool == "paint" and self.live:
            pts, self.live = self.live, []
            self.stroke.emit(pts)
        self.last = None
        self.update()

    def leaveEvent(self, _e):
        self.cursor = None
        self.update()

    def wheelEvent(self, e):
        if self.image is None:
            return
        steps = e.angleDelta().y() / 120.0
        if steps:
            fx, fy = self._frac(e.position())
            if self.tool in ("move", "size"):
                self.scaled.emit(1.06 ** steps, fx, fy)
            else:
                self.radius = min(0.2, max(0.004, self.radius * 1.12 ** steps))
                self.update()


class PaintEditor(QDialog):
    _loaded = Signal(object, str)
    _progress = Signal(float)

    def __init__(self, project, info, at, patch=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Paint a blur")
        self.resize(1040, 760)
        self.project, self.info = project, info
        self.fps = float(info.fps or 24.0)
        self.new = patch is None
        self.patch = copy.deepcopy(patch) if patch else paint.new_patch(at, at + 1.0)
        if self.new:
            self.patch["start"] = self.patch["end"] = paint.aligned(at, self.fps)
        self.result = None
        self.frames, self.t0, self.i = [], 0.0, 0
        self.cache, self.undo_stack = {}, []
        self._cancel = threading.Event()
        self._dir = project.dir / "paint-frames"
        self._playing = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.addWidget(label("Paint a blur", "H2"))
        self.help = label(
            "1. Pick Paint and scribble over what should be blurred.   2. Step to a later frame (← →). When it has "
            "moved, pick Move and drag the shape onto it, or scroll to resize it: you only need a few key frames, "
            "the blur glides between them.   3. Set where it starts and ends, then Save.", "Muted", wrap=True)
        lay.addWidget(self.help)
        self.canvas = Canvas()
        lay.addWidget(self.canvas, 1)
        self.bar = FrameBar()
        self.bar.seek.connect(self._goto)
        lay.addWidget(self.bar)

        nav = QHBoxLayout()
        for text, step, tip in (("⏮", -10, "Back 10 frames"), ("◀", -1, "Back one frame (←)"),
                                ("▶", 1, "Forward one frame (→)"), ("⏭", 10, "Forward 10 frames")):
            nav.addWidget(button(text, None, lambda _=False, s=step: self._goto(self.i + s), tip=tip))
        self.play_btn = button("▶ Play", None, self._toggle_play, tip="Plays these frames at the speed on the right")
        nav.addWidget(self.play_btn)
        self.speed = QComboBox()
        for name, v in (("¼ speed", 0.25), ("½ speed", 0.5), ("Full speed", 1.0)):
            self.speed.addItem(name, v)
        self.speed.setCurrentIndex(1)
        nav.addWidget(self.speed)
        self.time_lab = label("", "Small")
        nav.addWidget(self.time_lab, 1)
        nav.addWidget(button("⇤ 6 s earlier", None, lambda: self._load(self.t0 - WINDOW + 1.0)))
        nav.addWidget(button("6 s later ⇥", None, lambda: self._load(self.t0 + WINDOW - 1.0)))
        lay.addLayout(nav)

        tools = QHBoxLayout()
        grp = QButtonGroup(self)
        self.tool_btns = {}
        for key, text, tip in (("paint", "🖌 Paint", "Scribble over what should be blurred. Scroll to change the brush size."),
                               ("erase", "◌ Erase", "Rub out parts of the painted shape"),
                               ("move", "✥ Move", "Drag the painted shape to where it is on this frame. Scroll to resize it.")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            grp.addButton(b)
            b.clicked.connect(lambda _=False, k=key: self._tool(k))
            self.tool_btns[key] = b
            tools.addWidget(b)
        self.tool_btns["paint"].setChecked(True)
        tools.addSpacing(10)
        tools.addWidget(label("Brush", "Small"))
        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(4, 150)
        self.size.setValue(30)
        self.size.setFixedWidth(130)
        self.size.valueChanged.connect(lambda v: self._set_radius(v / 1000.0))
        tools.addWidget(self.size)
        tools.addSpacing(10)
        tools.addWidget(button("↩ Undo", None, self._undo, tip="Ctrl+Z"))
        tools.addWidget(button("📍 Remove this key frame", None, self._remove_key,
                               tip="Forget the position you set on this frame; the blur glides past it"))
        tools.addStretch()
        self.show_blur = QCheckBox("Show the blur")
        self.show_blur.setChecked(True)
        self.show_blur.toggled.connect(lambda _: self._show())
        tools.addWidget(self.show_blur)
        lay.addLayout(tools)

        span = QHBoxLayout()
        span.addWidget(button("⇥ Starts on this frame", None, self._start_here,
                              tip="The blur begins on the frame you're looking at"))
        span.addWidget(button("⇤ Ends on this frame", None, self._end_here,
                              tip="The blur stops after the frame you're looking at"))
        self.span_lab = label("", "Small")
        span.addWidget(self.span_lab, 1)
        span.addWidget(label("Name", "Small"))
        self.name = QLineEdit(self.patch.get("label") or "Painted blur")
        self.name.setFixedWidth(170)
        span.addWidget(self.name)
        lay.addLayout(span)

        end = QHBoxLayout()
        self.status = label("Reading the frames…", "Muted")
        end.addWidget(self.status, 1)
        end.addWidget(button("Cancel", None, self.reject))
        self.save_btn = button("Save blur", "Primary", self.accept)
        end.addWidget(self.save_btn)
        lay.addLayout(end)

        self.canvas.stroke.connect(self._stroke)
        self.canvas.moved.connect(self._move)
        self.canvas.erased.connect(self._erase)
        self.canvas.scaled.connect(self._scale)
        for seq, fn in (("Left", lambda: self._goto(self.i - 1)), ("Right", lambda: self._goto(self.i + 1)),
                        ("Shift+Left", lambda: self._goto(self.i - 10)), ("Shift+Right", lambda: self._goto(self.i + 10)),
                        ("Ctrl+Z", self._undo), ("Space", self._toggle_play)):
            QShortcut(QKeySequence(seq), self, activated=fn)
        self._loaded.connect(self._ready)
        self._load(max(0.0, at - 1.5))

    # ------------------------------------------------------------------ frames
    def _load(self, t0):
        self._stop_play()
        t0 = max(0.0, min(float(t0), max(0.0, float(self.info.duration) - 1.0)))
        t0 = round(t0 * self.fps) / self.fps
        keep = self.t0 + self.i / self.fps if self.frames else None
        self.status.setText("Reading the frames…")
        self.save_btn.setEnabled(False)

        def work():
            try:
                files = paint.extract_frames(self.info, t0, min(float(self.info.duration), t0 + WINDOW), self._dir,
                                             cancel=self._cancel)
                self._loaded.emit((t0, [str(f) for f in files], keep), "")
            except Exception as e:                                   # noqa: BLE001 - shown to the person
                self._loaded.emit(None, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _ready(self, res, err):
        if not res:
            self.status.setText("Couldn't read the frames: " + err[-250:])
            return
        self.t0, self.frames, keep = res
        self.cache.clear()
        self.save_btn.setEnabled(True)
        self.status.setText(f"{len(self.frames)} frames loaded. Use ← → to step.")
        want = keep if keep is not None else self.patch["start"]
        self.i = max(0, min(len(self.frames) - 1, int(round((want - self.t0) * self.fps))))
        self._show()

    def _t(self):
        return self.t0 + self.i / self.fps

    def _goto(self, i):
        if not self.frames:
            return
        self.i = max(0, min(len(self.frames) - 1, int(i)))
        self._show()

    def _show(self):
        if not self.frames:
            return
        t = self._t()
        a, b = paint.span(self.patch)
        inside = a - 1e-6 <= t < b - 1e-6 and bool(self.patch["strokes"])
        try:
            arr = paint.preview_frame(self.frames[self.i], self.patch, t, self.cache,
                                      show_blur=self.show_blur.isChecked())
        except Exception as e:                                       # noqa: BLE001
            self.status.setText(str(e))
            return
        h, w = arr.shape[:2]
        qimg = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        note = ""
        if self.patch["strokes"]:
            note = "● blurred on this frame" if inside else "○ not blurred on this frame"
        self.canvas.set_image(qimg, paint.shape_at(self.patch, t) if self.patch["strokes"] else [], note)
        keys = [int(round((k - self.t0) * self.fps)) for k in paint.key_times(self.patch)]
        n = len(self.frames)
        fa, fb = (a - self.t0) * self.fps, (b - self.t0) * self.fps
        self.bar.set_state(n, self.i, max(0, fa), min(n, fb), keys)
        self.time_lab.setText(f"{fmt_time(t)}  ·  frame {int(round(t * self.fps))}")
        self.span_lab.setText(f"Blurred from {fmt_time(a)} to {fmt_time(b)} ({(b - a):.2f} s)" if self.patch["strokes"]
                              else "Paint something to begin")

    # ------------------------------------------------------------------ editing
    def _push(self):
        self.undo_stack.append(copy.deepcopy(self.patch))
        del self.undo_stack[:-60]

    def _undo(self):
        if self.undo_stack:
            self.patch = self.undo_stack.pop()
            self._show()

    def _tool(self, k):
        self.canvas.tool = k
        self.canvas.update()

    def _set_radius(self, r):
        self.canvas.radius = r
        self.canvas.update()

    def _stroke(self, pts):
        if not self.frames:
            return
        self._push()
        paint.include_frame(self.patch, self._t(), self.fps)
        paint.add_stroke(self.patch, self._t(), pts, self.canvas.radius)
        paint.include_frame(self.patch, self._t(), self.fps)
        self._show()

    def _begin_drag_edit(self):
        """One undo step per mouse drag, not one per mouse movement."""
        if not getattr(self, "_edit_open", False):
            self._push()
            self._edit_open = True
            QTimer.singleShot(400, lambda: setattr(self, "_edit_open", False))

    def _move(self, dx, dy):
        if not (self.frames and self.patch["strokes"]):
            return
        self._begin_drag_edit()
        paint.include_frame(self.patch, self._t(), self.fps)
        paint.move_at(self.patch, self._t(), dx, dy)
        self._show()

    def _scale(self, f, x, y):
        if not (self.frames and self.patch["strokes"]):
            return
        self._begin_drag_edit()
        paint.include_frame(self.patch, self._t(), self.fps)
        paint.scale_at(self.patch, self._t(), f, (x, y))
        self._show()

    def _erase(self, x, y):
        if not (self.frames and self.patch["strokes"]):
            return
        self._begin_drag_edit()
        paint.erase_near(self.patch, self._t(), x, y, self.canvas.radius, self.canvas.aspect)
        self._show()

    def _remove_key(self):
        if self.frames:
            self._push()
            paint.remove_key(self.patch, self._t())
            self._show()

    def _start_here(self):
        if self.frames and self.patch["strokes"]:
            self._push()
            self.patch["start"] = self._t()
            if self.patch["end"] <= self.patch["start"]:
                self.patch["end"] = self.patch["start"] + 1.0 / self.fps
            self._show()

    def _end_here(self):
        if self.frames and self.patch["strokes"]:
            self._push()
            self.patch["end"] = self._t() + 1.0 / self.fps
            if self.patch["start"] >= self.patch["end"]:
                self.patch["start"] = self._t()
            self._show()

    # ------------------------------------------------------------------ playing
    def _toggle_play(self):
        if self._playing:
            self._stop_play()
        elif self.frames:
            self._playing = True
            self.play_btn.setText("⏸ Pause")
            self._timer.start(max(15, int(1000.0 / (self.fps * float(self.speed.currentData() or 0.5)))))

    def _stop_play(self):
        self._playing = False
        self._timer.stop()
        self.play_btn.setText("▶ Play")

    def _tick(self):
        if self.i >= len(self.frames) - 1:
            self._stop_play()
            return
        self._goto(self.i + 1)

    # ------------------------------------------------------------------ finishing
    def accept(self):
        if not self.patch["strokes"]:
            self.status.setText("Nothing painted yet. Paint over what should be blurred, or press Cancel.")
            return
        self.patch["label"] = self.name.text().strip() or "Painted blur"
        self.patch["on"] = True
        self.result = copy.deepcopy(self.patch)
        super().accept()

    def done(self, r):
        self._cancel.set()
        self._stop_play()
        shutil.rmtree(self._dir, ignore_errors=True)
        super().done(r)
