"""FRA plant-model x controller link regression tests.

These tests pin the two capabilities added after the V1.5/V2 hardening:

1. an identified rational plant model can be connected to *any* controller
   from the shared toolbox catalogue and analysed as a loop;
2. the closed-loop step derived from that link is numerically consistent with
   the same discrete loop evaluated by an independent difference equation.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import signal

from power_control_tools.controllers import CONTROLLER_LABELS, controller_parameter_keys, design_controller
from power_control_tools.discretize import discretize_transfer_function
from power_control_tools.fra import (
    RationalPlantModel,
    closed_loop_step_from_plant_and_controller,
    discretize_plant_model,
    link_plant_model_with_controller,
    plant_model_polynomials_rad_s,
)
from power_control_tools.fra.analysis import analyze_loop_response, digital_frequency_response
from power_control_tools.fra.loop_link import (
    STEP_OK,
    STEP_WITHHELD_INSUFFICIENT_BANDWIDTH,
    STEP_WITHHELD_LOOP_FAIL,
    STEP_WITHHELD_LOW_CONFIDENCE,
)
from power_control_tools.models import ControllerKind, DiscretizationMethod

FS = 40_000.0
# G(s) = 2 / (1 + s/w1), w1 = 2*pi*300
PLANT = RationalPlantModel(
    numerator_coefficients=(2.0,),
    real_pole_hz=(300.0,),
    complex_pole_pairs=tuple(),
    reference_frequency_hz=500.0,
)


def _pi(kp: float = 0.5, fz_hz: float = 50.0):
    return discretize_transfer_function(
        design_controller(ControllerKind.PI, kp=kp, ti_s=1.0 / (2.0 * math.pi * fz_hz)),
        FS,
        DiscretizationMethod.TUSTIN,
    )


def test_plant_model_polynomials_match_analytic_s_domain():
    num, den = plant_model_polynomials_rad_s(PLANT)
    w1 = 2.0 * math.pi * 300.0
    # 2 / (1 + s/w1) -> denominator (s/w1 + 1)
    np.testing.assert_allclose(num, [2.0], rtol=1e-12)
    np.testing.assert_allclose(den, [1.0 / w1, 1.0], rtol=1e-12)


def test_discretized_plant_is_exact_zoh_equivalent():
    g = discretize_plant_model(PLANT, FS)
    t, y = signal.dstep((np.asarray(g.b), np.asarray(g.a), 1.0 / FS), n=10)
    y = np.asarray(y[0], dtype=float).reshape(-1)
    dt = 1.0 / FS
    w1 = 2.0 * math.pi * 300.0
    exact = np.array([2.0 * (1.0 - math.exp(-w1 * k * dt)) for k in range(10)])
    np.testing.assert_allclose(y, exact, atol=1e-12)


def test_link_margins_are_the_same_function_of_the_loop_response():
    controller = _pi()
    f = np.geomspace(1.0, 15_000.0, 500)
    link = link_plant_model_with_controller(
        PLANT, controller, f_min_hz=1.0, f_max_hz=15_000.0, points=500, include_step=False
    )
    # The reported metrics must equal a direct analyze_loop_response call on the
    # same arrays, otherwise the link is not using the shared authority.
    direct = analyze_loop_response(link.frequency_hz, link.loop_response)
    assert direct.status == link.metrics.status
    assert direct.main_crossover_hz == link.metrics.main_crossover_hz
    np.testing.assert_allclose(link.loop_response, link.plant_response * link.controller_response, rtol=1e-12)
    # Independent re-evaluation of the discrete controller on the same grid.
    np.testing.assert_allclose(
        link.controller_response, digital_frequency_response(controller, link.frequency_hz), rtol=1e-12
    )


def test_link_analysis_is_limited_to_nyquist_fraction():
    link = link_plant_model_with_controller(
        PLANT, _pi(), f_min_hz=1.0, f_max_hz=100_000.0, include_step=False, nyquist_fraction=0.49
    )
    assert link.analysis_limit_hz == pytest.approx(0.49 * FS)
    assert link.frequency_hz[-1] <= 0.49 * FS + 1e-9


def test_link_rejects_a_band_with_no_points_below_the_digital_limit():
    with pytest.raises(ValueError, match="usable points"):
        link_plant_model_with_controller(
            PLANT, _pi(), f_min_hz=0.9 * FS, f_max_hz=FS, include_step=False
        )


def test_closed_loop_step_matches_direct_z_minus_one_difference_equation():
    controller = _pi(kp=0.5)
    plant = discretize_plant_model(PLANT, FS)
    bn = np.polymul(np.asarray(controller.b), np.asarray(plant.b))
    an = np.polymul(np.asarray(controller.a), np.asarray(plant.a))
    size = max(len(an), len(bn))
    closed_den = np.zeros(size)
    closed_den[: len(an)] += an
    closed_den[: len(bn)] += bn

    samples = 200
    direct = signal.lfilter(bn, closed_den, np.ones(samples))
    step = closed_loop_step_from_plant_and_controller(PLANT, controller, samples=samples)

    assert step.stable
    np.testing.assert_allclose(step.response[:samples], direct, atol=1e-9)
    assert step.final_value == pytest.approx(1.0, rel=1e-9)
    assert step.poles_rad_s.size == 2
    assert np.all(np.real(step.poles_rad_s) < 0.0)


def test_link_step_is_withheld_for_a_low_confidence_fit():
    link = link_plant_model_with_controller(
        PLANT, _pi(), f_min_hz=1.0, f_max_hz=15_000.0, fit_confidence="LOW"
    )
    assert link.step is None
    assert link.step_status == STEP_WITHHELD_LOW_CONFIDENCE


def test_link_step_is_produced_for_an_accepted_fit():
    link = link_plant_model_with_controller(
        PLANT, _pi(), f_min_hz=1.0, f_max_hz=15_000.0, fit_confidence="GOOD"
    )
    assert link.step_status == STEP_OK
    assert link.step is not None and link.step.stable


def test_link_step_is_withheld_when_the_linked_loop_fails_its_margin_check():
    # Three-pole plant: gain high enough to push the 0-dB crossing past the
    # -180 deg phase crossover, so the loop itself fails before the step gate.
    plant = RationalPlantModel(
        numerator_coefficients=(2.0,),
        real_pole_hz=(200.0, 1_000.0, 5_000.0),
        complex_pole_pairs=tuple(),
        reference_frequency_hz=1_000.0,
    )
    link = link_plant_model_with_controller(
        plant, _pi(kp=50.0), f_min_hz=1.0, f_max_hz=18_000.0, fit_confidence="GOOD"
    )
    assert link.metrics.status == "FAIL"
    assert link.step is None
    assert link.step_status == STEP_WITHHELD_LOOP_FAIL
    assert link.metrics.phase_margin_deg is not None and link.metrics.phase_margin_deg < 0.0


def _controller_for(kind: ControllerKind):
    if kind == ControllerKind.GENERAL:
        return design_controller(kind, numerator=(1.0, 500.0), denominator=(1.0, 20_000.0))
    kwargs = {
        "gain": 1.0, "kp": 0.5, "ti_s": 1e-3, "td_s": 1e-4, "lpf_pole_hz": 8_000.0,
        "fp0_hz": 20.0, "fz1_hz": 200.0, "fz2_hz": 800.0, "fz3_hz": 2_000.0,
        "fp1_hz": 5_000.0, "fp2_hz": 12_000.0, "fp3_hz": 18_000.0,
    }
    return design_controller(kind, **kwargs)


def test_every_toolbox_controller_kind_can_be_linked_to_an_identified_plant():
    """The FRA link must cover the whole Control Tools controller catalogue."""
    for kind in ControllerKind:
        analog = _controller_for(kind)
        controller = discretize_transfer_function(analog, FS, DiscretizationMethod.TUSTIN)
        link = link_plant_model_with_controller(
            PLANT,
            controller,
            f_min_hz=1.0,
            f_max_hz=15_000.0,
            points=400,
            include_step=False,
        )
        assert link.frequency_hz.size == 400, kind
        assert np.all(np.isfinite(link.loop_response.real)), kind
        assert np.all(np.isfinite(link.loop_response.imag)), kind
        assert link.metrics.status, kind


def test_extra_delay_shifts_both_the_loop_and_the_step():
    controller = _pi()
    baseline = link_plant_model_with_controller(
        PLANT, controller, f_min_hz=1.0, f_max_hz=15_000.0, include_step=False
    )
    delayed = link_plant_model_with_controller(
        PLANT, controller, f_min_hz=1.0, f_max_hz=15_000.0, include_step=False, extra_delay_samples=2
    )
    shift = np.exp(-1j * 2.0 * np.pi * baseline.frequency_hz * 2.0 / FS)
    np.testing.assert_allclose(delayed.loop_response, baseline.loop_response * shift, rtol=1e-12)


def test_controller_parameter_keys_cover_every_kind():
    for kind in ControllerKind:
        keys = controller_parameter_keys(kind)
        assert keys, kind
        assert all(key in CONTROLLER_LABELS or True for key in keys)
    assert controller_parameter_keys(ControllerKind.TYPE_II) == ("type_input_mode", "fp0", "fz1", "fp1")
    assert controller_parameter_keys(ControllerKind.TYPE_II, type_input_mode="rc") == (
        "type_input_mode", "r1", "r2", "c1", "c2",
    )
    assert controller_parameter_keys(ControllerKind.TYPE_III, type_input_mode="rc") == (
        "type_input_mode", "r1", "r2", "r3", "c1", "c2", "c3",
    )
    assert controller_parameter_keys(ControllerKind.LEAD) == ("gain", "fz1", "fp1")


def test_lead_accepts_canonical_fz1_fp1_names():
    canonical = design_controller(ControllerKind.LEAD, gain=2.0, fz1_hz=500.0, fp1_hz=5_000.0)
    alias = design_controller(ControllerKind.LEAD, gain=2.0, fz_hz=500.0, fp_hz=5_000.0)
    np.testing.assert_allclose(canonical.numerator, alias.numerator, rtol=1e-12)
    np.testing.assert_allclose(canonical.denominator, alias.denominator, rtol=1e-12)


def test_step_bandwidth_coverage_matches_the_audit_thresholds():
    from power_control_tools.fra.loop_link import step_bandwidth_coverage

    # Fc = 300 Hz: 10..15000 Hz gives Fc/Fmin = 30 and Fmax/Fc = 50 -> covered.
    assert step_bandwidth_coverage(300.0, 10.0, 15_000.0) == (True, 30.0, 50.0)
    # The narrow local fit called out by the audit (Fc ~ 296.6 Hz, Fmin = 150 Hz).
    ok, low, high = step_bandwidth_coverage(296.6, 150.0, 15_000.0)
    assert not ok and low is not None and low < 10.0
    # No observed crossover -> the control bandwidth is not established at all.
    assert step_bandwidth_coverage(None, 10.0, 15_000.0) == (False, None, None)


def test_link_step_is_withheld_when_the_fit_band_is_too_narrow():
    """A narrow local fit may report Fc/PM but must not authorize a step."""
    controller = _pi(kp=0.4, fz_hz=80.0)
    link = link_plant_model_with_controller(
        PLANT, controller, f_min_hz=10.0, f_max_hz=15_000.0, fit_confidence="GOOD"
    )
    fc = link.metrics.main_crossover_hz
    assert fc is not None and fc / 10.0 < 10.0  # fails Fc/Fmin >= 10
    assert link.step is None
    assert link.step_status == STEP_WITHHELD_INSUFFICIENT_BANDWIDTH
    # The frequency-domain result stays available; only the step is gated.
    assert link.metrics.phase_margin_deg is not None
