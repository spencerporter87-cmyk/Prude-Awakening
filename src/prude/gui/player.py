"""Built-in video player with the timeline underneath.

On the review screen it can preview the filters live: silencing words, skipping
cut scenes and blacking out scenes while you watch the original."""

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QSizePolicy, QStackedWidget,
                               QVBoxLayout, QWidget)

from .timeline import Timeline, legend
from .widgets import button, fmt_time, label

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    HAVE_MM = True
except Exception:  # pragma: no cover - QtMultimedia missing
    HAVE_MM = False


class Player(QWidget):
    loaded = Signal(float)         # duration in seconds, once the file is ready

    def __init__(self, live_toggle=False, timeline_inside=True, parent=None):
        super().__init__(parent)
        self.mutes, self.cuts, self.blackouts = [], [], []
        self.live = False
        self._stop_at = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.screen = QStackedWidget()
        self.screen.setMinimumSize(320, 180)
        self.screen.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.screen.setStyleSheet("background: #111; border-radius: 10px;")
        if HAVE_MM:
            self.video = QVideoWidget()
            self.video.setAspectRatioMode(Qt.KeepAspectRatio)
            self.screen.addWidget(self.video)
        else:
            self.video = label("Video preview isn't available on this computer.", "Muted")
            self.screen.addWidget(self.video)
        self.black = QLabel("⬛  Blacked out")
        self.black.setAlignment(Qt.AlignCenter)
        self.black.setStyleSheet("background: black; color: #777; border-radius: 10px;")
        self.screen.addWidget(self.black)
        lay.addWidget(self.screen, 1)

        bar = QHBoxLayout()
        self.play_btn = button("▶", None, self.toggle, tip="Play / pause")
        self.play_btn.setStyleSheet("padding: 4px 6px;")
        self.play_btn.setFixedWidth(52)
        bar.addWidget(self.play_btn)
        for txt, d, tip in (("« 10s", -10, "Back 10 seconds"), ("10s »", 10, "Forward 10 seconds")):
            b = button(txt, None, lambda _=False, d=d: self.jump(d), tip=tip)
            b.setStyleSheet("padding: 4px 6px;")
            b.setFixedWidth(64)
            bar.addWidget(b)
        self.time = label("0:00:00 / 0:00:00", "Muted")
        bar.addWidget(self.time)
        bar.addStretch()
        self.status = label("", "Small")
        bar.addWidget(self.status)
        if live_toggle:
            self.live_box = QCheckBox("Preview with filters on")
            self.live_box.setToolTip("Silences the words and skips or blacks out the scenes "
                                     "you've ticked, while you watch.")
            self.live_box.toggled.connect(self.set_live)
            bar.addWidget(self.live_box)
        lay.addLayout(bar)

        self.timeline = Timeline()
        self.timeline.seek.connect(self.seek_to)
        self.legend_box = QVBoxLayout()
        if timeline_inside:
            lay.addWidget(self.timeline)
            lay.addLayout(self.legend_box)

        if HAVE_MM:
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.player.setVideoOutput(self.video)
            self.player.positionChanged.connect(self._pos)
            self.player.durationChanged.connect(self._dur)
            self.player.playbackStateChanged.connect(self._state)
            self.player.errorOccurred.connect(lambda *_: self.status.setText(
                "This video can't be previewed here — use ▶ Play in the list instead."))
        self.tick = QTimer(self)
        self.tick.setInterval(40)
        self.tick.timeout.connect(self._filter_tick)

    def set_legend(self, mode, action="cut"):
        while self.legend_box.count():
            it = self.legend_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.legend_box.addWidget(legend(mode, action))

    # ------------------------------------------------------------ media
    def load(self, path):
        self.status.setText("")
        if HAVE_MM:
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            self.player.pause()   # shows the first frame

    def stop(self):
        if HAVE_MM:
            self.player.stop()
            self.player.setSource(QUrl())
        self.tick.stop()

    def duration(self):
        return self.player.duration() / 1000 if HAVE_MM else 0.0

    def position(self):
        return self.player.position() / 1000 if HAVE_MM else 0.0

    def toggle(self):
        if not HAVE_MM:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self._stop_at = None
            self.player.play()

    def seek_to(self, t):
        if HAVE_MM:
            self.player.setPosition(int(max(0.0, t) * 1000))
            self.timeline.set_position(t)

    def jump(self, d):
        self.seek_to(self.position() + d)

    def play_range(self, start, end=None):
        """Jump to a spot and play it (stops a couple of seconds after `end`)."""
        if not HAVE_MM:
            return False
        self.seek_to(max(0.0, start))
        self._stop_at = (end + 2.0) if end is not None else None
        self.player.play()
        return True

    # ------------------------------------------------------------ live filters
    def set_filters(self, mutes, cuts, blackouts, blurs=(), black_keep=()):
        """blackouts are silent; black_keep and blurs keep their sound."""
        self.mutes, self.cuts, self.blackouts = list(mutes), list(cuts), list(blackouts)
        self.blurs, self.black_keep = list(blurs), list(black_keep)

    def set_live(self, on):
        self.live = bool(on)
        if not on and HAVE_MM:
            self.audio.setMuted(False)
            self.screen.setCurrentIndex(0)

    def _filter_tick(self):
        if not HAVE_MM:
            return
        t = self.position()
        if self._stop_at is not None and t >= self._stop_at:
            self._stop_at = None
            self.player.pause()
            return
        if not self.live:
            return
        for a, b in self.cuts:
            if a <= t < b - 0.05:
                self.player.setPosition(int(b * 1000) + 20)
                self.status.setText(f"✂ Skipped {fmt_time(b - a, False)}")
                return
        black = any(a <= t < b for a, b in self.blackouts)
        black_snd = not black and any(a <= t < b for a, b in getattr(self, "black_keep", []))
        blur = not (black or black_snd) and any(a <= t < b for a, b in getattr(self, "blurs", []))
        self.black.setText("◌  Blurred in the clean copy (sound plays)" if blur else
                           "⬛  Black screen (sound plays)" if black_snd else "⬛  Blacked out")
        self.screen.setCurrentIndex(1 if (black or black_snd or blur) else 0)
        muted = black or any(a <= t < b for a, b in self.mutes)
        if self.audio.isMuted() != muted:
            self.audio.setMuted(muted)

    def _pos(self, ms):
        t = ms / 1000
        self.timeline.set_position(t)
        self.time.setText(f"{fmt_time(t, False)} / {fmt_time(self.duration(), False)}")

    def _dur(self, ms):
        if ms > 0:
            self.time.setText(f"{fmt_time(self.position(), False)} / {fmt_time(ms / 1000, False)}")
            self.loaded.emit(ms / 1000)

    def _state(self, st):
        playing = st == QMediaPlayer.PlayingState
        self.play_btn.setText("❚❚" if playing else "▶")
        if playing:
            self.tick.start()
        else:
            self.tick.stop()
            if self.live:
                self.audio.setMuted(False)
