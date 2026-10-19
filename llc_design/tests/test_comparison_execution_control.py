"""Real solvers and real Qt event-loop cancellation, never qWait polling."""
from __future__ import annotations

import math
import os
import threading
import time

import pytest

from llc_design.analysis.execution import SolveCancelled, SolveControl, SolveTimedOut
from llc_design.analysis.golden import GoldenSolverConfig, LLCGoldenSolver
from llc_design.analysis.harmonic_balance import HarmonicBalanceConfig
from llc_design.analysis.time_domain import TimeDomainConfig, solve_time_domain
from llc_design.analysis.types import FidelityLevel, LLCAnalysisRequest
from llc_design.tests.test_integrated_magnetics import reference_spec

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
qt = pytest.importorskip("PySide6.QtWidgets")


def test_execution_control_first_stop_and_budget_validation():
    for value in (0, -1, math.inf, math.nan):
        with pytest.raises(ValueError):
            SolveControl(value)
    control = SolveControl(300)
    control.cancel("inputs changed")
    control.deadline = 0
    with pytest.raises(SolveCancelled, match="inputs changed"):
        control.checkpoint()
    timeout = SolveControl(300)
    timeout.deadline = 0
    with pytest.raises(SolveTimedOut):
        timeout.checkpoint()
    timeout.cancel()
    assert timeout.outcome == "timed_out"


def test_hb_cancellation_escapes_actual_residual_without_fha_fallback():
    control = SolveControl(300)
    calls = 0

    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 100:
            control.cancel("residual probe")
        control.checkpoint()

    solver = LLCGoldenSolver(GoldenSolverConfig(include_time_domain=False,
        harmonic_balance=HarmonicBalanceConfig(max_harmonic=7, samples_per_cycle=1024)))
    with pytest.raises(SolveCancelled, match="residual probe"):
        solver.solve(LLCAnalysisRequest(reference_spec()), checkpoint=checkpoint)
    assert calls == 100


