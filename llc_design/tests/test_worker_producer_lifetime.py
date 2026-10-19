"""Every real worker producer releases jobs; cancellation preserves live work."""
from __future__ import annotations

import gc
import os
import weakref
from threading import Event, Timer
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_KINDS = (
    "llc", "pfc", "dab", "dab_control", "three_phase", "pll",
    "psd_stage", "psd_parameters", "psd_loop", "psd_corners", "psd_save", "psd_open",
    "toolbox", "toolbox_restore",
)
_FAILURE = "intentional worker lifetime failure"


def _settle(app):
    from PySide6.QtCore import QCoreApplication, QEvent

    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    app.processEvents()


def _wait(app, predicate):
    deadline = monotonic() + 10
    while not predicate():
        assert monotonic() < deadline, "Worker lifecycle did not settle"
        app.processEvents()
        sleep(0.001)


@pytest.fixture
def producer(request, desktop_qapp, monkeypatch, tmp_path):
    """Replace computation/rendering only; keep production launch/delivery code."""
    from shiboken6 import isValid

    from llc_design.gui.lifetime import dispose_widgets

    kind = request.param
    state = SimpleNamespace(fail=False, release=None, started=Event(), calls=0)
    accepted, errors = [], []

    def compute(*args, **kwargs):
        state.calls += 1
        state.started.set()
        if state.release is not None:
            assert state.release.wait(10)
        if state.fail:
            raise RuntimeError(_FAILURE)

    if kind in {"llc", "pfc"}:
        if kind == "llc":
            from llc_design.gui import main_window as module
            cls = module.LLCMainWindow
        else:
            from pfc_design.gui import main_window as module
            cls = module.PFCMainWindow
        monkeypatch.setattr(module, "check_for_updates", lambda *args, **kwargs: None)
        owner = cls()
        monkeypatch.setattr(owner, "_worker_error", errors.append)
        start = lambda: owner._run_worker("bounded lifetime test", compute, accepted.append)
        registry = lambda: owner._active_workers
    elif kind == "dab":
        from dab_design.gui import main_window as module
        owner = module.DABMainWindow()
        monkeypatch.setattr(module, "analyze_dab", compute)
        monkeypatch.setattr(owner, "set_result", accepted.append)
        monkeypatch.setattr(owner, "_error", errors.append)
        start = owner.run_analysis
        registry = lambda: [owner._worker] if owner._worker is not None else []
    elif kind == "dab_control":
        from dab_design.gui import control_view as module
        owner = module.DABControlView(lambda: None)
        monkeypatch.setattr(owner, "_display", accepted.append)
        start = lambda: owner._start(compute)
        registry = lambda: [owner._worker] if owner._worker is not None else []
    elif kind == "three_phase":
        from pfc_design.gui import three_phase_view as module
        owner = module.ThreePhaseDQView()
        monkeypatch.setattr(module, "simulate_three_phase", compute)
        monkeypatch.setattr(owner, "set_result", accepted.append)
        monkeypatch.setattr(owner, "_error", errors.append)
        start = owner.run_analysis
        registry = lambda: [owner._worker] if owner._worker is not None else []
    elif kind == "pll":
        from pfc_design.gui import pll_comparison_view as module
        owner = module.PLLComparisonView()
        monkeypatch.setattr(module, "run_comparison", compute)
        monkeypatch.setattr(owner, "set_result", accepted.append)
        start = owner.run
        registry = lambda: [owner._worker] if owner._worker is not None else []
    elif kind.startswith("psd_"):
        from power_stage_design import gui as module
        owner = module.PowerStageMainWindow()
        owner.calculate()  # Cheap real default buck establishes valid UI inputs.
        assert owner.result is not None
        operation = kind.removeprefix("psd_")
        if operation == "stage":
            owner.topology_combo.setCurrentIndex(owner.topology_combo.findData("T04"))
            owner._timer.stop()
            monkeypatch.setattr(module, "calculate_stage", compute)
            start, ready = owner.calculate, "_on_stage_calculated"
        elif operation == "parameters":
            monkeypatch.setattr(module, "suggest_parameters", compute)
            start, ready = owner.propose_parameters, "_on_parameter_proposal"
        elif operation == "loop":
            from power_stage_design import loop
            monkeypatch.setattr(loop, "synthesize_compensator", compute)
            start, ready = owner.synthesize_loop, "_on_synthesis"
        elif operation == "corners":
            from power_stage_design import corners
            monkeypatch.setattr(corners, "analyze_corners", compute)
            start, ready = owner.run_corners, "_on_corners"
        elif operation == "save":
            monkeypatch.setattr(module, "project_document", compute)
            start = lambda: owner._start_project_save(tmp_path / "lifetime.json")
            ready = "_on_project_saved"
        else:
            monkeypatch.setattr(module, "read_project", compute)
            start = lambda: owner._start_project_open(tmp_path / "lifetime.json")
            ready = "_on_project_opened"
        monkeypatch.setattr(owner, ready, accepted.append)
        registry = lambda: list(owner._jobs.values())
    else:
        from power_stage_design import toolbox_gui as module
        owner = module.ToolBoxWidget()
        owner.select_tool("X09")
        bits = owner.pages["X09"].widgets["bits"]
        bits.setCurrentIndex(bits.findData(13))
        owner._auto_timer.stop()
        if kind == "toolbox":
            spec = module.TOOLS["X09"]
            monkeypatch.setitem(module.TOOLS, "X09", (*spec[:3], compute))
            monkeypatch.setattr(owner, "_apply_result", accepted.append)
            start = owner.calculate
        else:
            snapshot = owner.state()
            monkeypatch.setattr(owner, "_compute_restore", compute)
            monkeypatch.setattr(owner, "_adopt_restore", lambda prepared, result: accepted.append(result))
            start = lambda: owner.restore_state(snapshot, background=True)
        registry = lambda: owner._workers

    def error_text():
        text = " ".join(errors)
        for name in ("status", "loop_status", "corner_status", "suggestion_output"):
            widget = getattr(owner, name, None)
            if widget is not None:
                text += widget.text() if hasattr(widget, "text") else widget.toPlainText()
        return text

    result = SimpleNamespace(kind=kind, owner=owner, module=module, start=start, registry=registry,
                             state=state, accepted=accepted, errors=errors, error_text=error_text)
    try:
        yield result
    finally:
        if state.release is not None:
            state.release.set()
        if isValid(owner):
            dispose_widgets([owner])


