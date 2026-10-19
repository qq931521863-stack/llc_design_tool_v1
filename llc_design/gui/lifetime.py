"""Explicit destruction at discarded-widget and application-owner boundaries."""
from __future__ import annotations

from time import monotonic, sleep

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QThreadPool, QTimer
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid


def dispose_dialog(dialog) -> None:
    """End a modal dialog's lifetime without touching reusable workspaces."""
    if isValid(dialog):
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)


def dispose_widgets(widgets) -> None:
    """Drain work, then destroy only the explicitly supplied owner trees.

    Ordinary close/hide intentionally permits reuse. This is the final ownership
    boundary, not a replacement for a workspace's closeEvent. In particular,
    PlotWidget.close() must not detach plots from the native tree being deleted.
    """
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from pyqtgraph import GraphicsLayoutWidget

    app = QApplication.instance()
    owners = tuple(widget for widget in widgets if isValid(widget))
    pools = set()
    worker_owners = []
    for owner in owners:
        owner.close()
        for timer in owner.findChildren(QTimer):
            timer.stop()
        pools.update(owner.findChildren(QThreadPool))
        worker_owners.extend([owner, *owner.findChildren(QWidget)])
    # LLC/PFC/DAB use a shared global pool. Waiting for that entire pool would
    # also wait for unrelated windows; their per-owner worker registries are
    # the authority for whose completion is needed at this lifetime boundary.
    def owned_work_pending():
        return any(isValid(widget) and (
            bool(getattr(widget, "_active_workers", ()))
            or getattr(widget, "_worker", None) is not None
            or bool(getattr(widget, "_jobs", ()))
        ) for widget in worker_owners)

    deadline = monotonic() + 15.0
    while owned_work_pending():
        if monotonic() >= deadline:
            raise RuntimeError("Owned workspace worker did not finish")
        if app is not None:
            app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        sleep(0.001)
    for pool in pools:
        if not pool.waitForDone(15000):
            raise RuntimeError("Owned workspace worker did not finish")
    if app is not None:
        app.processEvents()
    for owner in owners:
        if not isValid(owner):
            continue
        # Busy workspaces can decline the first close until queued completion
        # callbacks run. Never delete an owner that still reports live work.
        if not owner.close():
            raise RuntimeError("Owned workspace still refuses to close")
        for canvas in owner.findChildren(FigureCanvasQTAgg):
            canvas.figure.clear()
        for graphics in owner.findChildren(GraphicsLayoutWidget):
            if isValid(graphics) and not graphics.closed:
                graphics.close()
        owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    if app is not None:
        app.processEvents()
    if any(isValid(owner) for owner in owners):
        raise RuntimeError("Owned workspace was not destroyed")
