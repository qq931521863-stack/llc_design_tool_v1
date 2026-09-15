from __future__ import annotations

import math

import numpy as np

from power_control_tools.fra import (
    RationalPlantModel,
    auto_design_controller,
    closed_loop_step_from_fitted_loop,
    fit_rational_frequency_response,
)
from power_control_tools.models import ControllerKind, DiscretizationMethod


def test_auto_design_pi_hits_requested_fc_and_pm_on_simple_plant():
    f = np.geomspace(10.0, 15_000.0, 600)
    s = 1j * 2.0 * np.pi * f
    plant = 2.0 / (1.0 + s / (2.0 * np.pi * 300.0))

    result = auto_design_controller(
        f,
        plant,
        controller_kind=ControllerKind.PI,
        sample_rate_hz=40_000.0,
        discretization_method=DiscretizationMethod.TUSTIN,
        target_crossover_hz=1_000.0,
        target_phase_margin_deg=60.0,
    )

    assert result.selected is not None
    assert result.status == "PASS"
    assert not result.fallback_used
    assert result.selected.achieved_crossover_hz is not None
    assert abs(math.log(result.selected.achieved_crossover_hz / 1000.0)) < math.log(1.05)
    assert result.selected.achieved_phase_margin_deg is not None
    assert result.selected.achieved_phase_margin_deg >= 57.0


def test_auto_design_pi_reduces_fc_when_requested_point_needs_phase_lead():
    f = np.geomspace(10.0, 15_000.0, 700)
    s = 1j * 2.0 * np.pi * f
    plant = 5.0 / (
        (1.0 + s / (2.0 * np.pi * 200.0))
        * (1.0 + s / (2.0 * np.pi * 1_000.0))
    )

    result = auto_design_controller(
        f,
        plant,
        controller_kind=ControllerKind.PI,
        sample_rate_hz=40_000.0,
        target_crossover_hz=5_000.0,
        target_phase_margin_deg=60.0,
        fallback_points=28,
    )

    assert result.selected is not None
    assert result.fallback_used
    assert result.selected.requested_crossover_hz < 5_000.0
    assert result.selected.achieved_phase_margin_deg is not None
    assert result.selected.achieved_phase_margin_deg >= 55.0


def test_auto_design_2p2z_can_supply_phase_lead_at_target():
    f = np.geomspace(20.0, 18_000.0, 600)
    s = 1j * 2.0 * np.pi * f
    plant = 1.5 / (
        (1.0 + s / (2.0 * np.pi * 350.0))
        * (1.0 + s / (2.0 * np.pi * 2_500.0))
    )

    result = auto_design_controller(
        f,
        plant,
        controller_kind=ControllerKind.TWO_P_TWO_Z,
        sample_rate_hz=50_000.0,
        target_crossover_hz=3_000.0,
        target_phase_margin_deg=60.0,
    )

    assert result.selected is not None
    assert result.selected.controller_kind == ControllerKind.TWO_P_TWO_Z
    assert result.selected.achieved_crossover_hz is not None
    assert result.selected.achieved_phase_margin_deg is not None
    assert result.selected.achieved_phase_margin_deg >= 50.0


def test_rational_fit_recovers_two_real_poles_zero_and_delay():
    f = np.geomspace(10.0, 100_000.0, 500)
    s = 1j * 2.0 * np.pi * f
    measured = (
        3.0
        * (1.0 + s / (2.0 * np.pi * 1_000.0))
        / (
            (1.0 + s / (2.0 * np.pi * 200.0))
            * (1.0 + s / (2.0 * np.pi * 5_000.0))
        )
        * np.exp(-s * 8e-6)
    )

    result = fit_rational_frequency_response(
        f,
        measured,
        max_poles=5,
        focus_hz=1_000.0,
        fit_delay=True,
        max_nfev=180,
    )

    assert result.metrics.confidence == "GOOD"
    assert result.selected_order <= 3
    assert result.metrics.magnitude_rms_db < 0.2
    assert result.metrics.phase_rms_deg < 1.0
    assert abs(result.model.delay_s - 8e-6) < 3e-6


def test_rational_fit_uses_complex_pair_for_resonant_response():
    f = np.geomspace(20.0, 50_000.0, 450)
    s = 1j * 2.0 * np.pi * f
    wn = 2.0 * np.pi * 2_000.0
    zeta = 0.20
    measured = 2.0 / ((s / wn) ** 2 + 2.0 * zeta * (s / wn) + 1.0) * np.exp(-s * 5e-6)

    result = fit_rational_frequency_response(
        f,
        measured,
        min_poles=2,
        max_poles=4,
        focus_hz=2_000.0,
        fit_delay=True,
        max_nfev=220,
    )

    assert result.metrics.confidence == "GOOD"
    assert result.metrics.magnitude_rms_db < 0.5
    assert result.metrics.phase_rms_deg < 2.0
    assert len(result.model.complex_pole_pairs) >= 1


def test_closed_loop_step_from_fitted_open_loop_is_stable_and_finite():
    model = RationalPlantModel(
        numerator_coefficients=(5.0,),
        real_pole_hz=(500.0,),
        complex_pole_pairs=tuple(),
        reference_frequency_hz=500.0,
        delay_s=0.0,
    )
    step = closed_loop_step_from_fitted_loop(model)
    assert step.stable
    assert len(step.time_s) == len(step.response)
    assert len(step.time_s) >= 200
    assert np.all(np.isfinite(step.response))
    assert abs(step.final_value - 5.0 / 6.0) < 0.02
