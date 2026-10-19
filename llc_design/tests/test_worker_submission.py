"""Fast auto-deleting runnables must outlive the pool's wrapper handoff."""
from __future__ import annotations

import gc
import os
import weakref
from threading import Event
from time import monotonic, sleep

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _settle(app):
    from PySide6.QtCore import QCoreApplication, QEvent

    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    app.processEvents()


def _wait(app, predicate):
    deadline = monotonic() + 10
    while not predicate():
        assert monotonic() < deadline, "Worker submission did not settle"
        _settle(app)
        sleep(0.001)


@pytest.mark.parametrize("fail", (False, True))
def test_submission_holds_native_worker_until_pool_wrapper_handoff(
    desktop_qapp, monkeypatch, fail
):
    from PySide6.QtCore import QThreadPool
    from shiboken6 import isValid

    from llc_design.gui import workers

    waiting, computed = Event(), Event()
    accepted, errors, finished = [], [], []
    real_pool = QThreadPool()

    class ObservedEvent:
        def __init__(self):
            self.event = Event()

        def wait(self):
            waiting.set()
            self.event.wait()

        def set(self):
            self.event.set()

    class HandoffPool:
        def start(self, worker):
            real_pool.start(worker)
            # Hold submission open after the real native launch. No sleep:
            # the runnable must have reached the explicit submission gate.
            assert waiting.wait(5)
            assert not computed.is_set()
            assert isValid(worker) and isValid(worker.signals)
            assert worker.autoDelete()

    def compute():
        computed.set()
        if fail:
            raise ValueError("intentional submission failure")
        return 7

    monkeypatch.setattr(workers, "Event", ObservedEvent)
    worker = workers.FunctionWorker(compute)
    signals = worker.signals
    signals.result.connect(accepted.append)
    signals.error.connect(errors.append)
    signals.finished.connect(lambda: finished.append(True))
    worker.start(HandoffPool())
    ref = weakref.ref(worker)
    del worker
    _wait(desktop_qapp, lambda: bool(finished))
    _wait(desktop_qapp, lambda: ref() is None or not isValid(ref()))
    _wait(desktop_qapp, lambda: ref() is None)
    assert accepted == ([] if fail else [7])
    assert len(errors) == (1 if fail else 0)
    if fail:
        assert "intentional submission failure" in errors[0]
    assert finished == [True]
    signals.deleteLater()
    _settle(desktop_qapp)
    assert not isValid(signals)
    assert real_pool.waitForDone(5000)


def test_repeated_submission_is_rejected_before_touching_pool(desktop_qapp):
    from llc_design.gui.workers import FunctionWorker

    submissions = []

    class RecordingPool:
        def start(self, worker):
            submissions.append(worker)

    worker = FunctionWorker(lambda: None)
    pool = RecordingPool()
    worker.start(pool)
    with pytest.raises(RuntimeError, match="already been submitted"):
        worker.start(pool)
    assert submissions == [worker]
    assert worker._submission_ready.is_set()
    worker.signals.deleteLater()
    _settle(desktop_qapp)


@pytest.mark.parametrize("launch", (False, True))
def test_rejected_submission_releases_gate_and_stays_one_shot(desktop_qapp, launch):
    from PySide6.QtCore import QThreadPool
    from shiboken6 import isValid

    from llc_design.gui.workers import FunctionWorker

    gates, computed = [], Event()
    pool = QThreadPool()

    class RejectingPool:
        def start(self, worker):
            gates.append(worker._submission_ready)
            if launch:
                pool.start(worker)
            raise RuntimeError("submission wrapper rejected")

    worker = FunctionWorker(computed.set)
    signals = worker.signals
    with pytest.raises(RuntimeError, match="submission wrapper rejected"):
        worker.start(RejectingPool())
    assert gates[0].is_set()
    assert worker._submission_ready is gates[0]
    # An exception cannot tell us whether native submission already happened.
    with pytest.raises(RuntimeError, match="already been submitted"):
        worker.start(pool)
    ref = weakref.ref(worker)
    if launch:
        _wait(desktop_qapp, lambda: ref() is None or not isValid(ref()))
        assert computed.is_set()
    else:
        assert not computed.is_set() and isValid(worker)
    del worker
    _wait(desktop_qapp, lambda: ref() is None)
    assert pool.waitForDone(5000)
    signals.deleteLater()
    _settle(desktop_qapp)
    assert not isValid(signals)


def test_queued_submission_can_be_cleared_without_running(desktop_qapp):
    from PySide6.QtCore import QThreadPool
    from shiboken6 import isValid

    from llc_design.gui.workers import FunctionWorker

    pool = QThreadPool()
    pool.setMaxThreadCount(1)
    started, release, computed = Event(), Event(), Event()

    def block():
        started.set()
        assert release.wait(10)

    running = FunctionWorker(block)
    running.start(pool)
    try:
        assert started.wait(5)
        queued = FunctionWorker(computed.set)
        signals = queued.signals
        queued.start(pool)
        assert queued._submission_ready.is_set()
        pool.clear()
        assert not isValid(queued) and isValid(running)
        assert not computed.is_set()
        ref = weakref.ref(queued)
        del queued
        _wait(desktop_qapp, lambda: ref() is None)
        signals.deleteLater()
    finally:
        release.set()
        assert pool.waitForDone(5000)
        running.signals.deleteLater()
        _settle(desktop_qapp)
    assert not isValid(running) and not isValid(signals)


@pytest.mark.parametrize("fail", (False, True))
def test_direct_synchronous_run_needs_no_submission_gate(desktop_qapp, fail):
    from llc_design.gui.workers import FunctionWorker

    accepted, errors, finished = [], [], []

    def compute():
        if fail:
            raise ValueError("intentional synchronous failure")
        return 9

    worker = FunctionWorker(compute)
    worker.signals.result.connect(accepted.append)
    worker.signals.error.connect(errors.append)
    worker.signals.finished.connect(lambda: finished.append(True))
    worker.run()
    assert worker._submission_ready is None
    assert accepted == ([] if fail else [9])
    assert len(errors) == (1 if fail else 0)
    assert finished == [True]
    worker.signals.deleteLater()
    _settle(desktop_qapp)
