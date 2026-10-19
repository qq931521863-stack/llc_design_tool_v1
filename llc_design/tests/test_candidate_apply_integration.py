"""Candidate comparison is read-only until Apply, with exact maintained H(z)."""
from __future__ import annotations

import os
from dataclasses import replace
from functools import lru_cache
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def app(monkeypatch):
    qt = pytest.importorskip("PySide6.QtWidgets")
    from llc_design.gui.main_window import LLCMainWindow
    from pfc_design.gui.main_window import PFCMainWindow
    monkeypatch.setattr(LLCMainWindow, "_auto_check_update", lambda self: None)
    monkeypatch.setattr(PFCMainWindow, "_auto_check_update", lambda self: None)
    application = qt.QApplication.instance() or qt.QApplication([])
    yield application
    application.processEvents()


@lru_cache(maxsize=1)
def _llc_analysis():
    from llc_design.control.analysis import build_small_signal_analysis
    from llc_design.control.digital_loop import (
        PIControllerConfig,
        build_digital_loop_analysis,
    )
    from llc_design.core.spec import LLCDesignSpec
    from llc_design.models.system import LLCSystemAnalyzer
    spec = LLCDesignSpec()
    system = LLCSystemAnalyzer().analyze(spec)
    small = build_small_signal_analysis(spec, system_analysis=system, sample_time_s=20e-6)
    return build_digital_loop_analysis(small, controller_config=PIControllerConfig(kp=.002, ti_s=.003))


def _candidate(view, margins):
    view.live_fc.setValue(margins.critical_gain_crossover_hz)
    view.live_pm.setValue(margins.phase_margin_deg)
    view.gm_min.setValue(-100)
    view.ms_max.setValue(100)
    view.switch_max.setValue(100)
    # Maintained models intentionally stop below Fsw. Adopting a PI with that
    # missing evidence is an explicit conditional action, never FEASIBLE.
    view.allow_conditional.setChecked(True)
    view.generate_candidate()
    assert view.selected_point is not None
    assert view.can_apply(view.selected_point), view.details.toPlainText()
    return view.selected_point


def _llc_window(monkeypatch, pending_workers=None):
    from llc_design.core.spec import LLCDesignSpec
    from llc_design.gui.main_window import LLCMainWindow
    from llc_design.gui.solution_map_install import install_llc_solution_map
    window = LLCMainWindow(LLCDesignSpec())
    if pending_workers is None:
        monkeypatch.setattr(window, "_run_worker", lambda label, function, callback: callback(function()))
    else:
        window.thread_pool = SimpleNamespace(start=pending_workers.append)
    view = install_llc_solution_map(window)
    window._digital_loop_ready(_llc_analysis())
    return window, view


def _ttpl_window(monkeypatch):
    from pfc_design.control import build_pfc_control_lab_analysis
    from pfc_design.gui.main_window import PFCMainWindow
    from pfc_design.gui.solution_map_install import install_ttpl_solution_map
    window = PFCMainWindow()
    control = window.control_lab_view.control_lab
    # This test covers controller integration. Full waveform/render ownership
    # has independent V3 desktop coverage; keep this fixture inexpensive.
    monkeypatch.setattr(control, "set_result", lambda result, **kwargs: setattr(control, "result", result))
    view = install_ttpl_solution_map(window)
    config = control._config()
    config = replace(config, firmware=replace(config.firmware, current_loop_rate_hz=60_000.0, amc_rate_hz=30_000.0),
                     current_controller=replace(config.current_controller, sample_time_s=1 / 60_000.0),
                     current_sense=replace(config.current_sense, adc_bits=14, adc_vref_v=3.0,
                                           timing=replace(config.current_sense.timing, sample_rate_hz=60_000.0)))

    def run(config):
        control.set_busy(True)
        analysis = build_pfc_control_lab_analysis(config)
        window.result = (analysis, None, None)
        control.set_result(window.result)
        control.set_busy(False)

    monkeypatch.setattr(window, "run_ttpl_analysis", run)
    run(config)
    return window, view


