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
    candidate_spec = {"fields": {"ln_ratio": 4.5, "primary_turns": 32, "secondary_turns": 4}}
    candidate = {"candidate_id": hashlib.sha256(json.dumps(candidate_spec, sort_keys=True).encode()).hexdigest(),
        "spec": candidate_spec, "rejected": False, "fha_td_validity": "WARN", "total_loss_w": 10.,
        "sources": [{"components": {"semiconductor_current": "TIME_DOMAIN_WAVEFORM"}, "waveform_sha256": "e" * 64}]}
    return {"power_stage_advanced": advanced,
        "two_stage_optimization": {"real_stage_a_and_b": True, "verification_scope": "nominal", "verified_td_points": 1,
            "fha_td_validity": "WARN", "waveform_sha256": "e" * 64, "loss_source": "TIME_DOMAIN_WAVEFORM",
            "total_loss_w": 10., "selected_candidate": candidate, "applied_ln_ratio": 4.5,
            "applied_primary_turns": 32,
            "invalid_example": {"ln_ratio": 5.5, "primary_turns": 30, "secondary_turns": 4,
                "fha_td_validity": "FAIL", "reject_reason": "FHA↔TD MODEL_VALIDITY FAIL",
                "diagnostics": ["synthetic schema fixture; no physical evidence"],
                "apply_disabled": True, "project_unchanged": True},
            "apply_exact": True, "project_roundtrip": True, "restored_history_requires_rerun": True,
            "report_consistency": True, "model_limit": "Nominal-point schema fixture; no physical evidence"}}


def _priority_proof(advanced):
    """Synthetic contract fixture only; no executable qualification is implied."""
    from dataclasses import asdict, replace

    from power_stage_design.models import DesignInputs

    t17 = DesignInputs("T17", 48, 5, .005, 100e3, 470e-6,
                      capacitance_f=1e-3, turns_ratio_np_ns=2, magnetizing_inductance_h=.01)
    t05 = DesignInputs(**advanced["T05"]["inputs"])
    result = {}
    for name, source, maximum, ripple in (("full_bridge_suggestion", t17, .45, .02),
                                          ("advanced_suggestion", t05, .95, 1.)):
        duty = (5 / 24) * (math.sqrt(2) - 1) if source.topology == "T17" else .3
        chosen = replace(source, inductance_h=19*duty/(200e3*.02)) if source.topology == "T17" else source
        record = {"algorithm": "volt-second-targets-1", "source_inputs": asdict(source),
                  "source_design_id": source.design_id, "result_design_id": chosen.design_id,
                  "targets": {"ripple_pp_a": ripple, "max_duty": maximum, "secondary_ripple_pp_a": None,
                              "magnetizing_ripple_pp_a": None, "size_turns_ratio": False, "reflected_voltage_v": None},
                  "achieved": {"mode": "DCM" if source.topology == "T17" else "CCM", "duty": duty,
                               "ripple_pp_a": ripple, "switching_frequency_hz": 100e3},
                  "changed_fields": ["inductance_h"] if source.topology == "T17" else [],
                  "assumptions": ["Synthetic schema fixture only"]}
        result[name] = {"inputs": asdict(chosen), "design_id": chosen.design_id, "proposal": record,
                        "waveform_sha256": "f" * 64, "preview_did_not_apply": True, "apply_recomputed": True,
                        "nested_options_preserved": True, "project_roundtrip": True,
                        "recomputed_waveforms_identical": True, "report_consistency": True}
    components = {key: {"signals": signals, "source_identity": "1" * 64,
                        "max_curve_absolute_error": 0., "report_selection_exact": True}
                  for key, signals in {"coesr": ["v_output_cap_esr", "i_output_cap"],
                      "rseries": ["v_series_resistance", "i_resonant"], "bus": ["v_bus", "i_bus"]}.items()}
    result["llc_components"] = {"topology": "T21", "primary_topology": "FULL_BRIDGE", "solver": "fast",
        "source_identity": "1" * 64, "waveform_sha256": "2" * 64, "samples_per_signal": 2048,
        "components": components, "bus_power_max_absolute_error_w": 0., "bus_reverse_current_min_a": -1.,
        "bus_voltage_ac_rms_v": 0., "project_roundtrip": True, "recomputed_waveforms_identical": True,
        "model_name": "Schema fixture only", "warnings": ["Not real numerical evidence"]}
    return result


