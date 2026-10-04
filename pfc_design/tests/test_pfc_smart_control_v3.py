"""PFC Engineering V3 — Smart Control dual-loop separation / Exact H(z)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from llc_design.control.smart_control import MetricStatus
from pfc_design.control import PFCControlLabConfig, build_pfc_control_lab_analysis
from pfc_design.control.handoff import assert_handoff_matches_analysis
from pfc_design.engineering.pfc_v3 import (
    build_pfc_smart_control_v3,
    localize_distortion,
    build_line_cycle_result,
    run_pfc_engineering_v3_core,
)


def test_smart_control_exposes_separation_ms_mt_and_2fline():
    cfg = PFCControlLabConfig()
    smart = build_pfc_smart_control_v3(cfg)
    assert smart.separation.line_2x_hz == pytest.approx(
        2.0 * cfg.power_stage.line_frequency_hz
    )
    assert smart.current.ms >= 1.0
    assert smart.voltage.ms >= 1.0
    assert smart.current.mt >= 0.0
    for result, loop, keys in (
        (smart.current, smart.analysis.current_loop,
         ("controller_ci", "indu_comp_gain", "pwm_zoh", "plant_gid", "sense_hi")),
        (smart.voltage, smart.analysis.voltage_loop,
         ("controller_cv", "amc_vff", "bus_plant_gvg", "sense_hv")),
    ):
        budget = result.phase_budget
        assert result.fc_hz is not None
        assert budget is not None
        assert tuple(entry.key for entry in budget.entries) == keys
        assert budget.frequency_hz == pytest.approx(result.fc_hz)
        assert budget.phase_margin_deg == pytest.approx(result.pm_deg)
        assert budget.consistent is True
        assert abs(budget.residual_deg) <= budget.tolerance_deg
        assert budget.residual_deg == pytest.approx(0.0, abs=1e-9)
        expected_phase = np.interp(
            np.log10(result.fc_hz), np.log10(smart.analysis.frequencies_hz),
            np.rad2deg(np.unwrap(np.angle(loop.responses[f"open_{result.name}"]))),
        )
        assert budget.open_loop_phase_deg == pytest.approx(expected_phase, abs=1e-9)
        assert budget.total_phase_deg == pytest.approx(expected_phase, abs=1e-9)
    assert smart.handoff.current.b
    assert smart.handoff.voltage.a[0] == pytest.approx(1.0)
    assert_handoff_matches_analysis(smart.analysis, smart.handoff)


@pytest.fixture
def healthy_analysis():
    """Keep unrelated low-PM/multiple-crossing warnings out of warning tests."""
    cfg = PFCControlLabConfig()
    cfg = replace(
        cfg,
        current_controller=replace(cfg.current_controller, kp=0.005, ti_s=0.001),
        voltage_controller=replace(cfg.voltage_controller, ti_s=0.1),
        frequency_stop_hz=5e3,
    )
    analysis = build_pfc_control_lab_analysis(cfg)
    smart = build_pfc_smart_control_v3(cfg, analysis=analysis)
    for result in (smart.current, smart.voltage):
        assert result.pm_deg > 35.0
        assert result.multi_crossover is False
        assert result.phase_budget.consistent is True
        assert result.status is MetricStatus.APPROXIMATION
    assert smart.separation.status is MetricStatus.APPROXIMATION
    return analysis


@pytest.mark.parametrize("loop_name", ["current", "voltage"])
@pytest.mark.parametrize("pm_deg", [-5.0, 0.0, 34.9, 35.0, None])
def test_loop_status_uses_phase_margin_warning_threshold(healthy_analysis, loop_name, pm_deg):
    loop = getattr(healthy_analysis, f"{loop_name}_loop")
    loop = replace(loop, margins=replace(loop.margins, phase_margin_deg=pm_deg))
    analysis = replace(healthy_analysis, **{f"{loop_name}_loop": loop})
    smart = build_pfc_smart_control_v3(analysis.config, analysis=analysis)
    result = getattr(smart, loop_name)
    assert result.phase_budget.consistent is True
    assert result.multi_crossover is False
    assert result.status is (MetricStatus.APPROXIMATION if pm_deg == 35.0 else MetricStatus.WARN)
    assert result.as_dict()["status"] == result.status.value


@pytest.mark.parametrize("loop_name", ["current", "voltage"])
def test_missing_crossover_warns_for_loop_and_separation(healthy_analysis, loop_name):
    loop = getattr(healthy_analysis, f"{loop_name}_loop")
    loop = replace(loop, margins=replace(
        loop.margins, gain_crossovers_hz=(), phase_margins_deg=(),
        critical_gain_crossover_hz=None, phase_margin_deg=None,
    ))
    analysis = replace(healthy_analysis, **{f"{loop_name}_loop": loop})
    smart = build_pfc_smart_control_v3(analysis.config, analysis=analysis)
    result = getattr(smart, loop_name)
    assert result.fc_hz is None
    assert result.phase_budget is None
    assert result.status is MetricStatus.WARN
    assert smart.separation.ratio is None
    assert smart.separation.status is MetricStatus.WARN
    assert any("unavailable" in note.lower() for note in smart.separation.notes)


@pytest.mark.parametrize("loop_name,block_key", [
    ("current", "controller_ci"), ("voltage", "controller_cv"),
])
def test_inconsistent_phase_budget_warns_without_other_warning_causes(
    healthy_analysis, loop_name, block_key,
):
    loop = getattr(healthy_analysis, f"{loop_name}_loop")
    responses = dict(loop.responses)
    # Corrupt only the component evidence; keep the authoritative open loop/margins.
    responses[block_key] = responses[block_key] * np.exp(1j * np.deg2rad(10.0))
    analysis = replace(healthy_analysis, **{
        f"{loop_name}_loop": replace(loop, responses=responses),
    })
    smart = build_pfc_smart_control_v3(analysis.config, analysis=analysis)
    result = getattr(smart, loop_name)
    assert result.pm_deg > 35.0
    assert result.multi_crossover is False
    assert result.phase_budget.consistent is False
    assert result.phase_budget.residual_deg == pytest.approx(10.0, abs=1e-9)
    assert result.status is MetricStatus.WARN


@pytest.mark.parametrize("loop_name", ["current", "voltage"])
def test_multiple_crossovers_remain_explicit(healthy_analysis, loop_name):
    loop = getattr(healthy_analysis, f"{loop_name}_loop")
    fc = loop.margins.critical_gain_crossover_hz
    loop = replace(loop, margins=replace(
        loop.margins, gain_crossovers_hz=(fc, 2.0 * fc),
    ))
    analysis = replace(healthy_analysis, **{f"{loop_name}_loop": loop})
    smart = build_pfc_smart_control_v3(analysis.config, analysis=analysis)
    result = getattr(smart, loop_name)
    assert result.multi_crossover is True
    assert result.status is MetricStatus.MULTI_CROSSOVER


@pytest.mark.parametrize("fc_i,fc_v,expected_status", [
    (40.0, 10.0, MetricStatus.WARN),
    (2000.0, 31.0, MetricStatus.WARN),
    (150.0, 30.0, MetricStatus.APPROXIMATION),
])
def test_loop_separation_warning_thresholds(healthy_analysis, fc_i, fc_v, expected_status):
    analysis = replace(
        healthy_analysis,
        current_loop=replace(healthy_analysis.current_loop, margins=replace(
            healthy_analysis.current_loop.margins, critical_gain_crossover_hz=fc_i,
        )),
        voltage_loop=replace(healthy_analysis.voltage_loop, margins=replace(
            healthy_analysis.voltage_loop.margins, critical_gain_crossover_hz=fc_v,
        )),
    )
    separation = build_pfc_smart_control_v3(analysis.config, analysis=analysis).separation
    assert separation.ratio == pytest.approx(fc_i / fc_v)
    assert separation.voltage_fc_vs_2fline == pytest.approx(fc_v / 100.0)
    assert separation.status is expected_status


def test_exact_hz_matches_analysis_controllers():
    analysis = build_pfc_control_lab_analysis(PFCControlLabConfig())
    smart = build_pfc_smart_control_v3(PFCControlLabConfig(), analysis=analysis)
    assert np.allclose(smart.handoff.current.b, analysis.current_loop.controller.numerator)
    assert np.allclose(smart.handoff.current.a, analysis.current_loop.controller.denominator)


def test_distortion_localization_returns_regions():
    cfg = replace(
        PFCControlLabConfig(),
        waveform_line_cycles=3,
        waveform_integration_rate_hz=250e3,
    )
    line = build_line_cycle_result(cfg)
    regions = localize_distortion(line)
    assert len(regions) == 5
    assert regions[0].angle_start_deg == 0.0
    assert sum(r.harmonic_contribution_estimate for r in regions) == pytest.approx(1.0, abs=0.05)


def test_core_pack_runs_end_to_end():
    cfg = replace(
        PFCControlLabConfig(),
        waveform_line_cycles=3,
        waveform_integration_rate_hz=250e3,
    )
    pack = run_pfc_engineering_v3_core(cfg)
    assert pack["pf_thd"]["PF"] <= 1.01
    assert pack["pf_thd"]["THD"] >= 0.0
    assert "zero_crossing" in pack
    assert "smart_control" in pack
    assert pack["smart_control"]["separation"]["2x_line_hz"] == pytest.approx(100.0)
