"""Keep desktop workflow tests deterministic and offline."""
from __future__ import annotations

import pytest

_DESKTOP_MODULES = {
    "test_candidate_apply_integration.py", "test_system_modeling_gui.py",
    "test_solution_map_gui.py", "test_device_library_gui.py",
    "test_closed_loop_verification_gui.py", "test_help.py", "test_i18n.py",
    "test_controller_candidate_gui.py",
}


def _ignore_update_check(*args, **kwargs):
    pass


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


@pytest.fixture(autouse=True)
def isolate_desktop_background_updates(request, monkeypatch):
    # These tests instantiate windows, not the updater. Its own regression
    # module remains untouched, including its explicit mocked network tests.
    if request.node.path.name not in _DESKTOP_MODULES:
        yield
        return
    app = request.getfixturevalue("desktop_qapp")
    from PySide6.QtWidgets import QMainWindow

    from llc_design.gui import main_window as llc_window_module
    from pfc_design.gui import main_window as pfc_window_module
    # Leave the real QTimer bound methods intact. Replacing them can leave a
    # pending native connection to a restored/deleted Python callback. Only
    # the update-check network boundary needs to be disabled for these tests.
    monkeypatch.setattr(llc_window_module, "check_for_updates", _ignore_update_check)
    monkeypatch.setattr(pfc_window_module, "check_for_updates", _ignore_update_check)
    existing = {window for window in app.topLevelWidgets()
                if isinstance(window, QMainWindow)}
    try:
        yield
    finally:
        # close() preserves windows for reuse within a test. Release only this
        # test's new owners here, not other suites' existing windows or Qt's
        # internal top-level menus/viewports.
        windows = [window for window in app.topLevelWidgets()
                   if isinstance(window, QMainWindow) and window not in existing]
        from llc_design.gui.lifetime import dispose_widgets
        dispose_widgets(windows)
