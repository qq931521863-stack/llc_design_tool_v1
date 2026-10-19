"""Same-cycle consumer contract, using independent periodic synthetic signals."""
import json
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from pfc_design.control import PFCControlLabConfig
from pfc_design.engineering import (
    PFCDeviceDatabase,
    TTPLDesignSpec,
    analyze_ttpl_design,
)
from pfc_design.engineering.device_loss import (
    compare_ttpl_devices,
    evaluate_hf_device,
    evaluate_slow_device,
)
from pfc_design.engineering.pfc_v3 import (
    ConvergenceStatus,
    LineCycleConvergenceResult,
    PFCLineCycleResult,
    compute_pf_thd,
)
from pfc_design.gui.pfc_loss_summary import PFCDeviceLossRollup


@pytest.fixture(scope="module")
def periodic_line():
    cfg = PFCControlLabConfig()
    stage = cfg.power_stage
    n = 2000
    theta = 2 * np.pi * np.arange(n) / n
    time = np.arange(n) / (n * stage.line_frequency_hz)
    vac = np.sqrt(2) * stage.vin_rms_v * np.sin(theta)
    iac = np.sqrt(2) * stage.output_power_w / stage.vin_rms_v * np.sin(theta) + 2*np.sin(3*theta)
    il = np.abs(iac)
    duty = 1 - np.abs(vac) / stage.bus_voltage_v
    ripple = 2 * np.sin(theta)**2
    vbus = np.full(n, stage.bus_voltage_v)
    cap = (1-duty)*il - stage.output_power_w/vbus
    pf = compute_pf_thd(time, vac, iac, line_hz=stage.line_frequency_hz)
    waveform = SimpleNamespace(metrics=SimpleNamespace(power_factor=pf.pf,
        current_thd_percent=pf.thd_percent, bus_voltage_average_v=stage.bus_voltage_v))
    convergence = LineCycleConvergenceResult(ConvergenceStatus.LINE_CYCLE_CONVERGED,
        0., stage.bus_voltage_v, stage.bus_voltage_v, 0., 0., pf.iac_rms_a)
    return PFCLineCycleResult(waveform, np.degrees(theta), time, vac, np.abs(vac),
        iac, iac, vbus, duty, il, ripple, il+ripple/2, np.maximum(il-ripple/2, 0),
        stage.switching_frequency_hz, vac*iac, np.full(n, stage.output_power_w), il,
        cap, il, np.abs(iac), convergence, slice(None), cfg)


def test_closed_loop_loss_uses_full_cycle_weights_and_actual_waveforms(periodic_line, tmp_path):
    line = periodic_line
    design = analyze_ttpl_design(TTPLDesignSpec())  # deliberately different sizing fsw
    db = PFCDeviceDatabase(user_path=tmp_path / "unused.json")
    result = compare_ttpl_devices(design, db, workpoint="high", line_cycle=line)
    hf = result.hf_devices[0]
    slow = next(x for x in result.slow_devices if x.device == hf.device)
    expected_i2 = np.mean(line.il_avg_a**2 + line.il_ripple_pp_a**2/12)
    assert hf.active_conduction_w + hf.sr_conduction_w == pytest.approx(hf.rds_hot_ohm*expected_i2)
    assert slow.conduction_w == pytest.approx(slow.rds_hot_ohm*expected_i2)
    assert result.vin_rms_v == line.config.power_stage.vin_rms_v
    assert result.design.spec.switching_frequency_hz == line.fsw_hz
    assert result.workpoint == "closed_loop"
    assert result.capacitor_current_rms_a == pytest.approx(np.sqrt(np.mean(line.capacitor_current_a**2)))
    pf = compute_pf_thd(line.time_s, line.vac_v, line.iac_a, line_hz=50.)
    assert result.power_factor == pytest.approx(pf.pf)
    assert result.thd_percent == pytest.approx(pf.thd_percent)
    payload = json.loads(json.dumps(result.as_dict()))
    assert payload["source_config"]["power_stage"]["switching_frequency_hz"] == line.fsw_hz
    assert payload["capacitor_current_rms_a"] == result.capacitor_current_rms_a
    rollup = PFCDeviceLossRollup(result.workpoint, result.vin_rms_v, hf, slow,
        waveform_source=result.waveform_source, capacitor_current_rms_a=result.capacitor_current_rms_a,
        power_factor=result.power_factor, thd_percent=result.thd_percent)
    assert "closed_loop_final_line_cycle" in rollup.format_text()
    assert "HF moments shown separately" in rollup.format_text()


