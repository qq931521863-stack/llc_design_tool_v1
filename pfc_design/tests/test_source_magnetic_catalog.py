"""Source boundaries and independent unit/formula checks for uploaded catalogs."""
from copy import deepcopy
import json
import math

import pytest

from pfc_design.magnetics.core_database import CoreDatabase
from pfc_design.magnetics.manufacturer_catalog import (
    CatalogDomainError, ManufacturerCoreCatalog, SourceCoreSelection,
    evaluate_printed_formula, reference_evaluation, source_pfc_components,
)
from pfc_design.magnetics.pfc_inductor_designer import PFCInductorDesignRequest, design_pfc_inductor
from pfc_design.magnetics.source_project import project_document, read_project, recompute_project, save_project, write_report


@pytest.fixture
def catalog():
    return ManufacturerCoreCatalog()


@pytest.fixture
def selection(catalog):
    return catalog.select("csc2025:part:CS400060:054")


@pytest.fixture
def declaration():
    # TEST FIXTURE ONLY: explicit assumptions exercise the user-declared path.
    # These are never shipped as manufacturer coefficients or default UI values.
    return {"B_convention": "peak_ac", "waveform": "sinusoidal_reference",
            "dimension_basis": "after_coating_limits", "source": "Test-only explicit application assumptions",
            "H_Oe": [0, 1000], "B_T": [0, 1], "frequency_Hz": [5000, 300000],
            "temperature_C": [20, 100], "core_temperature_C": 25}


def request(selection, declaration):
    core, material = source_pfc_components(selection, declaration)
    return PFCInductorDesignRequest(
        topology="ttpl", input_rms_v=230, bus_voltage_v=400, output_power_w=1000,
        switching_frequency_hz=120000, target_inductance_uh=200, efficiency=.97,
        core=core, material=material, n_cores=2,
        source_snapshot=selection.snapshot, model_declaration=declaration)


def test_source_records_never_enter_legacy_fallback_path(catalog):
    db = CoreDatabase(include_user=False)
    assert len(catalog.documents) == 4
    assert len(catalog.geometries) >= 36
    assert len([r for r in catalog.records if catalog.record_types[r] == "explicit_part"]) == 630
    assert all(not c.part_number.startswith("csc2025:") for c in db.cores)
    assert db.source_catalog.select("csc2025:part:CM035060:032").record["density_g_per_cm3"] is None
    assert "dc_bias_coeffs" not in catalog.records["csc2025:part:CM035060:032"]
    assert len(db.query_source_catalog("CM035060", kind="explicit_part")) == 1


def test_independent_geometry_units_and_original_page_identity(catalog):
    selected = catalog.select("csc2025:part:CM035060:032")
    assert selected.geometry["ae_cm2"] == .0137
    assert selected.geometry["le_cm"] == .817
    assert selected.geometry["ve_cm3"] == .010746
    assert selected.geometry["source"]["pdf_page"] == 18
    assert selected.geometry["source"]["printed_page"] == "032"
    result = selected.calculate_zero_bias(20, 2)
    assert result["l_zero_bias_uh"] == pytest.approx(13 * 20**2 * 2 / 1000)
    assert result["l_tolerance_range_uh"] == pytest.approx([9.152, 11.648])
    # Original unit typo is retained as UNKNOWN, never silently normalized.
    assert catalog.geometries["csc2025:geometry:OD066:036"]["ve_cm3"] is None


def test_bias_and_two_term_loss_match_independent_native_unit_arithmetic(catalog):
    bias = catalog.models["csc2025:bias:MPP:60"]
    loss = catalog.models["csc2025:loss:MPP:26-60"]
    h = 100
    assert evaluate_printed_formula(bias, H_Oe=h) == pytest.approx(1 / (.01 + 1.27e-7 * h**2.412))
    # 0.1 T == 1 kG, 100000 Hz == 100 kHz, mW/cm3 * 1000 == W/m3.
    pv = 1**2.183 * (2.485 * 100 + .0125 * 100**2.099)
    assert evaluate_printed_formula(loss, B_kG=1, f_kHz=100) == pytest.approx(pv)
    broken = deepcopy(loss); broken["input_units"]["B"] = "T"
    with pytest.raises(ValueError, match="units"):
        evaluate_printed_formula(broken, B_kG=1, f_kHz=100)
    broken = deepcopy(loss); broken["equation"] = "other formula"
    with pytest.raises(ValueError, match="expression"):
        evaluate_printed_formula(broken, B_kG=1, f_khz=100)


