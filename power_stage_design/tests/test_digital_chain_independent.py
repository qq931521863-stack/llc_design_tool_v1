"""A05 consumer-chain checks using causal state evolution and compiled C.

Expected responses never call production's polynomial composition/SOS helpers.
The first-order plant oracle advances its continuous state exactly over each
held input interval; this detects losing a causal sample during composition.
"""
from __future__ import annotations

import math
import os

import numpy as np
import pytest
from scipy import signal

from power_control_tools.analysis import analyze_digital_filter
from power_control_tools.codegen import export_c99_filter, verify_c99_filter
from power_control_tools.fra.analysis import digital_frequency_response
from power_control_tools.fra.fitting import RationalPlantModel
from power_control_tools.fra.loop_link import closed_loop_step_from_plant_and_controller
from power_control_tools.models import DigitalTransferFunction
from power_sim.digital_control import DigitalTransferRuntime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.mark.parametrize("delay", [0, 1, 2, 3, 5])
def test_exact_delay_reaches_analysis_runtime_sos_and_compiled_c(tmp_path, delay):
    controller = DigitalTransferFunction((0.0,) * delay + (1.0,), (1.0, -0.5), 40_000.0)
    expected = np.zeros(32)
    expected[delay:] = 0.5 ** np.arange(32 - delay)
    analysis = analyze_digital_filter(controller, response_samples=32)
    runtime = DigitalTransferRuntime(controller)
    executed = [runtime.step(float(k == 0)).output for k in range(32)]
    np.testing.assert_allclose(analysis.impulse, expected, atol=1e-14)
    np.testing.assert_allclose(executed, expected, atol=1e-14)
    np.testing.assert_allclose(signal.sosfilt(controller.sos, np.eye(1, 32)[0]), expected, atol=1e-14)
    frequency = np.geomspace(1, 18_000, 101)
    expected_frd = np.exp(-2j * np.pi * frequency * delay / 40_000) / (
        1 - 0.5 * np.exp(-2j * np.pi * frequency / 40_000)
    )
    np.testing.assert_allclose(digital_frequency_response(controller, frequency), expected_frd, atol=1e-14)
    exported = export_c99_filter(controller, tmp_path / "exact.h", prefix="PSD_EXACT")
    verified = verify_c99_filter(controller, exported, samples=32)
    assert verified.available, "A host compiler is required for this export acceptance test"
    assert verified.passed, verified


@pytest.mark.parametrize("controller_delay", [0, 1, 2])
@pytest.mark.parametrize("extra_delay", [0, 1, 3])
def test_fra_step_matches_continuous_state_with_sampled_controller(controller_delay, extra_delay):
    fs, pole_hz, dc_gain, controller_gain = 40_000.0, 300.0, 2.0, 0.5
    plant = RationalPlantModel((dc_gain,), (pole_hz,), (), reference_frequency_hz=500.0)
    controller = DigitalTransferFunction((0.0,) * controller_delay + (controller_gain,), (1.0,), fs)
    actual = closed_loop_step_from_plant_and_controller(
        plant, controller, samples=200, extra_delay_samples=extra_delay,
    )
    # x[k] is measured before applying this tick's held command. The next
    # state follows the analytic constant-input solution of dx/dt=-w*x+w*G*u.
    state = 0.0
    decay = math.exp(-2 * math.pi * pole_hz / fs)
    commands = [0.0] * (controller_delay + extra_delay)
    expected = []
    for _ in range(200):
        expected.append(state)
        commands.append(controller_gain * (1.0 - state))
        applied = commands.pop(0)
        state = decay * state + dc_gain * (1.0 - decay) * applied
    assert actual.stable
    np.testing.assert_allclose(actual.response, expected, rtol=1e-11, atol=1e-12)
    assert actual.response[0] == 0.0
    assert actual.final_value == pytest.approx(0.5)


