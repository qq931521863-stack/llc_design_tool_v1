"""Keep desktop workflow tests deterministic and offline."""
from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def desktop_qapp():
    qt = pytest.importorskip("PySide6.QtWidgets")
    from pyqtgraph import GraphicsLayoutWidget
    from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
    from shiboken6 import isValid
    app = qt.QApplication.instance() or qt.QApplication([])
    yield app
    assert QThreadPool.globalInstance().waitForDone(15000)
    from llc_design.gui.updater import wait_for_update_checks
    wait_for_update_checks()
    app.closeAllWindows()
    # Release Python-backed scenes/viewports before Qt's interpreter-exit
    # cleanup can destroy their wrappers in a different order.
    windows = app.topLevelWidgets()
    for window in windows:
        if isValid(window):
            for graphics in window.findChildren(GraphicsLayoutWidget):
                if isValid(graphics) and not graphics.closed:
                    graphics.close()
    for window in windows:
        # Qt owns internal top-level menus/viewports through their scene/window.
        # Deleting those wrappers independently can race their owner teardown.
        if isValid(window) and isinstance(window, qt.QMainWindow):
            window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    # Other desktop suites share this QApplication; do not invalidate their
    # live Python wrappers with PySide's explicit C++ singleton destruction.
    app.quit()
