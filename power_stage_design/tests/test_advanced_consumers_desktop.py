"""Actual worker/solver GUI consumer workflows; no fabricated physics results.

Only update checks and native destination choosers are redirected. Qt runs
offscreen and screenshots are grabs of the running windows, not render mocks.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
qt = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtTest import QTest


@pytest.fixture(scope="module")
def app():
    application = qt.QApplication.instance() or qt.QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def windows(app, monkeypatch):
    from llc_design.gui.main_window import LLCMainWindow
    from pfc_design.gui.main_window import PFCMainWindow

    monkeypatch.setattr(LLCMainWindow, "_auto_check_update", lambda self: None)
    monkeypatch.setattr(PFCMainWindow, "_auto_check_update", lambda self: None)
    views, messages = [], []

    def dismiss_actual_dialogs():
        for widget in app.topLevelWidgets():
            if isinstance(widget, qt.QMessageBox) and widget.isVisible():
                messages.append(widget.text() + "\n" + widget.detailedText())
                widget.accept()

    timer = QTimer()
    timer.timeout.connect(dismiss_actual_dialogs)
    timer.start(20)
    yield views, messages
    for view in views:
        _wait(app, view)
        view.close()
        # PlotWidget.close() detaches plots; Qt must delete the intact owner tree.
        view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    timer.stop()


def _wait(app, window, seconds=120):
    deadline = time.monotonic() + seconds
    while window._active_workers and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)  # Yield the GIL to the actual Python worker.
    app.processEvents()
    assert not window._active_workers, "Actual GUI worker did not finish"
    assert window.thread_pool.waitForDone(30000)


def _click(app, button):
    assert button.isEnabled()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    app.processEvents()


def _directory(tmp_path, name):
    directory = Path(os.environ.get("PSD_EVIDENCE_DIR", tmp_path)) / name
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _capture(app, window, directory, name):
    app.processEvents()
    assert window.grab().save(str(directory / name))


def test_llc_real_td_worker_drives_loss_view_export_and_invalidates(app, windows, monkeypatch, tmp_path):
    from llc_design.analysis.types import FidelityLevel
    from llc_design.core.spec import LLCDesignSpec
    from llc_design.gui.main_window import LLCMainWindow
    from llc_design.models.devices import DeviceDatabase

    views, messages = windows
    window = LLCMainWindow(LLCDesignSpec())
    views.append(window)
    window.show()
    page = window.model_comparison_view
    window.tabs.setCurrentWidget(page)
    page.hb_samples.setValue(512)
    page.apply_td_losses.setChecked(True)
    _click(app, page.run_button)
    assert window._active_workers
    _wait(app, window)
    assert not messages, "\n".join(messages)
    comparison = window.multi_fidelity_analysis
    assert comparison is not None
    td = comparison.result(FidelityLevel.SWITCHED_TIME_DOMAIN)
    assert td.convergence.converged
    analysis = window.system_analysis
    assert analysis is not None
    assert analysis.spec == comparison.request.spec
    point = analysis.nominal
    assert point.semiconductor_source == "TIME_DOMAIN_WAVEFORM"
    # Derive conduction directly from accepted sample arrays, independently of
    # producer cached RMS or consumer bookkeeping.
    db = DeviceDatabase()
    ir2 = np.mean(td.waveform.signal("i_resonant").values**2)
    is2 = np.mean(td.waveform.signal("i_transformer_secondary").values**2)
    spec = analysis.spec
    assert point.primary.conduction_w == pytest.approx(
        ir2 * spec.bridge_series_devices * db.get_primary(spec.primary_device).rds_at(
            spec.primary_junction_temperature_c) / spec.primary_parallel_devices)
    assert point.synchronous_rectifier.conduction_w == pytest.approx(
        is2 * 2 * db.get_sr(spec.sr_device).rds_at(spec.sr_junction_temperature_c)
        / spec.sr_parallel_devices_per_position)
    assert point.resonant_capacitor.loss_w == pytest.approx(
        ir2 * spec.resonant_cap_esr_ohm, rel=2e-6)
    ico2 = np.mean(td.waveform.signal("i_output_cap").values**2)
    assert point.output_capacitor.capacitor_loss_w == pytest.approx(
        ico2 * spec.output_cap_esr_ohm, rel=2e-6)
    assert point.magnetic_source == "TIME_DOMAIN_WAVEFORM_IGSE_LITZ"
    assert point.capacitor_source == "TIME_DOMAIN_WAVEFORM_CONSTANT_ESR"
    assert point.commutation_source == "FHA_OPERATING_POINT_APPROXIMATION"
    assert len(point.loss_waveform_sha256) == 64
    assert point.total_loss_w == pytest.approx(sum(point.breakdown().values()))
    assert window.loss_summary_view._analysis is analysis
    assert "TIME_DOMAIN_WAVEFORM" in window.loss_summary_view.text.toPlainText()
    assert f"{point.total_loss_w:.5f}" in window.loss_summary_view.text.toPlainText()
    assert page.loss_report_button.isEnabled()
    directory = _directory(tmp_path, "advanced-llc-real-td")
    _capture(app, window, directory, "comparison-qt-offscreen.png")
    window.resize(1360, 900)
    _capture(app, window, directory, "comparison-1360-qt-offscreen.png")
    geometry = {
        "requested_width": 1360, "actual_width": window.width(),
        "report_button_right": page.loss_report_button.mapTo(
            window, page.loss_report_button.rect().topRight()).x(),
    }
    assert geometry["actual_width"] <= 1360
    assert geometry["report_button_right"] < geometry["actual_width"]
    window.resize(1920, 1080)
    window.tabs.setCurrentWidget(window.loss_summary_view)
    _capture(app, window, directory, "loss-consumer-qt-offscreen.png")
    window.tabs.setCurrentWidget(page)
    monkeypatch.setattr(qt.QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(directory))
    _click(app, page.loss_report_button)
    _wait(app, window)
    assert not messages, "\n".join(messages)
    report = (directory / "LLC_calculation_book.md").read_text(encoding="utf-8")
    assert "TIME_DOMAIN_WAVEFORM" in report
    assert f"{point.total_loss_w:.3f}" in report
    document = json.loads((directory / "analysis.json").read_text(encoding="utf-8"))
    assert document["operating_points"][0]["total_loss_w"] == point.total_loss_w
    assert document["operating_points"][0]["semiconductor_source"] == "TIME_DOMAIN_WAVEFORM"
    exported = document["operating_points"][0]
    for component in ("transformer", "resonant_inductor"):
        assert exported[f"{component}_source"] == "TIME_DOMAIN_WAVEFORM_IGSE_LITZ"
    for component in ("resonant_capacitor", "output_capacitor"):
        assert exported[f"{component}_source"] == "TIME_DOMAIN_WAVEFORM_CONSTANT_ESR"
    assert exported["loss_waveform_sha256"] == point.loss_waveform_sha256
    assert exported["transformer_flux_dc_t"] == point.transformer.b_dc_t
    assert exported["commutation_source"] == "FHA_OPERATING_POINT_APPROXIMATION"
    assert (directory / "model_comparison").is_dir()
    (directory / "workflow-observer.json").write_text(json.dumps({
        "options": page.options(), "td_converged": td.convergence.converged,
        "td_residual": td.convergence.residual_norm,
        "primary_sample_mean_square_a2": float(ir2),
        "secondary_sample_mean_square_a2": float(is2),
        "output_cap_sample_mean_square_a2": float(ico2),
        "component_sources": point.component_sources(),
        "loss_waveform_sha256": point.loss_waveform_sha256,
        "transformer_flux_dc_t": point.transformer.b_dc_t,
        "total_loss_w": point.total_loss_w,
        "narrow_layout": geometry,
    }, indent=2) + "\n", encoding="utf-8")
    # Workpoint fields are inputs too; an export button cannot keep describing
    # the old point after its displayed bus/load has changed.
    page.vbus.setValue(page.vbus.value() - 10)
    app.processEvents()
    assert not page.loss_report_button.isEnabled(), "Changed comparison workpoint kept old TD export enabled"
    assert window.multi_fidelity_analysis is None
    window.fields["pout_w"].setValue(window.fields["pout_w"].value() + 100)
    app.processEvents()
    assert window.system_analysis is None
    assert window.loss_summary_view._analysis is None
    assert "TIME_DOMAIN_WAVEFORM" not in window.loss_summary_view.text.toPlainText()


@pytest.mark.parametrize("cycles", [64, 8, 3])
def test_pfc_real_closed_loop_worker_shared_consumers_export_and_rejection(
        app, windows, monkeypatch, tmp_path, cycles):
    from pfc_design.engineering.pfc_v3 import ConvergenceStatus
    from pfc_design.gui.main_window import PFCMainWindow

    views, messages = windows
    window = PFCMainWindow()
    views.append(window)
    window.show()
    workbench = window.control_lab_view
    control = workbench.control_lab
    workbench.tabs.setCurrentWidget(control)
    control.waveform_line_cycles.setValue(cycles)
    control.waveform_integration_rate_hz.setValue(250000)
    config = control._config()
    assert config.waveform_line_cycles == cycles
    assert config.waveform_integration_rate_hz == 250000
    dispatched_before = window._worker_request_id
    _click(app, control.run_button)
    assert window._worker_request_id == dispatched_before + 1
    # A fast real worker may finish while click processing drains Qt events.
    assert window._active_workers or window.engineering_result is not None
    _wait(app, window)
    assert not messages, "\n".join(messages)
    assert window.engineering_result is not None
    line = window.engineering_result.line_cycle
    assert line.config == config
    assert line.convergence.checked_cycles == 3
    samples = len(line.time_s)
    current_cycles = line.waveforms.signals["i_input_signed"][-3 * samples:].reshape(3, samples)
    bus_cycles = line.waveforms.signals["vbus"][-3 * samples:].reshape(3, samples)
    current_delta_rms = np.sqrt(np.mean(np.diff(current_cycles, axis=0)**2, axis=1))
    bus_delta_rms = np.sqrt(np.mean(np.diff(bus_cycles, axis=0)**2, axis=1))
    devices, caps = workbench.device_loss_view, workbench.cap_thermal_view
    assert devices.line_cycle is line
    assert caps.line_cycle is line
    workbench.tabs.setCurrentWidget(devices)
    devices.source.setCurrentIndex(devices.source.findData("closed_loop"))
    app.processEvents()
    assert caps.source.currentData() == "closed_loop"
    directory = _directory(tmp_path, f"advanced-pfc-real-{cycles}cycles")
    _capture(app, window, directory, "devices-qt-offscreen.png")
    workbench.tabs.setCurrentWidget(caps)
    _capture(app, window, directory, "capacitor-thermal-qt-offscreen.png")
    caps.tabs.setCurrentIndex(1)
    _capture(app, window, directory, "physical-switch-thermal-qt-offscreen.png")
    (directory / "source-config.json").write_text(
        json.dumps(asdict(config), default=lambda value: value.value, indent=2) + "\n", encoding="utf-8")
    (directory / "line-observer.json").write_text(
        json.dumps(line.as_dict(), indent=2) + "\n", encoding="utf-8")
    np.savez_compressed(directory / "convergence-three-cycles.npz",
        current_a=current_cycles, bus_voltage_v=bus_cycles,
        input_power_w=line.waveforms.signals["input_power"][-3 * samples:].reshape(3, samples),
        adjacent_current_delta_rms_a=current_delta_rms,
        adjacent_bus_delta_rms_v=bus_delta_rms)
    export_path = directory / "device-comparison.json"
    monkeypatch.setattr(qt.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(export_path), "JSON (*.json)"))
    if cycles != 64:
        assert line.convergence.status is not ConvergenceStatus.LINE_CYCLE_CONVERGED
        assert devices._last_comparison is None
        assert caps.cap_result is None
        assert caps.thermal_result is None
        assert "failed" in devices.summary.text().lower()
        assert "failed" in caps.cap_summary.toPlainText().lower()
        assert "failed" in caps.thermal_summary.toPlainText().lower()
        devices._export_comparison()
        assert not export_path.exists()
        return
    assert line.convergence.status is ConvergenceStatus.LINE_CYCLE_CONVERGED
    assert max(current_delta_rms) < .01 * min(np.sqrt(np.mean(current_cycles**2, axis=1)))
    assert max(bus_delta_rms) < .005 * config.power_stage.bus_voltage_v
    comparison = devices._last_comparison
    assert comparison is not None and comparison.line_cycle is line
    bank, thermal = caps.cap_result, caps.thermal_result
    assert bank is not None and thermal is not None
    assert bank.waveform_source == thermal.waveform_source == "closed_loop_final_line_cycle"
    assert thermal.converged
    i2 = line.il_avg_a**2 + line.il_ripple_pp_a**2 / 12
    first_hf = comparison.hf_devices[0]
    assert first_hf.active_conduction_w + first_hf.sr_conduction_w == pytest.approx(
        first_hf.rds_hot_ohm * np.mean(i2))
    cap_rms = np.sqrt(np.mean(line.capacitor_current_a**2))
    assert comparison.capacitor_current_rms_a == pytest.approx(cap_rms)
    # Independently observe available local PWM current moments; unknown ZC/
    # negative-valley samples retain their full AC weight and remain explicit.
    zc = line.waveforms.signals["zero_cross_active"][line.settled_slice] > .5
    ccm = (line.il_avg_a >= .5 * line.il_ripple_pp_a) & ~zc
    nodes, quadrature_weights = np.polynomial.legendre.leggauss(8)
    u, quadrature_weights = .5 * (nodes + 1), .5 * quadrature_weights
    cap_m2 = []
    for avg, delta, duty, cap_mean, known in zip(line.il_avg_a,
            line.il_ripple_pp_a, line.duty, line.capacitor_current_a, ccm):
        load = (1 - duty) * avg - cap_mean
        off = avg + delta / 2 - delta * u - load
        cap_m2.append(duty * load**2 + (1 - duty) * np.sum(quadrature_weights * off**2)
                      if known else cap_mean**2)
    known_cap_rms = np.sqrt(np.mean(cap_m2))
    assert comparison.capacitor_ripple.valid_sample_mask.tolist() == ccm.tolist()
    assert bank.capacitor_ripple.as_dict() == comparison.capacitor_ripple.as_dict()
    assert bank.bank_ripple_current_rms_a == pytest.approx(known_cap_rms, rel=1e-7)
    assert bank.bank_esr_loss_w == pytest.approx(known_cap_rms**2 * bank.bank_esr_ohm, rel=1e-7)
    assert bank.bank_averaged_esr_loss_w == pytest.approx(cap_rms**2 * bank.bank_esr_ohm)
    assert bank.bank_known_switching_esr_loss_w > 0
    assert not bank.capacitor_ripple.complete
    assert bank.estimated_hotspot_c is None and bank.estimated_life_h is None
    assert bank.ripple_current_ok is None
    assert "PARTIAL; complete RMS unknown" in caps.cap_summary.toPlainText()
    assert "Ripple derating     : UNKNOWN" in caps.cap_summary.toPlainText()
    # Independent full-cycle electrical PF and FFT harmonic observer.
    pf = np.mean(line.vac_v * line.iac_a) / np.sqrt(np.mean(line.vac_v**2) * np.mean(line.iac_a**2))
    spectrum = np.fft.rfft(line.iac_a) / len(line.iac_a)
    included = window.engineering_result.pf_thd.convention.included_harmonics
    assert included == tuple(range(2, 26))
    thd = np.sqrt(np.sum(abs(spectrum[list(included)])**2)) / abs(spectrum[1]) * 100
    assert comparison.power_factor == pytest.approx(pf, rel=1e-10)
    assert comparison.thd_percent == pytest.approx(thd, rel=1e-10)
    zero = abs(line.vac_v) <= max(abs(line.vac_v)) * 1e-12
    upper_active = np.where(zero, .5, np.where(line.vac_v < 0, 1., 0.))
    for physical, active in ((thermal.hf_loss.upper, upper_active),
                             (thermal.hf_loss.lower, 1 - upper_active)):
        assert physical.conduction_w == pytest.approx(physical.rds_hot_ohm * np.mean(
            (active * line.duty + (1 - active) * (1 - line.duty)) * i2))
    for junction, power, resistance in (
        (thermal.hf_upper_junction_temperature_c, thermal.hf_upper_device_loss_w, thermal.config.hf_rth_ja_k_per_w),
        (thermal.hf_lower_junction_temperature_c, thermal.hf_lower_device_loss_w, thermal.config.hf_rth_ja_k_per_w),
        (thermal.slow_upper_junction_temperature_c, thermal.slow_upper_device_loss_w, thermal.config.slow_rth_ja_k_per_w),
        (thermal.slow_lower_junction_temperature_c, thermal.slow_lower_device_loss_w, thermal.config.slow_rth_ja_k_per_w),
    ):
        assert junction == pytest.approx(thermal.config.ambient_temperature_c + power * resistance,
                                         abs=thermal.config.convergence_tolerance_c)
    workbench.tabs.setCurrentWidget(devices)
    _click(app, devices.export_button)
    saved = json.loads(export_path.read_text(encoding="utf-8"))
    assert saved["source_config"]["waveform_line_cycles"] == cycles
    assert saved["source_config"]["waveform_integration_rate_hz"] == 250000
    assert saved["capacitor_current_rms_a"] == comparison.capacitor_current_rms_a
    assert saved["waveform_source"] == "closed_loop_final_line_cycle"
    assert saved["capacitor_ripple"] == comparison.capacitor_ripple.as_dict()
    assert saved["capacitor_ripple"]["total_current_rms_a"] is None
    capacitor_path = directory / "capacitor-bank.json"
    monkeypatch.setattr(qt.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(capacitor_path), "JSON (*.json)"))
    _click(app, caps.cap_export_button)
    capacitor_report = json.loads(capacitor_path.read_text(encoding="utf-8"))
    assert capacitor_report["status"] == "PARTIAL"
    assert capacitor_report["estimated_life_h"] is None
    assert capacitor_report["known_model_esr_loss_w"] == bank.bank_esr_loss_w
    np.savez_compressed(directory / "accepted-final-cycle.npz", time_s=line.time_s,
        vac_v=line.vac_v, iac_a=line.iac_a, vbus_v=line.vbus_v, duty=line.duty,
        il_avg_a=line.il_avg_a, il_ripple_pp_a=line.il_ripple_pp_a,
        capacitor_current_a=line.capacitor_current_a)
    (directory / "consumer-observer.json").write_text(json.dumps({
        "sample_pf": float(pf), "fft_harmonics_2_to_25_thd_percent": float(thd),
        "sample_capacitor_rms_a": float(cap_rms), "bank_esr_loss_w": bank.bank_esr_loss_w,
        "known_pwm_substep_capacitor_rms_a": float(known_cap_rms),
        "capacitor_ripple": bank.capacitor_ripple.as_dict(),
        "thermal_residual_c": thermal.temperature_residual_c,
        "hf_upper_temperature_c": thermal.hf_upper_junction_temperature_c,
        "hf_lower_temperature_c": thermal.hf_lower_junction_temperature_c,
    }, indent=2) + "\n", encoding="utf-8")
    # Changing a real physical field must sever every old-line consumer.
    workbench.tabs.setCurrentWidget(control)
    control.pout.setValue(control.pout.value() + 100)
    app.processEvents()
    assert window.engineering_result is None
    assert window.result is None
    assert devices.line_cycle is None and caps.line_cycle is None
    assert devices._last_comparison is None
    assert caps.cap_result is None and caps.thermal_result is None
    assert not caps.cap_export_button.isEnabled()
    stale_path = directory / "must-not-export-stale.json"
    monkeypatch.setattr(qt.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(stale_path), "JSON (*.json)"))
    devices._export_comparison()
    assert not stale_path.exists()


@pytest.mark.parametrize("variation", ["periodic", "current_phase", "bus_ripple", "older_pair"])
def test_convergence_gate_independent_equal_power_waveform_counterexamples(variation):
    """Manufactured gate inputs only; never substituted into a GUI solver.

    Orthogonal third harmonics preserve real input power. Their opposite phase
    also preserves RMS, so neither equal mean power nor equal RMS proves that
    phase-aligned successive trajectories repeat.
    """
    from types import SimpleNamespace

    from pfc_design.engineering.pfc_v3 import (
        ConvergenceStatus,
        assess_line_cycle_convergence,
    )

    samples, frequency = 1000, 50.
    theta = 2 * np.pi * np.arange(samples) / samples
    vac = np.sqrt(2) * 230 * np.sin(theta)
    third = (3., 3., -3.) if variation == "current_phase" else (
        (-3., 3., 3.) if variation == "older_pair" else (3., 3., 3.))
    ripple = (5., 5., 20.) if variation == "bus_ripple" else (5., 5., 5.)
    currents = np.array([20 * np.sin(theta) + a * np.sin(3 * theta) for a in third])
    bus = np.array([400 + a * np.cos(2 * theta) for a in ripple])
    pin = currents * vac
    np.testing.assert_allclose(pin.mean(axis=1), pin.mean(axis=1)[0], rtol=1e-14)
    np.testing.assert_allclose((currents**2).mean(axis=1), (currents**2).mean(axis=1)[0], rtol=1e-14)
    np.testing.assert_allclose(bus.mean(axis=1), 400, atol=1e-12)
    trajectory = SimpleNamespace(
        time_s=np.arange(3 * samples) / (samples * frequency),
        signals={"input_power": pin.ravel(), "vbus": bus.ravel(),
                 "i_input_signed": currents.ravel()},
    )
    result = assess_line_cycle_convergence(trajectory, line_hz=frequency, vbus_command_v=400)
    assert result.pin_cycle_relative_error < 1e-14
    assert result.vbus_relative_error < 1e-14
    expected = (ConvergenceStatus.LINE_CYCLE_CONVERGED if variation == "periodic"
                else ConvergenceStatus.NOT_CONVERGED)
    assert result.status is expected