def test_reference_uses_chart_coverage_without_claiming_manufacturer_validity(selection):
    result = reference_evaluation(selection, h_oe=100, b_kg=1, f_khz=100)
    assert result["reference_l_at_h_uh"] < result["l_zero_bias_uh"]
    assert result["reference_pv_mW_cm3"] > 0
    assert "not a validity guarantee" in result["loss_scope"]
    assert any("峰值" in v for v in result["full_pfc_blockers"])
    for kwargs in ({"h_oe": 1001}, {"b_kg": 0.001, "f_khz": 100}, {"b_kg": 1, "f_khz": 301}):
        with pytest.raises(CatalogDomainError): reference_evaluation(selection, **kwargs)


@pytest.mark.parametrize("value", [0, -1, 2.5, True, float("nan")])
def test_no_invalid_turns_are_coerced(selection, value):
    with pytest.raises(ValueError): selection.calculate_zero_bias(value)


def test_no_cross_material_or_conflict_override(selection, declaration):
    snapshot = deepcopy(selection.snapshot)
    snapshot["dc_bias_model"]["material"] = "MPP"
    with pytest.raises(ValueError, match="identity"):
        source_pfc_components(SourceCoreSelection(snapshot), declaration)
    snapshot = deepcopy(selection.snapshot)
    snapshot["dc_bias_model"]["conflict_ids"] = ["unresolved-material-label"]
    with pytest.raises(ValueError, match="conflict"):
        source_pfc_components(SourceCoreSelection(snapshot), declaration)
    with pytest.raises(ValueError): source_pfc_components(selection, None)


def test_explicit_user_model_reaches_existing_powder_solver(selection, declaration, monkeypatch):
    monkeypatch.setattr(CoreDatabase, "get_steinmetz", lambda *a: pytest.fail("Fuzzy legacy model lookup forbidden"))
    req = request(selection, declaration)
    result = design_pfc_inductor(req)
    assert req.core.ae_mm2 == pytest.approx(107.2)
    assert req.core.le_mm == pytest.approx(98.4)
    assert req.core.ve_mm3 == pytest.approx(10548.5)
    assert result.inductance_target_met and result.window_ok
    assert "CS400060" in result.request.core.core_data
    assert result.request.source_snapshot["record"]["record_id"] == selection.record_id
    assert result.core_loss_w > 0 and result.copper_loss_w > 0
    assert any("用户声明" in warning for warning in result.warnings)
    # Independent recomputation of the line-cycle source formula, no helper use.
    c = selection.snapshot["loss_model"]["coefficients"]
    expected = sum((10 * b)**c["beta"] * (c["a"] * 120 + c["b"] * 120**c["gamma"])
                   for b in result.line_b_ac_t) / len(result.line_b_ac_t) * 10.5485 * 2 / 1000
    assert result.core_loss_w == pytest.approx(expected)
    declaration["frequency_Hz"] = [5000, 100000]
    with pytest.raises(CatalogDomainError): design_pfc_inductor(request(selection, declaration))


def test_project_preserves_snapshot_recomputes_and_report_names_actual_core(selection, declaration, tmp_path):
    from dataclasses import fields
    req = request(selection, declaration)
    inputs = {f.name: getattr(req, f.name) for f in fields(req) if f.name not in {"core", "material", "source_snapshot", "model_declaration"}}
    doc = project_document(selection, reference_inputs={"turns": 37, "n_cores": 2, "h_oe": 100}, pfc_inputs=inputs, declaration=declaration)
    path = save_project(doc, tmp_path / "magnetic.json")
    restored = read_project(path)
    selected, reference, result = recompute_project(restored)
    assert selected.identity == selection.identity
    assert reference["turns"] == 37
    assert result.request.core.core_data == req.core.core_data
    report = write_report(restored, tmp_path / "report.html").read_text(encoding="utf-8")
    assert selection.record_id in report and selection.identity in report
    assert "用户声明模型 PFC 参考估算" in report
    assert "CSC2025.pdf" in report and "d89db47034ea0c56b3e42f9eee3005646766e3a664044d624e20628e2a3949f5" in report
    restored["selection"]["record"]["al_nH_per_t2"] *= 2
    path.write_text(json.dumps(restored), encoding="utf-8")
    with pytest.raises(ValueError, match="identity mismatch"): read_project(path)


