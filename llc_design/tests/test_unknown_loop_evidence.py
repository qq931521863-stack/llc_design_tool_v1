"""Evidence regressions: measured FRD never becomes an invented proof."""
from __future__ import annotations

import numpy as np
import pytest

from llc_design.control.digital_loop import PIControllerConfig, calculate_stability_margins
from llc_design.control.smart_control import MetricStatus, build_robustness_report
from llc_design.control.solution_map import (
    SolutionMapConstraints,
    SolutionStatus,
    evaluate_fixed_controller_point,
    evaluate_solution_point,
)


def _fixed_response(delay_s=20e-6):
    f = np.geomspace(10.0, 20_000.0, 1600)
    pole_hz = 1000.0 / np.tan(np.deg2rad(60.0))
    return f, np.exp(-2j * np.pi * f * delay_s) / (1.0 + 1j * f / pole_hz)


def _synthesized_point(*, delay_s=20e-6, switching_hz=20_000.0, constraints=None):
    f, fixed = _fixed_response(delay_s)
    return evaluate_solution_point(
        f, fixed, sample_rate_hz=50_000.0, switching_frequency_hz=switching_hz,
        target_crossover_hz=1000.0, target_phase_margin_deg=60.0,
        constraints=constraints,
    )


def _evaluate_prescribed_loop(f, loop):
    controller = PIControllerConfig(kp=1.0, ti_s=1e-3, sample_time_s=20e-6)
    fixed = loop / controller.transfer_function().frequency_response(f)
    return evaluate_fixed_controller_point(
        f, fixed, controller_cfg=controller, switching_frequency_hz=float(f[-1]),
        target_crossover_hz=1000.0, target_phase_margin_deg=60.0,
        constraints=SolutionMapConstraints(maximum_sensitivity=100.0,
                                           maximum_switching_loop_gain_db=None),
    )


def test_unobserved_required_gain_margin_is_conditional_never_infinite():
    point = _synthesized_point(delay_s=0.0)
    assert point.actual_phase_margin_deg == pytest.approx(60.0, abs=0.01)
    assert point.gain_margin_db is None
    assert point.status == SolutionStatus.CONDITIONAL
    assert not point.feasible
    assert "GM" in point.message and "UNKNOWN" in point.message


def test_no_positive_gm_requirement_still_discloses_missing_evidence():
    point = _synthesized_point(
        delay_s=0.0, constraints=SolutionMapConstraints(minimum_gain_margin_db=0.0),
    )
    assert point.gain_margin_db is None
    assert point.status == SolutionStatus.FEASIBLE
    assert "GM" in point.message and "UNKNOWN" in point.message
    assert "stability" in point.message


@pytest.mark.parametrize("switching_hz", [5.0, 30_000.0])
def test_required_switching_check_does_not_extrapolate_either_end(switching_hz):
    point = _synthesized_point(switching_hz=switching_hz)
    assert point.switching_loop_gain_db is None
    assert point.status == SolutionStatus.CONDITIONAL
    assert not point.feasible
    assert "UNKNOWN" in point.message


def test_explicitly_optional_switching_limit_does_not_erase_missing_evidence():
    point = _synthesized_point(
        switching_hz=30_000.0,
        constraints=SolutionMapConstraints(maximum_switching_loop_gain_db=None),
    )
    assert point.switching_loop_gain_db is None
    assert point.status == SolutionStatus.CONDITIONAL
    assert not point.feasible
    assert "UNKNOWN" in point.message


def test_no_observed_gain_crossover_is_unknown_not_stable():
    f = np.geomspace(10.0, 20_000.0, 100)
    point = _evaluate_prescribed_loop(f, np.full(f.shape, -0.1j))
    assert point.status == SolutionStatus.UNKNOWN
    assert point.actual_crossover_hz is None
    assert point.actual_phase_margin_deg is None
    assert not point.feasible


def test_negative_gain_margin_is_not_hidden_by_later_positive_margin():
    f = 100.0 * 2.0 ** np.arange(8)
    magnitude_db = np.array([20.0, 15.0, 10.0, 6.0, 2.0, -5.0, -10.0, -20.0])
    phase_deg = np.array([-90.0, -150.0, -210.0, -330.0, -450.0, -570.0, -690.0, -810.0])
    loop = 10.0 ** (magnitude_db / 20.0) * np.exp(1j * np.deg2rad(phase_deg))
    margins = calculate_stability_margins(f, loop)
    assert min(margins.gain_margins_db) < 0 < max(margins.gain_margins_db)
    point = _evaluate_prescribed_loop(f, loop)
    assert point.status == SolutionStatus.LOOP_UNSTABLE
    assert point.gain_margin_db == pytest.approx(min(margins.gain_margins_db))
    corner = build_robustness_report({"multi-crossing": (f, loop)}).corners[0]
    assert corner.gm_db == pytest.approx(min(margins.gain_margins_db))
    assert corner.constraint_status == MetricStatus.WARN


def test_multiple_gain_crossovers_use_worst_phase_margin():
    f = 100.0 * 2.0 ** np.arange(8)
    magnitude_db = np.array([10.0, 3.0, -2.0, -5.0, 3.0, 2.0, -2.0, -4.0])
    phase_deg = np.array([-90.0, -110.0, -120.0, -150.0, -200.0, -210.0, -240.0, -260.0])
    loop = 10.0 ** (magnitude_db / 20.0) * np.exp(1j * np.deg2rad(phase_deg))
    margins = calculate_stability_margins(f, loop)
    assert len(margins.gain_crossovers_hz) == 3
    assert min(margins.phase_margins_deg) < 0 < max(margins.phase_margins_deg)
    point = _evaluate_prescribed_loop(f, loop)
    assert point.status == SolutionStatus.LOOP_UNSTABLE
    assert point.actual_phase_margin_deg == pytest.approx(min(margins.phase_margins_deg))


