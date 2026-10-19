"""Integrated magnetic reports preserve physical/APR identity and absent Lr."""
import json
from dataclasses import replace

import pytest

from llc_design.core.spec import LLCDesignSpec, MagneticsMode
from llc_design.models.system import LLCSystemAnalyzer
from llc_design.report.export import (
    export_calculation_book,
    integrated_magnetic_record,
    operating_point_rows,
)
from llc_design.report.formula_pdf import build_formula_pdf, build_formula_steps


@pytest.fixture(scope="module")
def integrated_analysis():
    # A declared linear T fixture: Lmu=300 uH; primary and referred-secondary
    # leakage each 20 uH. This is software evidence, not a measured prototype.
    spec = LLCDesignSpec(
        magnetics_mode=MagneticsMode.INTEGRATED,
        integrated_characterized_core="PQ50_REF",
        integrated_characterized_primary_turns=30,
        integrated_characterized_secondary_turns=4,
        integrated_lp_h=320e-6, integrated_ls_h=320e-6 / 7.5**2,
        integrated_mutual_h=300e-6 / 7.5,
        integrated_measurement_note="Explicit linear report QA fixture, not hardware measurement",
        vbus_hold_end_v=320, bus_capacitance_f=.0033,
    )
    return LLCSystemAnalyzer().analyze(spec, work_points=[(400, 1)])


def indexed_steps(analysis):
    return {step.name: step for group in build_formula_steps(analysis).values() for step in group}


def test_integrated_report_carries_physical_and_electrical_ratios(integrated_analysis):
    record = integrated_magnetic_record(integrated_analysis.spec)
    assert record["physical_matrix"]["physical_turns_ratio"] == 7.5
    assert record["apr_effective_turns_ratio"] == pytest.approx(7.03125)
    assert record["physical_magnetizing_h"] == pytest.approx(300e-6)
    assert record["apr_lm_h"] == pytest.approx(281.25e-6)
    assert record["apr_lr_h"] == pytest.approx(38.75e-6)
    assert record["target_relative_error"] is None
    assert record["external_resonant_inductor_present"] is False
    row, = operating_point_rows(integrated_analysis)
    assert row["physical_turns_ratio"] != row["electrical_turns_ratio"]
    assert row["external_resonant_inductor_present"] is False
    assert row["resonant_inductor_core_w"] == row["resonant_inductor_copper_w"] == 0


def test_real_calculation_book_has_no_external_core_or_stale_candidates(integrated_analysis, tmp_path):
    stale = tmp_path / "resonant_inductor_core_candidates.csv"
    stale.write_text("obsolete external inductor", encoding="utf-8")
    paths = export_calculation_book(integrated_analysis, tmp_path)
    assert not stale.exists() and "inductor_candidates_csv" not in paths
    data = json.loads(paths["result_json"].read_text(encoding="utf-8"))
    assert data["resonant_inductor"] is None
    assert data["integrated_magnetics"] == integrated_magnetic_record(integrated_analysis.spec)
    text = paths["report"].read_text(encoding="utf-8")
    assert "无独立 Lr 磁件" in text and "电气等效匝比" in text
    assert "characterized" in text
    assert "绕组场能与漏感可实现性筛查" in text
    assert integrated_analysis.transformer.integrated_leakage.status in text
    assert "## 5. 外置谐振电感初算" not in text


def test_integrated_fha_formula_uses_physical_flux_and_gap(integrated_analysis, tmp_path):
    steps = indexed_steps(integrated_analysis)
    assert "n_eff" in steps["FHA load reflected to primary"].formula
    assert "0.0003" in steps["Transformer equivalent air gap"].substitution
    assert "Lmu" in steps["Transformer peak flux density"].formula
    assert "distinct from the GUI FHA" in steps["Transformer peak flux density"].method
    assert "physical" in steps["Transformer peak flux density"].formula
    assert steps["Resonant-inductor peak flux density"].formula == "NOT_APPLICABLE"
    assert "ABSENT" in steps["Magnetic thermal fixed-point iteration"].result
    assert "No leakage target" in steps["Leakage target versus characterization"].substitution
    path = build_formula_pdf(integrated_analysis, tmp_path / "integrated.pdf")
    assert path.read_bytes().startswith(b"%PDF")
    assert path.stat().st_size > 10000


