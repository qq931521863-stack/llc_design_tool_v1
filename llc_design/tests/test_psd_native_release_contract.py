"""Reject import-only or stale PSD evidence without weakening native release gates."""
from __future__ import annotations

import ast

import pytest

from llc_design.tests import test_release_verification as release


def _validate(proof):
    release.MODULE.validate_report(proof, expected_version="9.4.1",
        expected_taxonomy_sha256="a" * 64, expected_platform=proof["platform"])


@pytest.mark.parametrize("field,value", [
    ("workspaces", ["llc", "pfc", "control", "fra", "dab"]),
    ("proof_schema", 1), ("success", 1),
    ("power_stage_launcher_entry", False), ("power_stage_toolbox_filter", False),
    ("power_stage_model_revision", "stale"), ("power_stage_design_id", "d" * 20),
    ("power_stage_cases", {}), ("power_stage_buck_vmc", {}),
])
def test_incomplete_psd_report_is_rejected(field, value):
    with pytest.raises(RuntimeError, match="report"):
        _validate(release._proof(**{field: value}))


@pytest.mark.parametrize("topology", ["T01", "T02", "T09"])
@pytest.mark.parametrize("field,value", [
    ("duty", 0.75), ("switch_rms_a", 0), ("switch_voltage_v", float("nan")),
    ("inductor_ripple_pp_a", float("inf")), ("design_id", "not-a-design"),
    ("project_roundtrip", False), ("report_consistency", False), ("mode", "DCM"),
])
def test_wrong_numerics_or_roundtrip_fail_for_every_stage(topology, field, value):
    proof = release._proof()
    proof["power_stage_cases"][topology][field] = value
    with pytest.raises(RuntimeError, match="report"):
        _validate(proof)


@pytest.mark.parametrize("field,value", [
    ("control_mode", "peak_cmc"), ("frequency_points", 0),
    ("roundtrip_identical", False), ("plotted_curves", 0),
    ("max_plant_relative_error", 1e-5), ("max_plant_relative_error", float("nan")),
])
def test_invalid_buck_vmc_evidence_is_rejected(field, value):
    proof = release._proof()
    proof["power_stage_buck_vmc"][field] = value
    with pytest.raises(RuntimeError, match="report"):
        _validate(proof)


def test_duplicate_design_identities_are_rejected():
    proof = release._proof()
    proof["power_stage_cases"]["T02"]["design_id"] = proof["power_stage_design_id"]
    with pytest.raises(RuntimeError, match="identities"):
        _validate(proof)


def test_archive_gate_rejects_legacy_five_workspace_proof(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="report"):
        release._archive_gate(tmp_path, monkeypatch, proof_overrides={
            "workspaces": ["llc", "pfc", "control", "fra", "dab"]})


def test_archive_gate_never_publishes_extra_private_artifacts(tmp_path, monkeypatch):
    with pytest.raises(AssertionError, match="unexpected"):
        release._archive_gate(tmp_path, monkeypatch, extra_asset=True)


def test_publication_uses_same_strict_psd_validator_as_frozen_execution():
    workflow = (release.ROOT / ".github/workflows/build-release.yml").read_text(encoding="utf-8")
    assert 'runpy.run_path("packaging/verify_bundle.py")["validate_report"]' in workflow
    before, build = workflow.split("\n  build:\n", 1)
    tag = before.split("\n  tag-release:\n", 1)[1]
    assert "needs: [test, build]" in tag
    assert "    if:" not in build.split("    steps:", 1)[0]
    assert "fail-fast: false" in build
    assert "os: windows-latest" in build and "os: macos-latest" in build
    assert "--collect-data power_stage_design" in build
    assert "--collect-submodules power_stage_design" in build
    assert "run: python -m pytest" in build
    assert "continue-on-error" not in workflow


def test_packaging_text_io_is_explicitly_utf8():
    for name in ("launcher.py", "verify_bundle.py"):
        module = ast.parse((release.ROOT / "packaging" / name).read_text(encoding="utf-8"))
        for node in ast.walk(module):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"read_text", "write_text"}:
                assert any(keyword.arg == "encoding" and isinstance(keyword.value, ast.Constant)
                           and keyword.value.value == "utf-8" for keyword in node.keywords), name
