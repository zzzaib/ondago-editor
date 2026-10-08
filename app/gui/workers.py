"""Background task runner so the UI never freezes."""
from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal


class Task(QThread):
    progress = Signal(float, str)
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[..., Any], *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs

    def run(self):
        try:
            res = self.fn(*self.args, log=self.log.emit, progress=self.progress.emit, **self.kwargs)
            self.done.emit(res)
        except Exception as e:  # noqa: BLE001
            self.log.emit(traceback.format_exc())
            self.failed.emit(str(e))
