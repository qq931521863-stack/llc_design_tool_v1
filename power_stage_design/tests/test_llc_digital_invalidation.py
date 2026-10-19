"""Actual LLC input/worker/export identity, including late Qt completions."""
from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import pytest

from llc_design.tests.conftest import desktop_qapp  # noqa: F401 -- shared session-scoped Qt fixture

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_WINDOWS = []


@pytest.fixture
def desktop(monkeypatch, request):
    from pyqtgraph import GraphicsLayoutWidget
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QMessageBox

    from llc_design.gui import main_window
    from llc_design.gui.widgets import digital_loop_view

    app = request.getfixturevalue("desktop_qapp")
    monkeypatch.setattr(main_window.LLCMainWindow, "_auto_check_update", lambda self: None)
    issues = []
    for module in (main_window, digital_loop_view):
        monkeypatch.setattr(module, "show_operation_issue", lambda *a, **kw: issues.append(a[1:]))
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: issues.append(a[1:]))
    yield app
    # Keep complete main windows alive until pyqtgraph has released its scene;
    # failed assertions must not leave teardown order to Python's exit GC.
    for window in _WINDOWS:
        for graphics in window.findChildren(GraphicsLayoutWidget):
            if not graphics.closed:
                graphics.close()
        window.close()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _WINDOWS.clear()
    app.processEvents()
    assert not issues, issues


def _analyze(view):
    from llc_design.control.analysis import build_small_signal_analysis
    from llc_design.control.digital_loop import build_digital_loop_analysis
    from llc_design.core.spec import LLCDesignSpec

    options = view._analysis_options()
    small = build_small_signal_analysis(LLCDesignSpec(), **options["small_signal"])
    return build_digital_loop_analysis(small, **options["loop"])


def _window():
    from llc_design.gui.main_window import LLCMainWindow

    window = LLCMainWindow()
    _WINDOWS.append(window)
    pending = []
    # Preserve the real FunctionWorker, signals, host completion and analysis;
    # control only dispatch time so a queued result can deterministically arrive late.
    window.thread_pool = SimpleNamespace(start=pending.append)
    return window, pending


@pytest.mark.parametrize("field", ["kp", "sample_us", "computation_us", "rup_k", "soc_count",
                                   "lut_text", "include_zoh", "controller_kind"])
def test_llc_input_edit_blocks_old_exports_immediately(desktop, monkeypatch, field):
    from PySide6.QtWidgets import QFileDialog

    from llc_design.gui.widgets.digital_loop_view import DigitalLoopView

    view = DigitalLoopView()
    view.set_analysis(_analyze(view))
    widget = getattr(view, field)
    if field == "lut_text":
        widget.setPlainText(widget.toPlainText() + "\n")
    elif field == "include_zoh":
        widget.setChecked(not widget.isChecked())
    elif field == "controller_kind":
        widget.setCurrentIndex(1)
    else:
        widget.setValue(widget.value() + (1 if field == "soc_count" else .001))
    assert view.result is None
    assert view.research_export.analysis is None
    assert not view.codegen_button.isEnabled()
    assert not view.research_export.python_button.isEnabled()
    assert not view.text.toPlainText() and not view.loop_chain_text.toPlainText()
    assert not view.figure.axes
    dialogs = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: dialogs.append(a) or "")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (dialogs.append(a) or "", ""))
    view._generate_c99()
    view.research_export._export("python")
    view.set_busy(False)
    assert not view.codegen_button.isEnabled() and not dialogs
    view.close()


def test_llc_busy_and_programmatic_pcmd_write_preserve_current_identity(desktop, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from llc_design.gui.widgets.digital_loop_view import DigitalLoopView

    view = DigitalLoopView()
    result = _analyze(view)
    view.pcmd.setValue(0.123)
    view.set_analysis(result)
    assert view.result is result and view.research_export.analysis is result
    assert view.pcmd.value() == pytest.approx(result.fm_operating_point.command_pu, abs=5e-7)
    view.plot_group.setCurrentIndex(2)
    view.parameter_selector.setCurrentIndex(3)
    view.inspector_toggle.setChecked(False)
    assert view.result is result
    dialogs = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: dialogs.append(a) or "")
    view.set_busy(True)
    view._generate_c99()
    assert not dialogs and not view.codegen_button.isEnabled()
    view.set_busy(False)
    assert view.codegen_button.isEnabled() and view.result is result
    view.close()


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("edit", ["kp", "external_same_fs", "external_to_local", "plant"])
def test_llc_late_worker_never_reinstalls_previous_controller(desktop, installed, edit):
    from llc_design.gui.solution_map_install import install_llc_solution_map
    from power_control_tools.models import DigitalTransferFunction

    window, pending = _window()
    view = window.digital_loop_view
    if installed:
        install_llc_solution_map(window)
    old_external = DigitalTransferFunction((.02, -.019), (1., -1.), 50_000.)
    new_external = DigitalTransferFunction((.03, -.028), (1., -1.), 50_000.)
    if edit.startswith("external"):
        window.set_external_control_design(old_external, "old exact")
    view.request_analysis()
    worker = pending.pop()
    old_result = worker.function()
    if edit == "kp":
        view.kp.setValue(.023)
    elif edit == "external_same_fs":
        window.set_external_control_design(new_external, "new exact")
    elif edit == "external_to_local":
        view.external_controller_check.setChecked(False)
        assert window.external_control_design is None
    else:
        window.fields["pout_w"].setValue(window.fields["pout_w"].value() * .9)
    worker.signals.result.emit(old_result)
    worker.signals.finished.emit()
    assert window.digital_loop_analysis is None and view.result is None
    assert not view.codegen_button.isEnabled() and view.research_export.analysis is None
    view.request_analysis()
    pending.pop().run()
    fresh = window.digital_loop_analysis
    assert fresh is view.result and fresh is not None
    assert view.research_export.analysis is fresh and view.codegen_button.isEnabled()
    if edit == "external_same_fs":
        np.testing.assert_array_equal(fresh.controller.numerator, new_external.b)
        assert window.external_control_design == new_external
    elif edit == "external_to_local":
        assert fresh.controller_config is not None and window.external_control_design is None
    elif edit == "kp":
        assert fresh.controller_config.kp == .023
    else:
        assert fresh.small_signal.spec.pout_w == window.fields["pout_w"].value()
    window.close()


