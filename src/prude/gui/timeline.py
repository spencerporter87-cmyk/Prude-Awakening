"""VidAngel-style timeline: where words are silenced and scenes removed, with a playhead."""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolTip, QWidget

from .style import INK, LINE, MUTED, TEAL
from .widgets import fmt_time

AMBER = QColor("#E39B2D")
CUT = QColor("#C8412B")
BLACK = QColor("#2A1B2E")
BLUR = QColor("#7B61B0")
MUTE = QColor("#B5762A")
ACTION_COLOR = {"cut": CUT, "black": BLACK, "black_keep": BLACK, "blur": BLUR, "blur_parts": BLUR, "mute": MUTE}
ACTION_WORD = {"cut": "✂ Cut out", "black": "⬛ Black screen + silence", "black_keep": "⬛ Black screen, sound kept",
               "blur": "◌ Whole picture blurred, sound kept",
               "blur_parts": "◌ Private parts blurred, sound kept", "mute": "🔇 Sound silenced"}
OFF = QColor("#C9BBAA")
TRACK = QColor("#F1E9DD")

GUTTER = 74
PAD_R = 14


def _nice_step(duration, width):
    target = max(4, min(12, int(width / 90)))
    for step in (5, 10, 15, 30, 60, 120, 300, 600, 900, 1200, 1800, 3600, 7200):
        if duration / step <= target:
            return step
    return 7200