def test_measured_frd_without_pole_evidence_never_fits_or_invents_poles(monkeypatch):
    f, loop = _fixed_response()

    def forbid_root_fitting(*args, **kwargs):
        raise AssertionError("FRD-only reporting must not invent polynomial poles")

    monkeypatch.setattr(np, "roots", forbid_root_fitting)
    report = build_robustness_report({"measured": (f, 3.0 * loop)})
    corner = report.corners[0]
    assert corner.pm_deg is not None
    assert corner.pole_stable is None
    assert corner.constraint_status == MetricStatus.UNKNOWN
    assert "POLE_STABILITY_UNKNOWN" in corner.tags
    assert corner.as_dict()["pole_stable"] is None


def test_partial_pole_evidence_does_not_default_missing_corner_to_true():
    f, loop = _fixed_response()
    report = build_robustness_report(
        {"known": (f, 3.0 * loop), "missing": (f, 3.0 * loop)},
        discrete_stable={"known": True},
    )
    assert report.corners[0].pole_stable is True
    assert report.corners[1].pole_stable is None
    assert report.corners[1].constraint_status == MetricStatus.UNKNOWN


def test_explicit_unstable_pole_evidence_overrides_positive_margins():
    f, loop = _fixed_response()
    corner = build_robustness_report(
        {"unstable-poles": (f, 3.0 * loop)}, discrete_stable={"unstable-poles": False},
    ).corners[0]
    assert corner.pm_deg > 0.0
    assert corner.pole_stable is False
    assert corner.constraint_status == MetricStatus.WARN


@pytest.mark.parametrize("value", [1, "stable", [], float("nan")])
def test_nonboolean_pole_evidence_is_rejected(value):
    f, loop = _fixed_response()
    with pytest.raises(ValueError):
        build_robustness_report({"case": (f, loop)}, discrete_stable={"case": value})


@pytest.mark.parametrize("name", ["kp", "ti_s", "sample_time_s"])
@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf")])
def test_fixed_controller_rejects_nonfinite_or_nonpositive_parameters(name, value):
    f, fixed = _fixed_response()
    config = {"kp": 1.0, "ti_s": 1e-3, "sample_time_s": 20e-6, name: value}
    with pytest.raises(ValueError):
        evaluate_fixed_controller_point(
            f, fixed, controller_cfg=PIControllerConfig(**config),
            switching_frequency_hz=20_000.0,
            target_crossover_hz=1000.0, target_phase_margin_deg=60.0,
        )


@pytest.mark.parametrize("frequency,response", [
    ([1.0, 1.0, 3.0], [1.0, 1.0, 1.0]),
    ([1.0, 3.0, 2.0], [1.0, 1.0, 1.0]),
    ([0.0, 1.0, 2.0], [1.0, 1.0, 1.0]),
    ([1.0, 2.0, float("nan")], [1.0, 1.0, 1.0]),
    ([1.0, 2.0, 3.0], [1.0, float("inf"), 1.0]),
    ([[1.0, 2.0, 3.0]], [[1.0, 1.0, 1.0]]),
    ([1.0, 2.0, 3.0], [1.0, 1.0]),
])
def test_invalid_frequency_response_is_rejected(frequency, response):
    with pytest.raises(ValueError):
        build_robustness_report({"invalid": (np.asarray(frequency), np.asarray(response))})


@pytest.mark.parametrize("upper_hz", [25_000.0, 30_000.0, 50_000.0])
def test_nyquist_or_aliased_frequency_grid_cannot_supply_switching_evidence(upper_hz):
    f = np.geomspace(10.0, upper_hz, 1600)
    fixed = 1.0 / (1.0 + 1j * f / 577.0)
    with pytest.raises(ValueError, match="Nyquist"):
        evaluate_fixed_controller_point(
            f, fixed, controller_cfg=PIControllerConfig(kp=1.0, ti_s=1e-3, sample_time_s=20e-6),
            switching_frequency_hz=upper_hz,
            target_crossover_hz=1000.0, target_phase_margin_deg=60.0,
        )


@pytest.mark.parametrize("target", [
    {"target_crossover_hz": 0.0}, {"target_crossover_hz": -1.0},
    {"target_crossover_hz": float("nan")}, {"target_crossover_hz": float("inf")},
    {"target_phase_margin_deg": 0.0}, {"target_phase_margin_deg": 180.0},
    {"target_phase_margin_deg": float("nan")},
])
def test_synthesis_rejects_invalid_fc_pm_before_interpolation(target):
    f, fixed = _fixed_response()
    kwargs = {"target_crossover_hz": 1000.0, "target_phase_margin_deg": 60.0, **target}
    with pytest.raises(ValueError, match="Fc/PM"):
        evaluate_solution_point(
            f, fixed, sample_rate_hz=50_000.0, switching_frequency_hz=20_000.0,
            **kwargs,
        )


def test_finite_positive_target_outside_observed_band_is_invalid_point():
    f, fixed = _fixed_response()
    point = evaluate_solution_point(
        f, fixed, sample_rate_hz=50_000.0, switching_frequency_hz=20_000.0,
        target_crossover_hz=5.0, target_phase_margin_deg=60.0,
    )
    assert point.status == SolutionStatus.INVALID_POINT
    assert point.kp is None and point.ti_s is None


def test_singular_sensitivity_is_warning_not_unknown_or_ignored():
    f = np.geomspace(10.0, 20_000.0, 100)
    loop = np.full(f.shape, -1.0 + 0.0j)
    corner = build_robustness_report({"singular": (f, loop)}).corners[0]
    assert corner.constraint_status == MetricStatus.WARN
    assert not np.isfinite(corner.ms)
