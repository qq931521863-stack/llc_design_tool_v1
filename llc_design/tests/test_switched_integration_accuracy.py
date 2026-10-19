"""Switched LLC orbits must solve the circuit, not just a discrete RK map."""
from functools import cache
from math import gcd
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from llc_design.analysis.fha_td_validation import ModelValidity, validate_fha_td_point
from llc_design.analysis.time_domain import TimeDomainConfig, solve_time_domain
from llc_design.analysis.types import LLCAnalysisRequest
from llc_design.core.engineering import ConstraintStatus
from llc_design.core.spec import LLCDesignSpec, PrimaryTopology


@cache
def _solution(topology, samples):
    spec = LLCDesignSpec(
        primary_topology=topology,
        primary_turns=15 if topology == PrimaryTopology.HALF_BRIDGE else 30,
        ln_ratio=5.5, vbus_hold_end_v=320., bus_capacitance_f=.0033,
    )
    return solve_time_domain(
        LLCAnalysisRequest(spec, frequency_hz=99_632.20807785493),
        TimeDomainConfig(samples_per_cycle=samples, output_cycles=2),
    )


def _independent_cycle(parameters, frequency, initial, samples, sample_phase=0.):
    """Independent explicit DOP853, direct KVL/KCL, and integral balances.

    No production RHS, bridge function, integrator, or shooting map is used.
    The four auxiliary states integrate transformer volt-seconds, Co charge,
    input energy and dissipated/delivered energy rather than summing samples.
    """
    p = parameters
    dead = p.primary_deadtime_s * frequency
    edges = (0., dead / 2, .5 - dead / 2, .5 + dead / 2, 1 - dead / 2, 1.)
    levels = (0., p.bridge_gain * 400., 0., -p.bridge_gain * 400., 0.)
    values = np.empty((samples, 4))
    times = (np.arange(samples) + sample_phase) / samples
    extrema = [initial[2]]
    state = np.concatenate((initial, np.zeros(4)))
    for start, stop, bridge in zip(edges[:-1], edges[1:], levels):
        def equations(_time, x, bridge=bridge):
            ir, vcr, im, vco = x[:4]
            rectified = p.turns_ratio * abs(ir - im)
            vo = (vco + p.output_cap_esr_ohm * rectified) / (
                1 + p.output_cap_esr_ohm / p.load_resistance_ohm)
            vp = p.turns_ratio * (vo + p.rectifier_equivalent_drop_v) * np.tanh((ir - im) / .02)
            ico = rectified - vo / p.load_resistance_ohm
            flux_voltage = vp - p.magnetizing_series_resistance_ohm * im
            # Includes the explicitly smoothed rectifier's actual power, so
            # an ideal hard-clamp power assumption cannot conceal an error.
            delivered = vp * (ir - im) + p.series_resistance_ohm * ir**2
            delivered += p.magnetizing_series_resistance_ohm * im**2 - vco * ico
            return np.array([
                (bridge - vcr - vp - p.series_resistance_ohm * ir) / p.lr_h,
                ir / p.cr_f, flux_voltage / p.lm_h, ico / p.output_capacitance_f,
                flux_voltage, ico, bridge * ir, delivered,
            ]) / frequency

        # All these cases have zero magnetizing series loss, so dIm/dt=0
        # exactly when Ir=Im. This event uses no production derivative.
        sol = solve_ivp(equations, (start, stop), state, method="DOP853",
                        rtol=2e-11, atol=1e-12, max_step=1 / 128, dense_output=True,
                        events=lambda _time, x: x[0] - x[2])
        assert sol.success
        mask = (times >= start) & (times < stop)
        values[mask] = sol.sol(times[mask])[:4].T
        state = sol.y[:, -1]
        extrema.append(state[2])
        extrema.extend(event[2] for event in sol.y_events[0])
    return state, values, float(np.max(np.abs(extrema)))


@pytest.mark.parametrize("topology", list(PrimaryTopology))
def test_orbit_matches_independent_circuit_and_integral_balances(topology):
    result = _solution(topology, 512)
    assert result.convergence.converged
    assert result.convergence.residual_norm < 1e-7
    native = result.native_result
    keys = ("i_resonant", "v_resonant_cap", "i_magnetizing", "v_output_cap_internal")
    states = np.stack([result.waveform.signal(key).values for key in keys], axis=-1)
    scales = np.array([40., 400., 20., 53.])
    end, independent, peak = _independent_cycle(native.parameters, result.waveform.switching_frequency_hz,
                                         states[0], 512)
    np.testing.assert_allclose(states[:512] / scales, independent / scales, atol=2e-8, rtol=0)
    np.testing.assert_allclose(end[:4] / scales, states[0] / scales, atol=2e-8, rtol=0)
    np.testing.assert_allclose(states[:512] / scales, states[512:] / scales, atol=2e-8, rtol=0)
    symmetry = np.array([-1., -1., -1., 1.])
    np.testing.assert_allclose(states[:256] / scales, states[256:512] * symmetry / scales,
                               atol=2e-8, rtol=0)
    assert result.metrics.magnetizing_current_peak_a == pytest.approx(peak, abs=2e-7)
    assert abs(np.mean(states[:, 2])) < 1e-7  # no fictitious magnetizing DC bias
    assert abs(end[4]) < 2e-11  # flux balance in volt-seconds
    assert abs(end[5]) < 2e-11  # output capacitor charge in coulombs
    assert abs((end[6] - end[7]) * result.waveform.switching_frequency_hz) < 1e-4


