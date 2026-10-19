"""Wait for a frozen GUI self-test and require fresh, versioned evidence.

A windowed Windows executable need not have stdout. Its exit code alone is not
proof that the intended self-test ran, so the child must also write a report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path


def validate_report(report: dict, *, expected_version: str,
                    expected_taxonomy_sha256: str, expected_platform: str,
                    expected_magnetic_sha256: dict[str, str] | None = None) -> None:
    """One proof contract shared by native verification and release publication."""
    required = {
        "proof_schema": 3, "success": True, "frozen": True, "version": expected_version,
        "platform": expected_platform,
        "workspaces": ["llc", "pfc", "control", "fra", "dab", "power_stage"],
        "guided_design": True, "power_stage_designer": True,
        "power_stage_launcher_entry": True, "power_stage_toolbox_filter": True,
        "power_stage_model_revision": "psd-piecewise-2",
        "brand_taxonomy_sha256": expected_taxonomy_sha256,
    }
    if not isinstance(report, dict) or any(type(report.get(k)) is not type(v) or report[k] != v for k, v in required.items()):
        raise RuntimeError(f"invalid packaged self-test report: {report!r}")
    cases = report.get("power_stage_cases")
    if not isinstance(cases, dict) or set(cases) != {"T01", "T02", "T09"}:
        raise RuntimeError("packaged self-test report lacks Buck/Boost/Flyback evidence")
    # Analytical CCM acceptance values, not booleans or import-only evidence.
    expectations = {
        "T01": (5 / 12, 7 * (5 / 12) / 4.7, 3, 12),
        "T02": (0.5, 1.2, 4, 48),
        "T09": (0.5, 0.24, 0.5, 96),
    }
    identities = set()
    for topology, (duty, ripple, average, voltage) in expectations.items():
        case = cases[topology]
        if not isinstance(case, dict):
            raise RuntimeError(f"packaged self-test report has invalid {topology} evidence")  # noqa: TRY004 - proof schema failures have one contract
        identity = case.get("design_id")
        if not isinstance(identity, str) or len(identity) != 20 or any(c not in "0123456789abcdef" for c in identity):
            raise RuntimeError("packaged self-test report has invalid design identity")
        identities.add(identity)
        if case.get("mode") != "CCM" or case.get("project_roundtrip") is not True or case.get("report_consistency") is not True:
            raise RuntimeError("packaged self-test report lacks recomputed project/report evidence")
        expected = {"duty": duty, "inductor_ripple_pp_a": ripple,
                    "switch_rms_a": math.sqrt(duty * (average ** 2 + ripple ** 2 / 12)),
                    "switch_voltage_v": voltage}
        for key, value in expected.items():
            actual = case.get(key)
            if type(actual) not in (int, float) or not math.isfinite(actual) or not math.isclose(actual, value, rel_tol=1e-9, abs_tol=1e-12):
                raise RuntimeError(f"packaged self-test report fails {topology} {key}")
    if len(identities) != 3 or report.get("power_stage_design_id") != cases["T01"]["design_id"]:
        raise RuntimeError("packaged self-test report has inconsistent design identities")
    loop = report.get("power_stage_buck_vmc")
    if (not isinstance(loop, dict) or loop.get("control_mode") != "vmc"
            or type(loop.get("frequency_points")) is not int or loop["frequency_points"] < 100
            or loop.get("roundtrip_identical") is not True or loop.get("plotted_curves") != 4):
        raise RuntimeError("packaged self-test report lacks Buck VMC curve/restore evidence")
    error = loop.get("max_plant_relative_error")
    if type(error) not in (int, float) or not math.isfinite(error) or not 0 <= error <= 1e-10:
        raise RuntimeError("packaged self-test report fails independent Buck VMC numerical check")
    _validate_completion_report(report)
    _validate_priority_report(report)
    _validate_magnetic_report(report, expected_magnetic_sha256)


def _hex_digest(value, length=64):
    return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


def _validate_completion_report(report):
    advanced = report.get("power_stage_advanced")
    branches = {
        "T05": {"Q1", "Q2", "D1", "D2", "Dbody1", "Dbody2", "Coss1", "Coss2", "L1", "Rwinding", "Cout", "Cin"},
        "T11": {"Q1", "D1", "Dbody", "Cnode", "primary", "secondary", "Lm", "Cin", "Cout"},
        "T18": {"L1", "Lleak", "Lm", "primary", "secondary1", "secondary2", "Q1", "Q2", "Q3", "Q4",
                "Dbody1", "Dbody2", "Dbody3", "Dbody4", "Coss1", "Coss2", "Coss3", "Coss4",
                "D1", "D2", "Cprimary", "Crectifier", "Cout", "Cin", "node_A", "node_B"},
    }
    if not isinstance(advanced, dict) or set(advanced) != set(branches):
        raise RuntimeError("packaged self-test report lacks advanced PSD evidence")
    options = {"T05": ("two_switch_options", "t05-switched-1"),
               "T11": ("qr_options", "qr-dynamic-1"), "T18": ("psfb_options", "psfb-commutation-1")}
    identities = set()
    for topology, expected_branches in branches.items():
        case = advanced[topology]
        if not isinstance(case, dict):
            raise RuntimeError("packaged self-test report has invalid advanced case")  # noqa: TRY004 - one report-validation error contract
        selected = case.get("branches_clicked")
        if (not isinstance(selected, list) or not all(isinstance(x, str) for x in selected)
                or len(selected) != len(expected_branches) or set(selected) != expected_branches
                or type(case.get("samples_per_branch")) is not int or case["samples_per_branch"] != 800
                or type(case.get("max_curve_absolute_error")) not in (int, float)
                or case["max_curve_absolute_error"] != 0
                or not _hex_digest(case.get("waveform_sha256"))
                or any(case.get(key) is not True for key in
                       ("project_roundtrip", "recomputed_waveforms_identical", "report_consistency"))):
            raise RuntimeError("packaged self-test report has incomplete advanced curves/project/report evidence")
        inputs = case.get("inputs")
        option_key, revision = options[topology]
        if (not isinstance(inputs, dict) or inputs.get("topology") != topology
                or not isinstance(inputs.get(option_key), dict) or inputs[option_key].get("model_revision") != revision
                or any(inputs.get(other) is not None for other, _ in options.values() if other != option_key)
                or not isinstance(case.get("provenance"), dict) or not case["provenance"]):
            raise RuntimeError("packaged self-test report lacks exact advanced inputs/provenance")
        canonical = dict(inputs)
        for name, _ in options.values():
            if canonical.get(name) is None:
                canonical.pop(name, None)
        try:
            identity = hashlib.sha256(json.dumps({"model_version": "psd-piecewise-2", "inputs": canonical},
                sort_keys=True, allow_nan=False).encode()).hexdigest()[:20]
        except (TypeError, ValueError) as exc:
            raise RuntimeError("packaged self-test report has invalid advanced identity inputs") from exc
        if case.get("design_id") != identity:
            raise RuntimeError("packaged self-test report has mismatched advanced identity")
        identities.add(identity)
    if len(identities) != 3:
        raise RuntimeError("packaged self-test report duplicates advanced identities")
    two_stage = report.get("two_stage_optimization")
    if (not isinstance(two_stage, dict) or two_stage.get("verification_scope") != "nominal"
            or type(two_stage.get("verified_td_points")) is not int or two_stage["verified_td_points"] != 1
            or two_stage.get("loss_source") != "TIME_DOMAIN_WAVEFORM"
            or two_stage.get("fha_td_validity") not in ("PASS", "WARN")
            or not _hex_digest(two_stage.get("waveform_sha256"))
            or type(two_stage.get("applied_ln_ratio")) not in (int, float) or two_stage["applied_ln_ratio"] != 5.5
            or any(two_stage.get(key) is not True for key in ("real_stage_a_and_b", "apply_exact",
                   "project_roundtrip", "restored_history_requires_rerun", "report_consistency"))
            or not isinstance(two_stage.get("model_limit"), str) or "Nominal-point" not in two_stage["model_limit"]):
        raise RuntimeError("packaged self-test report lacks genuine bounded TwoStage workflow evidence")
    loss = two_stage.get("total_loss_w")
    candidate = two_stage.get("selected_candidate")
    if (type(loss) not in (int, float) or not math.isfinite(loss) or loss <= 0
            or not isinstance(candidate, dict) or candidate.get("rejected") is not False
            or candidate.get("total_loss_w") != loss or candidate.get("fha_td_validity") != two_stage["fha_td_validity"]
            or not isinstance(candidate.get("spec"), dict) or not isinstance(candidate.get("sources"), list)
            or not _hex_digest(candidate.get("candidate_id"))):
        raise RuntimeError("packaged self-test report has invalid TwoStage candidate")
    try:
        identity = hashlib.sha256(json.dumps(candidate["spec"], sort_keys=True, allow_nan=False).encode()).hexdigest()
        td_sources = [point for point in candidate["sources"]
                      if point["components"]["semiconductor_current"] == "TIME_DOMAIN_WAVEFORM"]
        consistent = (candidate["spec"]["fields"]["ln_ratio"] == 5.5
                      and identity == candidate["candidate_id"] and len(td_sources) == 1
                      and td_sources[0]["waveform_sha256"] == two_stage["waveform_sha256"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("packaged self-test report has invalid TwoStage source identity") from exc
    if not consistent:
        raise RuntimeError("packaged self-test report has mismatched TwoStage source identity")


def _priority_identity(inputs):
    canonical = dict(inputs)
    for name in ("two_switch_options", "qr_options", "psfb_options"):
        if canonical.get(name) is None:
            canonical.pop(name, None)
    return hashlib.sha256(json.dumps({"model_version": "psd-piecewise-2", "inputs": canonical},
                                    sort_keys=True, allow_nan=False).encode()).hexdigest()[:20]


def _finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _validate_priority_report(report):
    priority = report.get("power_stage_priority")
    if not isinstance(priority, dict) or set(priority) != {"full_bridge_suggestion", "advanced_suggestion", "llc_components"}:
        raise RuntimeError("packaged self-test report lacks priority workflow evidence")
    for name, topology, maximum in (("full_bridge_suggestion", "T17", .45), ("advanced_suggestion", "T05", .95)):
        case = priority[name]
        if (not isinstance(case, dict) or any(case.get(key) is not True for key in
                ("preview_did_not_apply", "apply_recomputed", "nested_options_preserved", "project_roundtrip",
                 "recomputed_waveforms_identical", "report_consistency"))
                or not _hex_digest(case.get("waveform_sha256"))):
            raise RuntimeError("packaged self-test report lacks priority suggestion consumers")
        record, inputs = case.get("proposal"), case.get("inputs")
        if not isinstance(record, dict) or not isinstance(inputs, dict):
            raise RuntimeError("packaged self-test report lacks priority suggestion provenance")  # noqa: TRY004
        source, targets, achieved = (record.get(key) for key in ("source_inputs", "targets", "achieved"))
        if (record.get("algorithm") != "volt-second-targets-1" or not isinstance(source, dict)
                or not isinstance(targets, dict) or not isinstance(achieved, dict)
                or inputs.get("topology") != topology or source.get("topology") != topology
                or targets.get("max_duty") != maximum or targets.get("size_turns_ratio") is not False
                or any(targets.get(key) is not None for key in
                       ("secondary_ripple_pp_a", "magnetizing_ripple_pp_a", "reflected_voltage_v"))
                or not isinstance(record.get("changed_fields"), list)
                or any(key != "inductance_h" for key in record["changed_fields"])
                or len(set(record["changed_fields"])) != len(record["changed_fields"])
                or {key: value for key, value in source.items() if key != "inductance_h"}
                   != {key: value for key, value in inputs.items() if key != "inductance_h"}):
            raise RuntimeError("packaged self-test report altered untargeted priority inputs")
        try:
            consistent = (record.get("source_design_id") == _priority_identity(source)
                          and record.get("result_design_id") == case.get("design_id") == _priority_identity(inputs))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("packaged self-test report has invalid priority identity inputs") from exc
        if not consistent:
            raise RuntimeError("packaged self-test report has mismatched priority suggestion identities")
        target, ripple, duty = targets.get("ripple_pp_a"), achieved.get("ripple_pp_a"), achieved.get("duty")
        frequency = achieved.get("switching_frequency_hz")
        if (not all(_finite_number(value) for value in (target, ripple, duty, frequency, inputs.get("inductance_h")))
                or min(target, ripple, duty, frequency, inputs["inductance_h"]) <= 0 or duty > maximum + 2e-6
                or not math.isclose(target, ripple, rel_tol=2e-6, abs_tol=1e-9)):
            raise RuntimeError("packaged self-test report fails priority suggestion numerical targets")
        if topology == "T17":
            # Independent positive quadratic root for joint magnetic DCM charge;
            # conventional output-only CCM duty cannot satisfy this light load.
            expected_source = {"vin_v": 48, "vout_v": 5, "iout_a": .005, "fsw_hz": 100e3,
                               "inductance_h": 470e-6, "turns_ratio_np_ns": 2, "magnetizing_inductance_h": .01}
            expected_duty = (5 / 24) * (math.sqrt(2) - 1)
            expected_l = 19 * expected_duty / (200e3 * .02)
            if (any(source.get(key) != value for key, value in expected_source.items())
                    or target != .02 or achieved.get("mode") != "DCM"
                    or record["changed_fields"] != ["inductance_h"]
                    or not math.isclose(frequency, 100e3, rel_tol=1e-12, abs_tol=1e-12)
                    or not math.isclose(duty, expected_duty, rel_tol=1e-9, abs_tol=1e-12)
                    or not math.isclose(inputs["inductance_h"], expected_l, rel_tol=1e-9, abs_tol=1e-12)):
                raise RuntimeError("packaged self-test report fails light-load Full Bridge sizing")
        elif (source != report["power_stage_advanced"]["T05"]["inputs"]
              or source.get("two_switch_options") != inputs.get("two_switch_options")
              or not isinstance(inputs.get("two_switch_options"), dict)):
            raise RuntimeError("packaged self-test report lost nested optional-model proposal parameters")
    llc = priority["llc_components"]
    expected = {"coesr": ["v_output_cap_esr", "i_output_cap"],
                "rseries": ["v_series_resistance", "i_resonant"], "bus": ["v_bus", "i_bus"]}
    if (not isinstance(llc, dict) or llc.get("topology") != "T21" or llc.get("primary_topology") != "FULL_BRIDGE"
            or llc.get("solver") != "fast" or not _hex_digest(llc.get("source_identity"))
            or not _hex_digest(llc.get("waveform_sha256")) or type(llc.get("samples_per_signal")) is not int
            or llc["samples_per_signal"] < 128 or not isinstance(llc.get("components"), dict)
            or set(llc["components"]) != set(expected)
            or any(llc.get(key) is not True for key in ("project_roundtrip", "recomputed_waveforms_identical"))
            or not isinstance(llc.get("model_name"), str) or not llc["model_name"]
            or not isinstance(llc.get("warnings"), list) or not all(isinstance(w, str) for w in llc["warnings"])):
        raise RuntimeError("packaged self-test report lacks actual T21 terminal workflow evidence")
    for component, keys in expected.items():
        case = llc["components"][component]
        if (not isinstance(case, dict) or case.get("signals") != keys or case.get("source_identity") != llc["source_identity"]
                or not _finite_number(case.get("max_curve_absolute_error")) or case["max_curve_absolute_error"] != 0
                or case.get("report_selection_exact") is not True):
            raise RuntimeError("packaged self-test report has mixed-source T21 curves or report selection")
    error, reverse, ac_rms = (llc.get(key) for key in
                            ("bus_power_max_absolute_error_w", "bus_reverse_current_min_a", "bus_voltage_ac_rms_v"))
    if (not all(_finite_number(value) for value in (error, reverse, ac_rms))
            or not 0 <= error <= 1e-10 or reverse >= 0 or ac_rms != 0):
        raise RuntimeError("packaged self-test report fails full-bridge DC-bus terminal invariants")


def _magnetic_catalog_contract():
    """Trusted checkout data for native and archive gates, with no GUI imports."""
    directory = Path(__file__).resolve().parents[1] / "engineering_data"
    paths = [directory / name for name in ("csc_magnetic_catalog.json", "magnetic_supplemental_records.json")]
    resources = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    catalog = json.loads(paths[0].read_text(encoding="utf-8"))
    record = next(row for row in catalog["part_records"] if row["record_id"] == "csc2025:part:CS400060:054")
    geometry = next(row for row in catalog["geometries"] if row["record_id"] == record["geometry_id"])
    models = {row["record_id"]: row for row in catalog["material_models"]}
    bias, loss = models[record["dc_bias_model_id"]], models[record["loss_model_id"]]
    linked = {row["record_id"] for row in (record, geometry, bias, loss)}
    conflict_ids = {key for row in (record, geometry, bias, loss) for key in row.get("conflict_ids", [])}
    snapshot = {"schema": "power-design-toolkit-source-magnetic-selection-v1", "record_type": "explicit_part",
                "record": record, "geometry": geometry, "dc_bias_model": bias, "loss_model": loss,
                "source_documents": catalog["source_documents"],
                "catalog_conflicts": [row for row in catalog.get("conflicts", [])
                    if row.get("conflict_id") in conflict_ids or linked.intersection(row.get("affected_record_ids", []))]}
    return resources, snapshot


def _validate_magnetic_report(report, expected_magnetic_sha256=None):
    case = report.get("magnetic_catalog")
    if not isinstance(case, dict):
        raise RuntimeError("packaged self-test report lacks magnetic source workflow evidence")  # noqa: TRY004
    try:
        resources, snapshot = _magnetic_catalog_contract()
        if expected_magnetic_sha256 is not None:
            if set(expected_magnetic_sha256) != set(resources):
                raise ValueError("Incomplete expected magnetic resource identities")
            resources = expected_magnetic_sha256
        identity = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                            allow_nan=False, separators=(",", ":")).encode()).hexdigest()
        record_id = snapshot["record"]["record_id"]
        sources = {row["filename"]: row["sha256"] for row in snapshot["source_documents"]}
        actual_identity = hashlib.sha256(json.dumps(case.get("selection"), ensure_ascii=False, sort_keys=True,
                                                   allow_nan=False, separators=(",", ":")).encode()).hexdigest()
        if (case.get("resource_sha256") != resources or any(not _hex_digest(value) for value in resources.values())
                or actual_identity != identity
                or case.get("selection") != snapshot or case.get("selection_identity") != identity
                or case.get("record_id") != record_id or case.get("source_documents_sha256") != sources):
            raise ValueError("Stale or mismatched magnetic resource/source identity")
        reference = case["reference"]
        ref = reference["result"]
        expected_inputs = {"turns": 37, "n_cores": 2, "h_oe": None, "b_kg": None, "f_khz": None}
        if (reference["inputs"] != expected_inputs
                or any(reference.get(key) is not True for key in
                       ("default_full_calculate_disabled", "project_roundtrip", "report_consistency"))
                or ref.get("record_id") != record_id or ref.get("selection_identity") != identity
                or ref.get("label") != "CS400060" or type(ref.get("turns")) is not int or ref["turns"] != 37
                or type(ref.get("n_cores")) is not int or ref["n_cores"] != 2
                or not isinstance(ref.get("full_pfc_blockers"), list) or not ref["full_pfc_blockers"]
                or not isinstance(ref.get("scope"), str) or "零偏磁" not in ref["scope"]):
            raise ValueError("Incomplete magnetic reference-only Calculate/Save/Open/Report evidence")
        for key, expected in (("al_nH_per_t2", 81), ("l_zero_bias_uh", 221.778)):
            if not _finite_number(ref.get(key)) or not math.isclose(ref[key], expected, rel_tol=1e-12):
                raise ValueError("Independent zero-bias numerical check failed")
        full = case["pfc"]
        core_identity = f"Chang Sung Corporation CS400060 [{record_id}]"
        declaration = {"B_convention": "peak_ac", "waveform": "sinusoidal_reference",
            "dimension_basis": "after_coating_limits", "source": "QA-only assumptions; not manufacturer qualification",
            "H_Oe": [0, 1000], "B_T": [0, 1], "frequency_Hz": [5000, 300000],
            "temperature_C": [20, 100], "core_temperature_C": 25}
        if (full.get("scope") != "QA-only user-declared reference model; not manufacturer qualification"
                or json.dumps(full.get("user_model_declaration"), sort_keys=True, allow_nan=False)
                   != json.dumps(declaration, sort_keys=True, allow_nan=False)
                or full.get("selection_identity") != identity or full.get("core_identity") != core_identity
                or full.get("self_loss_identity") != core_identity + "; user-declared reference model"
                or not _hex_digest(full.get("curve_sha256"))
                or any(full.get(key) is not True for key in ("real_calculate_and_apply", "project_roundtrip",
                       "recomputed_curves_identical", "stale_consumers_cleared", "report_consistency"))):
            raise ValueError("Incomplete actual PFC calculation/consumer evidence")
        curve_samples = full["curve_samples"]
        if (set(curve_samples) != {"current_a", "inductance_uh", "line_angle_deg", "line_b_ac_t", "line_core_loss_w", "line_ripple_pp_a"}
                or any(type(value) is not int or value < 100 for value in curve_samples.values())
                or curve_samples["current_a"] != curve_samples["inductance_uh"]
                or len({curve_samples[key] for key in curve_samples if key not in {"current_a", "inductance_uh"}}) != 1):
            raise ValueError("Incomplete magnetic source curves")
        inputs = full["inputs"]
        expected = {"topology": "ttpl", "input_rms_v": 230, "bus_voltage_v": 400, "output_power_w": 1000,
                    "switching_frequency_hz": 120000, "target_inductance_uh": 200, "efficiency": .97, "n_cores": 2}
        numeric_fields = {"wire_copper_diameter_mm", "enamel_build_mm", "target_current_density_a_mm2",
                          "copper_temperature_c", "max_fill_factor", "winding_length_factor", "curve_points"}
        if (set(inputs) != set(expected) | numeric_fields or any(inputs.get(key) != value for key, value in expected.items())
                or any(not _finite_number(value) for key, value in inputs.items() if key != "topology")
                or type(inputs["n_cores"]) is not int or type(inputs["curve_points"]) is not int
                or inputs["curve_points"] != curve_samples["current_a"]):
            raise ValueError("Unexpected magnetic application inputs")
        result = full["results"]
        result_fields = {"turns", "parallel_wires", "l_no_bias_uh", "l_full_load_peak_uh", "h_peak_oe",
                        "full_load_peak_with_ripple_a", "phase_current_rms_a", "total_current_rms_with_ripple_a",
                        "core_loss_w", "copper_loss_w", "total_loss_w", "rdc_hot_ohm", "fill_factor", "b_ac_line_max_t"}
        if (set(result) != result_fields or any(not _finite_number(value) or value <= 0 for value in result.values())
                or type(result["turns"]) is not int or not 1 <= result["turns"] <= 400
                or type(result["parallel_wires"]) is not int or result["parallel_wires"] < 1):
            raise ValueError("Invalid magnetic application numeric evidence")
        bias = snapshot["dc_bias_model"]["coefficients"]
        h = .4 * math.pi * result["turns"] * result["full_load_peak_with_ripple_a"] / snapshot["geometry"]["le_cm"]
        l0 = 81 * result["turns"] ** 2 * inputs["n_cores"] / 1000
        l_bias = l0 / (100 * (bias["a"] + bias["b"] * h ** bias["c"]))
        for actual, expected in ((result["l_no_bias_uh"], l0), (result["h_peak_oe"], h),
                (result["l_full_load_peak_uh"], l_bias), (result["phase_current_rms_a"], 1000 / (.97 * 230)),
                (result["copper_loss_w"], result["total_current_rms_with_ripple_a"] ** 2 * result["rdc_hot_ohm"]),
                (result["total_loss_w"], result["core_loss_w"] + result["copper_loss_w"]),
                (full["self_loss_w"], result["total_loss_w"]),
                (full["applied"]["inductance_uh"], round(result["l_full_load_peak_uh"], 2)),
                (full["applied"]["dcr_mohm"], round(result["rdc_hot_ohm"] * 1000, 3))):
            if not _finite_number(actual) or not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-12):
                raise ValueError("Magnetic application calculations/Apply/self-loss disagree")
        loss_check = full["printed_loss_check"]
        moment, error = loss_check["b_kg_beta_mean"], loss_check["max_curve_absolute_error_w"]
        if (not _finite_number(moment) or moment <= 0 or not _finite_number(error) or not 0 <= error <= 1e-10):
            raise ValueError("Invalid source double-term loss curve evidence")
        coefficients = snapshot["loss_model"]["coefficients"]
        frequency_khz = inputs["switching_frequency_hz"] / 1000
        rate = coefficients["a"] * frequency_khz + coefficients["b"] * frequency_khz ** coefficients["gamma"]
        expected_loss = moment * rate * snapshot["geometry"]["ve_cm3"] * inputs["n_cores"] / 1000
        if not math.isclose(result["core_loss_w"], expected_loss, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("PFC loss differs from the selected source double-term expression")
        if result["l_full_load_peak_uh"] < 200 or result["fill_factor"] > inputs["max_fill_factor"]:
            raise ValueError("QA application target was not met")
        llc = case["llc_reference_only"]
        if llc.get("record_id") != record_id or llc.get("apply_disabled") is not True:
            raise ValueError("LLC reference was treated as a bobbin preset")
        supplemental = case["supplemental"]
        if (supplemental.get("source_case") != "APP-023" or supplemental.get("source_arithmetic_only") is not True
                or supplemental.get("solver_baseline") is not False
                or not _finite_number(supplemental.get("Lpfc_min_uH")) or not 197.9 < supplemental["Lpfc_min_uH"] < 198
                or not isinstance(supplemental.get("engineering_cautions"), list)
                or not any(isinstance(note, str) and "正确性基准" in note for note in supplemental["engineering_cautions"])):
            raise ValueError("Companion example lacks original-arithmetic-only qualification")
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration) as exc:
        raise RuntimeError(f"packaged self-test report has invalid magnetic evidence: {exc}") from exc


def verify_bundle(
    command: Sequence[str], *, expected_version: str,
    expected_taxonomy_sha256: str, timeout_s: float = 900.0,
    expected_magnetic_sha256: dict[str, str] | None = None,
) -> dict:
    # Three advanced periodic solvers include genuine save/recompute; T18 alone
    # takes ~132 s on the qualification host. This is a safety timeout, not a
    # startup/performance acceptance target; numerical budgets are unchanged.
    if not command or timeout_s <= 0:
        raise ValueError("command and positive timeout are required")
    with tempfile.TemporaryDirectory(prefix="pdt-bundle-test-") as directory:
        report_path = Path(directory) / "self-test.json"
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
                   PDT_SELF_TEST_REPORT=str(report_path))
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        # Run outside the source tree; do not let the repository mask absent data.
        process = subprocess.run(
            [*command, "--self-test"], cwd=directory, env=env,
            capture_output=True, text=True, errors="replace", timeout=timeout_s, check=False,
        )
        for output in (process.stdout, process.stderr):
            if output:
                print(output, end="" if output.endswith("\n") else "\n")
        process.check_returncode()
        if not report_path.is_file():
            raise RuntimeError("packaged self-test report was not produced")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        validate_report(report, expected_version=expected_version,
                        expected_taxonomy_sha256=expected_taxonomy_sha256,
                        expected_platform=sys.platform, expected_magnetic_sha256=expected_magnetic_sha256)
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--taxonomy", type=Path, default=Path("engineering_data/brand_taxonomy.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)
    expected_sha = hashlib.sha256(args.taxonomy.read_bytes()).hexdigest()
    report = verify_bundle(
        [str(executable)], expected_version=args.version,
        expected_taxonomy_sha256=expected_sha,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    print("Frozen application verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