def test_llc_candidate_apply_and_next_run_keep_exact_controller_and_spice(app, monkeypatch, tmp_path):
    from llc_design.control.digital_loop import PIControllerConfig
    from llc_design.gui.closed_loop_install import (
        _loop_analysis_for_controller,
        _public_controller,
    )
    from power_codegen import generate_llc_control_code
    window, view = _llc_window(monkeypatch)
    original = window.digital_loop_analysis
    point = _candidate(view, original.margins_nominal_delay)
    expected = PIControllerConfig(point.kp, point.ti_s, 1 / view.sources["voltage"].sample_rate_hz)
    assert window.digital_loop_analysis is original
    view.cancel_preview()
    assert window.digital_loop_analysis is original
    point = _candidate(view, original.margins_nominal_delay)
    window.external_control_design = object()  # obsolete Control Tools link
    view._apply_selected()
    applied = window.digital_loop_analysis
    assert applied is not original
    assert applied.controller_config == expected
    assert applied.small_signal is original.small_signal
    assert applied.analog_sense is original.analog_sense
    assert applied.adc_sampling is original.adc_sampling
    np.testing.assert_array_equal(applied.controller.numerator, expected.transfer_function().numerator)
    assert window.external_control_design is None
    public, source = _public_controller(window.external_control_design, applied)
    np.testing.assert_array_equal(public.b, applied.controller.numerator)
    assert _loop_analysis_for_controller(window, window.spec, public, source) is applied
    generated = generate_llc_control_code(applied, tmp_path / "llc")
    assert generated.validation.passed
    requests = []
    monkeypatch.setattr(window, "run_digital_loop", requests.append)
    window.digital_loop_view.request_analysis()
    assert len(requests) == 1
    assert requests[0]["loop"]["controller_config"] == expected
    assert requests[0]["small_signal"]["sample_time_s"] == expected.sample_time_s
    assert requests[0]["loop"]["adc_sampling"].control_sample_time_s == expected.sample_time_s
    window.digital_loop_view.out_min.setValue(-0.1)
    window.digital_loop_view.request_analysis()
    changed_limits = requests[-1]["loop"]["controller_config"]
    assert changed_limits.output_min == -0.1
    assert changed_limits.kp == expected.kp
    assert changed_limits.ti_s == expected.ti_s
    np.testing.assert_array_equal(changed_limits.transfer_function().numerator, applied.controller.numerator)
    window.close()


def test_llc_input_edits_and_inflight_changes_invalidate_candidate(app, monkeypatch):
    pending = []
    window, view = _llc_window(monkeypatch, pending_workers=pending)
    original = window.digital_loop_analysis
    _candidate(view, original.margins_nominal_delay)
    before_controller = window.digital_loop_view._analysis_options()["loop"]["controller_config"]
    window.digital_loop_view.adc_r.setValue(window.digital_loop_view.adc_r.value() + 1)
    assert not view.sources
    assert not view.apply_button.isEnabled()
    view._apply_selected()
    assert window.digital_loop_analysis is None
    assert window.digital_loop_view.result is None
    assert window.digital_loop_view.research_export.analysis is None
    assert window.digital_loop_view._analysis_options()["loop"]["controller_config"] == before_controller
    # Retain the real FunctionWorker and revision-bound delivery path. Only
    # scheduling is controlled so the input edit deterministically precedes delivery.
    window.digital_loop_view.request_analysis()
    assert len(pending) == 1
    worker = pending.pop()
    completed = worker.function()
    window.fields["output_capacitance_f"].setValue(window.fields["output_capacitance_f"].value() + 1)
    worker.signals.result.emit(completed)
    worker.signals.finished.emit()
    assert window.digital_loop_analysis is None
    assert window.digital_loop_view.result is None
    assert window.digital_loop_view.research_export.analysis is None
    assert not view.sources
    assert not window.digital_loop_view.codegen_button.isEnabled()
    assert not window._active_workers
    assert "Discarded result: power-stage inputs changed" in window.log_text.toPlainText()
    window.close()


