"""A04 independent switched-branch integration and thermal/network oracles.

The fixture is a prescribed accepted averaged trajectory, not a claim of a
hardware-validated control solution. Fast triangular current is integrated on
separate on/off intervals to avoid importing the production loss formula.
"""
import json
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from pfc_design.control import PFCControlLabConfig
from pfc_design.core.spec import MosfetSpec
from pfc_design.engineering import TTPLDesignSpec, analyze_ttpl_design
from pfc_design.engineering.cap_thermal import (
    CapacitorUnitSpec,
    TTPLThermalConfig,
    design_bus_capacitor_bank,
    solve_ttpl_semiconductor_thermal,
)
from pfc_design.engineering.device_loss import (
    compare_ttpl_devices,
    evaluate_hf_device,
    evaluate_slow_device,
)
from pfc_design.engineering.pfc_v3 import (
    ConvergenceStatus,
    LineCycleConvergenceResult,
    PFCLineCycleResult,
)


def _device(temperature_dependent=False):
    return MosfetSpec("independent", "conduction-only-oracle", "Si", 900, 100, 100,
        0.08, 0.16 if temperature_dependent else 0.08, 0,
        0, 0, 0, 0, 10)


def _line(asymmetric=False):
    cfg = PFCControlLabConfig()
    stage = replace(cfg.power_stage, switching_frequency_hz=80_000.0,
                    boost_inductance_h=500e-6)
    cfg = replace(cfg, power_stage=stage)
    n = 512
    theta = 2 * np.pi * (np.arange(n) + 0.5) / n
    time = np.arange(n) / (n * stage.line_frequency_hz)
    vac = np.sqrt(2) * stage.vin_rms_v * np.sin(theta)
    iac = 12 * np.sin(theta) + 1.8 * np.sin(3 * theta)
    if asymmetric:
        iac = iac * np.where(vac > 0, 1.5, 0.7)
    il = np.abs(iac)
    duty = 1 - np.abs(vac) / stage.bus_voltage_v
    ripple = np.abs(vac) * duty / (stage.boost_inductance_h * stage.switching_frequency_hz)
    vbus = np.full(n, stage.bus_voltage_v)
    # Known orthogonal AC components make ESR/RMS a separate analytic oracle.
    capacitor = 5 * np.cos(2 * theta) + 2 * np.sin(4 * theta)
    pin = float(np.mean(vac * iac))
    pf = pin / (np.sqrt(np.mean(vac * vac)) * np.sqrt(np.mean(iac * iac)))
    waveform = SimpleNamespace(metrics=SimpleNamespace(power_factor=pf,
        current_thd_percent=15.0, bus_voltage_average_v=stage.bus_voltage_v))
    conv = LineCycleConvergenceResult(ConvergenceStatus.LINE_CYCLE_CONVERGED,
        0, stage.bus_voltage_v, stage.bus_voltage_v, 0, 0,
        float(np.sqrt(np.mean(iac * iac))))
    return PFCLineCycleResult(waveform, np.degrees(theta), time, vac, np.abs(vac),
        iac, iac.copy(), vbus, duty, il, ripple, il + ripple / 2, il - ripple / 2,
        stage.switching_frequency_hz, vac * iac, np.full(n, pin), il.copy(),
        capacitor, il.copy(), np.abs(iac), conv, slice(None), cfg)


def _branch_square_integrals(line):
    """Physical upper/lower/LF I² averages using independent switch substeps."""
    # Midpoint integration converges quadratically. 2048 subsamples make the
    # numerical oracle error much smaller than the asserted engineering tol.
    u = (np.arange(2048) + 0.5) / 2048
    upper = lower = lf_upper = lf_lower = 0.0
    for vin, current, delta, duty in zip(line.vac_v, line.il_avg_a,
                                        line.il_ripple_pp_a, line.duty):
        rising = current - delta / 2 + delta * u
        falling = current + delta / 2 - delta * u
        active_integral = float(np.mean(rising * rising)) * duty
        sr_integral = float(np.mean(falling * falling)) * (1 - duty)
        if vin >= 0:
            lower += active_integral
            upper += sr_integral
            lf_lower += active_integral + sr_integral
        else:
            upper += active_integral
            lower += sr_integral
            lf_upper += active_integral + sr_integral
    return np.array([upper, lower, lf_upper, lf_lower]) / len(line.time_s)


@pytest.fixture(scope="module")
def design():
    return analyze_ttpl_design(TTPLDesignSpec())


def test_two_series_legs_carry_identical_switching_ripple_current(design):
    line = _line()
    device = _device()
    hf = evaluate_hf_device(design, device, vin_rms_v=230,
                            deadtime_s=0, line_cycle=line)
    lf = evaluate_slow_device(design, device, vin_rms_v=230, line_cycle=line)
    expected = sum(_branch_square_integrals(line)[:2]) * 0.08
    assert hf.active_conduction_w + hf.sr_conduction_w == pytest.approx(expected, rel=1e-8)
    # Slow commutation frequency does not remove high-frequency channel ripple.
    assert lf.conduction_w == pytest.approx(expected, rel=1e-8)