def test_fra_edit_immediately_invalidates_exact_export(monkeypatch):
    qt = pytest.importorskip("PySide6.QtWidgets")
    from power_control_tools.fra.models import FRAMeasurement, FRASourceFormat
    from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow

    app = qt.QApplication.instance() or qt.QApplication([])
    window = FRALoopDesignerWindow()
    monkeypatch.setattr(window, "_render", lambda *args: None)
    monkeypatch.setattr(window, "_render_step", lambda *args: [])
    f = np.geomspace(2.0, 10_000.0, 101)
    plant = 2.0 / (1.0 + 1j * f / 300.0)
    window.measurement = FRAMeasurement(
        f, 20 * np.log10(abs(plant)), np.angle(plant, deg=True), FRASourceFormat.GENERIC,
    )
    window.recalculate()
    assert window.current_new_controller is not None
    window.output_text.setPlainText("previous coefficient output")
    window.new_kp.setValue(window.new_kp.value() * 1.5)
    assert window.current_new_controller is None
    assert not window.export_button.isEnabled()
    assert not window.output_text.toPlainText()
    monkeypatch.setattr(qt.QMessageBox, "information", lambda *a: None)
    monkeypatch.setattr(qt.QFileDialog, "getSaveFileName", lambda *a: pytest.fail("Stale export reached file chooser"))
    window.export_c99()
    window.timer.stop()
    window.close()
    app.processEvents()


@pytest.mark.parametrize("source", ["plant", "complete_loop", "identified"])
def test_fra_desktop_save_restore_recompute_and_export_same_exact_controller(tmp_path, monkeypatch, source):
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    from power_control_tools.controllers import EXACT_CONTROLLER
    from power_control_tools.fra.loop_session import read_loop_session
    from power_control_tools.fra.models import (
        FRAMeasurement,
        FRAMeasurementKind,
        FRASourceFormat,
    )
    from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow

    app = QApplication.instance() or QApplication([])
    window = FRALoopDesignerWindow()
    f = np.geomspace(1.0, 15_000.0, 121)
    plant = 2.0 / (1 + 1j * f / 300.0)
    old = DigitalTransferFunction((0.0, 0.25, -0.125), (1.0, -0.75), 40_000.0, "bench exact", "measured firmware")
    new = DigitalTransferFunction((0.0, 0.3, -0.11), (1.0, -0.65), 40_000.0)
    _, old_frd = signal.freqz(old.b, old.a, worN=2 * np.pi * f / old.sample_rate_hz)
    measured = plant * old_frd if source == "complete_loop" else plant
    window.measurement = FRAMeasurement(
        f, 20 * np.log10(abs(measured)), np.angle(measured, deg=True), FRASourceFormat.GENERIC,
        "independent-synthetic.csv", metadata={"operating_point": "400 V, 500 W; synthetic software fixture"},
    )
    if source == "complete_loop":
        window.measurement_kind.setCurrentIndex(window.measurement_kind.findData(FRAMeasurementKind.COMPLETE_LOOP))
        window.old_b.setText(",".join(map(repr, old.b)))
        window.old_a.setText(",".join(map(repr, old.a)))
    if source == "identified":
        window.set_identified_plant(RationalPlantModel((2.0,), (300.0,), (), 500.0), (1.0, 15_000.0), "GOOD")
    window.new_mode.setCurrentIndex(window.new_mode.findData("structure"))
    window.new_kind.setCurrentIndex(window.new_kind.findData(EXACT_CONTROLLER))
    window.new_exact_b.setText(",".join(map(repr, new.b)))
    window.new_exact_a.setText(",".join(map(repr, new.a)))
    window.step_enable.setChecked(False)
    window.recalculate()
    assert window.current_new_controller is not None, window.summary.toPlainText()
    original = window.current_new_controller
    original_old = window.current_old_controller
    original_loop = window.current_loop.copy()
    _, new_frd = signal.freqz(new.b, new.a, worN=2 * np.pi * window.current_frequency_hz / new.sample_rate_hz)
    expected_plant = 2.0 / (1 + 1j * window.current_frequency_hz / 300.0)
    np.testing.assert_allclose(original_loop, expected_plant * new_frd, atol=1e-12)
    path = tmp_path / "fra-exact.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(path), "JSON (*.json)"))
    window.save_session()
    saved = read_loop_session(path)
    assert saved.controller == original
    assert saved.old_controller == original_old
    assert "operating_point" in saved.measurement.metadata
    window.new_exact_b.setText("9.0")
    assert window.current_new_controller is None
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), "JSON (*.json)"))
    window.open_session()
    assert not window.timer.isActive()
    assert window.current_new_controller == original
    assert window.source_format.currentData() == FRASourceFormat.GENERIC
    assert window.current_old_controller == original_old
    np.testing.assert_array_equal(window.current_loop, original_loop)
    assert window.export_action.isEnabled()
    header = tmp_path / "restored.h"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(header), "C99 Header (*.h)"))
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: messages.append(a[-1]))
    window.export_c99()
    assert header.exists()
    assert messages and "PASS" in messages[-1]
    # Failed typed input must clear all prior analysis and coefficient displays.
    window.new_exact_a.setText("0")
    window.recalculate()
    assert window.current_loop is None and window.current_metrics is None
    assert not window.output_text.toPlainText()
    assert not window.export_action.isEnabled()
    window.close()
    app.processEvents()


