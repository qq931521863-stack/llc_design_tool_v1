from __future__ import annotations

import math

import numpy as np

from power_control_tools.fra import (
    FRAFitMetrics,
    FRAFitResult,
    RationalPlantModel,
    auto_design_controller,
    closed_loop_step_from_fitted_loop,
    fit_rational_frequency_response,
    validate_fitted_open_loop,
)
from power_control_tools.models import ControllerKind, DiscretizationMethod


def test_auto_design_pi_hits_requested_fc_pm_and_measured_gm_on_three_pole_plant():
    f = np.geomspace(10.0, 18_000.0, 700)
    s = 1j * 2.0 * np.pi * f
    plant = 2.0 / (
        (1.0 + s / (2.0 * np.pi * 300.0))
        * (1.0 + s / (2.0 * np.pi * 5_000.0))
        * (1.0 + s / (2.0 * np.pi * 10_000.0))
    )

    result = auto_design_controller(
        f,
        plant,
        controller_kind=ControllerKind.PI,
        sample_rate_hz=40_000.0,
        discretization_method=DiscretizationMethod.TUSTIN,
        target_crossover_hz=1_000.0,
        target_phase_margin_deg=60.0,
        min_gain_margin_db=6.0,
    )

    assert result.selected is not None
    assert result.status == "PASS"
    assert not result.fallback_used
    assert result.selected.accepted
    assert result.selected.achieved_crossover_hz is not None
    assert abs(math.log(result.selected.achieved_crossover_hz / 1000.0)) < math.log(1.05)
    assert result.selected.achieved_phase_margin_deg is not None
    assert result.selected.achieved_phase_margin_deg >= 57.0
    assert result.selected.metrics.worst_gain_margin_db is not None
    assert result.selected.metrics.worst_gain_margin_db >= 6.0


def test_auto_design_pi_reduces_fc_when_requested_point_needs_phase_lead():
    f = np.geomspace(10.0, 18_000.0, 750)
    s = 1j * 2.0 * np.pi * f
    plant = 5.0 / (
        (1.0 + s / (2.0 * np.pi * 200.0))
        * (1.0 + s / (2.0 * np.pi * 1_000.0))
        * (1.0 + s / (2.0 * np.pi * 8_000.0))
    )

    result = auto_design_controller(
        f,
        plant,
        controller_kind=ControllerKind.PI,
        sample_rate_hz=40_000.0,
        target_crossover_hz=5_000.0,
        target_phase_margin_deg=60.0,
        fallback_points=32,
    )

    assert result.selected is not None
    assert result.fallback_used
    assert result.selected.requested_crossover_hz < 5_000.0
    assert result.selected.achieved_phase_margin_deg is not None
    if result.status == "PASS":
        assert result.selected.accepted
        assert result.selected.achieved_phase_margin_deg >= 58.0
        assert result.selected.metrics.worst_gain_margin_db is not None
        assert result.selected.metrics.worst_gain_margin_db >= 6.0
    else:
        # REVIEW is acceptable only if the finite FRA window cannot prove every
        # robustness constraint. It must never be silently promoted to PASS.
        assert not result.selected.accepted


def test_auto_design_power_2p2z_uses_integrator_pole_and_can_supply_phase_lead():
    f = np.geomspace(20.0, 20_000.0, 700)
    s = 1j * 2.0 * np.pi * f
    plant = 1.5 / (
        (1.0 + s / (2.0 * np.pi * 350.0))
        * (1.0 + s / (2.0 * np.pi * 2_500.0))
        * (1.0 + s / (2.0 * np.pi * 12_000.0))
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
    selected = result.selected
    assert selected.controller_kind == ControllerKind.TWO_P_TWO_Z
    assert selected.achieved_crossover_hz is not None
    assert selected.achieved_phase_margin_deg is not None
    assert selected.achieved_phase_margin_deg >= 50.0
    assert selected.parameters["integrator_pole_hz"] == 0.0
    # Tustin maps the continuous integrator pole s=0 to z=1.
    assert min(abs(complex(p) - 1.0) for p in selected.controller.poles) < 1e-8


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
    assert result.metrics.magnitude_rms_db < 0.3
    assert result.metrics.phase_rms_deg < 1.5
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
    assert result.metrics.magnitude_rms_db < 0.7
    assert result.metrics.phase_rms_deg < 3.0
    assert len(result.model.complex_pole_pairs) >= 1


def test_tiny_fitted_delay_is_ignored_by_pade_time_domain_conversion():
    base = RationalPlantModel(
        numerator_coefficients=(2.0,),
        real_pole_hz=(500.0,),
        complex_pole_pairs=tuple(),
        reference_frequency_hz=500.0,
        delay_s=0.0,
    )
    tiny = RationalPlantModel(
        numerator_coefficients=(2.0,),
        real_pole_hz=(500.0,),
        complex_pole_pairs=tuple(),
        reference_frequency_hz=500.0,
        delay_s=1e-40,
    )
    n0, d0 = base.normalized_polynomials(pade_delay=True)
    n1, d1 = tiny.normalized_polynomials(pade_delay=True)
    assert np.allclose(n0, n1, rtol=0.0, atol=0.0)
    assert np.allclose(d0, d1, rtol=0.0, atol=0.0)


def test_fitted_open_loop_validation_accepts_exact_control_equivalent_model():
    f = np.geomspace(20.0, 20_000.0, 500)
    model = RationalPlantModel(
        numerator_coefficients=(5.0,),
        real_pole_hz=(500.0,),
        complex_pole_pairs=tuple(),
        reference_frequency_hz=500.0,
        delay_s=0.0,
    )
    measured = model.frequency_response(f)
    metrics = FRAFitMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "GOOD")
    fit = FRAFitResult(model, measured.copy(), metrics, 1, 1, "synthetic open loop", "exact")
    validation = validate_fitted_open_loop(f, measured, fit)
    assert validation.passed
    assert validation.crossover_count_match
    assert validation.crossover_error_percent is not None and validation.crossover_error_percent < 1e-9
    assert validation.phase_margin_error_deg is not None and validation.phase_margin_error_deg < 1e-9
    assert validation.fitted_closed_loop_stable


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