def _magnetic_proof():
    """Synthetic schema-only fixture; never evidence of a solver or executable run."""
    import hashlib

    resources, snapshot = MODULE._magnetic_catalog_contract()
    identity = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                        allow_nan=False, separators=(",", ":")).encode()).hexdigest()
    record_id = snapshot["record"]["record_id"]
    core = f"Chang Sung Corporation CS400060 [{record_id}]"
    l0 = 81 * 40 ** 2 * 2 / 1000
    h = .4 * math.pi * 40 * 7 / snapshot["geometry"]["le_cm"]
    bias = snapshot["dc_bias_model"]["coefficients"]
    l_bias = l0 / (100 * (bias["a"] + bias["b"] * h ** bias["c"]))
    loss = snapshot["loss_model"]["coefficients"]
    rate = loss["a"] * 120 + loss["b"] * 120 ** loss["gamma"]
    moment = 2. / (rate * snapshot["geometry"]["ve_cm3"] * 2 / 1000)
    return {"fixture": "schema-only; synthetic numbers are not executable or physical qualification",
        "resource_sha256": resources, "selection": snapshot, "selection_identity": identity, "record_id": record_id,
        "source_documents_sha256": {row["filename"]: row["sha256"] for row in snapshot["source_documents"]},
        "reference": {"inputs": {"turns": 37, "n_cores": 2, "h_oe": None, "b_kg": None, "f_khz": None},
            "result": {"record_id": record_id, "selection_identity": identity, "label": "CS400060",
                "turns": 37, "n_cores": 2, "al_nH_per_t2": 81., "l_zero_bias_uh": 221.778,
                "full_pfc_blockers": ["Schema-only fixture: applicability unknown"], "scope": "零偏磁 schema-only"},
            "default_full_calculate_disabled": True, "project_roundtrip": True, "report_consistency": True},
        "pfc": {"scope": "QA-only user-declared reference model; not manufacturer qualification",
            "inputs": {"topology": "ttpl", "input_rms_v": 230., "bus_voltage_v": 400., "output_power_w": 1000.,
                "switching_frequency_hz": 120000., "target_inductance_uh": 200., "efficiency": .97, "n_cores": 2,
                "wire_copper_diameter_mm": 1., "enamel_build_mm": .05, "target_current_density_a_mm2": 5.,
                "copper_temperature_c": 100., "max_fill_factor": .45, "winding_length_factor": 1.1, "curve_points": 161},
            "user_model_declaration": {"B_convention": "peak_ac", "waveform": "sinusoidal_reference",
                "dimension_basis": "after_coating_limits", "source": "QA-only assumptions; not manufacturer qualification",
                "H_Oe": [0, 1000], "B_T": [0, 1], "frequency_Hz": [5000, 300000],
                "temperature_C": [20, 100], "core_temperature_C": 25},
            "core_identity": core, "selection_identity": identity, "self_loss_identity": core + "; user-declared reference model",
            "results": {"turns": 40, "parallel_wires": 2, "l_no_bias_uh": l0, "l_full_load_peak_uh": l_bias,
                "h_peak_oe": h, "full_load_peak_with_ripple_a": 7., "phase_current_rms_a": 1000 / (.97 * 230),
                "total_current_rms_with_ripple_a": 5., "core_loss_w": 2., "copper_loss_w": .5,
                "total_loss_w": 2.5, "rdc_hot_ohm": .02, "fill_factor": .2, "b_ac_line_max_t": .03},
            "printed_loss_check": {"b_kg_beta_mean": moment, "max_curve_absolute_error_w": 0.},
            "curve_sha256": "3" * 64, "curve_samples": {"current_a": 161, "inductance_uh": 161,
                "line_angle_deg": 361, "line_b_ac_t": 361, "line_core_loss_w": 361, "line_ripple_pp_a": 361},
            "self_loss_w": 2.5, "applied": {"inductance_uh": round(l_bias, 2), "dcr_mohm": 20.},
            "real_calculate_and_apply": True, "project_roundtrip": True, "recomputed_curves_identical": True,
            "stale_consumers_cleared": True, "report_consistency": True},
        "llc_reference_only": {"record_id": record_id, "apply_disabled": True},
        "supplemental": {"source_case": "APP-023", "source_arithmetic_only": True, "solver_baseline": False,
            "Lpfc_min_uH": 197.955, "engineering_cautions": ["Schema-only fixture: 不作为真实求解器正确性基准"]}}