@pytest.mark.parametrize("producer", _KINDS, indirect=True)
@pytest.mark.parametrize("fail", (False, True))
def test_all_producers_release_completed_workers_and_signals(desktop_qapp, producer, fail):
    from shiboken6 import isValid

    producer.state.fail = fail
    workers, signals = [], []
    for _ in range(3):
        producer.start()
        worker = producer.registry()[-1]
        workers.append(weakref.ref(worker))
        signals.append(worker.signals)
        del worker
        _wait(desktop_qapp, lambda: not producer.registry())
        _wait(desktop_qapp, lambda: all(ref() is None or not isValid(ref()) for ref in workers))
        _settle(desktop_qapp)
        assert all(not isValid(signal) for signal in signals)
        def collected():
            _settle(desktop_qapp)
            return all(ref() is None for ref in workers)
        _wait(desktop_qapp, collected)
    assert producer.state.calls == 3
    assert len(producer.accepted) == (0 if fail else 3)
    if fail:
        assert _FAILURE in producer.error_text()
    else:
        assert not producer.errors


@pytest.mark.parametrize("producer", _KINDS, indirect=True)
def test_all_producers_reject_queued_post_finish_deliveries(desktop_qapp, producer, monkeypatch):
    from shiboken6 import isValid

    from llc_design.gui.workers import FunctionWorker

    emitted = Event()

    class LateWorker(FunctionWorker):
        def run(self):
            try:
                super().run()
                self.signals.result.emit("late payload must not reach the renderer")
                self.signals.error.emit("late error must not replace the status")
                self.signals.finished.emit()
            finally:
                emitted.set()

    monkeypatch.setattr(producer.module, "FunctionWorker", LateWorker)
    producer.start()
    worker = producer.registry()[-1]
    # Keep the GUI queue paused until every deliberately late event is queued.
    assert emitted.wait(5)
    _wait(desktop_qapp, lambda: not producer.registry())
    _settle(desktop_qapp)
    assert len(producer.accepted) == 1
    assert "late error" not in producer.error_text() and not producer.errors
    assert not isValid(worker) and not isValid(worker.signals)