def _fra_session_document():
    from power_control_tools.fra.loop_session import FRALoopSession
    from power_control_tools.fra.models import FRAMeasurementKind
    return FRALoopSession(
        DigitalTransferFunction((0.0, 0.2), (1.0, -0.5), 40_000.0), None, None,
        FRAMeasurementKind.PLANT, 0.0, "identified",
        RationalPlantModel((2.0,), (300.0,), (), 500.0), (1.0, 15_000.0), "GOOD",
    ).to_document()


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(model_revision="unrecognized"),
    lambda d: d["controller"].update(a=[0.0]),
    lambda d: d["controller"].update(b=[float("nan")]),
    lambda d: d["controller"].update(sample_rate_hz=40000.123),
    lambda d: d["controller"].update(b=[1.0] * 34),
    lambda d: d["settings"].update(step_samples=1_000_000),
    lambda d: d["identified"].update(band_hz=[100.0, 1.0]),
    lambda d: d.update(plant_source="measured"),
])
def test_fra_session_rejects_incompatible_or_silently_rounded_inputs(tmp_path, mutation):
    import json

    from power_control_tools.fra.loop_session import read_loop_session
    data = _fra_session_document()
    mutation(data)
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        read_loop_session(path)


def test_fra_session_failed_atomic_save_preserves_previous_file(tmp_path, monkeypatch):
    from pathlib import Path

    from power_control_tools.fra.loop_session import FRALoopSession, write_loop_session
    path = tmp_path / "design.json"
    path.write_text("previous valid bytes", encoding="utf-8")
    session = FRALoopSession.from_document(_fra_session_document())

    def fail_replace(*args):
        raise OSError("independent disk-failure injection")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError):
        write_loop_session(session, path)
    assert path.read_text(encoding="utf-8") == "previous valid bytes"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("invalid", [0.5, 1.5, -1, True, float("nan")])
def test_fra_timing_rejects_silent_fractional_tick_truncation(invalid):
    from power_control_tools.fra.loop_link import (
        discretize_plant_model,
        link_plant_model_with_controller,
    )
    plant = RationalPlantModel((2.0,), (300.0,), (), 500.0)
    controller = DigitalTransferFunction((0.5,), (1.0,), 40_000.0)
    with pytest.raises(ValueError, match="non-negative integer"):
        discretize_plant_model(plant, 40_000.0, extra_delay_samples=invalid)
    with pytest.raises(ValueError, match="non-negative integer"):
        link_plant_model_with_controller(plant, controller, f_min_hz=1.0, f_max_hz=15000.0,
                                         include_step=False, extra_delay_samples=invalid)