def _comparison_proof(identity):
    """Schema-only stand-in, not real solver/cancellation evidence."""
    stopped = {"elapsed_s": .2, "active_workers": 0, "active_threads": 0, "control_released": True,
        "no_partial_result": True, "rows": 0, "run_enabled": True, "report_disabled": True, "status_visible": True}
    return {"execution_module": "/schema-only/analysis/execution.py",
        "options": {"vbus_v": 400., "load_fraction": 1., "max_harmonic": 7, "hb_samples": 1024,
            "include_time_domain": False, "apply_td_losses": False, "execution_budget_s": 300.},
        "success": {"converged": True, "reference": "harmonic_balance", "models": ["fha", "harmonic_balance"],
            "spec_identity": identity, "harmonic_orders": [1, 3, 5, 7], "samples": 2048,
            "residual_norm": 1e-12, "exact_sign_residual_norm": 1e-12,
            "switching_frequency_hz": 113123.35618195974, "output_voltage_v": 53.,
            "resonant_current_rms_a": 8.570744923304485, "waveform_sha256": "4"*64,
            "gui_table_matches": True, "active_workers": 0, "active_threads": 0, "elapsed_s": 3.},
        "cancel": {**stopped, "outcome": "cancelled", "budget_s": 300.},
        "timeout": {**stopped, "outcome": "timed_out", "budget_s": .1}}