def test_ttpl_apply_and_next_run_preserve_exact_hz_and_hidden_guided_values(app, monkeypatch, tmp_path):
    from llc_design.control.digital_loop import PIControllerConfig
    from pfc_design.control.handoff import build_pfc_control_handoff
    from power_codegen import generate_ttpl_control_code
    window, view = _ttpl_window(monkeypatch)
    control = window.control_lab_view.control_lab
    original = window.result[0]
    point = _candidate(view, original.current_loop.margins)
    expected = PIControllerConfig(point.kp, point.ti_s, 1 / view.sources["current"].sample_rate_hz,
                                  original.config.current_controller.output_min,
                                  original.config.current_controller.output_max)
    assert window.result[0] is original
    view._apply_selected()
    applied = window.result[0]
    assert applied.config.current_controller == expected
    assert applied.config.voltage_controller is original.config.voltage_controller
    assert applied.config.current_sense is original.config.current_sense
    assert control._config() is applied.config
    np.testing.assert_array_equal(applied.current_loop.controller.numerator, expected.transfer_function().numerator)
    handoff = build_pfc_control_handoff(applied)
    np.testing.assert_array_equal(handoff.current.b, applied.current_loop.controller.numerator)
    generated = generate_ttpl_control_code(applied, tmp_path / "ttpl", require_stable=False)
    assert generated.directory.exists()
    control.inductance.setValue(control.inductance.value() + 1)
    assert not view.sources
    edited = control._config()
    assert edited.current_controller is expected or edited.current_controller == expected
    assert edited.current_sense.adc_bits == 14
    assert edited.current_sense.adc_vref_v == 3.0
    assert edited.power_stage.boost_inductance_h != applied.config.power_stage.boost_inductance_h
    control.current_ctrl["kp"].setValue(control.current_ctrl["kp"].value() * 1.1)
    edited = control._config()
    assert edited.current_controller.kp == control.current_ctrl["kp"].value()
    assert edited.current_controller.ti_s == expected.ti_s
    assert edited.current_controller.sample_time_s == expected.sample_time_s
    from llc_design.control.digital_loop import ControllerKind, PIFControllerConfig
    control.current_ctrl["kind"].setCurrentIndex(control.current_ctrl["kind"].findData(ControllerKind.PIF))
    edited = control._config()
    assert isinstance(edited.current_controller, PIFControllerConfig)
    assert edited.current_controller.sample_time_s == expected.sample_time_s
    assert edited.current_controller.ti_s == expected.ti_s
    window.close()


def test_guided_reedit_preserves_adopted_controller_and_only_previews(app, monkeypatch):
    from llc_design.gui.system_modeling import (
        SystemModelingDesignDialog,
        apply_definition_to_ttpl_window,
    )
    from power_control_tools.system_definition import SystemTopology
    window, view = _ttpl_window(monkeypatch)
    baseline = window.result[0]
    _candidate(view, baseline.current_loop.margins)
    view._apply_selected()
    adopted = window.result[0].config.current_controller
    wizard = SystemModelingDesignDialog()
    wizard.select_topology(SystemTopology.TTPL_PFC)
    definition = wizard.build_definition()
    definition = replace(definition, controller=replace(definition.controller,
                         design_mode="auto", target_crossover_hz=800.0, target_phase_margin_deg=65.0))
    config = apply_definition_to_ttpl_window(window, definition)
    assert config.current_controller.kp == adopted.kp
    assert config.current_controller.ti_s == adopted.ti_s
    window.run_ttpl_analysis(config)
    control = window.control_lab_view.control_lab
    assert window.result[0].config.current_controller is config.current_controller
    assert control.tabs.currentWidget() is view
    assert view.live_fc.value() == 800
    assert view.live_pm.value() == 65
    assert window._pending_controller_intent is None
    # Reopening and backing/cancelling the wizard has no connection to Apply.
    before = window.result[0]
    wizard.target_fc.setValue(1234)
    wizard.reject()
    assert window.result[0] is before
    wizard.close()
    window.close()