@pytest.mark.parametrize("asymmetric", [False, True])
def test_physical_hf_temperatures_follow_half_cycle_role_swapping(design, asymmetric):
    line = _line(asymmetric)
    config = TTPLThermalConfig(ambient_temperature_c=40,
        hf_rth_ja_k_per_w=2.5, slow_rth_ja_k_per_w=2,
        deadtime_s=0, reverse_drop_v=0, convergence_tolerance_c=1e-7,
        max_iterations=150)
    result = solve_ttpl_semiconductor_thermal(design, _device(), _device(),
                                               config=config, line_cycle=line)
    m2 = _branch_square_integrals(line)
    expected = m2[:2] * 0.08
    assert result.hf_upper_device_loss_w == pytest.approx(expected[0], rel=1e-8)
    assert result.hf_lower_device_loss_w == pytest.approx(expected[1], rel=1e-8)
    assert result.hf_upper_junction_temperature_c == pytest.approx(40 + expected[0] * 2.5)
    assert result.hf_lower_junction_temperature_c == pytest.approx(40 + expected[1] * 2.5)
    assert result.hf_loss.total_w == pytest.approx(sum(expected), rel=1e-8)
    if asymmetric:
        assert abs(expected[0] - expected[1]) > 0.1
    else:
        assert expected[0] == pytest.approx(expected[1])
    assert result.converged


def test_each_physical_die_uses_its_own_rds_temperature_feedback(design):
    line = _line(asymmetric=True)
    config = TTPLThermalConfig(ambient_temperature_c=40,
        hf_rth_ja_k_per_w=2.5, slow_rth_ja_k_per_w=2,
        deadtime_s=0, reverse_drop_v=0, convergence_tolerance_c=1e-8,
        max_iterations=200)
    result = solve_ttpl_semiconductor_thermal(design, _device(True), _device(True),
                                               config=config, line_cycle=line)
    m2 = _branch_square_integrals(line)[:2]
    # Independently solve T = Ta + theta * I² * (R25 + slope * (T - 25)).
    slope = (0.16 - 0.08) / 125
    expected_t = (40 + 2.5 * m2 * (0.08 - 25 * slope)) / (1 - 2.5 * m2 * slope)
    expected_p = (expected_t - 40) / 2.5
    assert result.hf_upper_junction_temperature_c == pytest.approx(expected_t[0], rel=1e-8)
    assert result.hf_lower_junction_temperature_c == pytest.approx(expected_t[1], rel=1e-8)
    assert result.hf_upper_device_loss_w == pytest.approx(expected_p[0], rel=1e-8)
    assert result.hf_lower_device_loss_w == pytest.approx(expected_p[1], rel=1e-8)
    assert result.hf_loss.total_w == pytest.approx(sum(expected_p), rel=1e-8)
    # The LF switches need their own feedback too: asymmetric half cycles
    # must not be hidden by the old pair-average "each" scalar.
    lf_m2 = _branch_square_integrals(line)[2:]
    lf_t = (40 + 2 * lf_m2 * (0.08 - 25 * slope)) / (1 - 2 * lf_m2 * slope)
    lf_p = (lf_t - 40) / 2
    assert result.slow_upper_junction_temperature_c == pytest.approx(lf_t[0], rel=1e-8)
    assert result.slow_lower_junction_temperature_c == pytest.approx(lf_t[1], rel=1e-8)
    assert result.slow_upper_device_loss_w == pytest.approx(lf_p[0], rel=1e-8)
    assert result.slow_lower_device_loss_w == pytest.approx(lf_p[1], rel=1e-8)
    assert result.slow_loss.total_w == pytest.approx(sum(lf_p), rel=1e-8)
    assert result.temperature_residual_c <= config.convergence_tolerance_c
    assert result.converged


