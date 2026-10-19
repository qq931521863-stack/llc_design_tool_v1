"""Reusable QRunnable wrapper for non-blocking engineering calculations."""

from __future__ import annotations

import traceback
from threading import Event
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    finished = Signal()
    result = Signal(object)
    error = Signal(str)
    progress = Signal(float, str)


class FunctionWorker(QRunnable):
    """QRunnable whose signals stay alive until emission completes.

    The thread pool auto-deletes the QRunnable after run(), so callers must
    hold a strong reference to this object until `finished` fires; otherwise
    the signals QObject can be garbage-collected mid-emission and the result
    callback silently never runs ("Signal source has been deleted"). Submit
    once through start(pool), not pool.start(worker), to protect the binding's
    wrapper ownership handoff. Direct synchronous run() remains available.
    """

    def __init__(self, function: Callable[..., Any], *args, **kwargs):
        super().__init__()
        self.setAutoDelete(True)
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self._submission_ready: Event | None = None

    def start(self, pool) -> None:
        """Submit without racing PySide's post-start wrapper ownership update.

        QThreadPool.start releases the GIL, then parents the Python runnable
        wrapper to the pool after the native call returns. A very short run
        must not auto-delete before that parenting step, or the pool can keep
        an already-invalid wrapper alive until the pool itself is destroyed.
        """
        if self._submission_ready is not None:
            raise RuntimeError("FunctionWorker has already been submitted")
        ready = Event()
        self._submission_ready = ready
        try:
            pool.start(self)
        finally:
            # A wrapper can raise after native submission. Release the job,
            # but never permit a second attempt on a possibly live runnable.
            ready.set()

    @Slot()
    def run(self) -> None:
        ready = self._submission_ready
        if ready is not None:
            ready.wait()
        try:
            result = self.function(*self.args, **self.kwargs)
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()
