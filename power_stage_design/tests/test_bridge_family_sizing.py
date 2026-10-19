"""T15/T16 inverse and half-bridge midpoint checks against circuit invariants.

The reusable T17 test oracle integrates KVL/ampere-turns with diode events. A
T15 circuit has the same primary pulse as the oracle at half its actual bus
voltage; source current is correspondingly halved. These tests never use the
production forward model to generate inverse targets. The split-cap checks use
independent node KCL, stiff-bus KVL and quadrature of charge and energy.
"""
from __future__ import annotations

import copy
import math
from dataclasses import fields, replace
from functools import cache

import numpy as np
import pytest

from power_stage_design import bridge_sizing
from power_stage_design.bridge_sizing import size_full_bridge, size_symmetric_bridge
from power_stage_design.models import DesignInputs, ModelDomainError
from power_stage_design.tests.test_full_bridge_sizing import (
    _half_cycle,
    _independent_fixture,
)
from power_stage_design.topologies import calculate_stage


@cache
def _fixture(topology, branch, vf=0.):
    # The oracle's bus is the primary pulse voltage, not the actual T15 bus.
    oracle, duty, ripple, magnetic, pieces, samples = _independent_fixture(branch, vf)
    physical = replace(oracle, topology=topology,
                       vin_v=2*oracle.vin_v if topology == "T15" else oracle.vin_v,
                       transfer_capacitance_f=100e-6)
    return physical, duty, ripple, magnetic, pieces, samples


def _value(wave, phase):
    segment = next(s for s in wave.segments if s.start_fraction <= phase < s.end_fraction)
    return segment.evaluate((phase-segment.start_fraction)/segment.duration_fraction)


@pytest.mark.parametrize("topology", ["T15", "T16"])
@pytest.mark.parametrize("branch", ["ordinary", "partial", "boundary", "dcm"])
@pytest.mark.parametrize("vf", [0., .4])
@pytest.mark.parametrize("size_lm,size_ratio", [(False, False), (True, False), (False, True), (True, True)])
def test_sizing_recovers_event_ivp_components_and_actual_charge_flux(topology, branch, vf, size_lm, size_ratio):
    actual, duty, ripple, magnetic, pieces, _ = _fixture(topology, branch, vf)
    source = replace(actual, inductance_h=120e-6,
                     magnetizing_inductance_h=.003 if size_lm else actual.magnetizing_inductance_h,
                     turns_ratio_np_ns=1.3 if size_ratio else actual.turns_ratio_np_ns,
                     secondary_inductance_h=300e-6, leakage_inductance_h=1e-6)
    unchanged = copy.deepcopy(source)
    result = size_symmetric_bridge(source, ripple, max_duty=duty if size_ratio else .8,
                                   magnetizing_ripple_pp_a=magnetic if size_lm else None,
                                   size_turns_ratio=size_ratio)
    assert source == unchanged
    assert result.inputs.inductance_h == pytest.approx(actual.inductance_h, rel=2e-9)
    assert result.inputs.magnetizing_inductance_h == pytest.approx(actual.magnetizing_inductance_h, rel=2e-9)
    assert result.inputs.turns_ratio_np_ns == pytest.approx(actual.turns_ratio_np_ns, rel=2e-9)
    assert result.duty == pytest.approx(duty, rel=2e-9)
    assert result.mode == {"ordinary": "CCM", "partial": "CCM", "boundary": "BCM", "dcm": "DCM"}[branch]
    assert result.metrics["inductor_ripple_pp_a"] == pytest.approx(ripple, rel=2e-9)
    assert result.stress["Lm"]["current_pp_a"] == pytest.approx(magnetic, rel=2e-9)
    assert result.metrics["per_switch_duty"] == pytest.approx(duty/2, rel=2e-9)
    assert result.metrics["rectified_ripple_frequency_hz"] == 2*actual.fsw_hz
    for field in fields(source):
        if field.name not in {"inductance_h", "magnetizing_inductance_h", "turns_ratio_np_ns"}:
            assert getattr(result.inputs, field.name) == getattr(source, field.name)
    assert result.design_id == result.inputs.design_id
    for piece in pieces:
        for phase in np.linspace(piece.t[0], piece.t[-1], 7)[1:-1]:
            states = piece.sol(phase)
            for key, index in (("L1", 0), ("Lm", 1)):
                assert _value(result.waveforms[key].current_a, phase/2) == pytest.approx(states[index], rel=2e-8, abs=2e-10)
    independent_power = pieces[-1].y[3, -1]
    assert independent_power == pytest.approx((actual.vout_v+vf)*actual.iout_a, rel=2e-10)
    assert result.metrics["input_power_w"] == pytest.approx(independent_power, rel=2e-9)
    assert result.metrics["input_current_mean_a"] == pytest.approx(independent_power/actual.vin_v, rel=2e-9)
    assert result.stress["Cout"]["current_mean_a"] == pytest.approx(0., abs=1e-11)
    assert result.diagnostics["transformer_mean_voltage_v"] == pytest.approx(0., abs=1e-11)
    assert result.stress["Q1"]["voltage_max_v"] == pytest.approx(actual.vin_v*(2 if topology == "T16" else 1))
    if topology == "T16":
        assert "primary1" in result.waveforms and "primary2" in result.waveforms
        assert "primary" not in result.waveforms
    # For joint CCM the output-only ripple > 2*Io test is insufficient.
    if branch == "partial":
        assert result.stress["L1"]["current_min_a"] > 0
        assert result.metrics["inductor_ripple_pp_a"] > 2*actual.iout_a