def test_target_and_non_target_modes_are_not_conflated(integrated_analysis):
    spec = integrated_analysis.spec.clone(integrated_target_lr_h=50e-6)
    record = integrated_magnetic_record(spec)
    assert record["target_relative_error"] == pytest.approx(.225)
    steps = indexed_steps(replace(integrated_analysis, spec=spec))
    assert "0.225" in steps["Leakage target versus characterization"].substitution
    assert "not evidence" in steps["Leakage target versus characterization"].method


def test_integrated_td_report_retains_physical_current_and_single_core(tmp_path):
    """Use the real accepted TD consumer, not a fabricated result/provenance flag."""
    import numpy as np

    from llc_design.analysis.time_domain import TimeDomainConfig, solve_time_domain
    from llc_design.analysis.types import LLCAnalysisRequest

    spec = LLCDesignSpec(
        magnetics_mode=MagneticsMode.INTEGRATED,
        integrated_characterized_core="PQ50_REF",
        integrated_characterized_primary_turns=30,
        integrated_characterized_secondary_turns=4,
        integrated_lp_h=0.00014268643235100037,
        integrated_ls_h=2.2010340190198587e-6,
        integrated_mutual_h=1.617760003979596e-5,
        integrated_measurement_note="Explicit linear TD report fixture, not hardware measurement",
    )
    analyzer = LLCSystemAnalyzer()
    fha = analyzer.analyze(spec, work_points=[(400, 1)])
    result = solve_time_domain(
        LLCAnalysisRequest(spec, vbus_v=400, samples_per_cycle=256, waveform_cycles=1),
        TimeDomainConfig(samples_per_cycle=256, output_cycles=1, strict_convergence=True),
    )
    assert result.convergence.converged
    accepted = analyzer.with_time_domain_losses(fha, [result])
    point = accepted.nominal
    trace = point.td_formula_inputs
    assert trace is not None and trace.inductor is None
    assert point.magnetic_source.startswith("TIME_DOMAIN")
    # Independently use actual terminal ampere-turns, not APR state algebra.
    signals = result.waveform.signals
    physical_i = (signals["i_resonant"].values
                  - signals["i_transformer_secondary"].values / spec.turns_ratio)
    assert trace.magnetizing_peak_a == pytest.approx(np.max(np.abs(physical_i)))
    assert trace.magnetizing_mean_a == pytest.approx(np.mean(physical_i), abs=1e-8)
    assert accepted.resonant_inductor is None
    assert point.resonant_inductor.total_w == 0
    core_input = trace.transformer
    core_from_trace = (core_input.correction * core_input.ki
                       * core_input.mean_abs_dbdt_alpha
                       * core_input.delta_b_t ** (core_input.beta-core_input.alpha)
                       * core_input.volume_m3)
    assert point.transformer.core_w == pytest.approx(core_from_trace)
    steps = indexed_steps(accepted)
    flux = steps["Transformer peak flux density"]
    assert "Accepted TD physical-current" in flux.method
    assert f"{trace.magnetizing_peak_a:.9g}" in flux.substitution
    assert "Lr:" not in steps["Magnetic core loss by iGSE"].substitution
    assert "Lr W:" not in steps["Litz winding loss by harmonic layer MMF"].substitution
    assert "external Lr ABSENT" in steps["Litz winding loss by harmonic layer MMF"].substitution
    assert "shared windings only" in steps["TD winding harmonic inputs"].result
    paths = export_calculation_book(accepted, tmp_path)
    document = json.loads(paths["result_json"].read_text(encoding="utf-8"))
    assert document["resonant_inductor"] is None
    assert document["integrated_magnetics"]["external_resonant_inductor_present"] is False
    assert build_formula_pdf(accepted, tmp_path / "integrated-td.pdf").read_bytes().startswith(b"%PDF")


def test_geometry_report_separates_reachability_from_hardware_proof(integrated_analysis):
    screen = integrated_analysis.transformer.integrated_leakage
    steps = indexed_steps(integrated_analysis)
    geometry = steps["Winding-field leakage realizability"]
    assert screen.status in geometry.result
    assert f"{screen.required_radial_build_m:.9g}" in geometry.result
    assert "not APR Lshort" in geometry.method
    assert "actual Lp/Ls/M and insulation require verification" in geometry.method
