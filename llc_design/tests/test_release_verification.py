"""Exercise real child processes so success cannot be inferred from a launch."""
from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("pdt_verify_bundle", ROOT / "packaging/verify_bundle.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _completion_proof():
    """Synthetic schema fixture; actual model evidence comes from native self-test."""
    import hashlib
    from dataclasses import asdict
    spec = importlib.util.spec_from_file_location("pdt_launcher_fixture", ROOT / "packaging/launcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    branches = {
        "T05": "Q1 Q2 D1 D2 Dbody1 Dbody2 Coss1 Coss2 L1 Rwinding Cout Cin",
        "T11": "Q1 D1 Dbody Cnode primary secondary Lm Cin Cout",
        "T18": "L1 Lleak Lm primary secondary1 secondary2 Q1 Q2 Q3 Q4 Dbody1 Dbody2 Dbody3 Dbody4 Coss1 Coss2 Coss3 Coss4 D1 D2 Cprimary Crectifier Cout Cin node_A node_B",
    }
    advanced = {}
    for inputs in module._advanced_inputs():
        advanced[inputs.topology] = {"design_id": inputs.design_id, "inputs": asdict(inputs),
            "waveform_sha256": "d" * 64, "branches_clicked": sorted(branches[inputs.topology].split()),
            "samples_per_branch": 800, "max_curve_absolute_error": 0.0,
            "project_roundtrip": True, "recomputed_waveforms_identical": True, "report_consistency": True,
            "mode": "CCM", "provenance": {"fixture": "schema only"}}
    candidate_spec = {"fields": {"ln_ratio": 5.5}}
    candidate = {"candidate_id": hashlib.sha256(json.dumps(candidate_spec, sort_keys=True).encode()).hexdigest(),
        "spec": candidate_spec, "rejected": False, "fha_td_validity": "WARN", "total_loss_w": 10.,
        "sources": [{"components": {"semiconductor_current": "TIME_DOMAIN_WAVEFORM"}, "waveform_sha256": "e" * 64}]}
    return {"power_stage_advanced": advanced,
        "two_stage_optimization": {"real_stage_a_and_b": True, "verification_scope": "nominal", "verified_td_points": 1,
            "fha_td_validity": "WARN", "waveform_sha256": "e" * 64, "loss_source": "TIME_DOMAIN_WAVEFORM",
            "total_loss_w": 10., "selected_candidate": candidate, "applied_ln_ratio": 5.5,
            "apply_exact": True, "project_roundtrip": True, "restored_history_requires_rerun": True,
            "report_consistency": True, "model_limit": "Nominal-point schema fixture; no physical evidence"}}


def _proof(**overrides):
    payload = {
        "proof_schema": 3, "success": True, "version": "9.4.1", "frozen": True, "platform": sys.platform,
        "workspaces": ["llc", "pfc", "control", "fra", "dab", "power_stage"],
        "guided_design": True, "power_stage_designer": True, "brand_taxonomy_sha256": "a" * 64,
        "power_stage_launcher_entry": True, "power_stage_toolbox_filter": True,
        "power_stage_design_id": "a" * 20, "power_stage_model_revision": "psd-piecewise-2",
        "power_stage_cases": {},
        "power_stage_buck_vmc": {"control_mode": "vmc", "frequency_points": 1600,
                                 "roundtrip_identical": True, "plotted_curves": 4,
                                 "max_plant_relative_error": 0.0},
    }
    for topology, identity, duty, ripple, average, voltage in (
        ("T01", "a", 5 / 12, 7 * (5 / 12) / 4.7, 3, 12),
        ("T02", "b", 0.5, 1.2, 4, 48), ("T09", "c", 0.5, 0.24, 0.5, 96),
    ):
        payload["power_stage_cases"][topology] = {
            "design_id": identity * 20, "mode": "CCM", "project_roundtrip": True,
            "report_consistency": True, "duty": duty, "inductor_ripple_pp_a": ripple,
            "switch_rms_a": math.sqrt(duty * (average ** 2 + ripple ** 2 / 12)),
            "switch_voltage_v": voltage,
        }
    payload.update(_completion_proof())
    payload.update(overrides)
    return payload


def _child(tmp_path, *, exit_code=0, report=True, delay=0, **overrides):
    payload = _proof(**overrides)
    script = tmp_path / "child.py"
    script.write_text(
        "import json, os, pathlib, sys, time\n"
        f"time.sleep({delay!r})\n"
        + (f"pathlib.Path(os.environ['PDT_SELF_TEST_REPORT']).write_text({json.dumps(payload)!r}, encoding='utf-8')\n" if report else "")
        + f"sys.exit({exit_code})\n", encoding="utf-8",
    )
    return [sys.executable, str(script)]


def _verify(command, **kwargs):
    return MODULE.verify_bundle(
        command, expected_version="9.4.1", expected_taxonomy_sha256="a" * 64,
        **kwargs,
    )


def test_completed_success_requires_child_report(tmp_path):
    result = _verify(_child(tmp_path, delay=0.05))
    assert result["success"] is True
    assert result["version"] == "9.4.1"


def test_nonzero_exit_rejected_even_with_success_report(tmp_path):
    with pytest.raises(subprocess.CalledProcessError):
        _verify(_child(tmp_path, exit_code=7))


def test_zero_exit_without_report_is_not_success(tmp_path):
    with pytest.raises(RuntimeError, match="report"):
        _verify(_child(tmp_path, report=False))


def test_hung_child_times_out(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        _verify(_child(tmp_path, delay=10), timeout_s=0.1)


@pytest.mark.parametrize("overrides", [
    {"success": False}, {"version": "9.4.0"}, {"frozen": False},
    {"workspaces": ["llc"]}, {"guided_design": False}, {"power_stage_designer": False},
    {"brand_taxonomy_sha256": "b" * 64}, {"platform": "wrong"},
])
def test_incomplete_or_wrong_bundle_report_is_rejected(tmp_path, overrides):
    with pytest.raises(RuntimeError, match="report"):
        _verify(_child(tmp_path, **overrides))


def test_publish_waits_for_both_platforms_and_stages_draft():
    text = (ROOT / ".github/workflows/build-release.yml").read_text(encoding="utf-8")
    before, publish = text.split("\n  publish:\n", 1)
    build = before.split("\n  build:\n", 1)[1]
    assert "gh release" not in build
    assert "contents: read" in build
    assert "needs: [test, build]" in publish
    assert "always()" not in publish
    assert "--verify-tag --draft" in publish
    assert publish.index("gh release upload") < publish.index("--draft=false --latest")
    assert "uploaded digest mismatch" in publish


def _archive_gate(tmp_path, monkeypatch, *, missing_mac=False, wrong_taxonomy=False,
                  windows_backslash=False, proof_overrides=None, extra_asset=False):
    import hashlib
    import textwrap
    import zipfile

    taxonomy = b'{"brands": [{"display_name": "TEST"}]}'
    digest = hashlib.sha256(taxonomy).hexdigest()
    (tmp_path / "engineering_data").mkdir()
    (tmp_path / "engineering_data/brand_taxonomy.json").write_bytes(taxonomy)
    (tmp_path / "packaging").mkdir()
    (tmp_path / "packaging/verify_bundle.py").write_bytes((ROOT / "packaging/verify_bundle.py").read_bytes())
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="9.4.1"\n')
    (tmp_path / "CHANGELOG.md").write_text('# Changelog\n\n## 9.4.1 — test\nFix\n\n## 9.4.0 — test\nFeatures\n', encoding="utf-8")
    (tmp_path / "release").mkdir()
    if extra_asset:
        (tmp_path / "release/private-test-archive.zip").write_bytes(b"must not be published")
    for runner, platform, name, exe in (
        ("Windows", "win32", "PowerDesignTool-Windows-x64.zip", "PowerDesignTool/PowerDesignTool.exe"),
        ("macOS", "darwin", "PowerDesignTool-macOS-arm64.zip", "PowerDesignTool.app/Contents/MacOS/PowerDesignTool"),
    ):
        if missing_mac and runner == "macOS":
            continue
        proof = _proof(platform=platform, brand_taxonomy_sha256=digest)
        proof.update(proof_overrides or {})
        (tmp_path / f"release/bundle-proof-{runner}.json").write_text(json.dumps(proof), encoding="utf-8")
        with zipfile.ZipFile(tmp_path / "release" / name, "w") as bundle:
            bundle.writestr(exe.replace("/", "\\") if windows_backslash and runner == "Windows" else exe, b"fixture executable, not a real bundle")
            bundle.writestr("root\\engineering_data\\brand_taxonomy.json" if windows_backslash and runner == "Windows" else "root/engineering_data/brand_taxonomy.json", b"wrong" if wrong_taxonomy else taxonomy)
    workflow = (ROOT / ".github/workflows/build-release.yml").read_text(encoding="utf-8")
    block = workflow.split("- name: Verify both archives and write checksums\n", 1)[1]
    block = block.split("run: |\n", 1)[1].split("\n      - name:", 1)[0]
    script = textwrap.dedent(block).split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REF_NAME", "v9.4.1")
    exec(compile(script, "release-archive-gate", "exec"), {})


def test_archive_gate_accepts_both_complete_packages(tmp_path, monkeypatch):
    _archive_gate(tmp_path, monkeypatch)
    assert len((tmp_path / "release/SHA256SUMS.txt").read_text().splitlines()) == 2


def test_archive_gate_rejects_partial_release(tmp_path, monkeypatch):
    with pytest.raises(AssertionError, match="missing release assets"):
        _archive_gate(tmp_path, monkeypatch, missing_mac=True)


def test_archive_gate_rejects_wrong_bundled_data(tmp_path, monkeypatch):
    with pytest.raises(AssertionError, match="taxonomy"):
        _archive_gate(tmp_path, monkeypatch, wrong_taxonomy=True)


@pytest.mark.parametrize("default_encoding", ["cp1252", "utf-8"])
def test_archive_gate_is_independent_of_default_text_encoding(tmp_path, monkeypatch, default_encoding):
    """Exercise the same archive gate under Windows and UTF-8 write defaults."""
    original_write_text = Path.write_text

    def locale_write_text(path, data, encoding=None, errors=None, newline=None):
        return original_write_text(
            path, data, encoding=encoding or default_encoding,
            errors=errors, newline=newline,
        )

    monkeypatch.setattr(Path, "write_text", locale_write_text)
    _archive_gate(tmp_path, monkeypatch)
    notes = (tmp_path / "release-notes.md").read_text(encoding="utf-8")
    assert "9.4.1 — test" in notes


def test_archive_gate_handles_windows_zip_separators(tmp_path, monkeypatch):
    _archive_gate(tmp_path, monkeypatch, windows_backslash=True)
    assert len((tmp_path / "release/SHA256SUMS.txt").read_text().splitlines()) == 2


def test_release_publisher_shell_is_syntactically_valid(tmp_path):
    import shutil
    import textwrap

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash is required to parse the GitHub publisher script")
    workflow = (ROOT / ".github/workflows/build-release.yml").read_text(encoding="utf-8")
    block = workflow.split("- name: Stage and publish complete release\n", 1)[1]
    block = block.split("run: |\n", 1)[1].split("\n      - name:", 1)[0]
    script = tmp_path / "publish.sh"
    script.write_text(textwrap.dedent(block), encoding="utf-8")
    result = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