@pytest.mark.parametrize("branch", ["ordinary", "partial", "boundary", "dcm"])
@pytest.mark.parametrize("vf", [0., .4])
def test_split_capacitors_obey_midpoint_kcl_stiff_bus_kvl_and_energy(branch, vf):
    p, _, _, _, pieces, _ = _fixture("T15", branch, vf)
    result = calculate_stage(p)
    # Gaussian quadrature integrates the independent polynomial branch tests
    # exactly, without relying on the production statistics or sizing formulas.
    nodes, weights = np.polynomial.legendre.leggauss(5)
    source_charge = 0.
    cap_charge = np.zeros(2)
    cap_energy = np.zeros(2)
    primary_energy = 0.
    for segment in result.waveforms["L1"].current_a.segments:
        span = segment.duration_fraction
        for node, weight in zip(nodes, weights):
            phase = segment.start_fraction+(node+1)*span/2
            current = lambda key,phase=phase: _value(result.waveforms[key].current_a, phase)
            voltage = lambda key,phase=phase: _value(result.waveforms[key].voltage_v, phase)
            q1, q2, ip = current("Q1"), current("Q2"), current("primary")
            ic1, ic2 = current("Csplit1"), current("Csplit2")
            vc1, vc2 = voltage("Csplit1"), voltage("Csplit2")
            # MID KCL and stiff total-bus KVL, independently of branch labels.
            assert ic1+ip == pytest.approx(ic2, abs=1e-12)
            assert ic1+ic2 == pytest.approx(0., abs=1e-12)
            assert vc1+vc2 == pytest.approx(p.vin_v, abs=2e-12)
            supply = q1+ic1  # VIN KCL: source supplies top switch and top C.
            assert supply == pytest.approx(q2+ic2, abs=1e-12)  # GND KCL.
            assert q1-q2 == pytest.approx(ip, abs=1e-12)
            if abs(ip) < 1e-12:
                assert ic1 == pytest.approx(0., abs=1e-12)
                assert ic2 == pytest.approx(0., abs=1e-12)
            dt = weight*span/(2*p.fsw_hz)
            source_charge += supply*dt
            primary_energy += ip*voltage("primary")*dt
            for index, key in enumerate(("Csplit1", "Csplit2")):
                wave = result.waveforms[key].voltage_v
                vseg = next(s for s in wave.segments if s.start_fraction <= phase < s.end_fraction)
                local = (phase-vseg.start_fraction)/vseg.duration_fraction
                dvdt = np.polynomial.polynomial.polyval(local, np.polynomial.polynomial.polyder(vseg.coefficients))*p.fsw_hz/vseg.duration_fraction
                assert p.transfer_capacitance_f*dvdt == pytest.approx(current(key), abs=2e-11)
                cap_charge[index] += current(key)*dt
                cap_energy[index] += current(key)*voltage(key)*dt
    assert cap_charge == pytest.approx([0., 0.], abs=1e-15)
    assert cap_energy == pytest.approx([0., 0.], abs=1e-13)
    independent_energy = pieces[-1].y[3, -1]/p.fsw_hz
    assert p.vin_v*source_charge == pytest.approx(independent_energy, rel=2e-10)
    assert primary_energy == pytest.approx(independent_energy, rel=2e-10)
    assert result.metrics["input_current_mean_a"] == pytest.approx(source_charge*p.fsw_hz, rel=2e-10)
    for key in ("Csplit1", "Csplit2"):
        wave = result.waveforms[key].voltage_v
        assert wave.segments[-1].evaluate(1.) == pytest.approx(wave.segments[0].evaluate(0.), abs=1e-12)
    assert any("Midpoint ripple is not fed back" in item for item in result.assumptions)