def test_llc_guided_reedit_preserves_exact_pi_at_new_sample_rate(app, monkeypatch):
    from llc_design.gui.system_modeling import (
        SystemModelingDesignDialog,
        apply_definition_to_llc_window,
    )
    window, view = _llc_window(monkeypatch)
    _candidate(view, window.digital_loop_analysis.margins_nominal_delay)
    view._apply_selected()
    adopted = window.digital_loop_analysis.controller_config
    wizard = SystemModelingDesignDialog()
    definition = wizard.build_definition()
    sample_rate = 63_123.45
    definition = replace(definition, sensors=(replace(definition.sensors[0], sample_rate_hz=sample_rate),),
                         controller=replace(definition.controller, design_mode="auto",
                                            target_crossover_hz=3.0, target_phase_margin_deg=93.0))
    apply_definition_to_llc_window(window, definition)
    assert window.digital_loop_analysis is None
    pending_controller = window.digital_loop_view._solution_map_current_controller()
    assert pending_controller.kp == adopted.kp
    assert pending_controller.ti_s == adopted.ti_s
    assert pending_controller.sample_time_s == 1 / sample_rate
    window.digital_loop_view.request_analysis()
    rebuilt = window.digital_loop_analysis
    assert rebuilt.controller_config.kp == adopted.kp
    assert rebuilt.controller_config.ti_s == adopted.ti_s
    assert rebuilt.controller.sample_time_s == 1 / sample_rate
    assert rebuilt.adc_sampling.control_sample_time_s == 1 / sample_rate
    assert window.digital_loop_view.result_tabs.currentWidget() is view
    assert view.live_fc.value() == 3.0
    assert view.live_pm.value() == 93.0
    wizard.target_fc.setValue(1500)
    wizard.reject()
    assert window.digital_loop_analysis is rebuilt
    wizard.close()
    window.close()


@pytest.mark.parametrize("topology", ["llc", "ttpl"])
def test_late_previous_worker_cannot_replace_newer_controller_source(app, monkeypatch, topology):
    pending = []
    if topology == "ttpl":
        from pfc_design.control import build_pfc_control_lab_analysis
        from pfc_design.gui.main_window import PFCMainWindow
        from pfc_design.gui.solution_map_install import install_ttpl_solution_map
        window = PFCMainWindow()
        control = window.control_lab_view.control_lab
        monkeypatch.setattr(control, "set_result", lambda result, **kwargs: setattr(control, "result", result))
        def ready(result):
            window.result = result
            control.set_result(result)
        monkeypatch.setattr(window, "_ttpl_ready", ready)
        monkeypatch.setattr(window, "_run_worker", lambda label, function, callback: pending.append(callback))
        view = install_ttpl_solution_map(window)
        first_config = control._config()
        window.run_ttpl_analysis(first_config)
        control.inductance.setValue(control.inductance.value() + 10)
        second_config = control._config()
        window.run_ttpl_analysis(second_config)
        first = (build_pfc_control_lab_analysis(first_config), None, None)
        second = (build_pfc_control_lab_analysis(second_config), None, None)
        pending[1](second)
        pending[0](first)
        assert window.result is second
        assert control.result is second
        assert control._config() is second_config
    else:
        from llc_design.control.digital_loop import build_digital_loop_analysis
        from llc_design.core.spec import LLCDesignSpec
        from llc_design.gui.main_window import LLCMainWindow
        from llc_design.gui.solution_map_install import install_llc_solution_map
        window = LLCMainWindow(LLCDesignSpec())
        monkeypatch.setattr(window, "_run_worker", lambda label, function, callback: pending.append(callback))
        view = install_llc_solution_map(window)
        control = window.digital_loop_view
        control.request_analysis()
        control.kp.setValue(control.kp.value() * 1.1)
        control.request_analysis()
        first = _llc_analysis()
        second = build_digital_loop_analysis(
            first.small_signal, controller_config=replace(first.controller_config, kp=.0023),
        )
        pending[1](second)
        pending[0](first)
        assert window.digital_loop_analysis is second
        assert control.result is second
    assert view.sources
    window.close()


@pytest.mark.parametrize("topology", ["llc", "ttpl"])
def test_maintained_model_missing_fsw_requires_explicit_conditional_adoption(app, monkeypatch, topology):
    from llc_design.control.solution_map import SolutionStatus
    window, view = (_llc_window(monkeypatch) if topology == "llc" else _ttpl_window(monkeypatch))
    try:
        margins = (window.digital_loop_analysis.margins_nominal_delay if topology == "llc"
                   else window.result[0].current_loop.margins)
        view.live_fc.setValue(margins.critical_gain_crossover_hz)
        view.live_pm.setValue(margins.phase_margin_deg)
        view.gm_min.setValue(-100)
        view.ms_max.setValue(100)
        view.switch_max.setValue(100)
        view.generate_candidate()
        assert not view.allow_conditional.isChecked()
        assert view.selected_point.status == SolutionStatus.CONDITIONAL
        assert not view.selected_point.feasible
        assert not view.apply_button.isEnabled()
        assert view.selected_point.switching_loop_gain_db is None
        assert "UNKNOWN" in view.details.toPlainText()
        view.allow_conditional.setChecked(True)
        view.generate_candidate()
        assert view.selected_point.status == SolutionStatus.CONDITIONAL
        assert not view.selected_point.feasible
        assert view.apply_button.isEnabled()
    finally:
        window.close()


