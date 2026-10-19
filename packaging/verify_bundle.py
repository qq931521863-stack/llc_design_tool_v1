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
                    expected_taxonomy_sha256: str, expected_platform: str) -> None:
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


def verify_bundle(
    command: Sequence[str], *, expected_version: str,
    expected_taxonomy_sha256: str, timeout_s: float = 900.0,
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
                        expected_platform=sys.platform)
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
