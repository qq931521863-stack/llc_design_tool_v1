"""Candidate classifications survive adoption/export without becoming a pass."""
from __future__ import annotations

from dataclasses import replace
import json

import numpy as np
import pytest

from llc_design.control.candidate_evidence import (
    build_candidate_evidence, check_evidence, controller_identity, exact_signature,
    historical_evidence, llc_export_evidence, llc_model_signature, ttpl_model_signature,
)
from llc_design.control.digital_loop import PIControllerConfig, PIFControllerConfig, build_digital_loop_analysis, export_digital_loop_analysis
from llc_design.control.smart_control import FixedLoopCorner, evaluate_common_solution_point
from llc_design.control.solution_map import SolutionMapConstraints, SolutionStatus


def _sample_evidence(*, conditional=True):
    f = np.geomspace(10.0, 20_000.0, 1000)
    fixed = 1.0 / (1.0 + 1j * f / (1000.0 / np.tan(np.deg2rad(60))))
    corners = [FixedLoopCorner("nominal exact", f, fixed, 50_000.0, 20_000.0, "maintained model"),
               FixedLoopCorner("low input / high load", f, .8 * fixed, 50_000.0,
                               30_000.0 if conditional else 20_000.0, "maintained model")]
    constraints = SolutionMapConstraints(maximum_switching_loop_gain_db=0)
    point = evaluate_common_solution_point(corners, target_crossover_hz=1000,
                                           target_phase_margin_deg=60, constraints=constraints)
    controller = PIControllerConfig(point.kp, point.ti_s, 1 / 50_000)
    evidence = build_candidate_evidence(
        point=point, corners=corners, constraints=constraints, controller=controller,
        loop_key="voltage", model_signature="exact model", compatibility_key="same design",
        conditional_approved=conditional,
        source_models={corner.corner_id: {"point": index, "hidden": 1.2345678901234567}
                       for index, corner in enumerate(corners)},
    )
    return evidence, point, corners, controller, constraints


def test_conditional_evidence_preserves_reasons_points_constraints_and_exact_hz():
    evidence, point, corners, controller, constraints = _sample_evidence()
    assert evidence["assessment"]["status"] == "CONDITIONAL"
    assert evidence["assessment"]["message"] == point.message
    assert evidence["conditional_adoption"] is True
    assert [item["id"] for item in evidence["operating_points"]] == list(point.corner_ids)
    assert evidence["operating_points"][1]["assessment"]["switching_loop_gain_db"] is None
    assert evidence["operating_points"][1]["assessment"]["status"] == "CONDITIONAL"
    assert evidence["controller"] == controller_identity(controller.transfer_function(), controller)
    assert evidence["constraints"]["maximum_switching_loop_gain_db"] == constraints.maximum_switching_loop_gain_db
    assert len({item["model_signature"] for item in evidence["operating_points"]}) == 2
    assert json.loads(json.dumps(evidence, allow_nan=False)) == evidence


def test_reopened_evidence_cannot_be_reaccepted_by_matching_hashes():
    evidence, _, _, controller, _ = _sample_evidence()
    restored = historical_evidence(evidence)
    assert restored["accepted_current"] is False
    check_evidence(restored, controller=controller.transfer_function(), config=controller,
                   model_signature="exact model")
    assert restored["freshness"] == "historical"
    assert not restored["accepted_current"]
    assert evidence["freshness"] == "current"
    assert restored["assessment"] == evidence["assessment"]


@pytest.mark.parametrize("status", [SolutionStatus.UNKNOWN, SolutionStatus.MS_CONSTRAINT_FAIL, SolutionStatus.INVALID_POINT])
def test_failed_and_unknown_candidates_cannot_be_accepted(status):
    _, point, corners, controller, constraints = _sample_evidence()
    with pytest.raises(ValueError):
        build_candidate_evidence(point=replace(point, status=status), corners=corners,
                                 controller=controller, constraints=constraints,
                                 loop_key="voltage", model_signature="x", compatibility_key="y",
                                 conditional_approved=True)


def test_nonfinite_candidate_metrics_rejected_instead_of_passing_as_null():
    _, point, corners, controller, constraints = _sample_evidence()
    with pytest.raises(ValueError, match="Nonfinite"):
        build_candidate_evidence(point=replace(point, ms=float("inf")), corners=corners,
                                 controller=controller, constraints=constraints,
                                 loop_key="voltage", model_signature="x", compatibility_key="y",
                                 conditional_approved=True)