def test_llc_retains_real_operating_points_and_core_edit_clears_them(app, monkeypatch):
    from llc_design.control.analysis import build_small_signal_analysis
    from llc_design.control.digital_loop import build_digital_loop_analysis
    from llc_design.models.system import LLCSystemAnalyzer
    window, view = _llc_window(monkeypatch)
    try:
        baseline = window.digital_loop_analysis
        view.retain_current_source()
        retained = next(iter(view._retained_sources.values()))
        control = window.digital_loop_view
        control.load_percent.setValue(75)
        control.vbus.setValue(380)
        assert not view.sources
        assert len(view._retained_sources) == 1
        spec = baseline.small_signal.spec
        system = LLCSystemAnalyzer().analyze(spec)
        small = build_small_signal_analysis(spec, vbus_v=380, load_fraction=.75,
                                            system_analysis=system, sample_time_s=20e-6)
        next_result = build_digital_loop_analysis(
            small, controller_config=baseline.controller_config,
            analog_sense=baseline.analog_sense, adc_sampling=baseline.adc_sampling,
            command_timing=baseline.command_timing, fm_lut=baseline.fm_lut,
        )
        control.set_busy(True)
        window._digital_loop_ready(next_result)
        assert len(view._retained_sources) == 1
        assert len(view._active_corners()) == 2
        assert "Vbus=380" in view.source_info.text()
        assert "load=75%" in view.source_info.text()
        np.testing.assert_allclose(retained.fixed_loop_response * baseline.responses["controller"],
                                   baseline.responses["open_loop_nominal"])
        np.testing.assert_allclose(view._source().fixed_loop_response * next_result.responses["controller"],
                                   next_result.responses["open_loop_nominal"])
        margins = next_result.margins_nominal_delay
        view.live_fc.setValue(margins.critical_gain_crossover_hz)
        view.live_pm.setValue(margins.phase_margin_deg)
        view.gm_min.setValue(-100)
        view.ms_max.setValue(100)
        view.switch_max.setValue(100)
        view.generate_candidate()
        point = view.selected_point
        assert len(point.corner_results) == 2
        assert all((item.kp, item.ti_s) == (point.kp, point.ti_s) for item in point.corner_results)
        control.adc_r.setValue(control.adc_r.value() + 1)
        assert not view._retained_sources
        assert view.selected_point is None
    finally:
        window.close()


def test_ttpl_retains_real_points_and_inner_change_invalidates_outer_snapshots(app, monkeypatch):
    window, view = _ttpl_window(monkeypatch)
    try:
        control = window.control_lab_view.control_lab
        baseline = window.result[0]
        voltage_source = view.sources["voltage"]
        voltage_band = baseline.frequencies_hz < 0.5 / baseline.voltage_loop.controller.sample_time_s
        assert voltage_source.frequencies_hz[-1] < 0.5 * voltage_source.sample_rate_hz
        np.testing.assert_array_equal(voltage_source.frequencies_hz, baseline.frequencies_hz[voltage_band])
        np.testing.assert_allclose(
            voltage_source.fixed_loop_response * baseline.voltage_loop.responses["controller_cv"][voltage_band],
            baseline.voltage_loop.responses["open_voltage"][voltage_band],
        )
        assert "below voltage-loop Nyquist" in voltage_source.provenance
        view.retain_current_source()
        view.source_combo.setCurrentIndex(view.source_combo.findData("voltage"))
        view.retain_current_source()
        control.vin_rms.setValue(200)
        control.angle.setValue(45)
        assert not view.sources
        assert len(view._retained_sources) == 2
        changed = replace(baseline.config, power_stage=replace(
            baseline.config.power_stage, vin_rms_v=200, line_angle_deg=45,
        ))
        window.run_ttpl_analysis(changed)
        assert len(view._retained_sources) == 2
        assert len(view._active_corners()) == 2
        assert "Vin=200" in view.source_info.text()
        assert "line phase=45" in view.source_info.text()
        view.source_combo.setCurrentIndex(view.source_combo.findData("current"))
        original = window.result[0]
        point = _candidate(view, original.current_loop.margins)
        assert len(point.corner_results) == 2
        assert all((item.kp, item.ti_s) == (point.kp, point.ti_s) for item in point.corner_results)
        # Rebuilding a new inner controller clears the outer-loop snapshots,
        # whose fixed return path depended on the previous current closure.
        new_inner = replace(changed.current_controller, kp=changed.current_controller.kp * 1.01)
        window.run_ttpl_analysis(replace(changed, current_controller=new_inner))
        assert all(source.key == "current" for source in view._retained_sources.values())
        control.inductance.setValue(control.inductance.value() + 1)
        assert not view._retained_sources
        assert view.selected_point is None
    finally:
        window.close()


