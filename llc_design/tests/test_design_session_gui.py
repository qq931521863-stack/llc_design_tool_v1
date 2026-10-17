"""Save → reopen → recompute → generate → Apply → export stays evidence-safe."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json

import numpy as np
import pytest

from llc_design.tests.test_candidate_apply_integration import app, _candidate, _llc_window, _ttpl_window
from llc_design.control.design_session import decode_model, encode_model, replay_llc
from llc_design.gui.design_session_install import validate_map_state

pytestmark = pytest.mark.usefixtures("desktop_qapp")


@pytest.fixture(autouse=True)
def fail_unexpected_dialogs(monkeypatch):
    import llc_design.gui.main_window as llc
    import pfc_design.gui.main_window as pfc
    import llc_design.gui.widgets.digital_loop_view as digital
    def fail(*args, **kwargs):
        raise AssertionError(f"Unexpected operation error: {args[1:]}")
    for module in (llc, pfc, digital):
        monkeypatch.setattr(module, "show_operation_issue", fail)


def _restore_llc(window, document):
    loop = window.digital_loop_view
    inputs = decode_model(document["inputs"])
    prepared = validate_map_state(loop.solution_map_view, document["solution_map"], "llc")
    window.spec = inputs["spec"]
    window._load_spec_to_widgets(window.spec)
    loop._session_restore(document, prepared)


def test_llc_full_session_reopen_recompute_apply_export(app, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog
    from llc_design.control.digital_loop import build_digital_loop_analysis, export_digital_loop_analysis
    window, view = _llc_window(monkeypatch)
    second = None
    try:
        original = window.digital_loop_analysis
        precise = replace(original.controller_config, kp=.002123456789123, ti_s=.003123456789,
                          output_min=.0123456789, output_max=.923456789)
        analog = replace(original.analog_sense, calibration_gain=75.123456789)
        fresh = build_digital_loop_analysis(original.small_signal, controller_config=precise,
                                           analog_sense=analog)
        window._digital_loop_ready(fresh)
        view.retain_current_source()
        _candidate(view, fresh.margins_nominal_delay)
        view._apply_selected()
        applied = window.digital_loop_analysis
        assert applied.candidate_evidence["accepted_current"]
        # Preserve a selected preview too; Apply itself clears the transient preview.
        selected = _candidate(view, applied.margins_nominal_delay)
        path = tmp_path / "full-project.json"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(path), "JSON"))
        window.save_json()
        assert path.exists()
        document = json.loads(path.read_text())
        session = document["control_session"]
        assert session["solution_map"]["selected_candidate"]["kp"] == selected.kp
        assert len(session["solution_map"]["snapshots"]) == 1
        second, restored = _llc_window(monkeypatch)
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(path), "JSON"))
        second.load_json()
        loop = second.digital_loop_view
        assert second.digital_loop_analysis is None
        assert restored.selected_point is None and not restored.apply_button.isEnabled()
        assert not restored.allow_conditional.isChecked()
        assert not restored.sources
        assert not next(iter(restored._retained_sources.values())).replay_verified
        assert loop._solution_map_current_controller() == applied.controller_config
        assert not loop._candidate_evidence()["accepted_current"]
        before = decode_model(loop._session_capture()["inputs"])
        assert before["digital_loop"]["analog_sense"].calibration_gain == analog.calibration_gain
        loop.request_analysis()
        assert len(restored._active_corners()) == 2
        assert next(iter(restored._retained_sources.values())).replay_verified
        assert not second.digital_loop_analysis.candidate_evidence["accepted_current"]
        _candidate(restored, second.digital_loop_analysis.margins_nominal_delay)
        restored._apply_selected()
        result = second.digital_loop_analysis
        assert result.candidate_evidence["accepted_current"]
        exported = export_digital_loop_analysis(result, tmp_path / "export")
        settings = next(item for item in exported.values() if str(item).endswith(".json"))
        payload = json.loads(settings.read_text())
        assert payload["candidate_evidence"]["assessment"]["status"] == "CONDITIONAL"
        assert len(payload["candidate_evidence"]["operating_points"]) == 2
    finally:
        window.close()
        if second is not None:
            second.close()


def test_ttpl_full_session_keeps_hidden_config_and_replays_curves(app, monkeypatch, tmp_path):
    window, view = _ttpl_window(monkeypatch)
    try:
        control = window.control_lab_view.control_lab
        baseline = window.result[0].config
        view.retain_current_source()
        _candidate(view, window.result[0].current_loop.margins)
        view._apply_selected()
        exact = window.result[0].config
        _candidate(view, window.result[0].current_loop.margins)
        document = control._session_capture()
        source_response = np.array(next(iter(view._retained_sources.values())).fixed_loop_response)
        tampered = deepcopy(document)
        tampered["solution_map"]["snapshots"][0]["source"]["fixed_loop_response"][0] = [123456, 54321]
        # Caller cannot grant fresh evidence by replacing stored assessment text.
        tampered["candidate_evidence"]["current"]["assessment"]["status"] = "FEASIBLE"
        prepared = validate_map_state(view, tampered["solution_map"], "ttpl")
        control._session_restore(tampered, prepared)
        assert window.result is None and not view.apply_button.isEnabled()
        assert control._config() == exact
        assert control._config().current_sense.adc_bits == 14
        assert control._config().firmware.current_loop_rate_hz == 60_000
        assert not control._candidate_evidence()["current"]["accepted_current"]
        window.run_ttpl_analysis(control._config())
        fresh = next(iter(view._retained_sources.values()))
        assert fresh.replay_verified
        np.testing.assert_array_equal(fresh.fixed_loop_response, source_response)
        assert len(view._active_corners()) == 2
        assert not window.result[0].candidate_evidence["current"]["accepted_current"]
        _candidate(view, window.result[0].current_loop.margins)
        view._apply_selected()
        assert window.result[0].candidate_evidence["current"]["assessment"]["status"] == "CONDITIONAL"
        assert window.result[0].candidate_evidence["current"]["accepted_current"]
        assert baseline.current_sense.adc_bits == exact.current_sense.adc_bits
    finally:
        window.close()


def test_llc_restore_external_exact_hz_and_never_import_local_limits(app, monkeypatch):
    from llc_design.control.digital_loop import DigitalTransferFunction, build_digital_loop_analysis
    window, view = _llc_window(monkeypatch)
    try:
        original = window.digital_loop_analysis
        digital = DigitalTransferFunction([.010123456789, -.008123456789], [1., -1.], 1/61543.21,
                                           name="Exact external H(z)")
        from llc_design.control.analysis import build_small_signal_analysis
        small = build_small_signal_analysis(original.small_signal.spec, sample_time_s=digital.sample_time_s)
        result = build_digital_loop_analysis(small, controller_transfer_function=digital)
        window._digital_loop_ready(result)
        # External result ownership is independent of previous local UI values.
        window.digital_loop_view.set_external_controller(
            __import__('power_control_tools.models', fromlist=['DigitalTransferFunction']).DigitalTransferFunction(
                tuple(digital.numerator), tuple(digital.denominator), 1/digital.sample_time_s, digital.name))
        window._digital_loop_ready(result)
        document = window.digital_loop_view._session_capture()
        _restore_llc(window, document)
        options = window.digital_loop_view._analysis_options()
        restored = options["loop"]["controller_transfer_function"]
        np.testing.assert_array_equal(restored.numerator, digital.numerator)
        assert restored.sample_time_s == digital.sample_time_s
        assert options["loop"]["controller_config"] is None
        window.digital_loop_view.request_analysis()
        assert window.digital_loop_analysis.controller_config is None
        np.testing.assert_array_equal(window.digital_loop_analysis.controller.numerator, digital.numerator)
    finally:
        window.close()


@pytest.mark.parametrize("mutation", ["schema", "version", "snapshot", "source_model", "controls"])
def test_invalid_session_load_is_atomic(app, monkeypatch, tmp_path, mutation):
    from PySide6.QtWidgets import QFileDialog
    import llc_design.gui.main_window as module
    window, view = _llc_window(monkeypatch)
    try:
        view.retain_current_source()
        point = _candidate(view, window.digital_loop_analysis.margins_nominal_delay)
        document = window.digital_loop_view._session_capture()
        if mutation == "schema": document["schema"] = "malicious/other"
        if mutation == "version": document["schema_version"] = 100
        if mutation == "snapshot": document["solution_map"]["snapshots"][0]["source"]["frequencies_hz"] = [1]
        if mutation == "source_model": document["solution_map"]["snapshots"][0]["source"]["model_inputs"] = {"__model__": "os.system", "fields": {}}
        if mutation == "controls": document["solution_map"]["controls"]["ms_max"] = True
        from llc_design.core.config import create_project_document
        payload = create_project_document(window.spec)
        payload["control_session"] = document
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(payload))
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(path), "JSON"))
        errors = []
        monkeypatch.setattr(module, "show_operation_issue", lambda *args, **kwargs: errors.append(args))
        original = window.digital_loop_analysis
        window.load_json()
        assert errors
        assert window.digital_loop_analysis is original
        assert view.selected_point is point
        assert view.apply_button.isEnabled()
    finally:
        window.close()


def test_saved_model_revision_requires_recompute_and_legacy_project_still_loads(app, monkeypatch):
    window, view = _llc_window(monkeypatch)
    try:
        view.retain_current_source()
        document = window.digital_loop_view._session_capture()
        document["model_revision"] = "older-model-v0"
        _restore_llc(window, document)
        assert "revision changed" in view._corner_notice
        assert not view.apply_button.isEnabled()
        assert not next(iter(view._retained_sources.values())).replay_verified
    finally:
        window.close()


def test_llc_changed_spec_discards_derived_damping_and_preserves_auto_mode(app, monkeypatch):
    window, _ = _llc_window(monkeypatch)
    try:
        loop = window.digital_loop_view
        assert loop.auto_pcmd.isChecked()
        first = loop._analysis_options()
        assert first["loop"]["command_pu"] is None
        assert "series_resistance_ohm" in first["small_signal"]
        window.fields["pout_w"].setValue(window.fields["pout_w"].value() * .7)
        edited = loop._analysis_options()
        assert "series_resistance_ohm" not in edited["small_signal"]
        assert edited["loop"]["command_pu"] is None
        document = loop._session_capture()
        inputs = decode_model(document["inputs"])
        assert "small_signal_metadata" not in inputs
        _restore_llc(window, document)
        assert loop.auto_pcmd.isChecked()
        loop.load_percent.setValue(60)
        assert loop._analysis_options()["loop"]["command_pu"] is None
    finally:
        window.close()


def test_ttpl_load_invalidates_pending_callback_and_unfreezes_inputs(app, monkeypatch):
    from pfc_design.gui.main_window import PFCMainWindow
    from pfc_design.gui.solution_map_install import install_ttpl_solution_map
    window = PFCMainWindow()
    pending, accepted = [], []
    monkeypatch.setattr(window, "_run_worker", lambda label, function, callback: pending.append(callback))
    monkeypatch.setattr(window, "_ttpl_ready", lambda result: accepted.append(result))
    view = install_ttpl_solution_map(window)
    control = window.control_lab_view.control_lab
    try:
        document = control._session_capture()
        window._run_worker("pending", lambda: None, window._ttpl_ready)
        window.set_busy(True, "old work")
        assert not control.run_button.isEnabled()
        prepared = validate_map_state(view, document["solution_map"], "ttpl")
        control._session_restore(document, prepared)
        pending[0]("old result")
        assert not accepted
        assert control.run_button.isEnabled()
        assert control.input_tabs.isEnabled()
        assert not control.codegen_button.isEnabled()
    finally:
        window.close()


def test_ttpl_save_load_clears_all_export_owners_and_refreshes_controller_fields(app, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog
    from llc_design.control.digital_loop import TwoP2ZControllerConfig
    window, view = _ttpl_window(monkeypatch)
    try:
        control = window.control_lab_view.control_lab
        analysis = window.result[0]
        exact = window.control_lab_view.exact_hz_view
        spice = window.control_lab_view.ngspice_closed_loop_view
        exact.set_analysis(analysis)
        spice.set_analysis(analysis)
        assert exact.export_button.isEnabled()
        config = replace(analysis.config, current_controller=TwoP2ZControllerConfig(
            .123456789123, -.023456789123, .000123456789, -.7, .01,
            sample_time_s=analysis.current_loop.controller.sample_time_s, output_min=-2., output_max=.98))
        # The fixture bypasses slow waveforms but retains ordinary config ownership.
        control._solution_map_restore_config(config)
        path = tmp_path / "ttpl.json"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(path), "JSON"))
        window.save_ttpl_session()
        assert path.exists()
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(path), "JSON"))
        window.load_ttpl_session()
        assert control._config().current_controller == config.current_controller
        assert control.current_ctrl["b0"].isEnabled()
        assert not control.current_ctrl["kp"].isEnabled()
        assert exact.analysis is None and exact.handoff is None
        assert not exact.export_button.isEnabled() and not exact.codegen_button.isEnabled()
        assert spice.analysis is None and not spice.run_button.isEnabled()
    finally:
        window.close()


def test_malformed_nested_evidence_rejected_before_model_mutation(app, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog
    import pfc_design.gui.main_window as module
    window, view = _ttpl_window(monkeypatch)
    try:
        control = window.control_lab_view.control_lab
        original = control._config()
        document = control._session_capture()
        modified = replace(original, power_stage=replace(original.power_stage, output_power_w=1234.0))
        document["inputs"] = encode_model(modified)
        document["candidate_evidence"] = {"current": [1]}
        path = tmp_path / "bad-evidence.json"
        path.write_text(json.dumps(document))
        errors = []
        monkeypatch.setattr(module, "show_operation_issue", lambda *args, **kwargs: errors.append(args))
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(path), "JSON"))
        window.load_ttpl_session()
        assert errors
        assert control._config() == original
        assert window.result is not None
    finally:
        window.close()


def test_failed_llc_project_save_preserves_existing_file(tmp_path, monkeypatch):
    from pathlib import Path
    from llc_design.core.config import save_project
    from llc_design.core.spec import LLCDesignSpec
    target = tmp_path / "existing.json"
    target.write_text("existing project")
    monkeypatch.setattr(Path, "replace", lambda *_args: (_ for _ in ()).throw(OSError("disk issue")))
    with pytest.raises(OSError):
        save_project(LLCDesignSpec(), target)
    assert target.read_text() == "existing project"
    assert list(tmp_path.iterdir()) == [target]


def test_llc_normal_signal_run_save_and_sample_rate_change(app, monkeypatch):
    window, view = _llc_window(monkeypatch)
    try:
        loop = window.digital_loop_view
        loop.request_analysis()
        document = loop._session_capture()
        assert decode_model(document["inputs"])["small_signal"]["control_input_kind"].value == "frequency_hz"
        loop.sample_us.setValue(100)
        assert "frequencies_hz" not in loop._analysis_options()["loop"]
        loop.request_analysis()
        assert window.digital_loop_analysis.frequencies_hz[-1] < 5000
        assert view.generate_button.isEnabled()
        loop._session_capture()
    finally:
        window.close()


def test_checked_unrecomputed_snapshot_cannot_silently_drop_from_common_design(app, monkeypatch):
    from llc_design.gui.design_session_install import _replay_retained
    window, view = _llc_window(monkeypatch)
    try:
        view.retain_current_source()
        point = _candidate(view, window.digital_loop_analysis.margins_nominal_delay)
        key, source = next(iter(view._retained_sources.items()))
        view._retained_sources[key] = replace(source, replay_verified=False, model_inputs=None,
                                              compatibility_key="unverified-incompatible-signature")
        view.set_sources(list(view.sources.values()))
        assert key in view._retained_sources
        _replay_retained(view, "llc")
        view._refresh_corners()
        assert not view.can_apply(point)
        assert not view.generate_button.isEnabled()
        view.generate_candidate()
        assert view.selected_point is None
        assert "need recomputation" in view.details.toPlainText()
        view._checked_corner_ids.clear()
        view._refresh_corners()
        assert view.generate_button.isEnabled()
    finally:
        window.close()


def test_target_edits_after_reopen_survive_first_run(app, monkeypatch):
    window, view = _llc_window(monkeypatch)
    try:
        document = window.digital_loop_view._session_capture()
        _restore_llc(window, document)
        view.live_fc.setValue(1123.0)
        view.gm_min.setValue(8.5)
        window.digital_loop_view.request_analysis()
        assert view.live_fc.value() == 1123.0
        assert view.gm_min.value() == 8.5
    finally:
        window.close()