@pytest.mark.parametrize("branch", ["ordinary", "partial", "boundary", "dcm"])
def test_split_capacitor_positive_voltage_and_small_ripple_domain(branch):
    p, _, ripple, _, _, _ = _fixture("T15", branch, .4)
    # In a symmetric cycle each half withdraws source charge Pin/(2*fs*Vin).
    # KCL puts that charge into one split C and removes it from the other.
    charge_excursion = (p.vout_v+p.diode_drop_v)*p.iout_a/(2*p.fsw_hz*p.vin_v)
    minimum_small_ripple_c = charge_excursion/(.1*p.vin_v/2)
    valid = calculate_stage(replace(p, transfer_capacitance_f=minimum_small_ripple_c*(1+1e-7)))
    assert valid.stress["Csplit1"]["voltage_pp_v"] == pytest.approx(.1*p.vin_v/2/(1+1e-7), rel=2e-9)
    assert valid.stress["Csplit1"]["voltage_min_v"] > 0
    with pytest.raises(ModelDomainError, match="ripple exceeds 10% of Vin/2"):
        calculate_stage(replace(p, transfer_capacitance_f=minimum_small_ripple_c*(1-1e-7)))
    # The reported 1 pF leak must fail through both forward and inverse callers.
    invalid = replace(p, transfer_capacitance_f=1e-12)
    with pytest.raises(ModelDomainError, match="voltage reaches or crosses zero"):
        calculate_stage(invalid)
    with pytest.raises(ModelDomainError, match="voltage reaches or crosses zero"):
        size_symmetric_bridge(invalid, ripple, max_duty=.8)


@pytest.mark.parametrize("topology", ["T15", "T16"])
@pytest.mark.parametrize("offset,mode", [(-1e-6, "DCM"), (0., "BCM"), (1e-6, "CCM")])
def test_independent_bcm_charge_boundary_and_mode_constraints(topology, offset, mode):
    p, _, _, _, _, _ = _fixture(topology, "boundary")
    result = calculate_stage(replace(p, iout_a=p.iout_a*(1+offset)))
    assert result.mode == mode
    assert result.stress["Cout"]["current_mean_a"] == pytest.approx(0., abs=1e-11)
    assert result.metrics["input_power_w"] == pytest.approx((p.vout_v+p.diode_drop_v)*result.inputs.iout_a, rel=1e-10)


@pytest.mark.parametrize("topology", ["T15", "T16"])
def test_unreachable_and_unmodeled_modes_fail_without_partial_candidate(topology):
    p, duty, ripple, _, _, _ = _fixture(topology, "dcm")
    with pytest.raises(ModelDomainError, match="Requested CCM"):
        size_symmetric_bridge(replace(p, mode="CCM"), ripple, max_duty=.8)
    with pytest.raises(ModelDomainError, match="dead time"):
        size_symmetric_bridge(replace(p, dead_time_s=100e-9), ripple, max_duty=.8)
    with pytest.raises(ModelDomainError, match="commutation-capacitance"):
        size_symmetric_bridge(p, .02, max_duty=.8, magnetizing_ripple_pp_a=.1)
    with pytest.raises(ModelDomainError, match="ratio solution"):
        size_symmetric_bridge(replace(p, iout_a=.001, magnetizing_inductance_h=.001), .02, max_duty=.8, size_turns_ratio=True)
    with pytest.raises(ModelDomainError, match="exceeds maximum"):
        size_symmetric_bridge(p, ripple, max_duty=.9*duty)
    with pytest.raises(ModelDomainError, match="positive output load"):
        size_symmetric_bridge(replace(p, iout_a=0.), ripple, max_duty=.8)
    if topology == "T15":
        with pytest.raises(ModelDomainError, match="split input capacitor"):
            size_symmetric_bridge(replace(p, transfer_capacitance_f=None), ripple, max_duty=.8)


@pytest.mark.parametrize("topology", ["T15", "T16"])
@pytest.mark.parametrize("kwargs", [
    {"ripple_pp_a": 0.}, {"ripple_pp_a": math.nan}, {"max_duty": 1.},
    {"max_duty": True}, {"magnetizing_ripple_pp_a": -1.}, {"size_turns_ratio": 1},
])
def test_invalid_targets_rejected(topology, kwargs):
    p, _, _, _, _, _ = _fixture(topology, "ordinary")
    with pytest.raises(ModelDomainError):
        size_symmetric_bridge(p, **{"ripple_pp_a": .02, "max_duty": .8, **kwargs})


