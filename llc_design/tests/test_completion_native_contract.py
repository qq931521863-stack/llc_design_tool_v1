"""Mandatory new artifact proofs; schema mutations never stand in for real Qt QA."""
import inspect
from copy import deepcopy

import pytest

from llc_design.tests import test_release_verification as release


def validate(payload):
    release.MODULE.validate_report(payload, expected_version="9.4.1",
        expected_taxonomy_sha256="a" * 64, expected_platform=payload["platform"])


def test_complete_fixture_is_accepted_and_old_schema_is_rejected():
    validate(release._proof())
    with pytest.raises(RuntimeError, match="report"):
        validate(release._proof(proof_schema=2))


@pytest.mark.parametrize("field", ["power_stage_advanced", "two_stage_optimization"])
def test_new_proof_sections_are_mandatory(field):
    payload = release._proof()
    del payload[field]
    with pytest.raises(RuntimeError, match="report"):
        validate(payload)


@pytest.mark.parametrize("topology", ["T05", "T11", "T18"])
@pytest.mark.parametrize("field,value", [
    ("branches_clicked", []), ("branches_clicked", [False]),
    ("samples_per_branch", True), ("samples_per_branch", 0),
    ("max_curve_absolute_error", 1e-12), ("max_curve_absolute_error", float("nan")),
    ("waveform_sha256", "missing"), ("design_id", "0" * 20), ("inputs", {}),
    ("project_roundtrip", False), ("recomputed_waveforms_identical", False),
    ("report_consistency", False), ("provenance", {}),
])
def test_advanced_partial_or_inconsistent_evidence_rejected(topology, field, value):
    payload = release._proof()
    payload["power_stage_advanced"][topology][field] = value
    with pytest.raises(RuntimeError, match="report"):
        validate(payload)


@pytest.mark.parametrize("topology", ["T05", "T11", "T18"])
def test_duplicate_branch_cannot_hide_missing_coverage(topology):
    payload = release._proof()
    selected = payload["power_stage_advanced"][topology]["branches_clicked"]
    selected[-1] = selected[0]
    with pytest.raises(RuntimeError, match="report"):
        validate(payload)


@pytest.mark.parametrize("field,value", [
    ("real_stage_a_and_b", False), ("verification_scope", "critical"),
    ("verified_td_points", True), ("verified_td_points", 0), ("verified_td_points", 2),
    ("fha_td_validity", "UNKNOWN"), ("fha_td_validity", "FAIL"),
    ("waveform_sha256", "missing"), ("loss_source", "FHA_OPERATING_POINT"),
    ("total_loss_w", -1), ("total_loss_w", float("nan")), ("total_loss_w", float("inf")),
    ("selected_candidate", {}), ("applied_ln_ratio", 5.6), ("apply_exact", False),
    ("project_roundtrip", False), ("restored_history_requires_rerun", False),
    ("report_consistency", False), ("model_limit", ""),
])
def test_two_stage_missing_scope_and_stale_workflow_evidence_rejected(field, value):
    payload = release._proof()
    payload["two_stage_optimization"][field] = value
    with pytest.raises(RuntimeError, match="report"):
        validate(payload)


@pytest.mark.parametrize("fault", ["identity", "rejected", "loss", "waveform", "source", "duplicate_td", "wrong_spec"])
def test_selected_candidate_is_checked_against_actual_td_source(fault):
    payload = release._proof()
    candidate = payload["two_stage_optimization"]["selected_candidate"]
    if fault == "identity": candidate["candidate_id"] = "0" * 64
    elif fault == "rejected": candidate["rejected"] = True
    elif fault == "loss": candidate["total_loss_w"] = 0
    elif fault == "waveform": candidate["sources"][0]["waveform_sha256"] = "0" * 64
    elif fault == "source": candidate["sources"][0]["components"]["semiconductor_current"] = "FHA_OPERATING_POINT"
    elif fault == "duplicate_td": candidate["sources"].append(deepcopy(candidate["sources"][0]))
    else: candidate["spec"]["fields"]["ln_ratio"] = 5.6
    with pytest.raises(RuntimeError, match="report"):
        validate(payload)


def test_timeout_allows_real_periodic_solves_without_relaxing_numerics():
    assert inspect.signature(release.MODULE.verify_bundle).parameters["timeout_s"].default == 900.0
    launcher = (release.ROOT / "packaging/launcher.py").read_text(encoding="utf-8")
    assert "advanced_proof = _exercise_advanced_power_stage(" in launcher
    assert "optimizer_proof = _exercise_two_stage(" in launcher
    assert "view.run_button.click()" in launcher and "view.apply_button.click()" in launcher
    assert "QTest.mouseClick(stage.schematic" in launcher