def test_scaling_same_cycle_changes_real_conduction_not_only_source(periodic_line, tmp_path):
    design = analyze_ttpl_design(TTPLDesignSpec())
    device = PFCDeviceDatabase(user_path=tmp_path / "unused.json").all[0]
    line = periodic_line
    scaled = replace(line, il_avg_a=2*line.il_avg_a, il_ripple_pp_a=2*line.il_ripple_pp_a,
        il_peak_a=2*line.il_peak_a, il_valley_a=2*line.il_valley_a, iac_a=2*line.iac_a)
    hf1 = evaluate_hf_device(design, device, vin_rms_v=230, line_cycle=line)
    hf2 = evaluate_hf_device(design, device, vin_rms_v=230, line_cycle=scaled)
    lf1 = evaluate_slow_device(design, device, vin_rms_v=230, line_cycle=line)
    lf2 = evaluate_slow_device(design, device, vin_rms_v=230, line_cycle=scaled)
    assert hf2.active_conduction_w == pytest.approx(4*hf1.active_conduction_w)
    assert hf2.sr_conduction_w == pytest.approx(4*hf1.sr_conduction_w)
    assert lf2.conduction_w == pytest.approx(4*lf1.conduction_w)


@pytest.mark.parametrize("bad", ["nonconverged", "identity", "nonuniform", "incomplete", "nan"])
def test_requested_closed_loop_refuses_invalid_line(periodic_line, tmp_path, bad):
    line = periodic_line
    if bad == "nonconverged":
        line = replace(line, convergence=replace(line.convergence, status=ConvergenceStatus.NOT_CONVERGED))
    elif bad == "identity":
        line = replace(line, config=None)
    elif bad == "nonuniform":
        time = line.time_s.copy(); time[5] += 1e-7
        line = replace(line, time_s=time)
    elif bad == "incomplete":
        line = replace(line, time_s=line.time_s/2)
    elif bad == "nan":
        line = replace(line, iac_a=np.full_like(line.iac_a, np.nan))
    design = analyze_ttpl_design(TTPLDesignSpec())
    db = PFCDeviceDatabase(user_path=tmp_path / "unused.json")
    with pytest.raises(ValueError):
        compare_ttpl_devices(design, db, line_cycle=line)


