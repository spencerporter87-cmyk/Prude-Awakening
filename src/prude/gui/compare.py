"""Side-by-side: the original and the clean copy playing together, kept in step.

The clean copy is shorter when scenes were cut, so the original's clock is the master and the
clean copy follows it through the cuts. Only one side's sound plays at a time."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QDialog, QHBoxLayout, QRadioButton, QSlider, QVBoxLayout)

from ..subtitles import TimeMap
from .player import HAVE_MM, Player
from .widgets import button, fmt_time, label


class CompareDialog(QDialog):
    def __init__(self, source, output, cuts, edits, audio_track=0, duration=0.0, parent=None):
        """cuts: removed ranges (original time). edits: [(time in original, text)] to jump between."""
        super().__init__(parent)
        self.setWindowTitle("Original vs clean copy")
        self.resize(1280, 720)
        self.cuts = sorted((float(a), float(b)) for a, b in cuts)
        self.map = TimeMap(self.cuts)
        self.edits = sorted(edits)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.addWidget(label("Original vs clean copy", "H1"))
        panes = QHBoxLayout()
        self.orig, self.clean = Player(False, False), Player(False, False)
        for cap, p in (("Original", self.orig), ("Clean copy", self.clean)):
            col = QVBoxLayout()
            col.addWidget(label(cap, "H2"))
            p.set_compact(True)
            col.addWidget(p, 1)
            panes.addLayout(col, 1)
        lay.addLayout(panes, 1)

        row = QHBoxLayout()
        self.play = button("▶", None, self.toggle)
        self.play.setFixedWidth(56)
        row.addWidget(self.play)
        row.addWidget(button("« Previous change", None, lambda: self.jump_change(-1)))
        row.addWidget(button("Next change »", None, lambda: self.jump_change(1)))
        self.time = label("0:00:00", "Muted")
        row.addWidget(self.time)
        row.addStretch()
        row.addWidget(label("Hear:", "Muted"))
        self.hear_o, self.hear_c = QRadioButton("Original"), QRadioButton("Clean copy")
        self.hear_c.setChecked(True)
        grp = QButtonGroup(self)
        for r in (self.hear_o, self.hear_c):
            grp.addButton(r)
            row.addWidget(r)
        lay.addLayout(row)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, max(1, int(duration or 1)))
        self.slider.sliderMoved.connect(self.seek)
        lay.addWidget(self.slider)
        self.note = label("", "Small")
        lay.addWidget(self.note)

        self.dur = float(duration or 0)
        self.orig.load(source, audio_track)
        self.clean.load(output, 0)
        if HAVE_MM:
            self.orig.player.positionChanged.connect(self._moved)
        self.tick = QTimer(self)
        self.tick.setInterval(120)
        self.tick.timeout.connect(self._follow)
        self.tick.start()

    # ---------------------------------------------------------------- controls
    def toggle(self):
        if not HAVE_MM:
            return
        from PySide6.QtMultimedia import QMediaPlayer
        if self.orig.player.playbackState() == QMediaPlayer.PlayingState:
            self.orig.player.pause()
            self.clean.player.pause()
            self.play.setText("▶")
        else:
            self.orig.player.play()
            self._follow()
            self.play.setText("❚❚")

    def seek(self, t):
        t = max(0.0, float(t))
        self.orig.seek_to(t)
        c = self._clean_time(t)
        self.clean.seek_to(c if c is not None else self._after_cut(t))

    def jump_change(self, direction):
        pos = self.orig.position()
        if direction > 0:
            nxt = next((t for t, _x in self.edits if t > pos + 2.5), None)
        else:
            nxt = next((t for t, _x in reversed(self.edits) if t < pos - 0.5), None)
        if nxt is None:
            self.note.setText("No more changes in that direction.")
            return
        self.seek(max(0.0, nxt - 2.0))
        txt = next((x for t, x in self.edits if t == nxt), "")
        self.note.setText(f"Change at {fmt_time(nxt, False)}: {txt}")
        if self.play.text() == "▶":
            self.toggle()

    # ---------------------------------------------------------------- keeping in step
    def _clean_time(self, t):
        return self.map(t)

    def _after_cut(self, t):
        for a, b in self.cuts:
            if a <= t < b:
                return self.map(b) or 0.0
        return 0.0

    def _moved(self, ms):
        t = ms / 1000
        self.slider.blockSignals(True)
        self.slider.setValue(int(t))
        self.slider.blockSignals(False)
        self.time.setText(f"{fmt_time(t, False)} / {fmt_time(self.dur or self.orig.duration(), False)}")

    def _follow(self):
        if not HAVE_MM:
            return
        from PySide6.QtMultimedia import QMediaPlayer
        playing = self.orig.player.playbackState() == QMediaPlayer.PlayingState
        t = self.orig.position()
        c = self._clean_time(t)
        if c is None:                                        # this stretch was removed
            self.clean.player.pause()
            self.clean.black.setText("✂  Removed in the clean copy")
            self.clean.screen.setCurrentIndex(1)
        else:
            self.clean.screen.setCurrentIndex(0)
            if abs(self.clean.position() - c) > 0.35:
                self.clean.seek_to(c)
            if playing and self.clean.player.playbackState() != QMediaPlayer.PlayingState:
                self.clean.player.play()
        if not playing:
            self.play.setText("▶")
        for p, mine in ((self.orig, self.hear_o.isChecked()), (self.clean, self.hear_c.isChecked())):
            for out in (p.audio, getattr(p, "aout", p.audio)):
                if out.isMuted() == mine:
                    out.setMuted(not mine)

    def done(self, r):
        self.tick.stop()
        self.orig.stop()
        self.clean.stop()
        super().done(r)
