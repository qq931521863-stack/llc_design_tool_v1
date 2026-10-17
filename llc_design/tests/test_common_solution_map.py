"""Common-controller regressions: one exact PI, genuine per-corner responses."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from llc_design.control import solution_map
from llc_design.control.smart_control import (
    FixedLoopCorner,
    build_common_fc_pm_solution_map,
    evaluate_common_solution_point,
)
from llc_design.control.solution_map import SolutionMapConstraints, SolutionStatus


def _corner(
    name: str,
    *,
    gain: float = 1.0,
    delay_s: float = 20e-6,
    grid: np.ndarray | None = None,
    switching_hz: float = 20_000.0,
) -> FixedLoopCorner:
    f = np.geomspace(10.0, 20_000.0, 1600) if grid is None else grid
    pole_hz = 1000.0 / np.tan(np.deg2rad(60.0))
    fixed = gain / (1.0 + 1j * f / pole_hz) * np.exp(-2j * np.pi * f * delay_s)
    return FixedLoopCorner(name, f, fixed, 50_000.0, switching_hz, "measured fixed-path FRD")


def _evaluate(corners, **kwargs):
    return evaluate_common_solution_point(
        corners, target_crossover_hz=1000.0, target_phase_margin_deg=60.0, **kwargs
    )


def test_common_point_synthesizes_once_and_reuses_identical_pi(monkeypatch):
    calls = []
    synthesize = solution_map.synthesize_tustin_pi_at_target

    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        return synthesize(*args, **kwargs)

    monkeypatch.setattr(solution_map, "synthesize_tustin_pi_at_target", counted)
    point = _evaluate([_corner("nominal"), _corner("low-gain", gain=0.8)])

    assert len(calls) == 1
    assert point.status == SolutionStatus.FEASIBLE
    assert point.corner_ids == ("nominal", "low-gain")
    assert len(point.corner_results) == 2
    assert {(item.kp, item.ti_s) for item in point.corner_results} == {(point.kp, point.ti_s)}
    nominal, low_gain = point.corner_results
    assert nominal.actual_crossover_hz == pytest.approx(1000.0, rel=0.001)
    # Corner bandwidth is allowed to move; it must not be resynthesized to Fc.
    assert low_gain.actual_crossover_hz < 0.9 * nominal.actual_crossover_hz
    assert low_gain.actual_phase_margin_deg >= 57.0


def test_common_point_preserves_each_corner_grid_and_switching_frequency():
    coarse_f = np.geomspace(30.0, 18_000.0, 601)
    nominal = _corner("nominal")
    other = _corner("different-grid", grid=coarse_f, gain=0.8, switching_hz=15_000.0)
    point = _evaluate([nominal, other])
    assert point.status == SolutionStatus.FEASIBLE
    cfg = solution_map.PIControllerConfig(point.kp, point.ti_s, 1 / 50_000.0)
    direct = solution_map.evaluate_fixed_controller_point(
        other.frequencies_hz,
        other.fixed_loop_response,
        controller_cfg=cfg,
        switching_frequency_hz=other.switching_frequency_hz,
        target_crossover_hz=1000.0,
        target_phase_margin_deg=60.0,
        match_nominal_target=False,
    )
    assert point.corner_results[1] == direct
    assert point.corner_results[0].switching_loop_gain_db != direct.switching_loop_gain_db


def test_known_corner_failure_blocks_common_candidate():
    point = _evaluate([_corner("nominal"), _corner("long-delay", delay_s=100e-6)])
    assert point.corner_results[0].feasible
    assert point.corner_results[1].status == SolutionStatus.MS_CONSTRAINT_FAIL
    assert point.status == SolutionStatus.MS_CONSTRAINT_FAIL
    assert not point.feasible
    assert "long-delay" in point.message


def test_nonnominal_pm_is_a_floor_even_when_crossover_moves():
    permissive = SolutionMapConstraints(maximum_sensitivity=100.0)
    point = _evaluate(
        [_corner("nominal"), _corner("delay-shift", delay_s=60e-6)],
        constraints=permissive,
    )
    assert point.corner_results[0].feasible
    assert point.corner_results[1].actual_phase_margin_deg < 57.0
    assert point.corner_results[1].status == SolutionStatus.TARGET_MISMATCH
    assert not point.feasible


def test_missing_corner_switching_evidence_is_conditional_and_not_feasible():
    point = _evaluate([_corner("nominal"), _corner("missing-fsw", switching_hz=30_000.0)])
    assert point.corner_results[0].feasible
    assert point.corner_results[1].status == SolutionStatus.CONDITIONAL
    assert point.corner_results[1].switching_loop_gain_db is None
    assert point.status == SolutionStatus.CONDITIONAL
    assert not point.feasible
    assert "missing-fsw" in point.message
    assert "UNKNOWN" in point.message


def test_optional_missing_corner_switching_evidence_remains_visible():
    point = _evaluate(
        [_corner("nominal"), _corner("missing-fsw", switching_hz=30_000.0)],
        constraints=SolutionMapConstraints(maximum_switching_loop_gain_db=None),
    )
    assert point.status == SolutionStatus.CONDITIONAL
    assert not point.feasible
    assert point.corner_results[1].switching_loop_gain_db is None
    assert "missing-fsw" in point.message
    assert "UNKNOWN" in point.message


def test_known_failure_takes_precedence_over_another_corner_unknown():
    point = _evaluate([
        _corner("nominal", switching_hz=30_000.0),
        _corner("known-bad-delay", delay_s=100e-6),
    ])
    assert point.corner_results[0].status == SolutionStatus.CONDITIONAL
    assert point.status == SolutionStatus.MS_CONSTRAINT_FAIL
    assert "known-bad-delay" in point.message


def test_common_map_matches_pointwise_results_and_preserves_axis_order():
    corners = [_corner("nominal"), _corner("low-gain", gain=0.8)]
    fc = [700.0, 1000.0]
    pm = [55.0, 60.0]
    result = build_common_fc_pm_solution_map(
        corners, crossover_targets_hz=fc, phase_margin_targets_deg=pm,
    )
    assert result.status_codes.shape == (2, 2)
    assert np.array_equal(result.feasible_mask, result.status_codes == int(SolutionStatus.FEASIBLE))
    for iy, target_pm in enumerate(pm):
        for ix, target_fc in enumerate(fc):
            direct = evaluate_common_solution_point(
                corners, target_crossover_hz=target_fc, target_phase_margin_deg=target_pm,
            )
            assert result.point(iy, ix) == direct


@pytest.mark.parametrize("alteration", [
    {"sample_rate_hz": 40_000.0},
    {"sample_rate_hz": float("nan")},
    {"switching_frequency_hz": 0.0},
    {"switching_frequency_hz": float("inf")},
    {"corner_id": "nominal"},
    {"corner_id": " "},
    {"frequencies_hz": np.array([1.0, 2.0])},
    {"fixed_loop_response": np.array([complex("nan")])},
])
def test_common_corner_validation_rejects_incompatible_or_invalid_input(alteration):
    with pytest.raises(ValueError):
        _evaluate([_corner("nominal"), replace(_corner("other"), **alteration)])


def test_common_point_requires_at_least_one_corner():
    with pytest.raises(ValueError):
        _evaluate([])


@pytest.mark.parametrize("fc, pm", [
    ([], [60.0]), ([1000.0], []), ([1000.0, 500.0], [60.0]),
    ([1000.0], [60.0, 60.0]), ([float("nan")], [60.0]),
    ([1000.0], [180.0]), ([0.0], [60.0]), ([[1000.0]], [60.0]),
])
def test_common_map_rejects_invalid_grids(fc, pm):
    with pytest.raises(ValueError):
        build_common_fc_pm_solution_map(
            [_corner("nominal")], crossover_targets_hz=fc, phase_margin_targets_deg=pm,
        )


def test_common_missing_crossover_overrides_conditional_evidence():
    point = _evaluate([
        _corner("nominal", switching_hz=30_000.0),
        _corner("no-crossover", gain=1e-10),
    ])
    assert point.corner_results[0].status == SolutionStatus.CONDITIONAL
    assert point.corner_results[1].status == SolutionStatus.UNKNOWN
    assert point.status == SolutionStatus.UNKNOWN
    assert not point.feasible


def test_conditional_map_cells_are_excluded_from_feasible_mask():
    result = build_common_fc_pm_solution_map(
        [_corner("nominal", switching_hz=30_000.0)],
        crossover_targets_hz=[1000.0], phase_margin_targets_deg=[60.0],
    )
    assert result.point(0, 0).status == SolutionStatus.CONDITIONAL
    assert not result.feasible_mask.any()
    assert result.feasible_fraction == 0.0
