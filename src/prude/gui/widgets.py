"""Small reusable widgets."""

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QHelpEvent, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QProgressBar, QPushButton,
                               QRadioButton, QSizePolicy, QToolTip, QVBoxLayout, QWidget)

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


def beta_badge(text="BETA"):
    """Small orange pill: this feature isn't fully tested or released yet."""
    b = QLabel(text)
    b.setStyleSheet("background:#E8872B; color:#FFFFFF; font-weight:700; font-size:10px; "
                    "border-radius:8px; padding:2px 8px;")
    b.setAlignment(Qt.AlignCenter)
    b.setFixedHeight(18)
    return b


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


class EnterButton(QPushButton):
    """A button that also activates on Enter/Return (Space already works)."""

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers():
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)


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


class ElideMiddleLabel(QLabel):
    """Label that elides the middle of long text; tooltip/accessibility keep the full text."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = ""
        self.set_full_text(text)

    def set_full_text(self, text):
        self._full = text or ""
        self.setToolTip(self._full)
        self.setAccessibleName(self._full)
        self.setAccessibleDescription(self._full)
        self.setWhatsThis(self._full)
        self._update_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_text()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if self._full:
            QTimer.singleShot(0, self._show_tooltip)

    def focusOutEvent(self, event):
        QToolTip.hideText()
        super().focusOutEvent(event)

    def _show_tooltip(self):
        if self._full and self.hasFocus():
            pos = self.rect().center()
            QCoreApplication.postEvent(self, QHelpEvent(QEvent.Type.ToolTip, pos, self.mapToGlobal(pos)))

    def _update_text(self):
        if not self._full:
            self.setText("")
            return
        w = self.width()
        if w <= 0:
            self.setText(self._full)
            return
        self.setText(QFontMetrics(self.font()).elidedText(self._full, Qt.ElideMiddle, w))


class StepIndicator(QWidget):
    """The four journey steps: a numbered marker plus the step name."""

    STEPS = ("Choose", "Scan", "Review", "Save")

    def __init__(self, current=1, parent=None):
        super().__init__(parent)
        self.setObjectName("StepIndicator")
        self.current = 1
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addStretch(1)
        self.dots = []
        self.labels = []
        for i, name in enumerate(self.STEPS, 1):
            item = QWidget(self)
            il = QHBoxLayout(item)
            il.setContentsMargins(0, 0, 0, 0)
            il.setSpacing(6)
            dot = QLabel()
            dot.setObjectName("StepDotTodo")
            dot.setFixedSize(22, 22)
            dot.setAlignment(Qt.AlignCenter)
            self.dots.append(dot)
            il.addWidget(dot)
            lab = label(name)
            lab.setObjectName("StepTodo")
            self.labels.append(lab)
            il.addWidget(lab)
            lay.addWidget(item)
            if i < len(self.STEPS):
                sep = label("·", "Muted")
                sep.setObjectName("StepSep")
                lay.addWidget(sep)
        lay.addStretch(1)
        self.set_current(current)

    def set_current(self, current):
        self.current = max(1, min(len(self.STEPS), int(current)))
        for i, (dot, lab) in enumerate(zip(self.dots, self.labels), 1):
            name = self.STEPS[i - 1]
            if i < self.current:
                dot.setObjectName("StepDotDone")
                dot.setText("✓")
                lab.setObjectName("StepDone")
                lab.setText(name)
                lab.setAccessibleName(f"Step {i}: {name}, done")
            elif i == self.current:
                dot.setObjectName("StepDotCurrent")
                dot.setText(str(i))
                lab.setObjectName("StepCurrent")
                lab.setText(name)
                lab.setAccessibleName(f"Step {i}: {name}, current step")
                lab.setAccessibleDescription("Current step")
            else:
                dot.setObjectName("StepDotTodo")
                dot.setText(str(i))
                lab.setObjectName("StepTodo")
                lab.setText(name)
                lab.setAccessibleName(f"Step {i}: {name}, not started")
            for w in (dot, lab):
                w.style().unpolish(w)
                w.style().polish(w)


class Disclosure(QWidget):
    """A labelled disclosure with a persisted open/closed state."""

    toggled = Signal(str, bool)

    def __init__(self, key, title, parent=None):
        super().__init__(parent)
        self.key = key
        self.title = title
        self.expanded = False
        self.badge = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.button = EnterButton()
        self.button.setObjectName("Disclosure")
        self.button.setCheckable(True)
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.toggled.connect(self._button_toggled)
        lay.addWidget(self.button)
        self.content = QWidget()
        self.content_lay = QVBoxLayout(self.content)
        self.content_lay.setContentsMargins(0, 2, 0, 0)
        self.content_lay.setSpacing(8)
        self.content.setVisible(False)
        lay.addWidget(self.content)
        self._render_button()

    def set_badge(self, text):
        self.badge = text or ""
        self._render_button()

    def is_expanded(self):
        return self.expanded

    def set_expanded(self, on, persist=True):
        on = bool(on)
        if on == self.expanded:
            return
        self.button.blockSignals(True)
        self.button.setChecked(on)
        self.button.blockSignals(False)
        self._apply(on, persist)

    def _button_toggled(self, on):
        if on != self.expanded:
            self._apply(on, True)

    def _apply(self, on, persist):
        self.expanded = on
        self.content.setVisible(on)
        self._render_button()
        if persist:
            self.toggled.emit(self.key, on)

    def _render_button(self):
        arrow = "▾" if self.expanded else "▸"
        suffix = f" · {self.badge}" if self.badge else ""
        self.button.setText(f"{arrow}  {self.title}{suffix}")
        state = "expanded" if self.expanded else "collapsed"
        self.button.setAccessibleName(f"{self.title}, {state}")
        self.button.setAccessibleDescription(f"{self.title}, {state}")


class FlowLayout(QLayout):
    """A wrapping layout for preset cards."""

    def __init__(self, parent=None, margin=0, hspace=10, vspace=10):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._hspace = hspace
        self._vspace = vspace
        self._items = []

    def addItem(self, item):
        self._items.append(item)
        self._relayout()

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self._relayout()
            return item
        return None

    def _relayout(self):
        # The flow's size hint does not change when cards are added or removed,
        # so Qt would never call setGeometry again and new cards keep their
        # default geometry. Lay out explicitly and let parents re-query heights.
        w = self.parentWidget()
        if w is None or w.width() <= 0:
            self.invalidate()
            return
        self.setGeometry(w.rect())
        w.updateGeometry()

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            w = item.widget()
            if w is not None and w.isHidden():
                continue
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _natural_width(self, item):
        w = item.widget()
        nw = getattr(w, "natural_width", None) if w is not None else None
        return nw or item.sizeHint().width()

    def _row_widths(self, row, area_w):
        naturals = [self._natural_width(it) for it in row]
        if area_w <= 0:
            return naturals
        widths = list(naturals)
        extra = area_w - sum(naturals) - self._hspace * max(0, len(row) - 1)
        if extra > 0:
            fill = [i for i, it in enumerate(row) if getattr(it.widget(), "fill_row", False)]
            if fill:
                share = extra / len(fill)
                for i in fill:
                    cap = getattr(row[i].widget(), "max_width", None) or 10 ** 6
                    widths[i] = min(naturals[i] + share, cap)
        if len(row) == 1 and widths[0] > area_w:
            widths[0] = area_w
        return widths

    def _item_height(self, item, width):
        w = item.widget()
        if w is not None:
            h = w.heightForWidth(width)
            if h and h > 0:
                return h
        return max(item.sizeHint().height(), item.minimumSize().height())

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        area = QRect(rect.x() + m.left(), rect.y() + m.top(),
                     max(0, rect.width() - m.left() - m.right()),
                     max(0, rect.height() - m.top() - m.bottom()))
        items = [it for it in self._items
                 if it.widget() is None or not it.widget().isHidden()]
        rows, row, row_w = [], [], 0
        for it in items:
            add = self._natural_width(it) + (self._hspace if row else 0)
            if row and row_w + add > area.width():
                rows.append(row)
                row, row_w = [it], self._natural_width(it)
            else:
                row.append(it)
                row_w += add
        if row:
            rows.append(row)
        y = area.top()
        for r in rows:
            widths = self._row_widths(r, area.width())
            line_h = 0
            x = area.left()
            for it, wdt in zip(r, widths):
                h = self._item_height(it, wdt)
                if not test_only:
                    it.setGeometry(QRect(x, y, wdt, h))
                x += wdt + self._hspace
                line_h = max(line_h, h)
            y += line_h + self._vspace
        if not rows:
            return m.top() + m.bottom()
        return y - self._vspace - rect.y() + m.bottom()


class FlowWidget(QWidget):
    """Widget wrapper that lets a FlowLayout report its wrapped height."""

    def __init__(self, parent=None, margin=0, hspace=10, vspace=10):
        super().__init__(parent)
        self.flow = FlowLayout(self, margin=margin, hspace=hspace, vspace=vspace)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def heightForWidth(self, width):
        return self.flow.heightForWidth(width)

    def sizeHint(self):
        return self.flow.sizeHint()


class PresetCard(QRadioButton):
    """One preset choice: name plus a wrapped description, drawn as a card."""

    arrowRequested = Signal(int)
    fill_row = True
    natural_width = 210
    max_width = 380

    def __init__(self, name, description="", parent=None):
        super().__init__(parent)
        self.name = name
        self.description = description or ""
        self.tab_next = None
        self.tab_prev = None
        self.setObjectName("PresetCard")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(176, 64)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self._sync_accessible()

    def setTextDescription(self, description):
        self.description = description or ""
        self._sync_accessible()
        self.update()

    def setChecked(self, on):
        super().setChecked(bool(on))
        self._sync_accessible()

    def _sync_accessible(self):
        state = "selected" if self.isChecked() else "not selected"
        desc = self.description.strip().rstrip(".")
        self.setAccessibleName(f"{self.name} preset, {desc}. {state}" if desc
                               else f"{self.name} preset, {state}")
        self.setAccessibleDescription(f"{desc}. {state}" if desc else state)
        self.setToolTip(f"{self.name}: {self.description}" if self.description.strip() else self.name)

    def _fonts(self):
        name_font = self.font()
        name_font.setBold(self.isChecked())
        desc_font = self.font()
        desc_font.setPointSizeF(10.0)
        return name_font, desc_font

    def _text_width(self, width):
        return max(48, width - 24 - 40)

    def _height_for(self, width):
        name_font, desc_font = self._fonts()
        h = 12 + QFontMetrics(name_font).height() + 2
        if self.description.strip():
            fm = QFontMetrics(desc_font)
            br = fm.boundingRect(QRect(0, 0, self._text_width(width), 100000),
                                 Qt.TextWordWrap | Qt.AlignLeft, self.description)
            h += 4 + br.height() + 4
        return h + 10

    def heightForWidth(self, width):
        return self._height_for(width)

    def sizeHint(self):
        return QSize(self.natural_width, self._height_for(self.natural_width))

    def minimumSizeHint(self):
        return QSize(176, self._height_for(176))

    def keyPressEvent(self, event):
        k = event.key()
        if k in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down) and not event.modifiers():
            self.arrowRequested.emit(k)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusNextPrevChild(self, next):
        # Tab/Backtab never reach keyPressEvent; the preset group is one tab stop.
        target = self.tab_next if next else self.tab_prev
        if target is not None and target.isVisible():
            target.setFocus(Qt.TabFocusReason if next else Qt.BacktabFocusReason)
            return True
        return super().focusNextPrevChild(next)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pad = 3
        card = self.rect().adjusted(pad, pad, -pad, -pad)
        checked = self.isChecked()
        border = QColor("#1E5A66" if checked else "#E8DDCF")
        p.setPen(QPen(border, 2 if checked else 1))
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(card, 10, 10)
        if self.hasFocus():
            p.setPen(QPen(QColor("#1E5A66"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 9, 9)
        name_font, desc_font = self._fonts()
        marker = QRect(card.left() + 12, card.top() + 13, 18, 18)
        if checked:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#2E7D8C"))
            p.drawEllipse(marker)
            cf = self.font()
            cf.setBold(True)
            cf.setPointSizeF(11)
            p.setFont(cf)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(marker, Qt.AlignCenter, "✓")
        else:
            p.setPen(QPen(QColor("#B8A99A"), 1.5))
            p.setBrush(QColor("#FFFFFF"))
            p.drawEllipse(marker)
        x = card.left() + 40
        avail = card.right() - 12 - x
        name_h = QFontMetrics(name_font).height()
        p.setFont(name_font)
        p.setPen(QColor("#2A1B2E"))
        name = QFontMetrics(name_font).elidedText(self.name, Qt.ElideMiddle, avail)
        p.drawText(QRect(x, card.top() + 12, avail, name_h + 2), Qt.AlignLeft | Qt.AlignVCenter, name)
        if self.description.strip():
            p.setFont(desc_font)
            p.setPen(QColor("#6B566E"))
            desc_h = max(0, card.height() - (12 + name_h + 4) - 12)
            p.drawText(QRect(x, card.top() + 12 + name_h + 4, avail, desc_h),
                       Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, self.description)
        p.end()