@pytest.mark.parametrize("samples", [128, 192, 256, 1024])
def test_recording_resolution_does_not_select_a_different_orbit(samples):
    reference = _solution(PrimaryTopology.FULL_BRIDGE, 512)
    candidate = _solution(PrimaryTopology.FULL_BRIDGE, samples)
    assert candidate.convergence.converged
    for key in ("i_resonant", "v_resonant_cap", "i_magnetizing", "v_output_cap_internal"):
        common = gcd(samples, 512)
        np.testing.assert_allclose(candidate.waveform.signal(key).values[::samples // common],
            reference.waveform.signal(key).values[::512 // common], rtol=0, atol=1e-10)
    # Gate metrics use derivative-zero extrema, independently of plot density.
    assert candidate.metrics.magnetizing_current_peak_a == pytest.approx(7.528812908, abs=2e-7)
    assert candidate.diagnostics["magnetizing_peak_source"] == "derivative_zero_events"
    assert candidate.metrics.magnetizing_current_peak_a >= candidate.waveform.signal(
        "i_magnetizing").statistics.absolute_peak


@pytest.mark.parametrize("samples", [128, 256, 512])
def test_old_ln55_example_is_rejected_by_unchanged_twenty_percent_gate(samples):
    spec = _solution(PrimaryTopology.FULL_BRIDGE, 256).request.spec
    validation = validate_fha_td_point(
        LLCAnalysisRequest(spec, samples_per_cycle=samples, waveform_cycles=1),
        time_domain=TimeDomainConfig(samples_per_cycle=samples, output_cycles=1,
            maximum_settling_cycles=120, shooting_max_evaluations=60, frequency_scan_points=5),
    )
    assert validation.td.convergence.converged
    assert validation.model_validity == ModelValidity.FAIL
    deviation = next(d for d in validation.deviations if d.metric == "magnetizing_current_peak_a")
    assert deviation.status == ConstraintStatus.FAIL
    assert 20.5 < deviation.deviation_percent < 21.5


@pytest.mark.parametrize("optimizer_success", [True, False])
def test_optimizer_status_cannot_certify_a_bad_periodic_residual(monkeypatch, optimizer_success):
    from llc_design.dynamics import switched

    def false_success(_residual, initial, **_kwargs):
        bad = initial.copy()
        bad[2] += .2  # inject a nonperiodic magnetizing current bias
        return SimpleNamespace(x=bad, success=optimizer_success)

    monkeypatch.setattr(switched, "root", false_success)
    spec = LLCDesignSpec()
    result = solve_time_domain(LLCAnalysisRequest(spec, frequency_hz=100e3),
        TimeDomainConfig(samples_per_cycle=256, retry_samples_per_cycle=256,
                         minimum_settling_cycles=1, output_cycles=1))
    assert not result.convergence.converged
    assert result.convergence.residual_norm > 1e-5
    with pytest.raises(RuntimeError, match="did not converge"):
        solve_time_domain(LLCAnalysisRequest(spec, frequency_hz=100e3),
            TimeDomainConfig(samples_per_cycle=256, retry_samples_per_cycle=256,
                minimum_settling_cycles=1, output_cycles=1, strict_convergence=True))


def test_stagnation_flag_does_not_discard_an_independently_verified_orbit(monkeypatch):
    from llc_design.dynamics import switched

    actual = switched.root

    def stagnation(*args, **kwargs):
        solution = actual(*args, **kwargs)
        solution.success = False
        return solution

    monkeypatch.setattr(switched, "root", stagnation)
    result = solve_time_domain(LLCAnalysisRequest(LLCDesignSpec(), frequency_hz=100e3),
        TimeDomainConfig(samples_per_cycle=256, output_cycles=1))
    assert result.convergence.converged
    assert result.convergence.residual_norm < 1e-7


@pytest.mark.parametrize("sample_phase", [0., .25, .5])
def test_continuous_peak_is_independent_of_measurement_grid_phase(sample_phase):
    result = _solution(PrimaryTopology.FULL_BRIDGE, 128)
    keys = ("i_resonant", "v_resonant_cap", "i_magnetizing", "v_output_cap_internal")
    initial = np.array([result.waveform.signal(key).values[0] for key in keys])
    _, sampled, continuous_peak = _independent_cycle(result.native_result.parameters,
        result.waveform.switching_frequency_hz, initial, 128, sample_phase)
    assert continuous_peak == pytest.approx(result.metrics.magnetizing_current_peak_a, abs=2e-7)
    assert np.max(np.abs(sampled[:, 2])) < continuous_peak
    # Waveform statistics remain a truthful description of the actual samples.
    assert result.waveform.signal("i_magnetizing").statistics.absolute_peak == np.max(
        np.abs(result.waveform.signal("i_magnetizing").values))
    assert result.waveform.metadata["waveform_statistics_source"] == "uniform_output_samples"