def test_gui_closed_loop_selection_and_failed_refresh_clear_stale_results(periodic_line, tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    from pfc_design.gui.pfc_device_view import TTPLDeviceLossView

    app = widgets.QApplication.instance() or widgets.QApplication([])
    db = PFCDeviceDatabase(user_path=tmp_path / "unused.json")
    view = TTPLDeviceLossView(analyze_ttpl_design(TTPLDesignSpec()), db)
    assert view._last_comparison.waveform_source == "ideal_sizing_half_cycle"
    view.set_line_cycle(periodic_line)
    view.source.setCurrentIndex(1)
    assert view._last_comparison.line_cycle is periodic_line
    assert "closed_loop_final_line_cycle" in view.rollup_text.toPlainText()
    bad = replace(periodic_line, convergence=replace(periodic_line.convergence,
                  status=ConvergenceStatus.NOT_CONVERGED))
    view.set_line_cycle(bad)
    assert view._last_comparison is None
    assert view.hf_table.rowCount() == 0
    assert view.slow_table.rowCount() == 0
    assert "No valid device loss result" in view.rollup_text.toPlainText()
    view.close(); app.processEvents()


def test_capacitor_esr_and_thermal_consume_the_selected_line(periodic_line, tmp_path):
    from pfc_design.engineering.cap_thermal import (
        CapacitorUnitSpec,
        design_bus_capacitor_bank,
        solve_ttpl_semiconductor_thermal,
    )
    design = analyze_ttpl_design(TTPLDesignSpec())
    unit = CapacitorUnitSpec()
    line = periodic_line
    bank = design_bus_capacitor_bank(design, unit, line_cycle=line)
    assert bank.waveform_source == "closed_loop_final_line_cycle"
    expected_square = np.mean(line.capacitor_current_a**2 +
        line.duty * (1 - line.duty) * line.il_avg_a**2 +
        (1 - line.duty) * line.il_ripple_pp_a**2 / 12)
    assert bank.capacitor_ripple.complete
    assert bank.bank_ripple_current_rms_a**2 == pytest.approx(expected_square)
    assert bank.bank_esr_loss_w == pytest.approx(expected_square * bank.bank_esr_ohm)
    assert bank.bank_averaged_esr_loss_w == pytest.approx(np.mean(line.capacitor_current_a**2) * bank.bank_esr_ohm)
    assert bank.design.spec.switching_frequency_hz == line.fsw_hz
    assert any("Local CCM triangular reconstruction" in note for note in bank.warnings)
    device = PFCDeviceDatabase(user_path=tmp_path / "unused.json").all[0]
    thermal = solve_ttpl_semiconductor_thermal(design, device, device, line_cycle=line)
    assert thermal.workpoint == "closed_loop"
    assert thermal.vin_rms_v == line.config.power_stage.vin_rms_v
    expected = thermal.hf_loss.rds_hot_ohm*np.mean(line.duty*(line.il_avg_a**2+line.il_ripple_pp_a**2/12))
    assert thermal.hf_loss.active_conduction_w == pytest.approx(expected)


def test_capacitor_gui_source_clears_unconverged_result(periodic_line, tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    from pfc_design.gui.cap_thermal_view import TTPLCapacitorThermalView
    app = widgets.QApplication.instance() or widgets.QApplication([])
    view = TTPLCapacitorThermalView(analyze_ttpl_design(TTPLDesignSpec()),
        PFCDeviceDatabase(user_path=tmp_path / "unused.json"))
    view.set_line_cycle(periodic_line)
    view.source.setCurrentIndex(1)
    assert view.cap_result.waveform_source == "closed_loop_final_line_cycle"
    assert view.thermal_result.workpoint == "closed_loop"
    view.set_line_cycle(replace(periodic_line, convergence=replace(periodic_line.convergence,
        status=ConvergenceStatus.NOT_CONVERGED)))
    assert view.cap_result is None
    assert view.thermal_result is None
    view.close(); app.processEvents()


def test_real_settled_simulation_is_shared_by_losses_capacitor_and_pf(tmp_path):
    from pfc_design.engineering.cap_thermal import (
        CapacitorUnitSpec,
        design_bus_capacitor_bank,
    )
    from pfc_design.engineering.pfc_v3 import build_line_cycle_result
    cfg = replace(PFCControlLabConfig(), waveform_line_cycles=64, waveform_integration_rate_hz=250e3)
    line = build_line_cycle_result(cfg)
    assert line.convergence.status == ConvergenceStatus.LINE_CYCLE_CONVERGED
    design = analyze_ttpl_design(TTPLDesignSpec())
    result = compare_ttpl_devices(design, PFCDeviceDatabase(user_path=tmp_path / "unused.json"), line_cycle=line)
    bank = design_bus_capacitor_bank(design, CapacitorUnitSpec(), line_cycle=line)
    assert result.line_cycle is line
    assert result.power_factor == pytest.approx(line.waveforms.metrics.power_factor)
    assert result.thd_percent == pytest.approx(line.waveforms.metrics.current_thd_percent)
    assert result.capacitor_current_rms_a == pytest.approx(line.waveforms.metrics.bus_capacitor_current_rms_a)
    assert bank.bank_ripple_current_rms_a == pytest.approx(result.capacitor_ripple.known_model_current_rms_a)
    assert bank.capacitor_ripple.as_dict() == result.capacitor_ripple.as_dict()
    assert bank.bank_ripple_current_rms_a > result.capacitor_current_rms_a
    assert not bank.capacitor_ripple.complete
    assert bank.estimated_hotspot_c is None and bank.ripple_current_ok is None


def test_physical_switch_breakdowns_preserve_role_energy_and_own_temperature(periodic_line, tmp_path):
    line = periodic_line
    factor = np.where(line.vac_v >= 0, 1.4, 0.8)
    line = replace(line, iac_a=line.iac_a*factor, il_avg_a=line.il_avg_a*factor,
        il_ripple_pp_a=line.il_ripple_pp_a*factor, il_peak_a=line.il_peak_a*factor,
        il_valley_a=line.il_valley_a*factor)
    device = replace(PFCDeviceDatabase(user_path=tmp_path / "unused.json").all[0],
        eon_ref_uj=20, eoff_ref_uj=10, e_ref_v=400, e_ref_i=20,
        qg_nc=100, coss_er_pF=100, vgs=10, rds_on_25c=0.05, rds_on_150c=0.10)
    hf = evaluate_hf_device(analyze_ttpl_design(TTPLDesignSpec()), device,
        vin_rms_v=230, line_cycle=line, upper_junction_temperature_c=120,
        lower_junction_temperature_c=60, deadtime_s=100e-9, reverse_drop_v=2)
    zero = np.abs(line.vac_v) <= np.max(np.abs(line.vac_v))*1e-12
    upper_active = np.where(zero, 0.5, np.where(line.vac_v < 0, 1., 0.))
    i2 = line.il_avg_a**2 + line.il_ripple_pp_a**2/12
    active_switch = line.fsw_hz * line.vbus_v/400 * (
        20e-6*line.il_valley_a/20 + 10e-6*line.il_peak_a/20)
    sr_switch = line.fsw_hz * line.vbus_v/400 * 10e-6*line.il_valley_a/20
    dead = line.fsw_hz*2*100e-9*(line.il_peak_a+line.il_valley_a)
    for physical, active, temperature in ((hf.upper, upper_active, 120),
                                         (hf.lower, 1-upper_active, 60)):
        rds = 0.05 + (temperature-25)/125*0.05
        assert physical.rds_hot_ohm == pytest.approx(rds)
        assert physical.conduction_w == pytest.approx(rds*np.mean(
            (active*line.duty+(1-active)*(1-line.duty))*i2))
        assert physical.switching_w == pytest.approx(np.mean(active*active_switch+(1-active)*sr_switch))
        assert physical.deadtime_reverse_w == pytest.approx(np.mean((1-active)*dead))
        assert physical.coss_w == pytest.approx(np.mean(0.5*100e-12*line.vbus_v**2*line.fsw_hz))
        assert physical.gate_drive_w == pytest.approx(100e-9*10*line.fsw_hz)
        assert physical.total_w == pytest.approx(sum((physical.conduction_w,
            physical.switching_w, physical.deadtime_reverse_w, physical.coss_w, physical.gate_drive_w)))
    assert hf.upper.total_w != pytest.approx(hf.lower.total_w)
    assert hf.total_w == pytest.approx(hf.upper.total_w+hf.lower.total_w)
    assert hf.total_w == pytest.approx(sum((hf.active_conduction_w, hf.sr_conduction_w,
        hf.active_switching_w, hf.sr_switching_w, hf.deadtime_reverse_w, hf.coss_w, hf.gate_drive_w)))


def test_legacy_mirrored_half_cycle_has_equal_physical_switch_temperatures(tmp_path):
    from pfc_design.engineering.cap_thermal import solve_ttpl_semiconductor_thermal
    device = PFCDeviceDatabase(user_path=tmp_path / "unused.json").all[0]
    result = solve_ttpl_semiconductor_thermal(analyze_ttpl_design(TTPLDesignSpec()), device, device)
    assert result.converged
    assert result.hf_upper_device_loss_w == pytest.approx(result.hf_lower_device_loss_w)
    assert result.hf_upper_junction_temperature_c == pytest.approx(result.hf_lower_junction_temperature_c)
    assert result.slow_upper_device_loss_w == pytest.approx(result.slow_lower_device_loss_w)
    assert result.slow_upper_junction_temperature_c == pytest.approx(result.slow_lower_junction_temperature_c)
    assert result.temperature_residual_c <= result.config.convergence_tolerance_c
    assert result.hf_loss.upper.rds_hot_ohm == pytest.approx(result.hf_loss.lower.rds_hot_ohm)


@pytest.mark.parametrize("bad", ["one_polarity", "current_identity", "regenerative", "negative_rectified"])
def test_physical_device_mapping_refuses_undefined_domain(periodic_line, tmp_path, bad):
    line = periodic_line
    if bad == "one_polarity":
        line = replace(line, vac_v=np.abs(line.vac_v))
    elif bad == "current_identity":
        line = replace(line, iac_a=2*line.iac_a)
    elif bad == "regenerative":
        line = replace(line, iac_a=-line.iac_a)
    else:
        line = replace(line, il_avg_a=-line.il_avg_a)
    device = PFCDeviceDatabase(user_path=tmp_path / "unused.json").all[0]
    design = analyze_ttpl_design(TTPLDesignSpec())
    for evaluator in (evaluate_hf_device, evaluate_slow_device):
        with pytest.raises(ValueError, match="allocation|rectified"):
            evaluator(design, device, vin_rms_v=230, line_cycle=line)


def test_gui_settling_controls_survive_real_session_roundtrip(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    from llc_design.control.design_session import read_session, write_session
    from llc_design.gui.design_session_install import validate_map_state
    from pfc_design.gui.main_window import PFCMainWindow
    from pfc_design.gui.solution_map_install import install_ttpl_solution_map
    app = widgets.QApplication.instance() or widgets.QApplication([])
    monkeypatch.setattr(PFCMainWindow, "_auto_check_update", lambda self: None)
    window = PFCMainWindow()
    control = window.control_lab_view.control_lab
    view = install_ttpl_solution_map(window)
    control.waveform_line_cycles.setValue(64)
    control.waveform_integration_rate_hz.setValue(250000)
    path = tmp_path / "settling-session.json"
    write_session(control._session_capture(), path)
    control.waveform_line_cycles.setValue(8)
    control.waveform_integration_rate_hz.setValue(500000)
    document = read_session(path)
    control._session_restore(document, validate_map_state(view, document.get("solution_map"), "ttpl"))
    assert control.waveform_line_cycles.value() == 64
    assert control.waveform_integration_rate_hz.value() == 250000
    assert control._config().waveform_line_cycles == 64
    assert control._config().waveform_integration_rate_hz == 250000
    assert window.engineering_result is None
    assert window.control_lab_view.device_loss_view.line_cycle is None
    assert not window.control_lab_view.device_loss_view.export_button.isEnabled()
    window.close(); app.processEvents()


def test_changed_gui_request_refuses_real_late_worker_result(monkeypatch):
    import os
    import time
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from pfc_design.gui.main_window import PFCMainWindow
    app = widgets.QApplication.instance() or widgets.QApplication([])
    monkeypatch.setattr(PFCMainWindow, "_auto_check_update", lambda self: None)
    window = PFCMainWindow()
    control = window.control_lab_view.control_lab
    control.waveform_line_cycles.setValue(64)
    control.waveform_integration_rate_hz.setValue(250000)
    QTest.mouseClick(control.run_button, Qt.MouseButton.LeftButton)
    assert window._active_workers
    # Programmatic/session edits are possible even while interactive inputs are
    # frozen. The real running solver must not repopulate invalidated consumers.
    control.pout.setValue(control.pout.value() + 100)
    deadline = time.monotonic() + 30
    while window._active_workers and time.monotonic() < deadline:
        app.processEvents(); QTest.qWait(10)
    assert not window._active_workers
    assert window.result is None and window.engineering_result is None
    assert control.result is None
    assert control.research_export.analysis is None
    assert not control.codegen_button.isEnabled()
    assert window.control_lab_view.device_loss_view.line_cycle is None
    assert window.control_lab_view.cap_thermal_view.cap_result is None
    assert not window.control_lab_view.device_loss_view.export_button.isEnabled()
    assert control.run_button.isEnabled()
    window.close(); app.processEvents()


@pytest.mark.parametrize("drift", ["voltage_shape", "current_phase", "none", "two_cycles"])
def test_periodicity_gate_rejects_equal_means_with_drifting_states(drift):
    from pfc_design.engineering.pfc_v3 import assess_line_cycle_convergence
    n = 400
    cycles = 2 if drift == "two_cycles" else 3
    theta = 2*np.pi*np.arange(n)/n
    time = np.arange(n*cycles)/(n*50.)
    voltage = np.concatenate([400+(5+10*k if drift == "voltage_shape" else 5)*np.cos(2*theta)
                              for k in range(cycles)])
    current = np.concatenate([10*np.sin(theta+(0.1*k if drift == "current_phase" else 0))
                              for k in range(cycles)])
    waveforms = SimpleNamespace(time_s=time, signals={
        "input_power": np.full_like(time, 1000.), "vbus": voltage, "i_input_signed": current})
    result = assess_line_cycle_convergence(waveforms, line_hz=50., vbus_command_v=400.)
    assert result.pin_cycle_relative_error == pytest.approx(0, abs=1e-12)
    assert result.vbus_relative_error == pytest.approx(0, abs=1e-12)
    if drift == "none":
        assert result.status == ConvergenceStatus.LINE_CYCLE_CONVERGED
    elif drift == "two_cycles":
        assert result.status == ConvergenceStatus.UNKNOWN
    else:
        assert result.status == ConvergenceStatus.NOT_CONVERGED
        assert any("waveform relative RMS error" in note for note in result.notes)
    assert result.checked_cycles == cycles
    assert result.as_dict()["thresholds"]["iac_waveform_relative_error"] == 0.01


def test_periodicity_gate_aligns_noninteger_samples_per_line_period():
    from pfc_design.engineering.pfc_v3 import assess_line_cycle_convergence
    rate, line = 10000., 60.
    time = np.arange(750)/rate  # 4.5 complete periods; fractional sample/cycle
    theta = 2*np.pi*line*time
    waves = SimpleNamespace(time_s=time, signals={
        "input_power": 1000*(1-np.cos(2*theta)),
        "vbus": 400+10*np.cos(2*theta), "i_input_signed": 10*np.sin(theta)})
    result = assess_line_cycle_convergence(waves, line_hz=line, vbus_command_v=400.)
    assert result.status == ConvergenceStatus.LINE_CYCLE_CONVERGED
    assert result.checked_cycles == 3
    assert result.vbus_waveform_relative_error < 1e-4
    assert result.iac_waveform_relative_error < 1e-3


@pytest.mark.parametrize("field,value", [("waveform_line_cycles", 513),
                                         ("waveform_integration_rate_hz", 199999.)])
def test_gui_session_refuses_unsupported_settling_values(field, value):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    from llc_design.gui.design_session_install import _reflect_ttpl
    from pfc_design.gui.control_lab_view import PFCControlLabView
    app = widgets.QApplication.instance() or widgets.QApplication([])
    view = PFCControlLabView()
    before = view._config()
    with pytest.raises(ValueError, match="supported GUI range"):
        _reflect_ttpl(view, replace(before, **{field: value}))
    assert view._config() == before
    view.close(); app.processEvents()


@pytest.mark.parametrize("count", [3.0, float("nan"), -1, 2, True, "3"])
def test_periodicity_cycle_count_rejects_noninteger_or_invalid_domain(count):
    from pfc_design.engineering.pfc_v3 import assess_line_cycle_convergence
    time = np.arange(1600)/20000.
    waves = SimpleNamespace(time_s=time, signals={
        "input_power": np.full_like(time, 1000.), "vbus": np.full_like(time, 400.),
        "i_input_signed": 10*np.sin(2*np.pi*50*time)})
    with pytest.raises(ValueError, match="integer count"):
        assess_line_cycle_convergence(waves, line_hz=50., vbus_command_v=400.,
                                      periodicity_cycles=count)
    accepted = assess_line_cycle_convergence(waves, line_hz=50., vbus_command_v=400.,
                                            periodicity_cycles=np.int64(3))
    assert accepted.status == ConvergenceStatus.LINE_CYCLE_CONVERGED
    assert json.loads(json.dumps(accepted.as_dict()))["checked_cycles"] == 3
