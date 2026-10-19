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
        "proof_schema": 2, "success": True, "frozen": True, "version": expected_version,
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


def verify_bundle(
    command: Sequence[str], *, expected_version: str,
    expected_taxonomy_sha256: str, timeout_s: float = 300.0,
) -> dict:
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