def test_td_cancellation_escapes_real_rk4_chunk(monkeypatch):
    from llc_design.dynamics import switched

    control = SolveControl(300)
    actual = switched._rk4_step
    calls = 0

    def observed(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 20:
            control.cancel("TD integration probe")
        return actual(*args, **kwargs)

    monkeypatch.setattr(switched, "_rk4_step", observed)
    with pytest.raises(SolveCancelled, match="TD integration probe"):
        solve_time_domain(LLCAnalysisRequest(reference_spec(), frequency_hz=100e3),
                          TimeDomainConfig(), checkpoint=control.checkpoint)
    assert 20 <= calls <= 32


@pytest.fixture(scope="module")
def app():
    return qt.QApplication.instance() or qt.QApplication([])


def until(predicate, seconds=30):
    """Release the GIL in the actual Qt event loop; qWait starves Python workers."""
    from PySide6.QtCore import QEventLoop, QTimer

    if predicate():
        return
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(5)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    poll.start()
    deadline.start(int(seconds * 1000))
    loop.exec()
    poll.stop()
    deadline.stop()
    poll.deleteLater()
    deadline.deleteLater()
    assert predicate(), f"condition did not finish within {seconds}s"


@pytest.fixture
def window(app, monkeypatch):
    from shiboken6 import isValid

    import llc_design.gui.main_window as module
    from llc_design.gui.lifetime import dispose_widgets

    monkeypatch.setattr(module, "check_for_updates", lambda *args, **kwargs: None)
    window = module.LLCMainWindow()
    window._load_reachable_integrated_example()
    window.show()
    window.test_errors = []
    monkeypatch.setattr(window, "_worker_error", window.test_errors.append)
    try:
        yield window
    finally:
        window._cancel_model_comparison("test teardown")
        until(lambda: not window._active_workers, 10)
        dispose_widgets([window])
        assert not isValid(window)


def start(window, *, td=False):
    view = window.model_comparison_view
    view.include_td.setChecked(td)
    assert view.max_harmonic.value() == 7
    assert view.hb_samples.value() == 1024
    view.run_button.click()
    assert window._active_workers and view.cancel_button.isEnabled()


def assert_stopped(window, text):
    view = window.model_comparison_view
    assert not window._active_workers and window._comparison_control is None
    assert window.multi_fidelity_analysis is None and not window._results_current
    assert view.analysis is None and view.table.rowCount() == 0
    assert not view.loss_report_button.isEnabled()
    assert view.run_button.isEnabled() and not view.cancel_button.isEnabled()
    assert text in view.details.toPlainText()
    assert text in window.statusBar().currentMessage()
    assert not window.test_errors
    window._export_td_loss_report()  # No accepted authority means no export job.
    assert not window._active_workers


def test_default_run_cancel_and_default_retry(window):
    from PySide6.QtCore import QTimer

    start(window)
    QTimer.singleShot(100, window.model_comparison_view.cancel_button.click)
    began = time.monotonic()
    until(lambda: not window._active_workers, 5)
    assert time.monotonic() - began < 5
    assert_stopped(window, "已取消")
    start(window)
    until(lambda: not window._active_workers)
    assert not window.test_errors and window._results_current
    result = window.multi_fidelity_analysis
    assert result is not None
    hb = result.result(FidelityLevel.HARMONIC_BALANCE)
    assert hb.convergence.converged and hb.convergence.residual_norm < 2e-6
    assert window.model_comparison_view.table.rowCount() == 2


def test_explicit_budget_reports_timeout_not_partial_fha(window):
    window.model_comparison_view.execution_budget_s.setValue(.1)
    start(window)
    until(lambda: not window._active_workers, 5)
    assert_stopped(window, "计算超时")


def test_global_input_change_cancels_and_discards_worker(window):
    from PySide6.QtCore import QTimer

    start(window)
    QTimer.singleShot(100, lambda: window.fields["integrated_mutual_h"].setValue(39.99))
    until(lambda: not window._active_workers, 5)
    assert_stopped(window, "输入已改变")


def test_comparison_page_input_change_cancels_through_invalidation(window):
    from PySide6.QtCore import QTimer

    start(window)
    view = window.model_comparison_view
    assert not view.vbus.isEnabled()  # User edits are blocked while busy.
    # Programmatic changes must also cancel via the existing invalidation chain.
    QTimer.singleShot(100, lambda: view.vbus.setValue(390))
    until(lambda: not window._active_workers, 5)
    assert_stopped(window, "输入已改变")


def test_close_cancels_then_native_owner_can_be_reopened(window):
    from PySide6.QtCore import QTimer

    start(window)
    QTimer.singleShot(100, window.close)
    until(lambda: not window._active_workers, 5)
    assert window._closing and not window._results_current
    assert window.multi_fidelity_analysis is None
    window.show()
    assert not window._closing and window.model_comparison_view.run_button.isEnabled()
    assert window.model_comparison_view.analysis is None


def test_cancel_after_actual_td_integration_starts(window, monkeypatch):
    from PySide6.QtCore import QTimer

    from llc_design.dynamics import switched

    entered = threading.Event()
    actual = switched._rk4_step

    def observed(*args, **kwargs):
        entered.set()
        return actual(*args, **kwargs)

    monkeypatch.setattr(switched, "_rk4_step", observed)
    poll = QTimer(window)
    poll.setInterval(5)

    def cancel_in_td():
        if entered.is_set():
            poll.stop()
            window.model_comparison_view.cancel_button.click()

    poll.timeout.connect(cancel_in_td)
    poll.start()
    start(window, td=True)
    until(lambda: not window._active_workers)
    poll.stop()
    assert entered.is_set()
    assert_stopped(window, "已取消")


def test_cancel_then_real_comparison_and_waveform_export(window, tmp_path):
    import json

    import numpy as np
    import pandas as pd
    from PySide6.QtCore import QTimer

    from llc_design.analysis import export_multifidelity_analysis

    start(window)
    cancelled_control = window._comparison_control
    QTimer.singleShot(100, window.model_comparison_view.cancel_button.click)
    until(lambda: not window._active_workers, 5)
    assert_stopped(window, "已取消")
    window.model_comparison_view.vbus.setValue(390)
    start(window)
    until(lambda: not window._active_workers)
    assert not window.test_errors and window._results_current
    result = window.multi_fidelity_analysis
    assert result is window.model_comparison_view.analysis
    assert result.request.spec == window._spec_from_widgets()
    assert result.request.bus_voltage_v == 390
    assert cancelled_control.outcome == "cancelled"
    hb = result.result(FidelityLevel.HARMONIC_BALANCE)
    assert hb.convergence.converged
    paths = export_multifidelity_analysis(result, tmp_path)
    document = json.loads(paths["summary_json"].read_text(encoding="utf-8"))
    assert document["request"]["vbus_v"] == 390
    assert document["models"][FidelityLevel.HARMONIC_BALANCE.value]["metrics"] == hb.metrics.as_dict()
    assert "390 V" in paths["report"].read_text(encoding="utf-8")
    for level, model in result.results.items():
        frame = pd.read_csv(paths[f"{level.value}_waveforms"])
        np.testing.assert_allclose(frame["i_resonant"], model.waveform.signals["i_resonant"].values,
                                   rtol=1e-12, atol=1e-12)
        metadata = json.loads(paths[f"{level.value}_metadata"].read_text(encoding="utf-8"))
        assert metadata["model_name"] == model.waveform.model_name
        assert metadata["metadata"] == dict(model.waveform.metadata)
        assert metadata["warnings"] == list(model.waveform.warnings)
        assert paths[f"{level.value}_plot_primary_tank"].stat().st_size > 10000
