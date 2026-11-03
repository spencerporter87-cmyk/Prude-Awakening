"""Batch: queue several movies or a whole TV season, processed one at a time."""

import logging
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QHBoxLayout, QHeaderView, QListWidget, QMessageBox,
                               QPlainTextEdit, QProgressBar, QRadioButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..export import NAMING, ExportOptions
from ..jobs import (CANCELLED, DONE, FAILED, QUEUED, REVIEW, RUNNING, STATUS_TEXT, expand_sources,
                    run_queue)
from ..media import VIDEO_EXTS, probe
from ..settings import FilterOptions
from .widgets import Card, button, label

log = logging.getLogger("prude")


class BatchRunner(QThread):
    jobChanged = Signal(str)

    def __init__(self, queue, settings):
        super().__init__()
        self.queue, self.settings = queue, settings
        self.cancel = threading.Event()
        self._stop = False

    def run(self):
        self._stop = False
        run_queue(self.queue, self.settings, on_change=lambda j: self.jobChanged.emit(j.id),
                  should_stop=lambda: self._stop, cancel=self.cancel, log_fn=log.info)

    def pause_after_current(self):
        self._stop = True

    def cancel_current(self):
        self.cancel.set()


class AddBatchDialog(QDialog):
    """Pick files/folders, a preset, and whether to save automatically or stop for review."""

    def __init__(self, settings, paths=(), parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Add to batch")
        self.setMinimumSize(760, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(10)
        lay.addWidget(label("Add movies or episodes to the batch", "H1"))
        row = QHBoxLayout()
        row.addWidget(button("+ Add files…", None, self._add_files))
        row.addWidget(button("+ Add a folder (e.g. a TV season)…", None, self._add_folder))
        row.addStretch()
        row.addWidget(button("Remove selected", "Link", self._remove))
        lay.addLayout(row)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        lay.addWidget(self.list, 1)
        self.count = label("", "Small")
        lay.addWidget(self.count)

        prow = QHBoxLayout()
        prow.addWidget(label("Filter with preset", None))
        self.preset = QComboBox()
        self.presets = settings.all_presets()
        self.preset.addItems(list(self.presets))
        if settings.last_preset in self.presets:
            self.preset.setCurrentText(settings.last_preset)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        prow.addWidget(self.preset)
        prow.addStretch()
        lay.addLayout(prow)

        self.mode_save = QRadioButton("Save each clean copy automatically, using everything the scan finds")
        self.mode_review = QRadioButton("Scan only, then stop so I can review each one before saving")
        g = QButtonGroup(self)
        for r in (self.mode_save, self.mode_review):
            g.addButton(r)
            lay.addWidget(r)
        self.mode_note = label("", "Small", wrap=True)
        lay.addWidget(self.mode_note)

        self.export = ExportOptions.from_dict(settings.export) if settings.export else ExportOptions()
        erow = QHBoxLayout()
        self.export_lbl = label("", "Muted", wrap=True)
        erow.addWidget(self.export_lbl, 1)
        erow.addWidget(button("Change…", None, self._change_export))
        lay.addLayout(erow)

        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.ok = button("Add to batch  →", "Primary", self.accept)
        bb.addButton(self.ok, QDialogButtonBox.AcceptRole)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.paths = []
        self._add(paths)
        self._preset_changed()
        self._show_export()

    # ------------------------------------------------------------ files
    def _add(self, paths):
        known = {str(p).lower() for p in self.paths}
        for f in expand_sources(paths):
            if str(f).lower() not in known:
                self.paths.append(f)
                self.list.addItem(str(f))
        self.count.setText(f"{len(self.paths)} file(s)")
        self.ok.setEnabled(bool(self.paths))

    def _add_files(self):
        exts = " ".join(f"*{x}" for x in sorted(VIDEO_EXTS))
        files, _ = QFileDialog.getOpenFileNames(self, "Choose movies or episodes", "", f"Videos ({exts})")
        self._add(files)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder (subfolders are included)")
        if d:
            self._add([d])

    def _remove(self):
        rows = sorted((self.list.row(i) for i in self.list.selectedItems()), reverse=True)
        for r in rows:
            self.list.takeItem(r)
            del self.paths[r]
        self.count.setText(f"{len(self.paths)} file(s)")
        self.ok.setEnabled(bool(self.paths))

    # ------------------------------------------------------------ options
    def _preset_changed(self, *_):
        f = FilterOptions.from_dict(self.presets[self.preset.currentText()])
        has_scenes = bool(f.scenes or f.scene_descriptions)
        (self.mode_review if has_scenes else self.mode_save).setChecked(True)
        self.mode_note.setText(
            "This preset looks for scenes. Reviewing first is recommended, since scene detection can "
            "flag things you'd want to keep." if has_scenes else
            "Language-only presets are safe to save automatically; you can still re-open any movie later.")

    def _show_export(self):
        e = self.export
        where = e.folder or "next to each original"
        name = NAMING.get(e.naming, e.naming).split(" (")[0]
        ed = f' "{e.edition}"' if e.naming == "plex_edition" else ""
        self.export_lbl.setText(f"<b>Saving:</b> {name}{ed} · {e.container.upper()} · "
                                f"{'original size' if e.resolution == 'source' else e.resolution + 'p'} · {where}")

    def _change_export(self):
        if not self.paths:
            QMessageBox.information(self, "Add a file first", "Add at least one movie to preview the names.")
            return
        from .export_dialog import ExportDialog
        try:
            info = probe(self.paths[0])
        except Exception as e:
            QMessageBox.warning(self, "Can't read the first file", str(e))
            return
        d = ExportDialog(self.paths[0], info, self.export, True, 0.0, "", self)
        d.ok.setText("Use these options")
        if d.exec():
            self.export = d.options()
            self._show_export()

    def result_values(self):
        name = self.preset.currentText()
        return (self.paths, name, self.presets[name], self.export.to_dict(),
                "review" if self.mode_review.isChecked() else "save")


class BatchPage(QWidget):
    back = Signal()
    openReview = Signal(str, dict)       # source, filters
    COLS = ["Movie / episode", "Preset", "Status", "Progress", "Details"]

    def __init__(self, queue, settings, is_busy=None):
        super().__init__()
        self.setObjectName("Page")
        self.queue, self.settings = queue, settings
        self.is_busy = is_busy or (lambda: False)
        self.runner = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 22, 36, 20)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(button("←  Home", "Link", self.back.emit))
        head.addStretch()
        lay.addLayout(head)
        lay.addWidget(label("Batch", "H1"))
        lay.addWidget(label("Movies and episodes are processed one at a time, so your graphics card isn't "
                            "overloaded. The list is saved, so you can close the app and resume later.",
                            "Muted", wrap=True))
        bar = QHBoxLayout()
        bar.addWidget(button("+ Add movies or a folder…", "Primary", lambda: self.add_dialog()))
        bar.addSpacing(12)
        self.start_btn = button("▶ Start", None, self.toggle_run)
        bar.addWidget(self.start_btn)
        self.cancel_btn = button("Cancel current", "Danger", self.cancel_current)
        bar.addWidget(self.cancel_btn)
        bar.addStretch()
        for text, fn in (("Review", self.review_selected), ("Retry", self.retry_selected),
                         ("Show log", self.show_log), ("Remove", self.remove_selected),
                         ("Clear finished", self.clear_finished)):
            bar.addWidget(button(text, None, fn))
        lay.addLayout(bar)
        card = Card()
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Fixed)
        self.table.setColumnWidth(3, 160)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.doubleClicked.connect(lambda *_: self.review_selected())
        card.lay.addWidget(self.table)
        lay.addWidget(card, 1)
        self.summary = label("", "Muted")
        lay.addWidget(self.summary)
        self.refresh()

    # ------------------------------------------------------------ table
    def refresh(self):
        self.table.setRowCount(0)
        for j in self.queue.jobs:
            self._row(j)
        self._update_controls()

    def _row(self, job, row=None):
        if row is None:
            row = self.table.rowCount()
            self.table.insertRow(row)
        name = QTableWidgetItem(job.name)
        name.setData(Qt.UserRole, job.id)
        name.setToolTip(job.source)
        self.table.setItem(row, 0, name)
        self.table.setItem(row, 1, QTableWidgetItem(job.preset))
        st = QTableWidgetItem(STATUS_TEXT.get(job.status, job.status))
        color = {DONE: "#3C7A30", FAILED: "#C8412B", REVIEW: "#2E7D8C", RUNNING: "#2E7D8C",
                 CANCELLED: "#6B566E"}.get(job.status)
        if color:
            st.setForeground(QBrush(QColor(color)))
        self.table.setItem(row, 2, st)
        holder = self.table.cellWidget(row, 3)
        bar = holder.findChild(QProgressBar) if holder is not None else None
        if bar is None:
            holder = QWidget()
            hl = QVBoxLayout(holder)
            hl.setContentsMargins(8, 0, 8, 0)
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setFixedHeight(10)
            hl.addWidget(bar, 0, Qt.AlignVCenter)
            self.table.setCellWidget(row, 3, holder)
        bar.setValue(int(job.progress * 1000))
        details = job.error if job.status == FAILED and job.error else job.message
        di = QTableWidgetItem(details)
        di.setToolTip(details)
        self.table.setItem(row, 4, di)

    def job_changed(self, job_id):
        job = self.queue.get(job_id)
        if job is None:
            return
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it and it.data(Qt.UserRole) == job_id:
                self._row(job, r)
                break
        self._update_controls()

    def _selected(self):
        ids = {self.table.item(i.row(), 0).data(Qt.UserRole) for i in self.table.selectedIndexes()
               if self.table.item(i.row(), 0)}
        return [j for j in self.queue.jobs if j.id in ids]

    def _update_controls(self):
        running = self.runner is not None and self.runner.isRunning()
        stopping = running and self.runner._stop
        self.start_btn.setText("⏸ Pause after this one" if running and not stopping else
                               "Pausing after this one…" if stopping else "▶ Start")
        self.start_btn.setEnabled(not stopping)
        self.cancel_btn.setEnabled(running)
        c = self.queue.counts()
        parts = [f"{c.get(QUEUED, 0)} waiting", f"{c.get(RUNNING, 0)} working", f"{c.get(DONE, 0)} saved",
                 f"{c.get(REVIEW, 0)} to review", f"{c.get(FAILED, 0)} failed"]
        self.summary.setText(" · ".join(parts))

    # ------------------------------------------------------------ actions
    def add_dialog(self, paths=()):
        d = AddBatchDialog(self.settings, paths, self)
        if d.exec():
            files, preset, filters, export, mode = d.result_values()
            added = self.queue.add(files, preset, filters, export, mode)
            self.settings.export = export
            self.settings.save()
            self.refresh()
            if added and not (self.runner and self.runner.isRunning()):
                if QMessageBox.question(self, "Start now?", f"{len(added)} added. Start the batch now?") \
                        == QMessageBox.Yes:
                    self.toggle_run()

    def toggle_run(self):
        if self.runner and self.runner.isRunning():
            self.runner.pause_after_current()
        else:
            if self.is_busy():
                QMessageBox.information(self, "Busy", "A scan or save is running in the main window. "
                                                      "Start the batch when it's finished.")
                return
            if not self.queue.next_job():
                QMessageBox.information(self, "Nothing waiting", "Add movies first, or Retry finished ones.")
                return
            self.runner = BatchRunner(self.queue, self.settings)
            self.runner.jobChanged.connect(self.job_changed)
            self.runner.finished.connect(self._update_controls)
            self.runner.start()
        self._update_controls()

    def cancel_current(self):
        if self.runner and self.runner.isRunning():
            self.runner.cancel_current()

    def running(self):
        return self.runner is not None and self.runner.isRunning()

    def retry_selected(self):
        for j in self._selected():
            self.queue.retry(j)
        self.refresh()

    def remove_selected(self):
        for j in self._selected():
            self.queue.remove(j)
        self.refresh()

    def clear_finished(self):
        self.queue.clear_finished()
        self.refresh()

    def review_selected(self):
        sel = [j for j in self._selected() if j.status in (REVIEW, DONE)]
        if not sel:
            QMessageBox.information(self, "Review", "Pick a movie that's been scanned (Ready to review or Saved).")
            return
        self.openReview.emit(sel[0].source, sel[0].filters)

    def show_log(self):
        sel = self._selected()
        if not sel:
            return
        j = sel[0]
        d = QDialog(self)
        d.setWindowTitle(f"Log - {j.name}")
        d.resize(820, 520)
        v = QVBoxLayout(d)
        t = QPlainTextEdit("\n".join(j.log))
        t.setReadOnly(True)
        v.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(d.reject)
        v.addWidget(bb)
        d.exec()