@pytest.mark.parametrize("mismatch", ["pif", "sample_time", "corner_sample_rate", "common_kp", "common_ti"])
def test_unassessed_controller_identity_cannot_inherit_current_evidence(mismatch):
    _, point, corners, controller, constraints = _sample_evidence()
    if mismatch == "pif":
        controller = PIFControllerConfig(controller.kp, controller.ti_s, 10.0, controller.sample_time_s)
    elif mismatch == "sample_time":
        controller = replace(controller, sample_time_s=4 * controller.sample_time_s)
    elif mismatch == "corner_sample_rate":
        corners[-1] = replace(corners[-1], sample_rate_hz=corners[-1].sample_rate_hz / 4)
    elif mismatch == "common_kp":
        point = replace(point, kp=point.kp * 1.01)
    else:
        point = replace(point, ti_s=point.ti_s * 1.01)
    with pytest.raises(ValueError, match="controller"):
        build_candidate_evidence(point=point, corners=corners, controller=controller,
                                 constraints=constraints, loop_key="voltage",
                                 model_signature="x", compatibility_key="y",
                                 conditional_approved=True)


def test_controller_limit_change_invalidates_even_when_hz_is_unchanged():
    evidence, _, _, controller, _ = _sample_evidence()
    edited = replace(controller, output_min=-.123)
    check_evidence(evidence, controller=edited.transfer_function(), config=edited, model_signature="exact model")
    assert not evidence["accepted_current"]
    assert evidence["freshness"] == "invalidated"
    assert evidence["assessment"]["status"] == "CONDITIONAL"


def _llc_with_evidence():
    from llc_design.tests.test_candidate_apply_integration import _llc_analysis
    from llc_design.gui.solution_map_install import _remove_controller
    source = _llc_analysis()
    f = source.frequencies_hz
    corner = FixedLoopCorner("LLC nominal", f,
                             _remove_controller(source.responses["open_loop_nominal"], source.responses["controller"]),
                             1 / source.controller.sample_time_s,
                             source.small_signal.operating_point.switching_frequency_hz, "LLC maintained")
    constraints = SolutionMapConstraints(minimum_gain_margin_db=-100, maximum_sensitivity=100,
                                         maximum_switching_loop_gain_db=100)
    point = evaluate_common_solution_point([corner],
                                           target_crossover_hz=source.margins_nominal_delay.critical_gain_crossover_hz,
                                           target_phase_margin_deg=source.margins_nominal_delay.phase_margin_deg,
                                           constraints=constraints)
    controller = replace(source.controller_config, kp=point.kp, ti_s=point.ti_s)
    evidence = build_candidate_evidence(point=point, corners=[corner], constraints=constraints,
                                        controller=controller, loop_key="voltage", model_signature=llc_model_signature(source),
                                        compatibility_key="LLC", conditional_approved=True)
    return build_digital_loop_analysis(source.small_signal, controller_config=controller,
                                       fm_lut=source.fm_lut, command_pu=source.fm_operating_point.command_pu,
                                       analog_sense=source.analog_sense, adc_sampling=source.adc_sampling,
                                       command_timing=source.command_timing, frequencies_hz=f,
                                       candidate_evidence=evidence)


def test_llc_apply_and_all_export_metadata_keep_conditional_evidence(tmp_path):
    from power_control_tools.research_export import build_llc_research_export, render_python, render_matlab
    from power_codegen import generate_llc_control_code
    analysis = _llc_with_evidence()
    evidence = analysis.candidate_evidence
    assert evidence["accepted_current"]
    assert evidence["assessment"]["status"] == "CONDITIONAL"
    paths = export_digital_loop_analysis(analysis, tmp_path / "loop")
    assert json.loads(paths["settings_json"].read_text(encoding="utf-8"))["candidate_evidence"] == evidence
    research = build_llc_research_export(analysis)
    assert json.loads(research.metadata_json)["candidate_evidence"] == evidence
    assert "candidate_evidence" in render_python(research)
    assert "candidate_evidence" in render_matlab(research)
    code = generate_llc_control_code(analysis, tmp_path / "code")
    assert json.loads(code.files["snapshot"].read_text(encoding="utf-8"))["candidate_evidence"] == evidence


def test_hidden_llc_and_ttpl_model_changes_invalidate_signatures():
    from pfc_design.control.config import PFCControlLabConfig
    analysis = _llc_with_evidence()
    modified = replace(analysis, adc_sampling=replace(analysis.adc_sampling,
                                                      adc_clock_hz=analysis.adc_sampling.adc_clock_hz + 1))
    assert llc_model_signature(modified) != llc_model_signature(analysis)
    assert llc_export_evidence(modified)["freshness"] == "invalidated"
    config = PFCControlLabConfig()
    edited = replace(config, current_sense=replace(config.current_sense, adc_bits=config.current_sense.adc_bits + 1))
    assert ttpl_model_signature(config, "current", [1, 2, 3]) != ttpl_model_signature(edited, "current", [1, 2, 3])
    inner = replace(config, current_controller=replace(config.current_controller, kp=config.current_controller.kp * 1.001))
    assert ttpl_model_signature(config, "voltage", [1, 2, 3]) != ttpl_model_signature(inner, "voltage", [1, 2, 3])
    assert exact_signature(float("nan")) != exact_signature(float("inf"))


