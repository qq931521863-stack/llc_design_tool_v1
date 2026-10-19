"""Fail-closed magnetic proof schema tests, not fabricated physical acceptance.

The actual GUI/helper is run separately inside the qualified wheel/executable.
These synthetic payloads only test rejection and preservation of old gates.
"""
from copy import deepcopy

import pytest

from llc_design.tests.test_release_verification import MODULE, _proof


def _validate(proof):
    import sys
    MODULE.validate_report(proof, expected_version="9.4.1", expected_taxonomy_sha256="a" * 64,
                           expected_platform=sys.platform)


def test_schema_only_fixture_covers_new_contract():
    proof = _proof()
    assert "schema-only" in proof["magnetic_catalog"]["fixture"]
    _validate(proof)


@pytest.mark.parametrize("path,value", [
    (("magnetic_catalog",), None),
    (("magnetic_catalog", "resource_sha256", "csc_magnetic_catalog.json"), "0" * 64),
    (("magnetic_catalog", "resource_sha256", "magnetic_supplemental_records.json"), "0" * 64),
    (("magnetic_catalog", "source_documents_sha256", "CSC2025.pdf"), "0" * 64),
    (("magnetic_catalog", "selection_identity"), "0" * 64),
    (("magnetic_catalog", "record_id"), "csc2025:part:CM035060:032"),
    (("magnetic_catalog", "selection", "record", "al_nH_per_t2"), 82),
    (("magnetic_catalog", "reference", "default_full_calculate_disabled"), False),
    (("magnetic_catalog", "reference", "result", "l_zero_bias_uh"), float("nan")),
    (("magnetic_catalog", "reference", "result", "l_zero_bias_uh"), True),
    (("magnetic_catalog", "reference", "result", "l_zero_bias_uh"), 221.777),
    (("magnetic_catalog", "reference", "result", "selection_identity"), "0" * 64),
    (("magnetic_catalog", "reference", "report_consistency"), False),
    (("magnetic_catalog", "pfc", "real_calculate_and_apply"), False),
    (("magnetic_catalog", "pfc", "project_roundtrip"), False),
    (("magnetic_catalog", "pfc", "stale_consumers_cleared"), False),
    (("magnetic_catalog", "pfc", "recomputed_curves_identical"), False),
    (("magnetic_catalog", "pfc", "report_consistency"), False),
    (("magnetic_catalog", "pfc", "inputs", "input_rms_v"), 220),
    (("magnetic_catalog", "pfc", "inputs", "n_cores"), True),
    (("magnetic_catalog", "pfc", "results", "turns"), True),
    (("magnetic_catalog", "pfc", "results", "core_loss_w"), float("nan")),
    (("magnetic_catalog", "pfc", "results", "copper_loss_w"), float("inf")),
    (("magnetic_catalog", "pfc", "results", "l_no_bias_uh"), 221.778),
    (("magnetic_catalog", "pfc", "results", "l_full_load_peak_uh"), 200),
    (("magnetic_catalog", "pfc", "selection_identity"), "0" * 64),
    (("magnetic_catalog", "pfc", "core_identity"), "Magnetics High Flux 254/custom"),
    (("magnetic_catalog", "pfc", "self_loss_identity"), "Magnetics High Flux 254/custom"),
    (("magnetic_catalog", "pfc", "self_loss_w"), 0),
    (("magnetic_catalog", "pfc", "self_loss_w"), True),
    (("magnetic_catalog", "pfc", "applied", "inductance_uh"), 200),
    (("magnetic_catalog", "pfc", "curve_sha256"), "not-a-digest"),
    (("magnetic_catalog", "pfc", "printed_loss_check", "b_kg_beta_mean"), 1.),
    (("magnetic_catalog", "pfc", "printed_loss_check", "max_curve_absolute_error_w"), float("nan")),
    (("magnetic_catalog", "pfc", "curve_samples", "current_a"), True),
    (("magnetic_catalog", "pfc", "user_model_declaration"), None),
    (("magnetic_catalog", "pfc", "user_model_declaration", "B_T"), [0, True]),
    (("magnetic_catalog", "pfc", "user_model_declaration", "source"), "Manufacturer qualified"),
    (("magnetic_catalog", "llc_reference_only", "apply_disabled"), False),
    (("magnetic_catalog", "supplemental", "solver_baseline"), True),
    (("magnetic_catalog", "supplemental", "source_arithmetic_only"), False),
    (("magnetic_catalog", "supplemental", "Lpfc_min_uH"), True),
    (("magnetic_catalog", "supplemental", "engineering_cautions"), []),
])
def test_magnetic_contract_rejects_missing_stale_nonfinite_or_wrong_identity(path, value):
    proof = deepcopy(_proof())
    target = proof
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(RuntimeError, match="magnetic"):
        _validate(proof)


@pytest.mark.parametrize("section", ["reference", "pfc", "selection", "source_documents_sha256",
                                      "resource_sha256", "llc_reference_only", "supplemental"])
def test_magnetic_contract_requires_every_section(section):
    proof = _proof()
    del proof["magnetic_catalog"][section]
    with pytest.raises(RuntimeError, match="magnetic"):
        _validate(proof)


@pytest.mark.parametrize("key", ["power_stage_cases", "power_stage_buck_vmc", "power_stage_advanced",
                                 "two_stage_optimization", "power_stage_priority"])
def test_magnetic_evidence_cannot_replace_previous_workflow_gates(key):
    proof = _proof()
    del proof[key]
    with pytest.raises(RuntimeError):
        _validate(proof)