@pytest.mark.parametrize("producer", _KINDS, indirect=True)
def test_all_producers_finish_across_close_and_reopen(desktop_qapp, producer):
    from shiboken6 import isValid

    producer.state.release = Event()
    producer.start()
    worker = producer.registry()[-1]
    assert producer.state.started.wait(5)
    assert isValid(worker) and isValid(worker.signals)
    # Dedicated-pool close handlers may wait without processing GUI events.
    release = Timer(0.02, producer.state.release.set)
    release.start()
    try:
        producer.owner.close()
        _wait(desktop_qapp, lambda: not producer.registry())
    finally:
        producer.state.release.set()
        release.join(timeout=5)
    _settle(desktop_qapp)
    assert not isValid(worker) and not isValid(worker.signals)
    assert producer.owner.close()
    producer.owner.show()
    producer.state.release = None
    before = len(producer.accepted)
    producer.start()
    _wait(desktop_qapp, lambda: not producer.registry())
    _settle(desktop_qapp)
    assert len(producer.accepted) == before + 1


@pytest.mark.parametrize("producer", _KINDS, indirect=True)
def test_final_owner_disposal_preserves_unrelated_global_job(desktop_qapp, producer):
    from PySide6.QtCore import QThreadPool
    from shiboken6 import isValid

    from llc_design.gui.lifetime import dispose_widgets
    from llc_design.gui.workers import FunctionWorker

    producer.start()
    owned = producer.registry()[-1]
    # Leave owned result/finished callbacks queued while its runnable returns.
    # An unrelated global job can then start even on a one-core test host.
    deadline = monotonic() + 10
    while isValid(owned):
        assert monotonic() < deadline
        sleep(0.001)
    started, release, returned = Event(), Event(), Event()

    def unrelated_calculation():
        started.set()
        assert release.wait(10)
        returned.set()

    unrelated = FunctionWorker(unrelated_calculation)
    pool = QThreadPool.globalInstance()
    pool.start(unrelated)
    try:
        assert started.wait(5)
        dispose_widgets([producer.owner])
        _settle(desktop_qapp)
        assert not producer.registry()
        assert not isValid(producer.owner) and not isValid(owned.signals)
        assert not returned.is_set()
        assert isValid(unrelated) and isValid(unrelated.signals)
        assert pool.activeThreadCount() > 0
    finally:
        release.set()
        _wait(desktop_qapp, lambda: not isValid(unrelated))
        unrelated.signals.deleteLater()
        _settle(desktop_qapp)
    assert returned.is_set()


@pytest.mark.parametrize("producer", ("toolbox", "toolbox_restore"), indirect=True)
def test_toolbox_cancels_queued_jobs_without_touching_running_job(desktop_qapp, producer):
    from shiboken6 import isValid

    producer.state.release = Event()
    producer.start()
    running = producer.registry()[-1]
    assert producer.state.started.wait(5)
    producer.start()
    queued = producer.registry()[-1]
    queued_ref = weakref.ref(queued)
    queued_signals = queued.signals
    assert queued is not running and isValid(queued)
    producer.owner.cancel_pending()
    assert producer.registry() == [running]
    assert isValid(running) and isValid(running.signals)
    assert not isValid(queued)
    del queued
    _settle(desktop_qapp)
    assert not isValid(queued_signals) and queued_ref() is None
    assert producer.state.calls == 1 and not producer.accepted
    producer.state.release.set()
    _wait(desktop_qapp, lambda: not producer.registry())
    _settle(desktop_qapp)
    assert not producer.accepted
    assert not isValid(running) and not isValid(running.signals)


@pytest.mark.parametrize("producer", ("toolbox", "toolbox_restore"), indirect=True)
def test_toolbox_cancel_handles_finished_runnable_before_queued_completion(desktop_qapp, producer):
    from shiboken6 import isValid

    producer.start()
    worker = producer.registry()[-1]
    # Waiting only for this private pool makes the native runnable dead while
    # its GUI finished callback is still queued: the former tryTake ABA edge.
    assert producer.owner._pool.waitForDone(5000)
    assert not isValid(worker) and isValid(worker.signals)
    producer.owner.cancel_pending()
    assert not producer.registry()
    _settle(desktop_qapp)
    assert not producer.accepted and not isValid(worker.signals)
