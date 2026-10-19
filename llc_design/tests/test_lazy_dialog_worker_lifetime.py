"""Lazy dialogs and PFC jobs must have bounded native and Python lifetimes."""
from __future__ import annotations

import gc
import os
import weakref
from threading import Event, Thread
from time import monotonic, sleep

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _settle(app):
    from PySide6.QtCore import QCoreApplication, QEvent

    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    app.processEvents()


def _wait_until(app, predicate):
    deadline = monotonic() + 10
    while not predicate():
        assert monotonic() < deadline, "PFC worker completion timed out"
        app.processEvents()
        sleep(0.001)


@pytest.fixture
def pfc_window(desktop_qapp, monkeypatch):
    from pfc_design.gui import main_window

    monkeypatch.setattr(main_window, "check_for_updates", lambda *args, **kwargs: None)
    return main_window.PFCMainWindow()


def test_catalog_reuses_one_dialog_after_close_cancel_and_apply(desktop_qapp):
    from PySide6.QtCore import QObject
    from shiboken6 import isValid

    from llc_design.gui.lifetime import dispose_widgets
    from llc_design.gui.widgets.magnetic_catalog_view import MagneticCatalogDialog
    from llc_design.gui.widgets.transformer_design_view import TransformerDesignView

    owner = TransformerDesignView()
    selected = []
    owner._select_catalog_preset = selected.append
    owner._open_catalog()
    dialog = owner.catalog_dialog
    native_children = dialog.findChildren(QObject)
    dialog_ref = weakref.ref(dialog)
    for dismiss in (dialog.close, dialog.reject, dialog.accept):
        owner._open_catalog()  # A second click must not allocate another dialog.
        assert owner.catalog_dialog is dialog
        assert dialog.isVisible()
        dialog.validation.frequency.setValue(123456)
        dismiss()
        _settle(desktop_qapp)
        assert owner.findChildren(MagneticCatalogDialog) == [dialog]
        assert isValid(dialog) and not dialog.isVisible()
        owner._open_catalog()
        assert owner.catalog_dialog is dialog
        assert dialog.validation.frequency.value() == 123456
        dialog.ready.selectRow(0)
        key = dialog.keys[0]
        before = len(selected)
        dialog._apply()
        assert selected[before:] == [key]
        assert not dialog.isVisible()
    dispose_widgets([owner])
    assert not isValid(owner) and not isValid(dialog)
    assert all(not isValid(child) for child in native_children)
    # Retained wrappers cannot retain native children, and dropping the owner
    # releases the single reusable dialog's Python wrapper as well.
    del owner, dialog, dismiss
    _settle(desktop_qapp)
    assert dialog_ref() is None


@pytest.mark.parametrize("fail", (False, True))
def test_completed_pfc_jobs_release_workers_and_native_signals(
    desktop_qapp, pfc_window, monkeypatch, fail
):
    from shiboken6 import isValid

    accepted, errors, worker_refs, signals = [], [], [], []
    monkeypatch.setattr(pfc_window, "_worker_error", errors.append)

    def calculate():
        if fail:
            raise ValueError("intentional lightweight worker failure")
        return 7

    for _ in range(3):
        pfc_window._run_worker("lightweight lifecycle probe", calculate, accepted.append)
        worker = pfc_window._active_workers[-1]
        worker_refs.append(weakref.ref(worker))
        signals.append(worker.signals)
        del worker
        _wait_until(desktop_qapp, lambda: not pfc_window._active_workers)
        _wait_until(desktop_qapp, lambda: all(ref() is None or not isValid(ref()) for ref in worker_refs))
        _settle(desktop_qapp)
        assert all(not isValid(signal) for signal in signals)
        assert all(ref() is None for ref in worker_refs)
        assert pfc_window.progress.isHidden()
    assert accepted == ([] if fail else [7, 7, 7])
    assert len(errors) == (3 if fail else 0)
    assert all("intentional lightweight worker failure" in error for error in errors)