def test_llc_local_edit_does_not_discard_unrelated_physical_worker(desktop):
    window, pending = _window()
    accepted = []
    marker = object()
    window._run_worker("physical-only result", lambda: marker, accepted.append)
    window.digital_loop_view.kp.setValue(.02)
    pending.pop().run()
    assert accepted == [marker]
    window.close()


@pytest.mark.parametrize("installed", [False, True])
def test_llc_late_error_is_silent_and_does_not_release_new_worker_busy_state(desktop, installed):
    from llc_design.gui.solution_map_install import install_llc_solution_map

    window, pending = _window()
    if installed:
        install_llc_solution_map(window)
    view = window.digital_loop_view
    view.request_analysis()
    old = pending.pop()
    view.kp.setValue(.023)
    view.request_analysis()
    current = pending.pop()
    old.signals.error.emit("Obsolete digital failure must not reach the new design")
    old.signals.finished.emit()
    assert view._busy and not view.run_button.isEnabled()
    assert window._active_workers == [current]
    current.run()
    assert not window._active_workers and not view._busy
    assert view.result is not None and view.result.controller_config.kp == .023
    assert view.codegen_button.isEnabled()
    window.close()


def test_llc_local_edit_does_not_suppress_unrelated_physical_error(desktop, monkeypatch):
    window, pending = _window()
    errors = []
    monkeypatch.setattr(window, "_worker_error", errors.append)
    window._run_worker("physical-only error", lambda: None, lambda result: None)
    window.digital_loop_view.kp.setValue(.02)
    worker = pending.pop()
    worker.signals.error.emit("current physical failure")
    worker.signals.finished.emit()
    assert errors == ["current physical failure"]
    assert not window._active_workers and not window.digital_loop_view._busy
    window.close()


def test_llc_digital_only_completed_result_is_invalidated_by_physical_input(desktop):
    window, _pending = _window()
    view = window.digital_loop_view
    # A maintained-model result can be installed without first running the
    # full system tab; this is the early-return branch's regression case.
    window._digital_loop_ready(_analyze(view))
    assert window.system_analysis is None and view.result is not None
    window.fields["pout_w"].setValue(window.fields["pout_w"].value() * .9)
    assert window.digital_loop_analysis is None and view.result is None
    assert window.small_signal_analysis is None
    assert view.research_export.analysis is None and not view.codegen_button.isEnabled()
    window.close()


def test_llc_direct_session_restore_rejects_late_error_and_recomputes_exact_controller(desktop):
    from llc_design.gui.design_session_install import validate_map_state
    from llc_design.gui.solution_map_install import install_llc_solution_map

    window, pending = _window()
    install_llc_solution_map(window)
    view = window.digital_loop_view
    view.request_analysis()
    pending.pop().run()
    exact = view.result.controller
    document = view._session_capture()
    prepared = validate_map_state(view.solution_map_view, document["solution_map"], "llc")
    view.request_analysis()
    old = pending.pop()
    view._session_restore(document, prepared)
    old.signals.error.emit("Obsolete pre-restore failure must not reach the reopened design")
    old.signals.finished.emit()
    assert view.result is None and not view.codegen_button.isEnabled()
    assert not window._active_workers and not view._busy
    view.request_analysis()
    pending.pop().run()
    np.testing.assert_array_equal(view.result.controller.numerator, exact.numerator)
    np.testing.assert_array_equal(view.result.controller.denominator, exact.denominator)
    assert view.result.controller.sample_time_s == exact.sample_time_s
    assert view.codegen_button.isEnabled() and view.research_export.analysis is view.result
    window.close()