def test_sos_does_not_discard_nonzero_small_feedthrough():
    controller = DigitalTransferFunction((1e-15, 1.0), (1.0, -0.5), 40_000.0)
    actual = signal.sosfilt(controller.sos, np.eye(1, 4)[0])
    assert actual[0] == pytest.approx(1e-15, rel=1e-12, abs=0)
    np.testing.assert_allclose(actual, [1e-15, 1 + 0.5e-15, 0.5 + 0.25e-15, 0.25 + 0.125e-15], rtol=1e-12)


def test_llc_bridge_keeps_frozen_ba_and_sampling_identity_after_save(tmp_path):
    from llc_design.control.analysis import build_small_signal_analysis
    from llc_design.control.design_session import (
        llc_inputs,
        make_session,
        read_session,
        replay_llc,
        write_session,
    )
    from llc_design.control.digital_loop import (
        ADCSamplingConfig,
        CommandTimingConfig,
        build_digital_loop_analysis,
    )
    from llc_design.core.spec import LLCDesignSpec

    controller = DigitalTransferFunction((0.0, 0.02, -0.013), (1.0, -0.6), 50_000.0, "frozen independent H(z)")
    small = build_small_signal_analysis(LLCDesignSpec(), sample_time_s=1/controller.sample_rate_hz)
    analysis = build_digital_loop_analysis(
        small, controller_transfer_function=controller,
        adc_sampling=ADCSamplingConfig(control_sample_time_s=20e-6),
        command_timing=CommandTimingConfig(computation_delay_s=1.234e-6, pwm_update_delay_s=2.345e-6),
        frequencies_hz=np.geomspace(1, 10000, 121),
    )
    path = write_session(make_session("llc", llc_inputs(analysis)), tmp_path / "llc-exact.json")
    restored = replay_llc(read_session(path)["inputs"])
    np.testing.assert_array_equal(restored.controller.numerator, controller.b)
    np.testing.assert_array_equal(restored.controller.denominator, controller.a)
    _, expected = signal.freqz(controller.b, controller.a, worN=2*np.pi*restored.frequencies_hz/controller.sample_rate_hz)
    np.testing.assert_allclose(restored.responses["controller"], expected, atol=1e-13)
    np.testing.assert_array_equal(restored.nominal_open_loop, analysis.nominal_open_loop)
    mismatched = DigitalTransferFunction(controller.b, controller.a, 40_000.0)
    with pytest.raises(ValueError, match="sample times must match"):
        build_digital_loop_analysis(small, controller_transfer_function=mismatched)


def test_pfc_handoff_runtime_and_manifest_use_same_coefficients(tmp_path):
    import json

    from pfc_design.control import (
        PFCControlLabConfig,
        build_pfc_control_handoff,
        build_pfc_control_lab_analysis,
    )
    from power_codegen import export_pfc_exact_hz_manifest

    analysis = build_pfc_control_lab_analysis(PFCControlLabConfig())
    handoff = build_pfc_control_handoff(analysis)
    path = export_pfc_exact_hz_manifest(analysis, tmp_path / "pfc-exact.json")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    impulse = np.eye(1, 100)[0]
    for name in ("current", "voltage"):
        loop = getattr(analysis, name + "_loop")
        artifact = getattr(handoff, name)
        stored = manifest["controllers"][name]
        np.testing.assert_array_equal(stored["b"], loop.controller.numerator)
        np.testing.assert_array_equal(stored["a"], loop.controller.denominator)
        assert stored["sample_rate_hz"] == artifact.runtime_transfer().sample_rate_hz
        runtime = DigitalTransferRuntime(artifact.runtime_transfer())
        response = [runtime.step(value).output for value in impulse]
        # SciPy is an independent recurrence implementation, not the project's
        # firmware-state algebra or handoff evaluator.
        expected = signal.lfilter(loop.controller.numerator, loop.controller.denominator, impulse)
        np.testing.assert_allclose(response, expected, rtol=1e-12, atol=1e-12)