@pytest.mark.parametrize("fail_old", (False, True))
def test_late_old_pfc_completion_cannot_overwrite_new_request(
    desktop_qapp, pfc_window, monkeypatch, fail_old
):
    from PySide6.QtCore import QThreadPool
    from shiboken6 import isValid

    # Use a real pool with explicit concurrency even on one-core test hosts.
    pfc_window.thread_pool = QThreadPool(pfc_window)
    pfc_window.thread_pool.setMaxThreadCount(2)
    started, release = Event(), Event()
    accepted, errors = [], []
    monkeypatch.setattr(pfc_window, "_worker_error", errors.append)

    def old_calculation():
        started.set()
        assert release.wait(10)
        if fail_old:
            raise ValueError("stale worker failure")
        return "old"

    pfc_window._run_worker("old", old_calculation, accepted.append)
    old = pfc_window._active_workers[-1]
    try:
        assert started.wait(5)
        pfc_window._run_worker("new", lambda: "new", accepted.append)
        new = pfc_window._active_workers[-1]
        _wait_until(desktop_qapp, lambda: new not in pfc_window._active_workers)
        _settle(desktop_qapp)
        assert accepted == ["new"] and not errors
        assert pfc_window.progress.isHidden()
        assert isValid(old) and isValid(old.signals)
        assert not isValid(new) and not isValid(new.signals)
        # The finished latest request must not make an older running worker
        # disappear from the close guard or delete its still-live signals.
        assert not pfc_window.close()
        assert old in pfc_window._active_workers and isValid(old.signals)
    finally:
        release.set()
        _wait_until(desktop_qapp, lambda: not pfc_window._active_workers)
    _settle(desktop_qapp)
    assert accepted == ["new"] and not errors
    assert not isValid(old.signals) and not isValid(new.signals)
    assert pfc_window.close()
    pfc_window.show()
    pfc_window._run_worker("reopened", lambda: "reopened", accepted.append)
    _wait_until(desktop_qapp, lambda: not pfc_window._active_workers)
    _settle(desktop_qapp)
    assert accepted == ["new", "reopened"]


def test_already_queued_pfc_callbacks_after_finish_are_ignored(
    desktop_qapp, pfc_window, monkeypatch
):
    from shiboken6 import isValid

    pending, accepted, errors = [], [], []

    class RecordingPool:
        def start(self, worker):
            pending.append(worker)

    pfc_window.thread_pool = RecordingPool()
    monkeypatch.setattr(pfc_window, "_worker_error", errors.append)
    pfc_window._run_worker("queued callback probe", lambda: None, accepted.append)
    worker = pending.pop()

    def queue_deliveries():
        worker.signals.result.emit("accepted")
        worker.signals.finished.emit()
        # Disconnect does not cancel events already queued to the GUI thread.
        worker.signals.result.emit("late result")
        worker.signals.error.emit("late error")
        worker.signals.finished.emit()

    delivery = Thread(target=queue_deliveries)
    delivery.start()
    delivery.join(timeout=5)
    assert not delivery.is_alive()
    _wait_until(desktop_qapp, lambda: not pfc_window._active_workers)
    _settle(desktop_qapp)
    assert accepted == ["accepted"] and not errors
    assert not isValid(worker.signals)