def test_llc_edit_inside_code_generation_dialog_cannot_export_obsolete_result(desktop, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    from llc_design.gui.widgets.digital_loop_view import DigitalLoopView

    view = DigitalLoopView()
    view.set_analysis(_analyze(view))

    def edit_while_dialog_open(*args):
        view.kp.setValue(.023)
        return str(tmp_path)

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", edit_while_dialog_open)
    view._generate_c99()
    assert not list(tmp_path.iterdir())
    view.close()


def test_llc_session_reload_rejects_late_worker_then_replays_exact_hz(desktop, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    from llc_design.gui.solution_map_install import install_llc_solution_map
    from power_control_tools.models import DigitalTransferFunction

    window, pending = _window()
    install_llc_solution_map(window)
    view = window.digital_loop_view
    exact = DigitalTransferFunction((.010123456789, -.008123456789), (1., -1.), 61543.21)
    window.set_external_control_design(exact, "exact session")
    view.request_analysis()
    pending.pop().run()
    path = tmp_path / "exact-llc.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(path), ""))
    window.save_json()
    view.request_analysis()
    old_worker = pending.pop()
    old_result = old_worker.function()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    window.load_json()
    old_worker.signals.result.emit(old_result)
    old_worker.signals.finished.emit()
    assert window.digital_loop_analysis is None and view.result is None
    assert view.research_export.analysis is None and not view.codegen_button.isEnabled()
    view.request_analysis()
    pending.pop().run()
    fresh = window.digital_loop_analysis
    assert fresh is not None and fresh is view.result
    np.testing.assert_array_equal(fresh.controller.numerator, exact.b)
    np.testing.assert_array_equal(fresh.controller.denominator, exact.a)
    assert fresh.controller.sample_time_s == 1 / exact.sample_rate_hz
    assert view.research_export.analysis is fresh
    # Project restoration now also recomputes its input-only waveform session.
    # A completed digital job must not release the remaining real worker's busy
    # ownership, and that waveform completion must not replace exact H(z).
    assert len(pending) == 1 and not view.codegen_button.isEnabled()
    waveform_worker = pending.pop()
    assert callable(getattr(waveform_worker.function, "_llc_waveform_current", None))
    waveform_worker.run()
    assert not window._active_workers and window.waveform_view.bundle is not None
    assert window.digital_loop_analysis is fresh and view.result is fresh
    np.testing.assert_array_equal(fresh.controller.numerator, exact.b)
    np.testing.assert_array_equal(fresh.controller.denominator, exact.a)
    assert view.research_export.analysis is fresh and view.codegen_button.isEnabled()
    window.close()


@pytest.mark.parametrize("late_event", ["result", "error"])
def test_llc_closed_loop_payload_cannot_restore_obsolete_hz(desktop, monkeypatch, late_event):
    from llc_design.gui import closed_loop_install as bridge
    from llc_design.gui.solution_map_install import install_llc_solution_map
    from power_control_tools.models import DigitalTransferFunction

    window, pending = _window()
    install_llc_solution_map(window)
    view = window.digital_loop_view
    exact = DigitalTransferFunction((.02, -.019), (1., -1.), 50_000.)
    window.set_external_control_design(exact, "old simulation H(z)")
    old_loop = _analyze(view)
    window._digital_loop_ready(old_loop)
    # Only execution availability and the completed simulation payload are
    # controlled. Actual signal dispatch, host ownership and worker lifetimes
    # are exercised; this test makes no new ngspice numerical-validity claim.
    monkeypatch.setattr(bridge, "find_ngspice_shared_library", lambda: "/controlled/libngspice.so")
    simulation = bridge.install_closed_loop_verification(window)
    displayed = []
    monkeypatch.setattr(simulation, "set_result", lambda bundle, control: displayed.append(bundle))

    def payload(loop):
        control = SimpleNamespace(samples=[], metadata={"sample_rate_hz": 50_000.})
        return (SimpleNamespace(control=control), object(), loop,
                SimpleNamespace(bus_voltage_v=400., load_fraction=1.))

    simulation.analysis_requested.emit({})
    old = pending.pop()
    new_exact = DigitalTransferFunction((.03, -.028), (1., -1.), 50_000.)
    window.set_external_control_design(new_exact, "new simulation H(z)")
    if late_event == "result":
        old.signals.result.emit(payload(old_loop))
    else:
        old.signals.error.emit("obsolete simulation error")
    old.signals.finished.emit()
    assert window.digital_loop_analysis is None and not displayed
    assert not window._active_workers and not view._busy
    simulation.analysis_requested.emit({})
    current = pending.pop()
    fresh = _analyze(view)
    fresh_payload = payload(fresh)
    current.signals.result.emit(fresh_payload)
    current.signals.finished.emit()
    assert window.digital_loop_analysis is fresh
    np.testing.assert_array_equal(fresh.controller.numerator, new_exact.b)
    assert displayed == [fresh_payload[1]]
    assert not window._active_workers and not view._busy
    window.close()
