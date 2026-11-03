"""Export window: where and how to save the clean copy."""

from pathlib import Path

from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QRadioButton,
                               QVBoxLayout, QWidget)

from ..export import NAMING, ExportOptions, estimate_size, human_size, output_path, target_height
from .widgets import button, label


class ExportDialog(QDialog):
    def __init__(self, source, info, opts: ExportOptions, needs_reencode, removed_seconds,
                 preset_name="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save the clean copy")
        self.setMinimumWidth(760)
        self.src, self.info = Path(source), info
        self.base_reencode, self.removed = needs_reencode, removed_seconds
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(10)
        lay.addWidget(label("Save the clean copy", "H1"))
        form = QFormLayout()
        form.setVerticalSpacing(10)
        lay.addLayout(form)

        self.folder = QLineEdit(opts.folder)
        self.folder.setPlaceholderText(f"Same folder as the movie ({self.src.parent})")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.folder, 1)
        row.addWidget(button("Browse…", None, self._browse))
        row.addWidget(button("Same as movie", "Link", lambda: self.folder.setText("")))
        w = QWidget()
        w.setLayout(row)
        form.addRow("Save to", w)
        self.own = QCheckBox("Put it in its own \"Title (Year)\" folder (how Plex likes it)")
        self.own.setChecked(opts.own_folder)
        form.addRow("", self.own)

        nb = QVBoxLayout()
        nb.setContentsMargins(0, 0, 0, 0)
        self.naming = QButtonGroup(self)
        self.radios = {}
        for key, text in NAMING.items():
            r = QRadioButton(text)
            self.radios[key] = r
            self.naming.addButton(r)
            nb.addWidget(r)
            if key == "plex_edition":
                er = QHBoxLayout()
                er.setContentsMargins(26, 0, 0, 4)
                er.addWidget(label("Edition name", "Small"))
                self.edition = QLineEdit(opts.edition or preset_name or "Family Edit")
                self.edition.setMaximumWidth(260)
                er.addWidget(self.edition)
                er.addStretch()
                nb.addLayout(er)
        (self.radios.get(opts.naming) or self.radios["plex_edition"]).setChecked(True)
        nw = QWidget()
        nw.setLayout(nb)
        form.addRow("File name", nw)

        self.container = QComboBox()
        self.container.addItem("MKV (recommended: keeps every audio and subtitle format)", "mkv")
        self.container.addItem("MP4 (plays on almost anything)", "mp4")
        self.container.setCurrentIndex(max(0, self.container.findData(opts.container)))
        form.addRow("Format", self.container)

        self.res = QComboBox()
        h = info.height or 0
        self.res.addItem(f"Same as the original ({info.width}×{h})" if h else "Same as the original", "source")
        for key, lab in (("2160", "4K (2160p)"), ("1080", "1080p"), ("720", "720p")):
            if h > int(key) + 8:
                self.res.addItem(f"{lab} - smaller file, re-encodes the video", key)
        self.res.setCurrentIndex(max(0, self.res.findData(opts.resolution)))
        form.addRow("Picture size", self.res)

        self.quality = QComboBox()
        self.quality.addItem("Very high (recommended)", "high")
        self.quality.addItem("Maximum (bigger file, slower)", "max")
        self.quality.setCurrentIndex(max(0, self.quality.findData(opts.quality)))
        form.addRow("Quality if re-encoding", self.quality)

        self.fast = QCheckBox("Fast cuts: copy the picture instead of re-encoding when scenes are only cut")
        self.fast.setToolTip("Saves in minutes with the original picture quality. Each cut lands on the next\n"
                             "keyframe, so it can run on up to a few seconds longer than marked.\n"
                             "Only used when every changed scene is cut (not blacked out or blurred).")
        self.fast.setChecked(opts.fast_cuts)
        form.addRow("", self.fast)
        self.other = QCheckBox("Also keep the other audio tracks (other languages, commentary) - NOT filtered")
        self.other.setChecked(opts.other_audio)
        form.addRow("", self.other)

        self.preview = label("", "Muted", wrap=True)
        self.preview.setStyleSheet("background: #FFFFFF; border: 1px solid #E8DDCF; border-radius: 10px; padding: 10px;")
        lay.addWidget(self.preview)

        for sig in (self.folder.textChanged, self.edition.textChanged, self.own.toggled,
                    self.container.currentIndexChanged, self.res.currentIndexChanged,
                    self.quality.currentIndexChanged, self.naming.buttonToggled, self.fast.toggled,
                    self.other.toggled):
            sig.connect(self._refresh)
        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.ok = button("Save clean copy  →", "Primary", self.accept)
        bb.addButton(self.ok, QDialogButtonBox.AcceptRole)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._refresh()

    def options(self) -> ExportOptions:
        naming = next(k for k, r in self.radios.items() if r.isChecked())
        return ExportOptions(folder=self.folder.text().strip(), container=self.container.currentData(),
                             resolution=self.res.currentData(), quality=self.quality.currentData(),
                             naming=naming, edition=self.edition.text().strip() or "Family Edit",
                             own_folder=self.own.isChecked(), fast_cuts=self.fast.isChecked(),
                             other_audio=self.other.isChecked())

    def output(self) -> Path:
        return output_path(self.src, self.options())

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Where to save", self.folder.text() or str(self.src.parent))
        if d:
            self.folder.setText(d)

    def _refresh(self, *_):
        o = self.options()
        self.edition.setEnabled(o.naming == "plex_edition")
        self.own.setEnabled(bool(o.folder) and o.naming != "simple")
        out = self.output()
        resized = target_height(self.info, o) is not None
        reencode = self.base_reencode or resized
        size = estimate_size(self.info, reencode, o, self.removed)
        lines = [f"<b>Will be saved as:</b><br>{out}",
                 "Picture: " + ("re-encoded at very high quality" if reencode else
                                "copied bit-for-bit (no quality change, saves in minutes)"),
                 f"Estimated size: about {human_size(size)}"]
        if out.exists():
            lines.append("<span style='color:#C8412B'>A file with this name already exists and will be replaced.</span>")
        if o.naming == "plex_edition":
            lines.append("Plex shows this as an edition of the movie (needs Plex Pass on the server account).")
        self.preview.setText("<br>".join(lines))
