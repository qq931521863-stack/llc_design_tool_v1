"""Reject incomplete integrated proofs; synthetic fixtures are schema tests only.

Production launcher exercises real modules, solver, Qt consumers and reports
inside the separately qualified installed wheel and frozen executable.
"""
import hashlib
import json
import sys
from copy import deepcopy

import pytest

from llc_design.tests.test_release_verification import MODULE, _proof


def _validate(proof):
    MODULE.validate_report(proof, expected_version="9.4.1", expected_taxonomy_sha256="a" * 64,
                           expected_platform=sys.platform)


def test_schema_four_requires_integrated_evidence():
    proof = _proof()
    assert proof["proof_schema"] == 4
    assert "schema-only" in proof["integrated_magnetics"]["fixture"]
    _validate(proof)


@pytest.mark.parametrize("old_schema", [1, 2, 3])
def test_pre_integrated_proofs_are_explicitly_obsolete(old_schema):
    with pytest.raises(RuntimeError, match="invalid packaged self-test report"):
        _validate(_proof(proof_schema=old_schema))


@pytest.mark.parametrize("path,value", [
    (("integrated_magnetics",), None),
    (("modules",), {}), (("modules", "llc_design.core.integrated_magnetics"), ""),
    (("golden", "lr_h"), 20e-6), (("golden", "flux_density_t"), float("nan")),
    (("golden", "physical_current_a"), True),
    (("geometry_counterexample", "reachable"), True),
    (("geometry_counterexample", "available_window_height_m"), .01),
    (("spec", "magnetics_mode"), "SEPARATE"), (("spec", "integrated_lp_h"), 320e-6),
    (("spec", "primary_turns"), True), (("spec", "integrated_measurement_note"), "Hardware validated"),
    (("spec_identity",), "0"*64), (("core",), "PQ40_REF"),
    (("electrical", "lr_h"), 7e-6), (("electrical", "effective_turns_ratio"), 7.5),
    (("electrical", "physical_magnetizing_h"), .0002965403624382207),
    (("external_resonant_inductor_present",), True), (("external_resonant_inductor_loss_w",), 1.),
    (("external_resonant_inductor_loss_w",), False),
    (("geometry", "reachable"), False), (("geometry", "status"), "HARDWARE_VALIDATED"),
    (("geometry", "balanced_geometry_h"), 6.9596375617793165e-6),
    (("geometry", "required_interwinding_gap_m"), .0002),
    (("geometry", "required_radial_build_m"), .018),
    (("geometry", "relative_error"), float("nan")), (("geometry", "notes"), []),
    (("geometry", "regions"), []), (("geometry", "minimum_insulation_m"), .002),
    (("geometry", "geometry_apr_lr_lower_h"), float("inf")),
    (("nominal_frequency_hz",), float("inf")), (("nominal_loss_w",), True),
    (("solved_points",), True), (("solved_points",), 7), (("loss_source",), "TIME_DOMAIN_WAVEFORM"),
    (("is_hardware_validated",), True), (("all_conditions_feasible",), True), (("feasible",), True),
    (("warnings",), []), (("feasibility_reasons",), []),
    (("gui_run_design",), False), (("project_roundtrip",), False),
    (("recomputed_analysis_identical",), False), (("stale_consumers_cleared",), False),
    (("report_consistency",), False), (("consumers", "summary_spec_identity"), "0"*64),
    (("consumers", "loss_spec_identity"), "0"*64), (("consumers", "report_spec_identity"), "0"*64),
])
def test_integrated_contract_fails_closed(path, value):
    proof = deepcopy(_proof())
    target = proof if path[0] == "integrated_magnetics" else proof["integrated_magnetics"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(RuntimeError, match="integrated magnetic"):
        _validate(proof)


@pytest.mark.parametrize("section", ["modules", "golden", "geometry_counterexample", "spec", "spec_identity",
    "electrical", "geometry", "consumers", "is_hardware_validated", "all_conditions_feasible", "report_consistency"])
def test_integrated_contract_requires_every_section(section):
    proof = _proof()
    del proof["integrated_magnetics"][section]
    with pytest.raises(RuntimeError, match="integrated magnetic"):
        _validate(proof)


def test_rehashing_a_changed_characterization_does_not_defeat_independent_golden():
    proof = _proof()
    case = proof["integrated_magnetics"]
    case["spec"]["integrated_ls_h"] *= 1.01
    identity = hashlib.sha256(json.dumps(case["spec"], sort_keys=True, allow_nan=False).encode()).hexdigest()
    case["spec_identity"] = identity
    case["consumers"] = dict.fromkeys(case["consumers"], identity)
    with pytest.raises(RuntimeError, match="integrated magnetic"):
        _validate(proof)


def test_balanced_total_cannot_hide_incorrect_region_energy():
    proof = _proof()
    rows = proof["integrated_magnetics"]["geometry"]["regions"]
    rows[0]["inductance_h"] += 1e-7
    rows[1]["inductance_h"] -= 1e-8
    rows[2]["inductance_h"] -= 9e-8
    with pytest.raises(RuntimeError, match="integrated magnetic"):
        _validate(proof)


@pytest.mark.parametrize("previous_gate", ["magnetic_catalog", "power_stage_cases", "power_stage_buck_vmc",
    "power_stage_advanced", "two_stage_optimization", "power_stage_priority", "brand_taxonomy_sha256"])
def test_integrated_proof_cannot_replace_previous_gates(previous_gate):
    proof = _proof()
    del proof[previous_gate]
    with pytest.raises(RuntimeError):
        _validate(proof)


@pytest.mark.parametrize("path,value", [
    (("comparison_execution",), None), (("execution_module",), ""),
    (("options", "max_harmonic"), 3), (("options", "hb_samples"), 256),
    (("options", "include_time_domain"), True), (("options", "execution_budget_s"), .1),
    (("success", "converged"), False), (("success", "reference"), "fha"),
    (("success", "spec_identity"), "0"*64), (("success", "models"), ["fha"]),
    (("success", "harmonic_orders"), [1, 3, 5]), (("success", "samples"), 512),
    (("success", "gui_table_matches"), False), (("success", "waveform_sha256"), "none"),
    (("success", "active_threads"), 1), (("cancel", "active_threads"), True),
    (("timeout", "active_threads"), 1), (("success", "active_workers"), True), (("success", "elapsed_s"), float("inf")),
    (("success", "residual_norm"), 3e-6), (("success", "exact_sign_residual_norm"), float("nan")),
    (("success", "switching_frequency_hz"), 127759.), (("success", "output_voltage_v"), 54.),
    (("success", "resonant_current_rms_a"), True),
    (("cancel", "outcome"), "completed"), (("cancel", "budget_s"), .1),
    (("cancel", "no_partial_result"), False), (("cancel", "active_workers"), 1),
    (("cancel", "control_released"), False), (("cancel", "run_enabled"), False),
    (("cancel", "report_disabled"), False), (("cancel", "status_visible"), False),
    (("timeout", "outcome"), "cancelled"), (("timeout", "budget_s"), 300.),
    (("timeout", "rows"), 1), (("timeout", "no_partial_result"), False),
    (("timeout", "elapsed_s"), float("nan")), (("timeout", "active_workers"), False),
])
def test_comparison_execution_contract_rejects_incomplete_or_weakened_evidence(path, value):
    proof = _proof()
    target = proof["integrated_magnetics"]
    if path[0] != "comparison_execution":
        target = target["comparison_execution"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(RuntimeError, match="integrated magnetic"):
        _validate(proof)


@pytest.mark.parametrize("section", ["options", "success", "cancel", "timeout", "execution_module"])
def test_comparison_execution_requires_every_outcome(section):
    proof = _proof()
    del proof["integrated_magnetics"]["comparison_execution"][section]
    with pytest.raises(RuntimeError, match="integrated magnetic"):
        _validate(proof)
