from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from tatclean.core.models import TaskFailure, TaskProgress


class CancelledError(Exception):
    """Cooperative cancellation; it never terminates a QThread."""


class TaskContext:
    def __init__(
        self,
        cancel_event: threading.Event | None = None,
        progress: Callable[[TaskProgress], None] | None = None,
        log: Callable[[str], None] | None = None,
    ):
        self.cancel_event = cancel_event or threading.Event()
        self._progress = progress or (lambda _: None)
        self._log = log or (lambda _: None)
        self._last_progress = 0.0

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def checkpoint(self) -> None:
        if self.cancelled:
            raise CancelledError("Operation cancelled. Already-applied changes are not undone.")

    def report(self, progress: TaskProgress, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_progress >= 0.1:
            self._last_progress = now
            self._progress(progress)

    def log(self, text: str) -> None:
        logging.getLogger("tatclean").info(text)
        self._log(text)


class TaskWorker(QThread):
    progress = pyqtSignal(object)
    log_message = pyqtSignal(str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, function: Callable[[TaskContext], Any], parent: QObject | None = None):
        super().__init__(parent)
        self._function = function
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()
        self.requestInterruption()

    def run(self) -> None:
        context = TaskContext(self._cancel_event, self.progress.emit, self.log_message.emit)
        try:
            result = self._function(context)
        except CancelledError as exc:
            self.failed.emit(TaskFailure(str(exc), "", cancelled=True))
        except Exception as exc:
            details = traceback.format_exc()
            logging.getLogger("tatclean").exception("Background task failed")
            self.failed.emit(TaskFailure(str(exc) or type(exc).__name__, details))
        else:
            self.succeeded.emit(result)


class TaskManager(QObject):
    """One maintenance task at a time; telemetry has its own read-only thread."""

    busy_changed = pyqtSignal(bool, str)
    progress = pyqtSignal(object)
    log_message = pyqtSignal(str)
    failed = pyqtSignal(object)
    idle = pyqtSignal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._worker: TaskWorker | None = None
        self._callback: Callable[[Any], None] | None = None
        self._pending_result: Any = None
        self._has_result = False

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def start(
        self, title: str, function: Callable[[TaskContext], Any], callback: Callable[[Any], None]
    ) -> bool:
        if self.busy:
            return False
        worker = TaskWorker(function, self)
        self._worker = worker
        self._callback = callback
        self._pending_result = None
        self._has_result = False
        worker.progress.connect(self.progress)
        worker.log_message.connect(self.log_message)
        worker.succeeded.connect(self._receive_result)
        worker.failed.connect(self.failed)
        worker.finished.connect(self._finished)
        self.busy_changed.emit(True, title)
        worker.start()
        return True

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _receive_result(self, result: Any) -> None:
        self._pending_result = result
        self._has_result = True

    def _finished(self) -> None:
        # Deliver callbacks after finished, allowing callbacks to start another task safely.
        worker = self._worker
        callback = self._callback
        result, has_result = self._pending_result, self._has_result
        self._worker = None
        self._callback = None
        self._pending_result = None
        self._has_result = False
        if worker is not None:
            worker.deleteLater()
        self.busy_changed.emit(False, "")
        if callback is not None and has_result:
            try:
                callback(result)
            except Exception as exc:
                logging.getLogger("tatclean").exception("Task result handler failed")
                self.failed.emit(TaskFailure(str(exc), traceback.format_exc()))
        if not self.busy:
            self.idle.emit()
