"""Explicit destruction at discarded-widget and application-owner boundaries."""
from __future__ import annotations

from time import monotonic, sleep

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QEventLoop,
    QObject,
    QThreadPool,
    QTimer,
)
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid


def dispose_dialog(dialog) -> None:
    """End a modal dialog's lifetime without touching reusable workspaces."""
    if isValid(dialog):
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)


def _parameter_tree(parameter):
    from pyqtgraph.parametertree import Parameter

    result = set()
    pending = [parameter]
    while pending:
        item = pending.pop()
        if isinstance(item, Parameter) and item not in result:
            result.add(item)
            pending.extend(item.children())
    return result


def _plot_auxiliaries(views):
    """Find pyqtgraph's Python-owned objects outside the native scene tree."""
    from pyqtgraph import GraphicsLayout, GraphicsLayoutWidget, PlotItem, ViewBox

    auxiliaries = set()
    for view in views:
        if not isValid(view):
            continue
        scene = view.scene()
        if scene is None or not isValid(scene):
            continue
        export_dialog = getattr(scene, "exportDialog", None)
        auxiliaries.add(export_dialog)
        exporter = getattr(export_dialog, "currentExporter", None)
        auxiliaries.add(getattr(exporter, "fileDialog", None))
        auxiliaries.update(_parameter_tree(getattr(exporter, "params", None)))
        items = list(scene.items())
        if isinstance(view, GraphicsLayoutWidget):
            # GraphicsView.close() clears native items but its Python layout
            # still owns their wrappers. Read only those exact Python links;
            # never call a native method on an already-invalid scene item.
            pending = [view.ci]
            while pending:
                item = pending.pop()
                items.append(item)
                if isinstance(item, GraphicsLayout):
                    pending.extend(item.items)
                elif isinstance(item, PlotItem):
                    pending.append(item.vb)
        for item in items:
            if isinstance(item, PlotItem):
                auxiliaries.update((item.ctrlMenu, item.stateGroup, item.fileDialog))
            elif isinstance(item, ViewBox) and item.menu is not None:
                auxiliaries.add(item.menu)
                auxiliaries.update(getattr(item.menu, "widgetGroups", ()))
    return {obj for obj in auxiliaries if obj is not None and isValid(obj)}


def dispose_widgets(widgets) -> None:
    """Drain work, then destroy only the explicitly supplied owner trees.

    Ordinary close/hide intentionally permits reuse. This is the final ownership
    boundary, not a replacement for a workspace's closeEvent. In particular,
    PlotWidget.close() must not detach plots from the native tree being deleted.
    """
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from pyqtgraph import GraphicsLayoutWidget, GraphicsView

    app = QApplication.instance()
    owners = tuple(widget for widget in widgets if isValid(widget))
    views = {view for owner in owners
             for view in (owner, *owner.findChildren(GraphicsView))
             if isinstance(view, GraphicsView)}
    # A custom closeEvent may clear a scene. Capture its separate ownership
    # first, but do not destroy anything until the owner's work has finished.
    auxiliaries = _plot_auxiliaries(views)
    pools = set()
    worker_owners = []
    for owner in owners:
        if isinstance(owner, GraphicsView):
            QWidget.close(owner)
        else:
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
    views.update(view for owner in owners if isValid(owner)
                 for view in (owner, *owner.findChildren(GraphicsView))
                 if isinstance(view, GraphicsView))
    auxiliaries.update(_plot_auxiliaries(views))
    if app is not None and auxiliaries:
        # A caller can supply a menu shared with another live plot. Reference
        # only this owner's objects; never sweep all application top levels.
        other_views = {view for view in app.allWidgets()
                       if isinstance(view, GraphicsView) and view not in views}
        auxiliaries.difference_update(_plot_auxiliaries(other_views))
    # An explicitly parented auxiliary already has an owner. Respect external
    # ownership; the supplied native trees will destroy their own children.
    protected_dependencies = set()
    for obj in auxiliaries:
        if not isValid(obj) or QObject.parent(obj) is None:
            continue
        parent = QObject.parent(obj)
        while parent is not None and parent not in owners:
            parent = QObject.parent(parent)
        if parent is None:
            protected_dependencies.update(getattr(obj, "widgetGroups", ()))
            protected_dependencies.update(_parameter_tree(obj))
    # Parameter.parent() is a logical tree link, not Qt native ownership.
    auxiliaries = {obj for obj in auxiliaries if isValid(obj)
                   and QObject.parent(obj) is None and obj not in protected_dependencies}
    for owner in owners:
        if not isValid(owner):
            continue
        # Busy workspaces can decline the first close until queued completion
        # callbacks run. Never delete an owner that still reports live work.
        closed = QWidget.close(owner) if isinstance(owner, GraphicsView) else owner.close()
        if not closed:
            raise RuntimeError("Owned workspace still refuses to close")
        for canvas in owner.findChildren(FigureCanvasQTAgg):
            canvas.figure.clear()
        layouts = [owner] if isinstance(owner, GraphicsLayoutWidget) else []
        layouts.extend(owner.findChildren(GraphicsLayoutWidget))
        for graphics in layouts:
            if isValid(graphics) and not graphics.closed:
                graphics.close()
        owner.deleteLater()
    # Queue these after their scenes, whose destruction updates other live
    # ViewBox menus. Retained Python wrappers must not keep native menus alive.
    for obj in auxiliaries:
        if isValid(obj):
            obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    if app is not None:
        app.processEvents()
    if any(isValid(owner) for owner in owners):
        raise RuntimeError("Owned workspace was not destroyed")
    if any(isValid(obj) for obj in auxiliaries):
        raise RuntimeError("Owned plot auxiliary was not destroyed")