class Timeline(QWidget):
    seek = Signal(float)
    itemClicked = Signal(str, int)    # ("mute" | "scene", index)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(92)
        self.setMaximumHeight(110)
        self.duration = 1.0
        self.mutes, self.scenes, self.cut_points = [], [], []
        self.action = "cut"
        self.mode = "plan"
        self.pos = None
        self.fill = None
        self._hits = []

    # ------------------------------------------------------------ data
    def set_plan(self, duration, mutes, scenes, action="cut"):
        """Original movie: every planned edit (ticked ones solid, unticked ones faint)."""
        self.mode, self.duration, self.action = "plan", max(1.0, float(duration)), action
        self.mutes, self.scenes, self.cut_points = list(mutes), list(scenes), []
        self.update()

    def set_result(self, duration, mutes, blackouts, cut_points):
        """Clean copy: silenced words, blacked-out scenes, and markers where scenes were cut."""
        self.mode, self.duration, self.action = "result", max(1.0, float(duration)), "result"
        self.mutes = [dict(m, on=True) for m in mutes]
        self.scenes = [dict(s, on=True, action=s.get("action", "black")) for s in blackouts]
        self.cut_points = list(cut_points)
        self.update()

    def set_fill(self, t):
        """Shade the part that's finished (used while saving)."""
        self.fill = None if t is None else max(0.0, float(t))
        self.update()

    def set_position(self, t):
        self.pos = None if t is None else max(0.0, float(t))
        self.update()

    # ------------------------------------------------------------ geometry
    def _x(self, t):
        w = self.width() - GUTTER - PAD_R
        return GUTTER + w * min(1.0, max(0.0, t / self.duration))

    def _t(self, x):
        w = self.width() - GUTTER - PAD_R
        return min(self.duration, max(0.0, (x - GUTTER) / max(1, w) * self.duration))

    # ------------------------------------------------------------ paint
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        small = QFont(self.font())
        small.setPointSizeF(max(7.5, self.font().pointSizeF() - 1.5))
        p.setFont(small)
        lanes = [("Scenes", 10), ("Words", 38)]
        lane_h = 20
        x0, x1 = GUTTER, self.width() - PAD_R
        self._hits = []
        for name, y in lanes:
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(0, y, GUTTER - 10, lane_h), Qt.AlignRight | Qt.AlignVCenter, name)
            p.setPen(Qt.NoPen)
            p.setBrush(TRACK)
            p.drawRoundedRect(QRectF(x0, y, x1 - x0, lane_h), 6, 6)

        if self.fill is not None:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(46, 125, 140, 70))
            p.drawRoundedRect(QRectF(x0, lanes[0][1] - 3, max(0.0, self._x(self.fill) - x0),
                                     lanes[1][1] + lane_h - lanes[0][1] + 6), 6, 6)

        # scenes lane
        y = lanes[0][1]
        for i, s in enumerate(self.scenes):
            a = self._x(s["start"] - float(s.get("pad_before") or 0))
            b = self._x(s["end"] + float(s.get("pad_after") or 0))
            r = QRectF(a, y + 1, max(3.0, b - a), lane_h - 2)
            on = s.get("on", True)
            if on:
                p.setPen(Qt.NoPen)
                p.setBrush(ACTION_COLOR.get(s.get("action") or self.action, CUT))
            else:
                p.setPen(QPen(OFF, 1.2, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, 4, 4)
            self._hits.append((r.adjusted(-3, -2, 3, 2), "scene", i, self._scene_tip(s)))
        for t, removed, label in self.cut_points:
            x = self._x(t)
            p.setPen(Qt.NoPen)
            p.setBrush(CUT)
            tri = QPolygonF([QPointF(x - 6, y + 1), QPointF(x + 6, y + 1), QPointF(x, y + lane_h - 3)])
            p.drawPolygon(tri)
            p.drawRect(QRectF(x - 1, y + 1, 2, lane_h - 2))
            tip = f"✂ Cut here: {label}\n{fmt_time(removed, False)} removed"
            self._hits.append((QRectF(x - 7, y, 14, lane_h), "cut", -1, tip))

        # words lane
        y = lanes[1][1]
        for i, m in enumerate(self.mutes):
            a, b = self._x(m["start"]), self._x(m["end"])
            r = QRectF(a, y + 1, max(3.0, b - a), lane_h - 2)
            on = m.get("on", True)
            p.setPen(Qt.NoPen if on else QPen(OFF, 1))
            p.setBrush(AMBER if on else Qt.NoBrush)
            p.drawRoundedRect(r, 2, 2)
            tip = f"{fmt_time(m['start'])}  🔇 {m.get('words', '')}" + ("" if on else "  (kept)")
            self._hits.append((r.adjusted(-3, -2, 3, 2), "mute", i, tip))

        # time axis
        ya = lanes[1][1] + lane_h + 6
        step = _nice_step(self.duration, x1 - x0)
        p.setPen(QPen(QColor(LINE), 1))
        p.drawLine(QPointF(x0, ya), QPointF(x1, ya))
        t = 0.0
        while t <= self.duration + 0.01:
            x = self._x(t)
            p.setPen(QPen(QColor(LINE), 1))
            p.drawLine(QPointF(x, ya), QPointF(x, ya + 4))
            p.setPen(QColor(MUTED))
            lab = fmt_time(t, False)
            if self.duration < 3600:
                lab = lab[2:] if lab.startswith("0:") else lab
            p.drawText(QRectF(x - 40, ya + 4, 80, 16), Qt.AlignHCenter | Qt.AlignTop, lab)
            t += step

        # playhead
        if self.pos is not None:
            x = self._x(self.pos)
            p.setPen(QPen(QColor(TEAL), 2))
            p.drawLine(QPointF(x, 4), QPointF(x, ya))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(TEAL))
            p.drawEllipse(QPointF(x, 4), 5, 5)
        p.end()

    def _scene_tip(self, s):
        verb = ACTION_WORD.get(s.get("action") or self.action, "")
        state = verb if s.get("on", True) else "Kept (unticked)"
        if s.get("detail"):
            state = f"{s['detail']} · {state}"
        return f"{fmt_time(s['start'])} – {fmt_time(s['end'])}\n{s.get('label', 'Scene')} · {state}"

    # ------------------------------------------------------------ mouse
    def _hit(self, pos):
        for r, kind, i, tip in reversed(self._hits):
            if r.contains(pos):
                return kind, i, tip
        return None

    def mouseMoveEvent(self, e):
        h = self._hit(e.position())
        t = self._t(e.position().x())
        if e.buttons() & Qt.LeftButton and e.position().x() >= GUTTER:
            self.seek.emit(t)   # drag to scrub
            return
        if h:
            QToolTip.showText(e.globalPosition().toPoint(), h[2], self)
        elif GUTTER <= e.position().x() <= self.width() - PAD_R:
            QToolTip.showText(e.globalPosition().toPoint(), fmt_time(t), self)
        self.setCursor(Qt.PointingHandCursor if e.position().x() >= GUTTER else Qt.ArrowCursor)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or e.position().x() < GUTTER - 4:
            return
        h = self._hit(e.position())
        if h and h[0] in ("mute", "scene"):
            self.itemClicked.emit(h[0], h[1])
            item = (self.mutes if h[0] == "mute" else self.scenes)[h[1]]
            self.seek.emit(max(0.0, float(item["start"]) - 2.0))
        else:
            self.seek.emit(self._t(e.position().x()))


def legend(mode="plan", action="cut"):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(GUTTER, 0, 0, 0)
    lay.setSpacing(16)

    def sw(color, text, outline=False):
        box = QLabel()
        box.setFixedSize(14, 10)
        box.setStyleSheet(f"border-radius: 2px; background: {'transparent' if outline else color};"
                          f"border: 1px {'dashed' if outline else 'solid'} {color};")
        lab = QLabel(text)
        lab.setStyleSheet(f"color: {MUTED}; font-size: 9pt;")
        lay.addWidget(box)
        lay.addWidget(lab)

    sw(AMBER.name(), "Silenced word")
    if mode == "plan":
        sw(CUT.name(), "Cut")
        sw(BLACK.name(), "Black screen")
        sw(BLUR.name(), "Blurred")
        sw(MUTE.name(), "Scene silenced")
        sw(OFF.name(), "Kept (unticked)", outline=True)
    else:
        sw(CUT.name(), "Where a scene was cut")
        sw(BLACK.name(), "Black screen")
        sw(BLUR.name(), "Blurred")
        sw(MUTE.name(), "Scene silenced")
    sw(TEAL, "Saved so far" if mode == "progress" else "Now playing")
    lay.addStretch()
    return w