# Reuse the established GUI fixture so this regression follows the real Apply
# signal and maintained-result owner rather than manufacturing an export record.
from llc_design.tests.test_candidate_apply_integration import app  # noqa: E402,F401


@pytest.mark.usefixtures("desktop_qapp")
def test_llc_gui_common_apply_keeps_assessment_and_edit_invalidates(app, monkeypatch):
    from llc_design.tests.test_candidate_apply_integration import _llc_window, _candidate
    window, view = _llc_window(monkeypatch)
    try:
        view.retain_current_source()
        point = _candidate(view, window.digital_loop_analysis.margins_nominal_delay)
        assert len(point.corner_results) == 2
        view._apply_selected()
        evidence = window.digital_loop_analysis.candidate_evidence
        assert evidence is window.digital_loop_view._candidate_evidence()
        assert evidence["accepted_current"]
        assert evidence["assessment"]["message"] == point.message
        assert [item["id"] for item in evidence["operating_points"]] == list(point.corner_ids)
        assert all(item["model_signature"] for item in evidence["operating_points"])
        assert "APPLIED CANDIDATE EVIDENCE" in view.details.toPlainText()
        assert "CONDITIONAL" in view.details.toPlainText()
        view.gm_min.setValue(view.gm_min.value() + 1)
        assert not evidence["accepted_current"]
        assert "constraints" in evidence["freshness_reason"]
        window.digital_loop_view.out_min.setValue(-.2)
        assert evidence["freshness"] == "invalidated"
        assert not evidence["accepted_current"]
        assert evidence["assessment"]["status"] == "CONDITIONAL"
    finally:
        window.close()


@pytest.mark.usefixtures("desktop_qapp")
def test_ttpl_gui_common_apply_exports_and_restore_never_upgrades(app, monkeypatch, tmp_path):
    from llc_design.tests.test_candidate_apply_integration import _ttpl_window, _candidate
    from pfc_design.control.handoff import build_pfc_control_handoff
    from power_control_tools.research_export import build_pfc_research_export
    from power_codegen.pfc_exact import export_pfc_exact_hz_manifest
    from power_codegen import generate_ttpl_control_code
    window, view = _ttpl_window(monkeypatch)
    control = window.control_lab_view.control_lab
    try:
        view.retain_current_source()
        point = _candidate(view, window.result[0].current_loop.margins)
        view._apply_selected()
        analysis = window.result[0]
        evidence = analysis.candidate_evidence["current"]
        assert evidence["accepted_current"]
        assert evidence["assessment"]["status"] == "CONDITIONAL"
        assert len(evidence["operating_points"]) == 2
        assert evidence["assessment"]["message"] == point.message
        assert analysis.summary_dict()["candidate_evidence"]["current"] == evidence
        assert build_pfc_control_handoff(analysis).as_dict()["candidate_evidence"]["current"] == evidence
        assert json.loads(build_pfc_research_export(analysis).metadata_json)["candidate_evidence"]["current"] == evidence
        path = export_pfc_exact_hz_manifest(analysis, tmp_path / "exact.json")
        assert json.loads(path.read_text(encoding="utf-8"))["candidate_evidence"]["current"] == evidence
        code = generate_ttpl_control_code(analysis, tmp_path / "code", require_stable=False)
        assert json.loads(code.files["snapshot"].read_text(encoding="utf-8"))["analysis"]["candidate_evidence"]["current"] == evidence
        control._restore_candidate_evidence(analysis.candidate_evidence)
        assert not control._candidate_evidence()["current"]["accepted_current"]
        window.run_ttpl_analysis(control._config())
        assert window.result[0].candidate_evidence["current"]["freshness"] == "historical"
        assert not window.result[0].candidate_evidence["current"]["accepted_current"]
    finally:
        window.close()


@pytest.mark.parametrize("workspace,payload", [("ttpl", {"current": [1]}), ("llc", [1]),
                                               ("ttpl", {"other": {}}), ("llc", {"schema_version": 2})])
def test_malformed_saved_evidence_rejected_before_restoration(workspace, payload):
    from llc_design.control.candidate_evidence import validate_saved_evidence
    with pytest.raises(ValueError):
        validate_saved_evidence(workspace, payload)


def test_saved_evidence_validator_never_trusts_claimed_acceptance():
    from llc_design.control.candidate_evidence import validate_saved_evidence
    evidence, *_ = _sample_evidence()
    assert evidence["accepted_current"]
    restored = validate_saved_evidence("llc", evidence)
    assert restored["freshness"] == "historical"
    assert not restored["accepted_current"]
    assert evidence["accepted_current"]