def test_companion_case_is_independent_and_arithmetic_matches_source():
    from pfc_design.magnetics.supplemental_catalog import load_supplemental_catalog, replay_pfc_document_example
    data = load_supplemental_catalog()
    assert len(data["documents"]) == 3
    assert len(data["material_profiles"]) == 29
    assert len(data["part_records"]) == 46
    assert len(data["performance_points"]) == 99
    result = replay_pfc_document_example(data)
    assert result["Lpfc_min_uH"] == pytest.approx(197.955172094428, abs=1e-9)
    assert result["Bac_T"] == pytest.approx(.04925238234335266, abs=1e-12)
    assert result["Pcv_mW_per_cm3"] == pytest.approx(181.99762105932373, abs=1e-9)
    assert result["PL_W"] == pytest.approx(3.4640853817133337, abs=1e-12)
    assert result["L_shortfall_vs_200uH_percent"] > 1
    assert any("120kHz" in x for x in result["engineering_cautions"])


@pytest.mark.parametrize("field,value", [("efficiency", 1.01), ("efficiency", float("nan")),
    ("input_rms_v", float("inf")), ("n_cores", 1.5), ("n_cores", True),
    ("curve_points", 0), ("curve_points", 10001), ("enamel_build_mm", -1)])
def test_source_project_strict_numeric_inputs(selection, declaration, field, value):
    from dataclasses import fields
    req = request(selection, declaration)
    inputs = {f.name: getattr(req, f.name) for f in fields(req) if f.name not in {"core", "material", "source_snapshot", "model_declaration"}}
    inputs[field] = value
    with pytest.raises(ValueError):
        project_document(selection, pfc_inputs=inputs, declaration=declaration)


def test_declared_negative_temperature_supported_without_guessing(selection, declaration):
    declaration["temperature_C"] = [-40, 100]
    declaration["core_temperature_C"] = -20
    _, material = source_pfc_components(selection, declaration)
    assert material.declaration["core_temperature_C"] == -20
    declaration["temperature_C"] = [-274, 100]
    with pytest.raises(ValueError): source_pfc_components(selection, declaration)


@pytest.mark.parametrize("topology,vin,bus", [("ttpl", 300, 400), ("ttpl", 400/math.sqrt(2), 400), ("vienna", 500, 700)])
def test_source_solver_rejects_nonboost_operating_points(selection, declaration, topology, vin, bus):
    from dataclasses import replace
    req = replace(request(selection, declaration), topology=topology, input_rms_v=vin, bus_voltage_v=bus)
    with pytest.raises(ValueError, match="headroom"): design_pfc_inductor(req)


def test_source_request_rejects_geometry_and_provenance_mismatch(selection, declaration):
    from dataclasses import replace
    req = request(selection, declaration)
    with pytest.raises(ValueError, match="geometry identity"):
        design_pfc_inductor(replace(req, core=replace(req.core, ae_mm2=req.core.ae_mm2 * 2)))
    with pytest.raises(ValueError, match="provenance"):
        design_pfc_inductor(replace(req, source_snapshot=None))


@pytest.mark.parametrize("key,value", [("input_rms_v", float("nan")), ("efficiency", 1.1), ("n_cores", 1.5), ("n_cores", True)])
def test_direct_source_request_has_same_numeric_gates(selection, declaration, key, value):
    from dataclasses import replace
    with pytest.raises(ValueError): design_pfc_inductor(replace(request(selection, declaration), **{key: value}))


def test_conflicting_al_is_not_calculated_even_as_nominal(catalog):
    selected = catalog.select("csc2025:availability:OD035:173:016")
    with pytest.raises(ValueError, match="AL"):
        selected.calculate_zero_bias(20)


def test_al_measurement_conditions_are_carried_to_result(selection):
    result = selection.calculate_zero_bias(20)
    assert result["al_measurement_conditions"]["frequency_Hz"] == 10000
    assert result["al_measurement_conditions"]["B_peak_T"] == .001


def test_source_winding_model_rejects_negative_resistance_temperature(selection, declaration):
    from dataclasses import replace
    with pytest.raises(ValueError, match="copper temperatures"):
        design_pfc_inductor(replace(request(selection, declaration), copper_temperature_c=-250))