def test_fra_cancel_and_invalid_session_load_leave_current_design_intact(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QFileDialog

    from power_control_tools.fra.loop_session import FRALoopSession
    from power_control_tools.gui import fra_loop_designer as gui

    app = QApplication.instance() or QApplication([])
    window = gui.FRALoopDesignerWindow()
    monkeypatch.setattr(window, "_render", lambda *args: None)
    monkeypatch.setattr(window, "_render_step", lambda *args: [])
    window._restore_session(FRALoopSession.from_document(_fra_session_document()))
    controller, loop = window.current_new_controller, window.current_loop.copy()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    window.open_session()
    assert window.current_new_controller == controller
    path = tmp_path / "wrong.json"
    path.write_text('{"schema": "unknown", "schema": "duplicate"}', encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    issues = []
    monkeypatch.setattr(gui, "show_operation_issue", lambda *args, **kwargs: issues.append(args[2]))
    window.open_session()
    assert issues and "duplicate" in issues[0]
    assert window.current_new_controller == controller
    np.testing.assert_array_equal(window.current_loop, loop)
    assert window.export_action.isEnabled()
    window.close()
    app.processEvents()


@pytest.mark.parametrize("b,a", [
    ((0.0, 0.0, 0.0), (2.0, -1.0)),
    ((0.0, 0.0, 4.0), (2.0, -1.0)),
    ((0.0, 0.0, 0.0, 0.2, -0.1, 0.02), (2.0, -2.0, 0.7, -0.1, 0.0048)),
    ((0.0, 0.0, 0.0, 0.0, 0.2, -0.1, 0.02), (2.0, -2.0, 0.7, -0.1, 0.0048)),
])
def test_sos_zero_non_normalized_and_multisection_coefficients_match_independent_scipy_and_c(tmp_path, b, a):
    controller = DigitalTransferFunction(b, a, 50_000.0)
    analysis = analyze_digital_filter(controller, response_samples=512)
    _, expected_frd = signal.freqz(b, a, worN=2*np.pi*analysis.frequency_hz/controller.sample_rate_hz)
    nonzero = abs(expected_frd) > 0
    np.testing.assert_allclose(analysis.magnitude_db[nonzero], 20*np.log10(abs(expected_frd[nonzero])), atol=1e-12)
    _, sos_frd = signal.sosfreqz(controller.sos, worN=2*np.pi*analysis.frequency_hz/controller.sample_rate_hz)
    np.testing.assert_allclose(sos_frd, expected_frd, rtol=1e-12, atol=1e-14)
    impulse = np.eye(1, 512)[0]
    np.testing.assert_allclose(signal.sosfilt(controller.sos, impulse), signal.lfilter(b, a, impulse), rtol=1e-11, atol=1e-14)
    assert np.isfinite(controller.sos).all()
    if len(a) > 2:
        assert len(controller.sos) >= 4  # multiple plant sections plus explicit delay sections
    verified = verify_c99_filter(controller, export_c99_filter(controller, tmp_path / "boundary.h"), samples=512)
    assert verified.available and verified.passed, verified


@pytest.mark.parametrize("kind", ["pi", "pif", "pid", "2p2z", "3p3z"])
def test_fra_auto_apply_preserves_selected_exact_controller_without_parameter_rebuild(tmp_path, monkeypatch, kind):
    from dataclasses import replace

    from PySide6.QtWidgets import QApplication, QMessageBox

    from power_control_tools.controllers import EXACT_CONTROLLER, design_controller
    from power_control_tools.discretize import discretize_transfer_function
    from power_control_tools.fra.analysis import LoopStabilityResult
    from power_control_tools.fra.auto_design import (
        AutoDesignCandidate,
        AutoDesignResult,
    )
    from power_control_tools.fra.loop_session import (
        FRALoopSession,
        read_loop_session,
        write_loop_session,
    )
    from power_control_tools.gui.fra_advanced import FRAAutoDesignDialog
    from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow
    from power_control_tools.models import ControllerKind

    app = QApplication.instance() or QApplication([])
    window = FRALoopDesignerWindow()
    monkeypatch.setattr(window, "recalculate", lambda: None)
    dialog = FRAAutoDesignDialog(window)
    kind = ControllerKind(kind)
    analog = design_controller(kind, kp=0.123456789012345, ti_s=0.001234567891234,
                               td_s=0.00001234567891234, lpf_pole_hz=1234.56789123,
                               gain=0.1234567890123, fz1_hz=123.456789, fz2_hz=789.123456,
                               fz3_hz=1234.56789, fp1_hz=5678.123456, fp2_hz=12345.6789, fp3_hz=18000.1234)
    exact = discretize_transfer_function(analog, 50_000.0)
    # This is an application-boundary fixture, not a claim that the synthetic
    # candidate satisfies a physical plant. The real auto-design tests remain.
    metrics = LoopStabilityResult((), (), 3000.0, 60.0, 10.0, 60.0, 10.0, 1.2, 1.1,
                                  np.array([1+0j]), np.array([0.5+0j]), "PASS")
    candidate = AutoDesignCandidate(3000.0, 3000.0, 60.0, 10.0, 1.2, 1.1, exact, kind,
                                    {"kp": 0.123456789012345, "ti_s": 0.001234567891234,
                                     "td_s": 0.00001234567891234, "lpf_pole_hz": 1234.56789123},
                                    metrics, True, 0.0, "identity test")
    dialog.result = AutoDesignResult(3000.0, 60.0, candidate, (candidate,), False, "PASS", "identity test")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    dialog.apply_result()
    assert window.new_kind.currentData() == EXACT_CONTROLLER
    applied = window._new_structure_controller()
    assert applied == exact.normalized()
    session = replace(FRALoopSession.from_document(_fra_session_document()), controller=applied)
    path = write_loop_session(session, tmp_path / "auto-exact.json")
    restored = read_loop_session(path).controller
    assert restored == exact.normalized()
    exported = export_c99_filter(restored, tmp_path / "auto-exact.h")
    verified = verify_c99_filter(restored, exported, samples=512)
    assert verified.available
    # Independent float32 SOS evaluation consumes the coefficients actually
    # emitted in C, separating implementation identity from float64 error.
    import re
    import subprocess
    header = exported.file_path.read_text(encoding="utf-8")
    rows = []
    for section in range(1, exported.sections + 1):
        values = [float(re.search(rf"CTRL_FILTER_S{section}_{label} \(([-+0-9.eE]+)F\)", header)[1])
                  for label in ("B0", "B1", "B2", "A1", "A2")]
        rows.append([*values[:3], 1.0, *values[3:]])
    raw = subprocess.check_output([str(tmp_path / "_verify_filter.exe")], text=True).splitlines()
    actual = np.array([float(line.split()[1]) for line in raw if line.startswith("S ")])
    expected32 = signal.sosfilt(np.asarray(rows, dtype=np.float32), np.ones(512, dtype=np.float32))
    np.testing.assert_allclose(actual, expected32, rtol=1e-7, atol=1e-8)
    if kind == ControllerKind.PIF:
        # Deliberate long-record quantization boundary: exact save/restore and
        # emitted runtime agree, but float32 drift fails the unchanged budget.
        assert not verified.passed and verified.step_max_abs_error > 6e-5
        assert verified.message == "float32_t response mismatch"
    else:
        assert verified.passed, verified
    dialog.apply_button.setEnabled(True)
    dialog.fs.setValue(dialog.fs.value() + 1000.0)
    assert dialog.result is None and not dialog.apply_button.isEnabled()
    assert not dialog.output.toPlainText()
    dialog.close()
    window.timer.stop()
    window.close()
    app.processEvents()


@pytest.mark.parametrize("compiler_available", [True, False])
def test_fra_export_does_not_claim_success_when_compiled_check_fails(tmp_path, monkeypatch, compiler_available):
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    from power_control_tools.codegen import C99Verification
    from power_control_tools.gui import fra_loop_designer as gui

    app = QApplication.instance() or QApplication([])
    window = gui.FRALoopDesignerWindow()
    window.current_new_controller = DigitalTransferFunction((0.2,), (1.0, -0.5), 40000.0)
    path = tmp_path / "unverified.h"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(path), ""))
    monkeypatch.setattr(gui, "verify_c99_filter", lambda *a: C99Verification(
        compiler_available, False, 0.0, 1.0, "gcc", "deliberate verification failure"))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[-1]))
    monkeypatch.setattr(QMessageBox, "information", lambda *a: pytest.fail("Unverified C99 claimed successful"))
    window.export_c99()
    assert path.exists() and warnings and "deliberate verification failure" in warnings[0]
    window.close()
    app.processEvents()


