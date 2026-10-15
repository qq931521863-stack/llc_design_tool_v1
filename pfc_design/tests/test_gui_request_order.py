"""Out-of-order delivery must not change accepted results or current busy state."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.mark.parametrize("old_finishes_first", (False, True))
def test_latest_pfc_request_owns_results_errors_and_busy(
    monkeypatch, old_finishes_first
):
    qt = pytest.importorskip("PySide6.QtWidgets")
    from pfc_design.gui.main_window import PFCMainWindow

    monkeypatch.setattr(PFCMainWindow, "_auto_check_update", lambda self: None)
    app = qt.QApplication.instance() or qt.QApplication([])
    window = PFCMainWindow()
    pending = []
    accepted = []
    errors = []

    class Pool:
        def start(self, worker):
            pending.append(worker)

    window.thread_pool = Pool()
    monkeypatch.setattr(window, "_worker_error", errors.append)
    window._run_worker("old", lambda: None, accepted.append)
    window._run_worker("new", lambda: None, accepted.append)
    old, new = pending
    if old_finishes_first:
        old.signals.error.emit("stale failure")
        old.signals.finished.emit()
        assert not window.progress.isHidden()
    new.signals.result.emit("new result")
    window.statusBar().showMessage("new completion summary")
    new.signals.finished.emit()
    assert window.progress.isHidden()
    assert window.statusBar().currentMessage() == "new completion summary"
    old.signals.result.emit("old result")
    old.signals.error.emit("stale failure")
    if not old_finishes_first:
        old.signals.finished.emit()
    assert accepted == ["new result"] and not errors
    assert not window._active_workers
    window.close()
    app.processEvents()