@pytest.mark.parametrize("start,count", [(10000, 0), (4990, 1), (4900, 2)])
def test_ttpl_short_outer_principal_band_is_visible_and_inactive(app, monkeypatch, start, count):
    window, view = _ttpl_window(monkeypatch)
    try:
        baseline = window.result[0].config
        view.source_combo.setCurrentIndex(view.source_combo.findData("voltage"))
        view.retain_current_source()
        window.run_ttpl_analysis(replace(baseline, frequency_start_hz=start,
                                         frequency_stop_hz=20000, frequency_points=100))
        assert view.source_combo.currentData() == "voltage"
        assert view.sources["voltage"].frequencies_hz.size == count
        assert not view.generate_button.isEnabled()
        assert not view.run_button.isEnabled()
        assert not view.apply_button.isEnabled()
        assert not view.retain_button.isEnabled()
        assert "Insufficient response coverage" in view.source_info.text()
        assert "insufficient" in view.source_combo.currentText()
        assert len(view._retained_sources) == 1
        assert not view._active_corners()
        view.generate_candidate()
        assert view.selected_point is None
        window.run_ttpl_analysis(baseline)
        assert len(view._active_corners()) == 2
        assert view.generate_button.isEnabled()
    finally:
        window.close()


def test_llc_spec_save_load_clears_hidden_field_stale_candidates_and_cancel_preserves(app, monkeypatch, tmp_path):
    import json
    from dataclasses import asdict
    from PySide6.QtWidgets import QFileDialog
    from llc_design.core.config import load_spec, save_project
    window, view = _llc_window(monkeypatch)
    try:
        original = window.digital_loop_analysis
        view.retain_current_source()
        candidate = _candidate(view, original.margins_nominal_delay)
        saved_path = tmp_path / "baseline-project.json"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(saved_path), "JSON"))
        window.save_json()
        saved = load_spec(saved_path)
        # Legacy JSON stores tuple-valued family lists as lists on reload.
        assert json.loads(json.dumps(asdict(saved))) == json.loads(json.dumps(asdict(window.spec)))
        assert view.selected_point is candidate
        assert len(view._retained_sources) == 1

        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: ("", "JSON"))
        window.load_json()
        assert view.selected_point is candidate
        assert view.apply_button.isEnabled()
        assert len(view._retained_sources) == 1

        changed_path = tmp_path / "hidden-field-project.json"
        # This field has no editing widget, so loading it emits no field signal.
        assert "resonant_cap_esr_ohm" not in window.fields
        changed = saved.clone(resonant_cap_esr_ohm=0.04)
        save_project(changed, changed_path)
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(changed_path), "JSON"))
        window.load_json()
        assert window.spec.resonant_cap_esr_ohm == 0.04
        assert view.selected_point is None
        assert not view.sources
        assert not view._retained_sources
        assert not view.apply_button.isEnabled()
        view._apply_selected()
        assert window.digital_loop_analysis is None
        assert window.digital_loop_view.research_export.analysis is None
        # The existing controller input survives; only its stale plant result
        # is retired. A real rebuild must use the restored hidden plant value.
        window.digital_loop_view.request_analysis()
        rebuilt = window.digital_loop_analysis
        assert rebuilt is not None and rebuilt is not original
        assert rebuilt.small_signal.spec.resonant_cap_esr_ohm == 0.04
        np.testing.assert_array_equal(rebuilt.controller.numerator, original.controller.numerator)
        np.testing.assert_array_equal(rebuilt.controller.denominator, original.controller.denominator)
    finally:
        window.close()