def test_fra_auto_button_runs_real_optimizer_applies_and_reopens_exact_result(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    from power_control_tools.controllers import EXACT_CONTROLLER
    from power_control_tools.fra.loop_session import read_loop_session
    from power_control_tools.fra.models import FRAMeasurement, FRASourceFormat
    from power_control_tools.gui import fra_advanced as auto_gui
    from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow
    from power_control_tools.models import ControllerKind, DiscretizationMethod

    app = QApplication.instance() or QApplication([])
    window = FRALoopDesignerWindow()
    frequency = np.geomspace(10.0, 18_000.0, 700)
    plant = 2.0 / ((1 + 1j * frequency / 300.0)
                   * (1 + 1j * frequency / 5_000.0)
                   * (1 + 1j * frequency / 10_000.0))
    window.measurement = FRAMeasurement(
        frequency, 20 * np.log10(abs(plant)), np.angle(plant, deg=True),
        FRASourceFormat.GENERIC, "synthetic-three-pole-auto-button.csv",
    )
    window.step_enable.setChecked(False)
    dialog = auto_gui.FRAAutoDesignDialog(window)
    dialog.kind.setCurrentIndex(dialog.kind.findData(ControllerKind.PI))
    dialog.method.setCurrentIndex(dialog.method.findData(DiscretizationMethod.TUSTIN))
    dialog.fs.setValue(40_000.0)
    dialog.fc.setValue(1_000.0)
    issues, messages = [], []
    monkeypatch.setattr(auto_gui, "show_operation_issue", lambda *a, **kw: issues.append(a[2]))
    monkeypatch.setattr(QMessageBox, "information", lambda *a: messages.append(a[-1]))
    dialog.run_button.click()
    assert not issues, issues
    assert dialog.result is not None and dialog.result.status == "PASS"
    assert dialog.result.selected is not None and dialog.result.selected.accepted
    assert dialog.apply_button.isEnabled()
    assert "EXACT DIGITAL H(z)" in dialog.output.toPlainText()
    exact = dialog.result.selected.controller.normalized()
    dialog.apply_button.click()
    assert window.new_kind.currentData() == EXACT_CONTROLLER
    assert window.current_new_controller == exact
    _, expected_controller = signal.freqz(exact.b, exact.a, worN=2*np.pi*frequency/exact.sample_rate_hz)
    np.testing.assert_allclose(window.current_loop, plant * expected_controller, atol=1e-12)
    path = tmp_path / "actual-auto-button.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(path), ""))
    window.save_session()
    assert read_loop_session(path).controller == exact
    window.new_exact_b.setText("9")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    window.open_session()
    assert window.current_new_controller == exact
    header = tmp_path / "actual-auto-button.h"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(header), ""))
    window.export_c99()
    assert header.exists() and "PASS" in messages[-1]
    dialog.close()
    window.close()
    app.processEvents()
