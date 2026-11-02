"""Runs scans and renders in the background so the window stays responsive."""

import threading
import traceback

from PySide6.QtCore import QThread, Signal

from ..media import Cancelled


class Worker(QThread):
    progress = Signal(str, float, str)
    log = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str, str)      # friendly message, details
    cancelled = Signal()

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        try:
            result = self.fn(*self.args, progress=lambda s, f, m: self.progress.emit(s, float(f), m or ""),
                             cancel=self.cancel_event, log=self.log.emit, **self.kwargs)
            self.finished_ok.emit(result)
        except Cancelled:
            self.cancelled.emit()
        except Exception as e:
            details = traceback.format_exc()
            self.failed.emit(str(e) or e.__class__.__name__, details)