def test_pfc_queued_callbacks_ignore_a_destroyed_native_owner(desktop_qapp):
    from PySide6.QtCore import QCoreApplication, QEvent, QObject
    from shiboken6 import isValid

    from pfc_design.gui.main_window import PFCMainWindow

    class CallbackOwner(QObject):
        # Exercise the exact production closures on a native QObject without
        # needing to forcibly destroy an unrelated graphics scene tree.
        _run_worker = PFCMainWindow._run_worker
        _worker_finished = PFCMainWindow._worker_finished

        def __init__(self):
            super().__init__()
            self._active_workers = []
            self._worker_request_id = 0
            self.thread_pool = self
            self.busy_calls = []
            self.errors = []

        def start(self, worker):
            self.worker = worker

        def set_busy(self, *args):
            self.busy_calls.append(args)

        def _worker_error(self, error):
            self.errors.append(error)

    owner = CallbackOwner()
    accepted = []
    owner._run_worker("destroyed receiver", lambda: None, accepted.append)
    worker = owner.worker
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(owner, QEvent.Type.DeferredDelete)
    assert not isValid(owner) and isValid(worker.signals)

    def queue_deliveries():
        worker.signals.result.emit("late result")
        worker.signals.error.emit("late error")
        worker.signals.finished.emit()

    delivery = Thread(target=queue_deliveries)
    delivery.start()
    delivery.join(timeout=5)
    assert not delivery.is_alive()
    _wait_until(desktop_qapp, lambda: not owner._active_workers)
    _settle(desktop_qapp)
    assert not accepted and not owner.errors
    assert owner.busy_calls == [(True, "destroyed receiver")]
    assert not isValid(worker.signals)


def test_pfc_pool_releases_runnable_only_after_run_returns(
    desktop_qapp, pfc_window, monkeypatch
):
    from shiboken6 import isValid

    from llc_design.gui.lifetime import dispose_widgets
    from llc_design.gui.workers import FunctionWorker
    from pfc_design.gui import main_window

    release_return = Event()
    accepted = []

    class PausedReturnWorker(FunctionWorker):
        def run(self):
            super().run()
            assert release_return.wait(10)

    monkeypatch.setattr(main_window, "FunctionWorker", PausedReturnWorker)
    pfc_window._run_worker("paused return", lambda: "result", accepted.append)
    worker = pfc_window._active_workers[-1]
    try:
        _wait_until(desktop_qapp, lambda: not pfc_window._active_workers)
        _settle(desktop_qapp)
        assert accepted == ["result"]
        assert isValid(worker) and not isValid(worker.signals)
        dispose_widgets([pfc_window])
        assert not isValid(pfc_window) and isValid(worker)
    finally:
        release_return.set()
        _wait_until(desktop_qapp, lambda: not isValid(worker))


def test_pfc_owner_disposal_drains_owned_job_and_preserves_unrelated_global_job(
    desktop_qapp, pfc_window
):
    from PySide6.QtCore import QThreadPool, QTimer
    from shiboken6 import isValid

    from llc_design.gui.lifetime import dispose_widgets
    from llc_design.gui.workers import FunctionWorker

    owned_started, owned_release = Event(), Event()
    unrelated_started, unrelated_release, unrelated_finished = Event(), Event(), Event()
    accepted = []

    def owned_calculation():
        owned_started.set()
        assert owned_release.wait(10)
        return "owned result"

    def unrelated_calculation():
        unrelated_started.set()
        assert unrelated_release.wait(10)
        unrelated_finished.set()

    # Explicit separate pools guarantee overlap even on one-core test hosts.
    pfc_window.thread_pool = QThreadPool(pfc_window)
    pfc_window._run_worker("owned", owned_calculation, accepted.append)
    owned = pfc_window._active_workers[-1]
    unrelated = FunctionWorker(unrelated_calculation)
    unrelated.setAutoDelete(True)
    global_pool = QThreadPool.globalInstance()
    unrelated.start(global_pool)
    try:
        assert owned_started.wait(5) and unrelated_started.wait(5)
        assert not pfc_window.close()
        assert isValid(pfc_window) and isValid(owned.signals)
        QTimer.singleShot(20, owned_release.set)
        dispose_widgets([pfc_window])
        _settle(desktop_qapp)
        assert accepted == ["owned result"]
        assert not isValid(pfc_window) and not isValid(owned.signals)
        assert not unrelated_finished.is_set()
        assert global_pool.activeThreadCount() > 0 and isValid(unrelated.signals)
    finally:
        owned_release.set()
        unrelated_release.set()
        assert global_pool.waitForDone(5000)
        unrelated.signals.deleteLater()
        _settle(desktop_qapp)
    assert unrelated_finished.is_set()
