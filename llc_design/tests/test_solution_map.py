from __future__ import annotations

import numpy as np
import pytest

from llc_design.control.solution_map import (
    SolutionMapConstraints,
    SolutionStatus,
    build_fc_pm_solution_map,
    evaluate_solution_point,
    synthesize_tustin_pi_at_target,
)


def _first_order_fixed_loop(frequencies_hz: np.ndarray, pole_hz: float) -> np.ndarray:
    s = 1j * frequencies_hz / pole_hz
    return 1.0 / (1.0 + s)


def test_tustin_pi_synthesis_hits_requested_complex_loop_point() -> None:
    fs = 50_000.0
    fc = 1_000.0
    pm = 60.0
    # pole=fc/tan(60°) -> plant phase is -60° at fc, therefore PI must add -60°
    pole = fc / np.tan(np.deg2rad(60.0))
    fixed_at_fc = 1.0 / (1.0 + 1j * fc / pole)
    result = synthesize_tustin_pi_at_target(
        fixed_at_fc,
        target_crossover_hz=fc,
        target_phase_margin_deg=pm,
        sample_rate_hz=fs,
    )
    assert result is not None
    controller, controller_phase = result
    response = controller.transfer_function().frequency_response(np.asarray([fc]))[0]
    loop = fixed_at_fc * response
    assert abs(loop) == pytest.approx(1.0, rel=2e-10)
    phase = np.angle(loop, deg=True)
    assert phase == pytest.approx(-120.0, abs=1e-8)
    assert controller_phase == pytest.approx(-60.0, abs=1e-8)
    assert controller.kp > 0.0 and controller.ti_s > 0.0


def test_solution_point_reports_unreachable_phase_instead_of_inventing_controller() -> None:
    fs = 50_000.0
    fc = 1_000.0
    fixed = np.exp(1j * np.deg2rad(-170.0))
    point = synthesize_tustin_pi_at_target(
        fixed,
        target_crossover_hz=fc,
        target_phase_margin_deg=60.0,
        sample_rate_hz=fs,
    )
    assert point is None


def test_fc_pm_map_uses_full_fixed_loop_and_finds_feasible_region() -> None:
    f = np.geomspace(10.0, 20_000.0, 1600)
    pole = 1_000.0 / np.tan(np.deg2rad(60.0))
    fixed = _first_order_fixed_loop(f, pole)
    result = build_fc_pm_solution_map(
        f,
        fixed,
        sample_rate_hz=50_000.0,
        switching_frequency_hz=20_000.0,
        crossover_targets_hz=np.asarray([700.0, 1_000.0, 1_400.0]),
        phase_margin_targets_deg=np.asarray([50.0, 60.0, 70.0]),
        constraints=SolutionMapConstraints(
            minimum_gain_margin_db=0.0,
            maximum_sensitivity=3.0,
            maximum_switching_loop_gain_db=0.0,
            crossover_tolerance_fraction=0.08,
            phase_margin_tolerance_deg=5.0,
        ),
        loop_label="synthetic full loop",
    )
    assert result.status_codes.shape == (3, 3)
    assert result.feasible_mask.any()
    centre = result.point(1, 1)
    assert centre.status == SolutionStatus.FEASIBLE
    assert centre.actual_crossover_hz == pytest.approx(1_000.0, rel=0.02)
    assert centre.actual_phase_margin_deg == pytest.approx(60.0, abs=1.0)
    assert centre.ms is not None and centre.mt is not None


def test_solution_map_constraint_failure_is_explicit() -> None:
    f = np.geomspace(10.0, 20_000.0, 1600)
    fc = 1_000.0
    pole = fc / np.tan(np.deg2rad(60.0))
    fixed = _first_order_fixed_loop(f, pole)
    point = evaluate_solution_point(
        f,
        fixed,
        sample_rate_hz=50_000.0,
        switching_frequency_hz=20_000.0,
        target_crossover_hz=fc,
        target_phase_margin_deg=60.0,
        constraints=SolutionMapConstraints(
            minimum_gain_margin_db=0.0,
            maximum_sensitivity=1.00001,
            maximum_switching_loop_gain_db=100.0,
            crossover_tolerance_fraction=0.08,
            phase_margin_tolerance_deg=5.0,
        ),
    )
    assert point.status == SolutionStatus.MS_CONSTRAINT_FAIL
    assert point.kp is not None and point.ti_s is not None