@pytest.mark.parametrize("topology", ["T15", "T16"])
@pytest.mark.parametrize("branch", ["ordinary", "partial", "boundary", "dcm"])
def test_final_acceptance_calls_actual_model_once_and_cancels(topology, branch, monkeypatch):
    p, _, ripple, _, _, _ = _fixture(topology, branch)
    real = bridge_sizing.calculate_stage
    calls = []
    def observed(*args, **kwargs):
        calls.append(args[0])
        return real(*args, **kwargs)
    monkeypatch.setattr(bridge_sizing, "calculate_stage", observed)
    size_symmetric_bridge(p, ripple, max_duty=.8)
    assert len(calls) == 1
    with pytest.raises(InterruptedError):
        size_symmetric_bridge(p, ripple, max_duty=.8, cancelled=lambda: True)
    assert len(calls) == 1
    def wrong_ripple(*args, **kwargs):
        result = real(*args, **kwargs)
        return replace(result, metrics={**result.metrics, "inductor_ripple_pp_a": ripple*1.001})
    monkeypatch.setattr(bridge_sizing, "calculate_stage", wrong_ripple)
    with pytest.raises(ModelDomainError, match="output ripple misses"):
        size_symmetric_bridge(p, ripple, max_duty=.8)


def test_full_bridge_compatibility_entry_remains_t17_only():
    p, _, ripple, _, _, _ = _fixture("T16", "ordinary")
    with pytest.raises(ModelDomainError, match="applies only to T17"):
        size_full_bridge(p, ripple, max_duty=.8)
    with pytest.raises(ModelDomainError, match="only to T15, T16 and T17"):
        size_symmetric_bridge(replace(p, topology="T13"), ripple, max_duty=.8)
    full = replace(p, topology="T17")
    assert size_full_bridge(full, ripple, max_duty=.8) == size_symmetric_bridge(full, ripple, max_duty=.8)


def test_same_bus_keeps_half_bridge_and_push_pull_primary_amplitudes_distinct():
    p = DesignInputs("T15", 48., 5., 1., 100e3, 100e-6,
                     turns_ratio_np_ns=2., magnetizing_inductance_h=.01,
                     transfer_capacitance_f=100e-6)
    half = size_symmetric_bridge(p, .2, max_duty=.8)
    push = size_symmetric_bridge(replace(p, topology="T16"), .2, max_duty=.8)
    assert half.mode == push.mode == "CCM"
    assert half.stress["Lm"]["voltage_max_v"] == p.vin_v/2
    assert push.stress["Lm"]["voltage_max_v"] == p.vin_v
    assert half.duty == pytest.approx(2*push.duty, rel=1e-12)
    assert half.stress["Q1"]["voltage_max_v"] == p.vin_v
    assert push.stress["Q1"]["voltage_max_v"] == 2*p.vin_v
    assert half.metrics["input_power_w"] == pytest.approx(push.metrics["input_power_w"], rel=1e-12)
    assert half.inputs.inductance_h != pytest.approx(push.inputs.inductance_h)


@pytest.mark.parametrize("topology", ["T15", "T16"])
@pytest.mark.parametrize("ratio", [1., 2., 3.5, 4.])
@pytest.mark.parametrize("vf", [0., .4])
def test_zero_freewheel_endpoint_and_unmodeled_commutation_boundary(topology, ratio, vf):
    p = DesignInputs(topology, 96. if topology == "T15" else 48., 5., .001,
                     100e3, 470e-6, capacitance_f=1e-3,
                     turns_ratio_np_ns=ratio, magnetizing_inductance_h=.01,
                     transfer_capacitance_f=100e-6, diode_drop_v=vf)
    result = size_symmetric_bridge(p, .02, max_duty=.8, magnetizing_ripple_pp_a=.04/ratio)
    # In this endpoint the diode event occurs exactly at turn-off. Reusing
    # the event IVP with the actual primary pulse still checks charge/energy.
    oracle = replace(result.inputs, topology="T17", vin_v=48.)
    final, _, samples = _half_cycle(oracle, result.duty, 0., 0., skip_freewheel=True)
    assert result.mode == "DCM"
    assert final[:2] == pytest.approx([0., 0.], abs=1e-12)
    assert final[2] == pytest.approx(p.iout_a, rel=1e-10)
    assert final[3] == pytest.approx((p.vout_v+vf)*p.iout_a, rel=1e-10)
    assert np.ptp(samples[0]) == pytest.approx(.02, rel=1e-10)
    assert 2*np.max(samples[1]) == pytest.approx(.04/ratio, rel=1e-10)
    with pytest.raises(ModelDomainError, match="commutation capacitances"):
        calculate_stage(replace(result.inputs, magnetizing_inductance_h=result.inputs.magnetizing_inductance_h*(1-1e-10)))
    above = calculate_stage(replace(result.inputs, magnetizing_inductance_h=result.inputs.magnetizing_inductance_h*(1+1e-10)))
    assert above.mode == "DCM"
    assert above.stress["Cout"]["current_mean_a"] == pytest.approx(0., abs=1e-12)