def test_pf_thd_cap_esr_report_share_accepted_cycle_and_actual_frequency(design):
    line = _line()
    device = replace(_device(), qg_nc=19)
    comparison = compare_ttpl_devices(design, SimpleNamespace(all=[device]),
        workpoint="high", deadtime_s=0, line_cycle=line)
    capacitor = design_bus_capacitor_bank(design, CapacitorUnitSpec(), line_cycle=line)
    assert comparison.line_cycle is line
    assert comparison.power_factor == pytest.approx(1 / np.sqrt(1 + 0.15 ** 2), abs=1e-10)
    assert comparison.thd_percent == pytest.approx(15.0, abs=1e-9)
    assert comparison.capacitor_current_rms_a == pytest.approx(np.sqrt(14.5))
    # Independently integrate capacitor KCL on the two switching intervals.
    u = (np.arange(4096) + 0.5) / 4096
    squares = []
    for mean, delta, duty, cap_mean in zip(line.il_avg_a, line.il_ripple_pp_a,
                                           line.duty, line.capacitor_current_a):
        load = (1 - duty) * mean - cap_mean
        off_cap = mean + delta / 2 - delta * u - load
        squares.append(duty * load**2 + (1 - duty) * np.mean(off_cap**2))
    cap_square = np.mean(squares)
    assert capacitor.capacitor_ripple.complete
    assert capacitor.bank_ripple_current_rms_a == pytest.approx(np.sqrt(cap_square), rel=1e-8)
    assert capacitor.bank_esr_loss_w == pytest.approx(cap_square * capacitor.bank_esr_ohm, rel=1e-8)
    assert capacitor.bank_averaged_esr_loss_w == pytest.approx(14.5 * capacitor.bank_esr_ohm)
    assert comparison.hf_devices[0].gate_drive_w == pytest.approx(2 * 19e-9 * 10 * 80_000)
    report = json.loads(json.dumps(comparison.as_dict()))
    assert report["source_config"]["power_stage"]["switching_frequency_hz"] == 80_000
    assert report["source_line_cycle"]["fsw_hz"] == 80_000
    assert report["source_line_cycle"]["capacitor_current_rms_a"] == pytest.approx(np.sqrt(14.5))
    assert report["power_factor"] == pytest.approx(report["source_line_cycle"]["metrics"]["pf"])


@pytest.mark.parametrize("consumer", ["device", "capacitor", "thermal"])
def test_unaccepted_cycle_is_rejected_by_every_downstream_consumer(design, consumer):
    line = _line()
    line = replace(line, convergence=replace(line.convergence, status=ConvergenceStatus.NOT_CONVERGED))
    with pytest.raises(ValueError, match="converged"):
        if consumer == "device":
            compare_ttpl_devices(design, SimpleNamespace(all=[_device()]), line_cycle=line)
        elif consumer == "capacitor":
            design_bus_capacitor_bank(design, CapacitorUnitSpec(), line_cycle=line)
        else:
            solve_ttpl_semiconductor_thermal(design, _device(), _device(), line_cycle=line)


def test_polarity_reversal_swaps_every_physical_switching_loss_bucket(design):
    line = _line(asymmetric=True)
    inverted = replace(line, vac_v=-line.vac_v, iac_a=-line.iac_a)
    device = replace(_device(), eon_ref_uj=70, eoff_ref_uj=23,
                     coss_er_pF=90, qg_nc=19)
    first = evaluate_hf_device(design, device, vin_rms_v=230, line_cycle=line)
    second = evaluate_hf_device(design, device, vin_rms_v=230, line_cycle=inverted)
    # Reverse external AC polarity while preserving the same rectified
    # trajectory. Every fixed device's electrical history must exchange sides.
    for name in ("conduction_w", "switching_w", "deadtime_reverse_w", "coss_w", "gate_drive_w", "total_w"):
        assert getattr(first.upper, name) == pytest.approx(getattr(second.lower, name))
        assert getattr(first.lower, name) == pytest.approx(getattr(second.upper, name))
    assert first.upper.switching_w != pytest.approx(first.lower.switching_w)
    assert first.total_w == pytest.approx(second.total_w)
    lf1 = evaluate_slow_device(design, device, vin_rms_v=230, line_cycle=line)
    lf2 = evaluate_slow_device(design, device, vin_rms_v=230, line_cycle=inverted)
    assert lf1.upper.total_w == pytest.approx(lf2.lower.total_w)
    assert lf1.lower.total_w == pytest.approx(lf2.upper.total_w)


@pytest.mark.parametrize("workpoint", ["low", "nominal", "high"])
def test_original_sizing_entrypoints_preserve_symmetric_physical_domain(design, workpoint):
    result = solve_ttpl_semiconductor_thermal(design, _device(), _device(),
        workpoint=workpoint, config=TTPLThermalConfig(deadtime_s=0, reverse_drop_v=0))
    assert result.workpoint == workpoint
    assert result.waveform_source == "ideal_sizing_half_cycle"
    assert result.hf_upper_device_loss_w == pytest.approx(result.hf_lower_device_loss_w)
    assert result.slow_upper_device_loss_w == pytest.approx(result.slow_lower_device_loss_w)
    assert result.hf_loss.total_w == pytest.approx(result.slow_loss.total_w)
    assert result.hf_loss.total_w == pytest.approx(
        result.hf_upper_device_loss_w + result.hf_lower_device_loss_w)
    assert result.temperature_residual_c <= result.config.convergence_tolerance_c
    assert result.converged
