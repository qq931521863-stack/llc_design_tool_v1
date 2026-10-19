"""Independent switching-cycle checks for Power Stage Designer.

The oracle solves Kirchhoff/inductor state and output-charge constraints using
SciPy's nonlinear root solver, then integrates each conducting branch with
adaptive quadrature. It does not call production duty/ripple/statistics helpers.
These are ideal (or constant diode-drop) circuit checks, not SPICE or hardware
validation. A separate midpoint refinement check states its integration budget.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
import pytest
from scipy.integrate import quad
from scipy.optimize import root


@dataclass(frozen=True)
class _Circuit:
    topology: str
    vin: float
    vout: float
    iout: float
    fsw: float
    inductance: float
    turns_np_ns: float = 1.0
    diode_drop: float = 0.0


@dataclass(frozen=True)
class _ReferenceCycle:
    circuit: _Circuit
    on: float
    off: float
    initial_current: float
    mode: str

    @property
    def slopes_per_period(self):
        c = self.circuit
        if c.topology == "buck":
            voltage = (c.vin - c.vout, -c.vout - c.diode_drop)
        elif c.topology == "boost":
            voltage = (c.vin, c.vin - c.vout - c.diode_drop)
        elif c.topology == "flyback":
            voltage = (c.vin, -c.turns_np_ns * (c.vout + c.diode_drop))
        else:
            raise AssertionError(c.topology)
        return tuple(v / (c.inductance * c.fsw) for v in voltage)

    @property
    def peak_current(self):
        return self.initial_current + self.slopes_per_period[0] * self.on

    @property
    def intervals(self):
        return ((0., self.on), (self.on, self.on + self.off),
                (self.on + self.off, 1.))

    def current(self, phase):
        if phase < self.on:
            return self.initial_current + self.slopes_per_period[0] * phase
        if phase < self.on + self.off:
            return self.peak_current + self.slopes_per_period[1] * (phase - self.on)
        return 0.

    def branch(self, name, phase):
        c = self.circuit
        i = self.current(phase)
        is_on = phase < self.on
        is_off = self.on <= phase < self.on + self.off
        diode_i = i * (c.turns_np_ns if c.topology == "flyback" else 1.) if is_off else 0.
        output_i = i if c.topology == "buck" else diode_i
        if name == "inductor":
            return i
        if name == "switch":
            return i if is_on else 0.
        if name == "diode":
            return diode_i
        if name == "input":
            return i if c.topology == "boost" or is_on else 0.
        if name == "output":
            return output_i
        if name == "capacitor":
            return output_i - c.iout
        raise AssertionError(name)

    def moment(self, name, power=1):
        # Split at circuit events: no quadrature interpolation across jumps.
        return sum(quad(lambda x: self.branch(name, x) ** power, start, end,
                        epsabs=1e-12, epsrel=1e-12)[0]
                   for start, end in self.intervals if end > start)

    def rms(self, name):
        return math.sqrt(self.moment(name, 2))

    def sampled_moment(self, name, power, subdivisions):
        total = 0.
        for start, end in self.intervals:
            if end > start:
                phase = start + (np.arange(subdivisions) + .5) * (end - start) / subdivisions
                total += (end - start) * np.mean([self.branch(name, t) ** power for t in phase])
        return float(total)


def _solve_circuit(circuit):
    """Solve periodic state + capacitor charge, trying CCM before DCM.

    Circuit equations are expressed as constraints rather than a closed-form
    duty/ripple solution. Diode complementarity chooses the conduction mode.
    """
    def residual_ccm(unknown):
        on, initial = unknown
        cycle = _ReferenceCycle(circuit, on, 1. - on, initial, "CCM")
        closure = cycle.peak_current + cycle.slopes_per_period[1] * cycle.off - initial
        return (closure, cycle.moment("output") - circuit.iout)

    continuous = root(residual_ccm, (.5, circuit.iout), tol=1e-11)
    assert continuous.success, continuous.message
    assert np.linalg.norm(residual_ccm(continuous.x)) < 1e-10
    on, initial = continuous.x
    if initial >= -1e-10:
        mode = "BCM" if abs(initial) < 1e-10 else "CCM"
        return _ReferenceCycle(circuit, float(on), 1. - float(on), max(0., float(initial)), mode)

    def residual_dcm(unknown):
        on, off = unknown
        cycle = _ReferenceCycle(circuit, on, off, 0., "DCM")
        closure = cycle.peak_current + cycle.slopes_per_period[1] * off
        return (closure, cycle.moment("output") - circuit.iout)

    discontinuous = root(residual_dcm, (max(float(on) / 2, .01), max((1. - float(on)) / 2, .01)), tol=1e-11)
    assert discontinuous.success, discontinuous.message
    assert np.linalg.norm(residual_dcm(discontinuous.x)) < 1e-10
    on, off = discontinuous.x
    assert on > 0 and off > 0 and on + off < 1
    return _ReferenceCycle(circuit, float(on), float(off), 0., "DCM")


GOLDEN_CIRCUITS = (
    _Circuit("buck", 12., 5., 3., 100e3, 47e-6),
    _Circuit("boost", 24., 48., 2., 100e3, 100e-6),
    _Circuit("flyback", 48., 12., 1., 100e3, 1e-3, 4.),
)


@pytest.mark.parametrize("circuit", GOLDEN_CIRCUITS, ids=lambda c: c.topology)
def test_independent_reference_satisfies_charge_state_and_power(circuit):
    for load_multiplier in (1., .005):
        for diode_drop in (0., .7):
            c = replace(circuit, iout=circuit.iout * load_multiplier, diode_drop=diode_drop)
            ref = _solve_circuit(c)
            assert ref.moment("capacitor") == pytest.approx(0., abs=1e-11)
            flux_change = sum(s * d for s, d in zip(ref.slopes_per_period, (ref.on, ref.off)))
            assert flux_change == pytest.approx(0., abs=1e-11)
            pin = c.vin * ref.moment("input")
            pdiode = c.diode_drop * ref.moment("diode")
            assert pin == pytest.approx(c.vout * c.iout + pdiode, rel=1e-11, abs=1e-11)


@pytest.mark.parametrize("circuit", GOLDEN_CIRCUITS, ids=lambda c: c.topology)
def test_independent_midpoint_square_integral_converges(circuit):
    for load_multiplier in (1., .005):
        ref = _solve_circuit(replace(circuit, iout=circuit.iout * load_multiplier))
        exact = ref.moment("inductor", 2)
        errors = [abs(ref.sampled_moment("inductor", 2, n) - exact) for n in (8, 16, 32)]
        assert errors[1] < errors[0] * .251
        assert errors[2] < errors[1] * .251
        assert errors[-1] / exact < 1e-3  # Declared discrete-integral error budget.


def _production_input(circuit, **changes):
    from power_stage_design.models import DesignInputs
    return DesignInputs(
        topology={"buck": "T01", "boost": "T02", "flyback": "T09"}[circuit.topology],
        vin_v=circuit.vin, vout_v=circuit.vout, iout_a=circuit.iout,
        fsw_hz=circuit.fsw, inductance_h=circuit.inductance,
        turns_ratio_np_ns=circuit.turns_np_ns, diode_drop_v=circuit.diode_drop,
        **changes,
    )


def _wave_value(wave, phase):
    # Read the public waveform coefficients without production evaluation code.
    for segment in wave.segments:
        if segment.start_fraction <= phase < segment.end_fraction:
            u = (phase - segment.start_fraction) / (segment.end_fraction - segment.start_fraction)
            value = 0.
            for coefficient in reversed(segment.coefficients):
                value = value * u + coefficient
            cosine, sine, angular_span = getattr(segment, "harmonic", (0., 0., 0.))
            if angular_span:
                value += cosine * math.cos(angular_span * u) + sine * math.sin(angular_span * u)
            return value
    raise AssertionError(f"Waveform does not cover phase {phase}")


def test_g04_hold_up_matches_charge_domain_energy_integral_and_discharge_path():
    from scipy.integrate import solve_ivp

    from power_stage_design.toolbox import hold_up_capacitance
    result = hold_up_capacitance(400.,300.,100.,.02)
    c = result.outputs["capacitance_f"]
    assert c == pytest.approx(57.14285714285714e-6,rel=1e-12)
    # Integrate V dq in charge coordinates, independently of C(V1²-V2²)/2.
    energy = quad(lambda charge: charge/c, c*300.,c*400.,epsabs=1e-12)[0]
    assert energy == pytest.approx(2.,rel=1e-12)
    # Charge decays at load current P/V; power is drawn from the capacitor,
    # rather than a downstream power whose efficiency might be unspecified.
    trajectory = solve_ivp(lambda _t,q: [-100.*c/q[0]], [0.,.02], [c*400.],
                           rtol=2e-12,atol=1e-14,dense_output=True)
    assert trajectory.success
    assert trajectory.y[0,-1]/c == pytest.approx(300.,abs=2e-7)
    for t in np.linspace(0.,.02,11):
        voltage = trajectory.sol(t)[0]/c
        removed = quad(lambda charge: charge/c,c*voltage,c*400.)[0]
        assert removed == pytest.approx(100.*t,abs=1e-8)


@pytest.mark.parametrize("frequency", [1e3,100e3,2e6])
def test_g04_equal_sharing_and_three_branch_mna_without_production_impedance(frequency):
    from power_stage_design.toolbox import CapacitorBranch, capacitor_current_sharing
    identical = CapacitorBranch(100e-6,.01,2e-9)
    two = capacitor_current_sharing([identical,identical],3.,frequency)
    assert [b["current_rms_a"] for b in two.outputs["branches"]] == pytest.approx([1.5,1.5])
    branches = [CapacitorBranch(100e-6,.01,2e-9),CapacitorBranch(10e-6,.025,7e-9),
                CapacitorBranch(1e-6,.003,1e-9)]
    result = capacitor_current_sharing(branches,3.,frequency)
    # MNA unknowns [I1,I2,I3,Vbus], three series-branch KVL rows plus KCL.
    matrix = np.zeros((4,4),dtype=complex)
    omega = 2*np.pi*frequency
    for index,branch in enumerate(branches):
        matrix[index,index] = branch.esr_ohm+1j*omega*branch.esl_h+1/(1j*omega*branch.capacitance_f)
        matrix[index,3] = -1
    matrix[3,:3] = 1
    expected = np.linalg.solve(matrix,np.asarray([0,0,0,3],dtype=complex))
    for row,current in zip(result.outputs["spectrum"][0]["branches"],expected[:3],strict=True):
        assert complex(row["current_real_a"],row["current_imag_a"]) == pytest.approx(current)
        # Independent real-time sinusoidal RMS; complex magnitude isn't added.
        mean_square = quad(lambda phase,current=current:(np.sqrt(2)*(current.real*np.cos(phase)-current.imag*np.sin(phase)))**2,
                           0.,2*np.pi,epsabs=1e-12)[0]/(2*np.pi)
        assert row["current_rms_a"] == pytest.approx(np.sqrt(mean_square))


def test_g04_divider_matches_feedback_node_and_actual_tolerance_corners():
    from itertools import product

    from power_stage_design.toolbox import design_divider
    result = design_divider(2.5,12.,10e3,resistor_tolerance=.01,reference_tolerance=.01)
    values = result.outputs
    assert values["high_ohm"] == pytest.approx(38000.)
    assert (12.-2.5)/values["high_ohm"]-2.5/values["low_ohm"] == pytest.approx(0.,abs=1e-15)
    voltages = []
    for reference,high,low in product((2.475,2.525),(37620.,38380.),(9900.,10100.)):
        # Solve the two resistor-current equations, using feedback-node KCL.
        current,output = np.linalg.solve([[1.,-1./high],[1.,0.]],[-reference/high,reference/low])
        assert current == pytest.approx(reference/low)
        voltages.append(output)
    assert values["minimum_output_v"] == pytest.approx(min(voltages))
    assert values["maximum_output_v"] == pytest.approx(max(voltages))


def _wave_integral(wave, power=1):
    return sum(quad(lambda phase: _wave_value(wave, phase) ** power,
                    segment.start_fraction, segment.end_fraction,
                    epsabs=1e-12, epsrel=1e-12)[0] for segment in wave.segments)


def _assert_against_reference(circuit, result):
    ref = _solve_circuit(circuit)
    assert result.mode == ref.mode
    assert result.duty == pytest.approx(ref.on, rel=1e-10, abs=1e-12)
    keys = {"inductor": "Lm" if circuit.topology == "flyback" else "L1",
            "switch": "Q1", "diode": "D1", "capacitor": "Cout"}
    for branch, device in keys.items():
        current = result.waveforms[device].current_a
        assert current.period_s == pytest.approx(1. / circuit.fsw, rel=1e-12)
        for start, end in ref.intervals:
            if end - start < 1e-12:
                continue
            for phase in start + (np.arange(17) + .5) * (end - start) / 17:
                assert _wave_value(current, phase) == pytest.approx(ref.branch(branch, phase), rel=1e-9, abs=1e-10)
        # Compare published stats against independent quadrature of BOTH the
        # circuit oracle and the exact coefficients consumed by GUI plotting.
        for moment, field in ((1, "current_mean_a"), (2, "current_rms_a")):
            expected = ref.moment(branch, moment)
            actual = _wave_integral(current, moment)
            assert actual == pytest.approx(expected, rel=1e-10, abs=1e-11)
            if moment == 2:
                expected = math.sqrt(expected)
            assert result.stress[device][field] == pytest.approx(expected, rel=1e-10, abs=1e-11)
    inductor = result.waveforms[keys["inductor"]]
    assert _wave_integral(inductor.voltage_v) == pytest.approx(0., abs=1e-10)
    for interval_index, (start, end) in enumerate(ref.intervals):
        if end - start < 1e-12:
            continue
        phase = (start + end) / 2.
        expected_voltage = ref.slopes_per_period[interval_index] * circuit.inductance * circuit.fsw if interval_index < 2 else 0.
        assert _wave_value(inductor.voltage_v, phase) == pytest.approx(expected_voltage, rel=1e-10, abs=1e-10)
    assert _wave_integral(result.waveforms["Cout"].current_a) == pytest.approx(0., abs=1e-11)
    if circuit.topology == "boost":
        input_current = _wave_integral(inductor.current_a)
    else:
        input_current = _wave_integral(result.waveforms["Q1"].current_a)
    diode_current = _wave_integral(result.waveforms["D1"].current_a)
    assert circuit.vin * input_current == pytest.approx(
        circuit.vout * circuit.iout + circuit.diode_drop * diode_current, rel=1e-10, abs=1e-10)
    return ref


@pytest.mark.parametrize("circuit", GOLDEN_CIRCUITS, ids=lambda c: c.topology)
@pytest.mark.parametrize("load_multiplier", [1., .005])
@pytest.mark.parametrize("diode_drop", [0., .7])
def test_stage_matches_independent_circuit_solution(circuit, load_multiplier, diode_drop):
    from power_stage_design.topologies import calculate_stage
    c = replace(circuit, iout=circuit.iout * load_multiplier, diode_drop=diode_drop)
    result = calculate_stage(_production_input(c))
    _assert_against_reference(c, result)


@pytest.mark.parametrize("index,duty,rms_values", [
    (0, .4166666667, (3.0053439053, 1.9399411491, 2.2953693226, .1791423708)),
    (1, .5, (4.0149719800, 2.8390139133, 2.8390139133, 2.0149441680)),
    (2, .5, (None, .3569313660, 1.4277254638, None)),
])
def test_g01_g02_g03_published_starting_points(index, duty, rms_values):
    """Rounded specification rows; strict circuit oracle above is primary."""
    from power_stage_design.topologies import calculate_stage
    c = GOLDEN_CIRCUITS[index]
    result = calculate_stage(_production_input(c))
    assert result.duty == pytest.approx(duty, rel=1e-8, abs=5e-11)
    for device, expected in zip(("Lm" if index == 2 else "L1", "Q1", "D1", "Cout"), rms_values):
        if expected is not None:
            assert result.stress[device]["current_rms_a"] == pytest.approx(expected, rel=1e-8, abs=5e-11)
    if index == 2:
        assert result.stress["Q1"]["voltage_max_v"] == pytest.approx(96.)
        assert result.stress["D1"]["voltage_max_v"] == pytest.approx(24.)


@pytest.mark.parametrize("circuit", GOLDEN_CIRCUITS, ids=lambda c: c.topology)
def test_actual_conduction_intervals_change_at_mode_boundary(circuit):
    from scipy.optimize import brentq

    from power_stage_design.models import ModelDomainError
    from power_stage_design.topologies import calculate_stage
    dummy = _ReferenceCycle(circuit, .5, .5, 0., "BCM")
    slopes = dummy.slopes_per_period
    duty = brentq(lambda d: slopes[0] * d + slopes[1] * (1. - d), 0., 1.)
    boundary_cycle = _ReferenceCycle(circuit, duty, 1. - duty, 0., "BCM")
    boundary_load = boundary_cycle.moment("output")
    results = []
    for factor, expected_mode in ((1. - 1e-6, "DCM"), (1., "BCM"), (1. + 1e-6, "CCM")):
        c = replace(circuit, iout=boundary_load * factor)
        result = calculate_stage(_production_input(c))
        assert result.mode == expected_mode
        _assert_against_reference(c, result)
        results.append(result)
    # DCM solves a different duty and leaves a true zero-current interval.
    assert results[0].duty < results[1].duty
    inductor = "Lm" if circuit.topology == "flyback" else "L1"
    assert len(results[0].waveforms[inductor].current_a.segments) == 3
    assert results[0].waveforms[inductor].current_a.segments[-1].coefficients == (0., 0.)
    with pytest.raises(ModelDomainError):
        calculate_stage(_production_input(replace(circuit, iout=boundary_load * .5), mode="ccm"))
    with pytest.raises(ModelDomainError):
        calculate_stage(_production_input(replace(circuit, iout=boundary_load * 2.), mode="dcm"))


@pytest.mark.parametrize("turns", [.25, 1., 4., 7.])
def test_flyback_turns_ratio_is_np_over_ns_and_real_windings_are_gated(turns):
    from power_stage_design.topologies import calculate_stage
    circuit = replace(GOLDEN_CIRCUITS[2], turns_np_ns=turns)
    result = calculate_stage(_production_input(circuit))
    ref = _assert_against_reference(circuit, result)
    for phase in (.5 * ref.on, ref.on + .5 * ref.off):
        assert _wave_value(result.waveforms["primary"].current_a, phase) == pytest.approx(ref.branch("input", phase))
        assert _wave_value(result.waveforms["secondary"].current_a, phase) == pytest.approx(ref.branch("diode", phase))
    assert result.stress["Q1"]["voltage_max_v"] == pytest.approx(circuit.vin + turns * circuit.vout)
    assert result.stress["D1"]["voltage_max_v"] == pytest.approx(circuit.vout + circuit.vin / turns)


@pytest.mark.parametrize("circuit", GOLDEN_CIRCUITS, ids=lambda c: c.topology)
def test_capacitor_voltage_derivative_matches_actual_current_and_closes(circuit):
    from power_stage_design.topologies import calculate_stage
    for load_multiplier in (1., .005):
        inputs = _production_input(replace(circuit, iout=circuit.iout * load_multiplier))
        result = calculate_stage(inputs)
        capacitor = result.waveforms["Cout"]
        first = capacitor.voltage_v.segments[0].coefficients[0]
        final = sum(capacitor.voltage_v.segments[-1].coefficients)
        assert final == pytest.approx(first, rel=1e-11, abs=1e-11)
        assert _wave_integral(capacitor.voltage_v) == pytest.approx(circuit.vout, rel=1e-11)
        for segment in capacitor.voltage_v.segments:
            phase = (segment.start_fraction + segment.end_fraction) / 2
            delta = (segment.end_fraction - segment.start_fraction) * 1e-4
            numerical_derivative = (_wave_value(capacitor.voltage_v, phase + delta) -
                                    _wave_value(capacitor.voltage_v, phase - delta)) / (2 * delta / circuit.fsw)
            assert numerical_derivative * inputs.capacitance_f == pytest.approx(
                _wave_value(capacitor.current_a, phase), rel=1e-6, abs=1e-7)


@pytest.mark.parametrize("changes", [
    {"vin_v": 0.}, {"vin_v": float("nan")}, {"vout_v": float("inf")},
    {"iout_a": -1.}, {"fsw_hz": 0.}, {"inductance_h": -1e-6},
    {"capacitance_f": 0.}, {"diode_drop_v": -.7}, {"mode": "forced_pwm"},
])
def test_invalid_inputs_fail_without_a_plausible_result(changes):
    from power_stage_design.models import ModelDomainError
    from power_stage_design.topologies import calculate_stage
    with pytest.raises(ModelDomainError):
        calculate_stage(replace(_production_input(GOLDEN_CIRCUITS[0]), **changes))


@pytest.mark.parametrize("vin,vout", [(24., 12.), (24., 24.), (24., 23.9)])
def test_boost_rejects_non_boost_voltage_domain(vin, vout):
    from power_stage_design.models import ModelDomainError
    from power_stage_design.topologies import calculate_stage
    with pytest.raises(ModelDomainError):
        calculate_stage(_production_input(replace(GOLDEN_CIRCUITS[1], vin=vin, vout=vout)))


@pytest.mark.parametrize("circuit", [
    replace(GOLDEN_CIRCUITS[0], vout=11.9),
    replace(GOLDEN_CIRCUITS[0], vout=.1),
    replace(GOLDEN_CIRCUITS[1], vout=240.),
    replace(GOLDEN_CIRCUITS[1], vout=24.01),
    replace(GOLDEN_CIRCUITS[2], inductance=10e-6),
    replace(GOLDEN_CIRCUITS[2], inductance=100e-6, diode_drop=.7),
], ids=["buck-near-unity", "buck-low-output", "boost-tenfold", "boost-near-unity",
        "flyback-small-Lp", "flyback-small-Lp-diode"])
def test_conversion_extremes_and_small_flyback_inductance(circuit):
    from power_stage_design.topologies import calculate_stage
    _assert_against_reference(circuit, calculate_stage(_production_input(circuit)))
