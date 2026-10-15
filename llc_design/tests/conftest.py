"""Keep desktop workflow tests deterministic and offline."""
from __future__ import annotations

import pytest

_DESKTOP_MODULES = {
    "test_candidate_apply_integration.py", "test_system_modeling_gui.py",
    "test_solution_map_gui.py", "test_device_library_gui.py",
    "test_closed_loop_verification_gui.py", "test_help.py", "test_i18n.py",
    "test_controller_candidate_gui.py",
}


@pytest.fixture(scope="session")
def desktop_qapp():
    qt = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
    from pyqtgraph import GraphicsLayoutWidget
    from shiboken6 import isValid
    app = qt.QApplication.instance() or qt.QApplication([])
    yield app
    assert QThreadPool.globalInstance().waitForDone(15000)
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
        if isValid(window):
            window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    # Other desktop suites share this QApplication; do not invalidate their
    # live Python wrappers with PySide's explicit C++ singleton destruction.
    app.quit()


@pytest.fixture(autouse=True)
def isolate_desktop_background_updates(request, monkeypatch):
    # These tests instantiate windows, not the updater. Its own regression
    # module remains untouched, including its explicit mocked network tests.
    if request.node.path.name not in _DESKTOP_MODULES:
        yield
        return
    request.getfixturevalue("desktop_qapp")
    from llc_design.gui.main_window import LLCMainWindow
    from pfc_design.gui.main_window import PFCMainWindow
    monkeypatch.setattr(LLCMainWindow, "_auto_check_update", lambda self: None)
    monkeypatch.setattr(PFCMainWindow, "_auto_check_update", lambda self: None)
    yield