def _integrated_proof():
    """Synthetic schema-only data; no solver/GUI/artifact acceptance is implied."""
    import hashlib
    spec = {"magnetics_mode": "INTEGRATED", "integrated_inductance_source": "INDUCTANCE_MATRIX",
        "integrated_characterized_core": "PQ50_REF", "transformer_winding_layout": "P/2-S-P/2",
        "integrated_measurement_note": "Synthetic two-leakage artifact QA model; NOT a hardware measurement",
        "primary_turns": 30, "secondary_turns": 4, "integrated_characterized_primary_turns": 30,
        "integrated_characterized_secondary_turns": 4, "integrated_lp_h": 303.5e-6,
        "integrated_ls_h": 303.5e-6/7.5**2, "integrated_mutual_h": 300e-6/7.5,
        "integrated_interwinding_gap_m": .0010325249820337418, "vbus_nom_v": 400., "pout_w": 3000.}
    identity = hashlib.sha256(json.dumps(spec, sort_keys=True, allow_nan=False).encode()).hexdigest()
    gap, primary, secondary = .0010325249820337418, .002022599587389726, .00543557306504609
    regions = []
    for kind, thickness, mlt, start, end in (
        ("primary", primary, .116, 0, 14), ("interlayer insulation", .00005, .116, 14, 14),
        ("primary", primary, .116, 14, 15), ("P-S insulation", gap, .11, 15, 15),
        ("secondary", secondary, .104, 15, -15), ("P-S insulation", gap, .11, -15, -15),
        ("primary", primary, .116, -15, -1), ("interlayer insulation", .00005, .116, -1, -1),
        ("primary", primary, .116, -1, 0)):
        regions.append({"kind": kind, "thickness_m": thickness, "mean_turn_length_m": mlt,
            "mmf_start_a_turn": start, "mmf_end_a_turn": end,
            "inductance_h": 4*math.pi*1e-7*mlt*thickness/.032*(start**2+start*end+end**2)/3})
    return {"fixture": "schema-only; never physical or executable qualification",
        "scope": "Synthetic characterization; FHA GUI/report QA, not hardware or full-envelope qualification",
        "comparison_execution": _comparison_proof(identity),
        "modules": {"llc_design.core.integrated_magnetics": "/schema-only/core/integrated_magnetics.py",
                    "llc_design.magnetics.leakage_geometry": "/schema-only/magnetics/leakage_geometry.py"},
        "golden": {"lr_h": 36e-6, "lm_h": 64e-6, "effective_turns_ratio": 1.6, "physical_magnetizing_h": 80e-6,
                   "primary_leakage_h": 20e-6, "secondary_leakage_h": 5e-6, "physical_current_a": 1.4, "flux_density_t": .056},
        "geometry_counterexample": {"reachable": False, "required_interwinding_gap_m": .0014,
            "required_radial_build_m": .0048, "available_window_height_m": .003,
            "balanced_geometry_h": 2.178170906488923e-6},
        "spec": spec, "spec_identity": identity, "core": "PQ50_REF",
        "electrical": {"lr_h": 6.9596375617793165e-6, "lm_h": .0002965403624382207,
            "effective_turns_ratio": 7.413509060955519, "physical_turns_ratio": 7.5,
            "physical_magnetizing_h": .0003, "primary_leakage_h": 3.5e-6, "secondary_leakage_h": 3.5e-6/7.5**2},
        "geometry": {"reachable": True, "status": "MODEL_REACHABLE_REQUIRES_CHARACTERIZATION",
            "balanced_geometry_h": 7e-6, "characterized_balanced_h": 7e-6,
            "configured_interwinding_gap_m": gap, "required_interwinding_gap_m": gap,
            "radial_build_m": .015691021378672478, "required_radial_build_m": .015691021378672478,
            "available_window_height_m": .017, "minimum_insulation_m": .0002, "relative_error": 0.,
            "geometry_apr_lr_lower_h": 300e-6*7e-6/(300e-6+7e-6), "geometry_apr_lr_upper_h": 7e-6,
            "regions": regions, "notes": ["schema-only; one-dimensional geometry does not certify hardware"]},
        "external_resonant_inductor_present": False, "external_resonant_inductor_loss_w": 0.,
        "solved_points": 4, "nominal_frequency_hz": 127759.39831780495, "nominal_loss_w": 60.,
        "loss_source": "FHA_OPERATING_POINT", "is_hardware_validated": False, "all_conditions_feasible": False,
        "feasible": False, "feasibility_reasons": ["Schema-only fixture retains unsolved envelope points"],
        "warnings": ["Schema-only fixture, not hardware qualification"], "gui_run_design": True,
        "project_roundtrip": True, "recomputed_analysis_identical": True, "stale_consumers_cleared": True,
        "report_consistency": True, "consumers": {key: identity for key in
            ("summary_spec_identity", "loss_spec_identity", "report_spec_identity")}}


def _proof(**overrides):
    payload = {
        "proof_schema": 4, "success": True, "version": "9.4.1", "frozen": True, "platform": sys.platform,
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
    payload["power_stage_priority"] = _priority_proof(payload["power_stage_advanced"])
    payload["magnetic_catalog"] = _magnetic_proof()
    payload["integrated_magnetics"] = _integrated_proof()
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
    # Real resource bytes, but the child/proof fixture remains schema-only.
    for name in ("csc_magnetic_catalog.json", "magnetic_supplemental_records.json"):
        (tmp_path / "engineering_data" / name).write_bytes((ROOT / "engineering_data" / name).read_bytes())
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
