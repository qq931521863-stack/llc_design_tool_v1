"""Declared constant-Coss switching model of the two-switch buck-boost.

This optional model is separate from the ideal CCM model.  The circuit is
VIN--Q1--A--L--B--D2--OUT, D1 from GND to A, and Q2 from B to GND.
Each FET has a constant drain-source capacitor and an antiparallel body diode.
All conductive branches have finite resistance.  No node is assigned an
arbitrary voltage when the external diodes cease conducting.
"""
from __future__ import annotations

import itertools
import json
import math
from dataclasses import asdict, dataclass, replace
from numbers import Real

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares, root

from .models import DesignInputs, DeviceWaveforms, ModelDomainError, StageResult
from .waveforms import Waveform, WaveformSegment


@dataclass(frozen=True)
class T05Options:
    """SI-unit, illustrative circuit parameters; none are vendor device data."""

    coss_q1_f: float = 1e-9
    coss_q2_f: float = 1e-9
    ron_q1_ohm: float = 0.08
    ron_q2_ohm: float = 0.08
    diode_resistance_ohm: float = 0.05
    body_diode_drop_v: float = 0.7
    body_diode_resistance_ohm: float = 0.05
    inductor_resistance_ohm: float = 0.02
    gate_strategy: str = "simultaneous"
    relative_tolerance: float = 2e-7
    max_iterations: int = 40
    model_revision: str = "t05-switched-1"
    parameter_source: str = "Illustrative constants; user must verify"

    def __post_init__(self):
        _validate_options(self)


def _validate_options(options):
    if type(options) is not T05Options:
        raise ModelDomainError("T05 dynamic model requires T05Options")
    for name, value in asdict(options).items():
        if name in ("gate_strategy", "max_iterations", "model_revision", "parameter_source"):
            continue
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
            raise ModelDomainError(f"T05 {name} must be a finite real scalar")
        if value < 0 or (value == 0 and name not in ("body_diode_drop_v", "inductor_resistance_ohm")):
            raise ModelDomainError(f"T05 {name} must be positive (drop and winding resistance may be zero)")
    if options.gate_strategy != "simultaneous":
        raise ModelDomainError("T05 dynamic gate_strategy must be 'simultaneous'; buck-only/boost-only gates are not implemented")
    if options.model_revision != "t05-switched-1":
        raise ModelDomainError("Unsupported T05 dynamic model_revision")
    if not isinstance(options.parameter_source, str) or not options.parameter_source.strip() or len(options.parameter_source) > 1000:
        raise ModelDomainError("T05 parameter_source must be a nonempty string of at most 1000 characters")
    if not 1e-10 <= options.relative_tolerance <= 1e-5:
        raise ModelDomainError("T05 relative_tolerance must lie between 1e-10 and 1e-5")
    if isinstance(options.max_iterations, bool) or not isinstance(options.max_iterations, int) or not 1 <= options.max_iterations <= 200:
        raise ModelDomainError("T05 max_iterations must be an integer in [1, 200]")


class _Circuit:
    """Normalized-time circuit equations, including explicit branch currents."""

    def __init__(self, inputs, options, cancelled):
        self.p, self.o, self.cancelled = inputs, options, cancelled
        self.period = 1 / inputs.fsw_hz
        self.current_scale = max(0.1, inputs.iout_a * (1 + inputs.vout_v / inputs.vin_v),
                                 inputs.vin_v * self.period / inputs.inductance_h)
        rail = max(1.0, inputs.vin_v, inputs.vout_v)
        self.scale = np.array([self.current_scale, rail, rail, inputs.vout_v, inputs.vout_v])
        self.evaluations = 0

    def check_cancel(self):
        if self.cancelled is not None and self.cancelled():
            raise InterruptedError("Two-switch dynamic calculation cancelled")

    def currents(self, state, gate):
        _, a, b, v = state[:4]
        p, o = self.p, self.o
        q1 = (p.vin_v - a) / o.ron_q1_ohm if gate else 0.0
        q2 = b / o.ron_q2_ohm if gate else 0.0
        d1 = max(0.0, (-a - p.diode_drop_v) / o.diode_resistance_ohm)
        d2 = max(0.0, (b - v - p.diode_drop_v) / o.diode_resistance_ohm)
        body1 = max(0.0, (a - p.vin_v - o.body_diode_drop_v) / o.body_diode_resistance_ohm)
        body2 = max(0.0, (-b - o.body_diode_drop_v) / o.body_diode_resistance_ohm)
        return q1, q2, d1, d2, body1, body2

    def derivative(self, state, gate):
        i, a, b, _ = state[:4]
        q1, q2, d1, d2, body1, body2 = self.currents(state, gate)
        o, p = self.o, self.p
        return np.array([(a-b-o.inductor_resistance_ohm*i)/p.inductance_h,
                         (q1-body1+d1-i)/o.coss_q1_f,
                         (i-q2+body2-d2)/o.coss_q2_f,
                         (d2-p.iout_a)/p.capacitance_f])

    def rhs(self, gate):
        def evaluate(t, z):
            self.check_cancel()
            state = z * self.scale
            return np.r_[self.period*self.derivative(state, gate), state[3]] / self.scale
        return evaluate

    def jacobian(self, gate):
        def evaluate(t, z):
            p, o = self.p, self.o
            _, a, b, v = (z*self.scale)[:4]
            g1, g2 = (1/o.ron_q1_ohm, 1/o.ron_q2_ohm) if gate else (0., 0.)
            gd1 = 1/o.diode_resistance_ohm if a < -p.diode_drop_v else 0.
            gd2 = 1/o.diode_resistance_ohm if b > v+p.diode_drop_v else 0.
            gb1 = 1/o.body_diode_resistance_ohm if a > p.vin_v+o.body_diode_drop_v else 0.
            gb2 = 1/o.body_diode_resistance_ohm if b < -o.body_diode_drop_v else 0.
            matrix = np.array([
                [-o.inductor_resistance_ohm/p.inductance_h, 1/p.inductance_h, -1/p.inductance_h, 0., 0.],
                [-1/o.coss_q1_f, -(g1+gd1+gb1)/o.coss_q1_f, 0., 0., 0.],
                [1/o.coss_q2_f, 0., -(g2+gd2+gb2)/o.coss_q2_f, gd2/o.coss_q2_f, 0.],
                [0., 0., gd2/p.capacitance_f, -gd2/p.capacitance_f, 0.],
                [0., 0., 0., 1/self.period, 0.],
            ])
            return self.period*matrix*self.scale[np.newaxis, :]/self.scale[:, np.newaxis]
        return evaluate

    def cycle(self, unknown, retain=False, tolerance=None):
        self.check_cancel()
        self.evaluations += 1
        state = np.r_[unknown[:4], 0.]
        duty = unknown[4]
        pieces = []
        tolerance = tolerance or self.o.relative_tolerance * .025
        for start, end, gate in ((0., duty, True), (duty, 1., False)):
            sol = solve_ivp(self.rhs(gate), (start, end), state,
                            method="Radau", jac=self.jacobian(gate), rtol=tolerance,
                            atol=tolerance*.02, max_step=1/60, dense_output=retain)
            if not sol.success or not np.all(np.isfinite(sol.y[:, -1])):
                raise ModelDomainError("T05 switching integration failed to converge")
            state = sol.y[:, -1]
            if retain:
                pieces.append((sol, gate))
        return state, pieces


def _shoot(circuit):
    p, o = circuit.p, circuit.o
    voltage = p.vout_v + 2*p.diode_drop_v
    dccm = voltage / (p.vin_v + voltage)
    ripple = p.vin_v*dccm*circuit.period/p.inductance_h
    low = p.iout_a/(1-dccm)-ripple/2
    duty = dccm if low > 0 else math.sqrt(2*p.iout_a*p.inductance_h*p.fsw_hz*voltage)/p.vin_v
    duty = min(.95, max(.001, duty*1.01))
    guess = np.r_[np.array([max(0., low), 0., p.vout_v, p.vout_v])/circuit.scale[:4], duty]
    # A few physical cycles improve the floating-node guess without relying on
    # slow output-capacitor settling to establish a periodic solution.
    for _ in range(3):
        final, _ = circuit.cycle(guess)
        guess[:3] = final[:3]
    def residual(unknown, tolerance=None):
        final, _ = circuit.cycle(unknown, tolerance=tolerance)
        # The output-voltage step per cycle is tiny for a large Cout. Scaling
        # its closure by load charge prevents a seemingly converged voltage
        # solution from concealing a substantial output-current error.
        weights = np.array([1., 1., 1., p.vout_v*p.capacitance_f/(p.iout_a*circuit.period), 1.])
        return np.r_[final[:4]-unknown[:4], final[4]-1.]*weights
    lower = np.array([-100., -5., -5., .02, 1e-5])
    upper = np.array([100., 5., 5., 10., .995])
    class OutsideTrialDomain(Exception):
        pass
    def fast_residual(unknown):
        if np.any(unknown <= lower) or np.any(unknown >= upper):
            raise OutsideTrialDomain
        return residual(unknown, o.relative_tolerance*.5)
    # A hybrid Newton solve reuses its Jacobian between iterations. The bounded
    # least-squares route is retained for failed/out-of-domain trial steps.
    solution = None
    try:
        solution = root(fast_residual, guess,
                        options={"eps": 1e-8, "xtol": 1e-9, "maxfev": o.max_iterations*6})
    except OutsideTrialDomain:
        pass
    if solution is None or not solution.success or np.max(np.abs(solution.fun)) > o.relative_tolerance:
        solution = least_squares(residual, guess, bounds=(lower, upper),
                                 diff_step=2e-4, max_nfev=o.max_iterations,
                                 ftol=2e-11, xtol=2e-11, gtol=2e-11)
    final, pieces = circuit.cycle(solution.x, retain=True)
    error = float(np.max(np.abs(np.r_[final[:4]-solution.x[:4], final[4]-1.])))
    if not solution.success or error > max(2e-8, o.relative_tolerance):
        raise ModelDomainError(f"T05 periodic solution did not converge (normalized residual {error:.3g})")
    return solution.x, final, pieces, error


_POLYNOMIAL_FIT = np.linalg.inv(np.vander(np.linspace(0., 1., 4), 4, increasing=True))


def _coefficients(solution, start, stop, scale):
    """Recover the public Radau dense-output cubic without private API access."""
    times = start + (stop-start)*np.linspace(0., 1., 4)
    values = solution.sol(times)[:4]*scale[:4, np.newaxis]
    base = values[:, :1]
    coefficients = (values-base) @ _POLYNOMIAL_FIT.T
    coefficients[:, 0] += base[:, 0]
    return coefficients


def _real_crossings(coefficients):
    roots = np.polynomial.polynomial.polyroots(coefficients)
    return [float(z.real) for z in roots if abs(z.imag) < 1e-8 and 1e-9 < z.real < 1-1e-9]


def _build_waveforms(circuit, pieces):
    """Preserve switched edges and diode thresholds in the shared representation.

    Branch currents follow their constitutive laws from the solved node
    voltages. Coss currents use node KCL; their independently differentiated
    charge is checked below rather than hiding interpolation error.
    """
    p, o, period = circuit.p, circuit.o, circuit.period
    names = ("i", "a", "b", "vo", "q1", "q2", "d1", "d2", "body1", "body2",
             "c1", "c2", "cout", "input", "vl", "vr", "vq1", "vq2", "vd1", "vd2", "vbody1", "vbody2")
    signals = {name: [] for name in names}
    maximum_ode_residual = 0.
    diode_off_fraction = 0.
    zero = np.zeros(4)

    def affine(value, offset=0., scale=1.):
        result = value*scale
        result[0] += offset
        return result

    for solution, gate in pieces:
        for start, stop in itertools.pairwise(solution.t):
            circuit.check_cancel()
            state_coefficients = _coefficients(solution, start, stop, circuit.scale)
            _, a, b, vo = state_coefficients
            thresholds = (affine(a, -p.diode_drop_v, -1), affine(b-vo, -p.diode_drop_v),
                          affine(a, -p.vin_v-o.body_diode_drop_v), affine(b, -o.body_diode_drop_v, -1))
            crossings = sorted({0., 1., *[crossing for c in thresholds for crossing in _real_crossings(c)]})
            for left, right in itertools.pairwise(crossings):
                aphase, bphase = float(start+(stop-start)*left), float(start+(stop-start)*right)
                if not bphase > aphase:
                    continue
                # Re-evaluate the dense polynomial on each diode subinterval.
                i, a, b, vo = _coefficients(solution, aphase, bphase, circuit.scale)
                q1 = affine(a, p.vin_v, -1)/o.ron_q1_ohm if gate else zero
                q2 = b/o.ron_q2_ohm if gate else zero
                drives = (affine(a, -p.diode_drop_v, -1), affine(b-vo, -p.diode_drop_v),
                          affine(a, -p.vin_v-o.body_diode_drop_v), affine(b, -o.body_diode_drop_v, -1))
                currents = [drive/resistance if np.polynomial.polynomial.polyval(.5, drive) > 0 else zero
                            for drive, resistance in zip(drives, (o.diode_resistance_ohm, o.diode_resistance_ohm,
                                                                 o.body_diode_resistance_ohm, o.body_diode_resistance_ohm))]
                d1, d2, body1, body2 = currents
                c1 = i-d1-q1+body1
                c2 = i-q2+body2-d2
                values = {
                    "i": i, "a": a, "b": b, "vo": vo, "q1": q1, "q2": q2,
                    "d1": d1, "d2": d2, "body1": body1, "body2": body2,
                    "c1": c1, "c2": c2, "cout": affine(d2, -p.iout_a), "input": i-d1,
                    "vl": a-b-o.inductor_resistance_ohm*i, "vr": o.inductor_resistance_ohm*i,
                    "vq1": affine(a, p.vin_v, -1), "vq2": b,
                    "vd1": a, "vd2": vo-b, "vbody1": affine(a, p.vin_v, -1), "vbody2": b,
                }
                if not gate and not np.any(d1) and not np.any(d2):
                    diode_off_fraction += bphase-aphase
                for key, coefficients in values.items():
                    signals[key].append(WaveformSegment(aphase, bphase, tuple(float(x) for x in coefficients)))
                # Independent constitutive residual: displayed iC versus Cdv/dt,
                # and displayed vL versus Ldi/dt (not the KCL construction).
                for fraction in (.211324865405187, .5, .788675134594813):
                    def at(c, fraction=fraction):
                        return float(np.polynomial.polynomial.polyval(fraction, c))
                    def derivative(c, at=at, dt=(bphase-aphase)*period):
                        return at(np.polynomial.polynomial.polyder(c))/dt
                    residuals = ((at(c1)+o.coss_q1_f*derivative(a))/circuit.current_scale,
                                 (at(c2)-o.coss_q2_f*derivative(b))/circuit.current_scale,
                                 (at(values["cout"])-p.capacitance_f*derivative(vo))/circuit.current_scale,
                                 (at(values["vl"])-p.inductance_h*derivative(i))/circuit.scale[1])
                    # Tiny initial stiff-solver steps can put the change in
                    # Cout voltage below float resolution. Do not pretend
                    # that differentiating that rounded voltage is evidence.
                    roundoff = 200*np.finfo(float).eps/((bphase-aphase)*period)
                    floors = (roundoff*o.coss_q1_f*circuit.scale[1]/circuit.current_scale,
                              roundoff*o.coss_q2_f*circuit.scale[2]/circuit.current_scale,
                              roundoff*p.capacitance_f*circuit.scale[3]/circuit.current_scale,
                              roundoff*p.inductance_h*circuit.current_scale/circuit.scale[1])
                    maximum_ode_residual = float(max(maximum_ode_residual, *(max(0., abs(r)-floor) for r, floor in zip(residuals, floors))))

    def wave(key, unit, reference):
        return Waveform(period, unit, reference, tuple(signals[key]))
    devices = {
        "Q1": DeviceWaveforms("Input MOSFET channel (Coss/body separate)", wave("q1", "A", "channel drain to source; signed"), wave("vq1", "V", "VIN minus A")),
        "Q2": DeviceWaveforms("Output MOSFET channel (Coss/body separate)", wave("q2", "A", "channel drain to source; signed"), wave("vq2", "V", "B minus GND")),
        "D1": DeviceWaveforms("Input freewheel diode", wave("d1", "A", "GND to A"), wave("vd1", "V", "cathode A minus anode GND")),
        "D2": DeviceWaveforms("Output rectifier diode", wave("d2", "A", "B to OUT"), wave("vd2", "V", "cathode OUT minus anode B")),
        "Dbody1": DeviceWaveforms("Q1 antiparallel body clamp", wave("body1", "A", "A to VIN"), wave("vbody1", "V", "cathode VIN minus anode A")),
        "Dbody2": DeviceWaveforms("Q2 antiparallel body clamp", wave("body2", "A", "GND to B"), wave("vbody2", "V", "cathode B minus anode GND")),
        "Coss1": DeviceWaveforms("Q1 declared constant Coss", wave("c1", "A", "VIN to A; into positive plate"), wave("vq1", "V", "VIN minus A")),
        "Coss2": DeviceWaveforms("Q2 declared constant Coss", wave("c2", "A", "B to GND; into positive plate"), wave("vq2", "V", "B minus GND")),
        "L1": DeviceWaveforms("Energy-transfer inductance (copper separate)", wave("i", "A", "A to B"), wave("vl", "V", "ideal inductance voltage; A minus B minus copper drop")),
        "Rwinding": DeviceWaveforms("Inductor winding resistance", wave("i", "A", "A to B"), wave("vr", "V", "passive copper-drop sign")),
        "Cout": DeviceWaveforms("Dynamic output capacitor", wave("cout", "A", "into OUT positive plate"), wave("vo", "V", "OUT minus GND")),
    }
    input_current = wave("input", "A", "from VIN source, including Coss charge and returned clamp current")
    input_capacitor = input_current.affine(scale=-1., offset=input_current.statistics()["mean"], reference="into Cin positive plate")
    devices["Cin"] = DeviceWaveforms("Input capacitor on stiff source", input_capacitor,
                                     input_current.affine(scale=0., offset=p.vin_v, unit="V", reference="VIN minus GND"))
    return devices, input_current, maximum_ode_residual, diode_off_fraction


def _product_mean(left, right):
    total = 0.
    for a, b in zip(left.segments, right.segments):
        coefficients = np.polynomial.polynomial.polymul(a.coefficients, b.coefficients)
        total += a.duration_fraction*sum(c/(index+1) for index, c in enumerate(coefficients))
    return float(total)


def _stress(devices, check_cancel):
    result = {}
    for key, device in devices.items():
        check_cancel()
        result[key] = {}
        for prefix, wave, unit in (("current", device.current_a, "a"), ("voltage", device.voltage_v, "v")):
            stats = wave.statistics()
            for suffix, field in (("min", "min"), ("max", "max"), ("mean", "mean"), ("rms", "rms"), ("ac_rms", "ac_rms"), ("pp", "peak_to_peak")):
                result[key][f"{prefix}_{suffix}_{unit}"] = stats[field]
    return result


def calculate_two_switch_dynamic(inputs: DesignInputs, options: T05Options,
                                 cancelled=None) -> StageResult:
    """Solve periodic iL, VA, VB and Vout plus duty for the requested DC output."""
    from .topologies import _topology_id, _validate
    _validate(inputs)
    _validate_options(options)
    if _topology_id(inputs.topology) != "T05":
        raise ModelDomainError("T05Options can only be applied to two-switch Buck-Boost T05")
    if inputs.iout_a <= 0:
        raise ModelDomainError("T05 dynamic model requires positive load; no-load regulation is not modeled")
    if inputs.dead_time_s:
        raise ModelDomainError("T05 simultaneous gate strategy has no independently specified dead time")
    inputs = replace(inputs, two_switch_options=options)
    circuit = _Circuit(inputs, options, cancelled)
    if not np.all(np.isfinite(circuit.scale)) or not math.isfinite(circuit.period) or circuit.period <= 0:
        raise ModelDomainError("T05 derived state scales exceed finite numerical resolution")
    fastest_rc = min(options.ron_q1_ohm*options.coss_q1_f,
                     options.ron_q2_ohm*options.coss_q2_f,
                     options.diode_resistance_ohm*min(options.coss_q1_f, options.coss_q2_f),
                     options.body_diode_resistance_ohm*min(options.coss_q1_f, options.coss_q2_f),
                     options.diode_resistance_ohm*inputs.capacitance_f)
    effective_c = 1/(1/options.coss_q1_f+1/options.coss_q2_f)
    resonance_product = inputs.inductance_h*effective_c
    if fastest_rc <= 0 or effective_c <= 0 or resonance_product <= 0 or not math.isfinite(resonance_product):
        raise ModelDomainError("T05 circuit time scales exceed finite numerical resolution")
    if circuit.period/fastest_rc > 1e10 or circuit.period/math.sqrt(resonance_product) > 1e4:
        raise ModelDomainError("T05 circuit time scales exceed the declared numerical resolution domain; increase finite Coss/resistance or revise frequency")
    if inputs.iout_a*circuit.period/(inputs.capacitance_f*inputs.vout_v) < 1e-12:
        raise ModelDomainError("T05 output-capacitor charge per cycle is below the declared floating-point resolution domain")
    solved, _, pieces, error = _shoot(circuit)
    integration_tolerance = options.relative_tolerance*.0025
    fine_final, fine_pieces = circuit.cycle(solved, retain=True, tolerance=integration_tolerance)
    refinement_error = 0.
    for (coarse, _), (fine, _) in zip(pieces, fine_pieces):
        grid = np.unique(np.r_[coarse.t, fine.t, (fine.t[:-1]+fine.t[1:])/2])
        refinement_error = max(refinement_error, float(np.max(np.abs(coarse.sol(grid)-fine.sol(grid)))))
    refined_closure = float(np.max(np.abs(np.r_[fine_final[:4]-solved[:4], fine_final[4]-1.])))
    tolerance = max(1e-8, options.relative_tolerance*5)
    if max(refinement_error, refined_closure) > tolerance:
        raise ModelDomainError(f"T05 refinement/periodicity did not converge ({max(refinement_error, refined_closure):.3g})")
    devices, input_current, ode_residual, floating_fraction = _build_waveforms(circuit, fine_pieces)
    constitutive_budget = max(2e-4, options.relative_tolerance*1000)
    for _ in range(3):
        if ode_residual <= constitutive_budget:
            break
        integration_tolerance *= .1
        fine_final, fine_pieces = circuit.cycle(solved, retain=True, tolerance=integration_tolerance)
        devices, input_current, ode_residual, floating_fraction = _build_waveforms(circuit, fine_pieces)
    if ode_residual > constitutive_budget:
        raise ModelDomainError(f"T05 waveform constitutive residual exceeds its numerical budget ({ode_residual:.3g})")
    refinement_error = 0.
    for (coarse, _), (fine, _) in zip(pieces, fine_pieces):
        grid = np.unique(np.r_[coarse.t, fine.t, (fine.t[:-1]+fine.t[1:])/2])
        refinement_error = max(refinement_error, float(np.max(np.abs(coarse.sol(grid)-fine.sol(grid)))))
    refined_closure = float(np.max(np.abs(np.r_[fine_final[:4]-solved[:4], fine_final[4]-1.])))
    if max(refined_closure, refinement_error) > tolerance:
        raise ModelDomainError(f"T05 final refined periodic state did not converge ({refined_closure:.3g})")
    stress = _stress(devices, circuit.check_cancel)
    if stress["Cout"]["voltage_min_v"] <= 0:
        raise ModelDomainError("T05 solved output crosses zero; the constant-current load operating point is invalid")
    i_min = stress["L1"]["current_min_a"]
    mode = "DCM" if i_min < -1e-7*circuit.current_scale and floating_fraction > 1e-6 else ("CCM" if i_min > 1e-7*circuit.current_scale else "BCM")
    requested = inputs.mode.upper()
    if requested != "AUTO" and requested != mode and not (mode == "BCM" and requested in ("CCM", "DCM")):
        raise ModelDomainError(f"Requested {requested}, but the switched T05 operating point requires {mode}")
    losses = {key: _product_mean(device.current_a, device.voltage_v)*(-1 if key.startswith("D") else 1)
              for key, device in devices.items() if key in ("Q1", "Q2", "D1", "D2", "Dbody1", "Dbody2", "Rwinding")}
    diode_loss = sum(losses[key] for key in ("D1", "D2", "Dbody1", "Dbody2"))
    other_loss = sum(losses[key] for key in ("Q1", "Q2", "Rwinding"))
    pin = input_current.statistics()["mean"]*inputs.vin_v
    pout = stress["Cout"]["voltage_mean_v"]*inputs.iout_a
    balance = pin-pout-diode_loss-other_loss
    initial_state = solved[:4]*circuit.scale[:4]
    final_state = fine_final[:4]*circuit.scale[:4]
    def energy(state):
        i, a, b, v = state
        return .5*(inputs.inductance_h*i*i + options.coss_q1_f*(inputs.vin_v-a)**2
                   + options.coss_q2_f*b*b + inputs.capacitance_f*v*v)
    storage_change_w = float((energy(final_state)-energy(initial_state))/circuit.period)
    balance_scale = max(1., abs(pin), abs(pout))
    if abs(balance-storage_change_w) > max(3e-6, options.relative_tolerance*100)*balance_scale:
        raise ModelDomainError(f"T05 physical branch energy does not close ({balance-storage_change_w:.3g} W)")
    output_charge = devices["Cout"].current_a.statistics()["mean"]
    if abs(output_charge) > max(2e-7, options.relative_tolerance*20)*max(1., inputs.iout_a):
        raise ModelDomainError("T05 output charge does not close within its numerical budget")
    charge_errors = {}
    for key, capacitance in (("Coss1", options.coss_q1_f), ("Coss2", options.coss_q2_f), ("Cout", inputs.capacitance_f)):
        device = devices[key]
        start = float(device.voltage_v.segments[0].evaluate(0.))
        stop = float(device.voltage_v.segments[-1].evaluate(1.))
        charge_errors[f"{key.lower()}_charge_balance_c"] = device.current_a.statistics()["mean"]*circuit.period-capacitance*(stop-start)
    warnings = ("Declared constant-Coss, finite-resistance model; illustrative defaults are not measured device data.",
                "Voltage-dependent Coss, reverse recovery, gate-charge dynamics, stray inductance, thermal feedback, and control-loop/subharmonic stability are not modeled.",
                "TI executable equivalence and hardware correlation are UNKNOWN.")
    if mode == "DCM":
        warnings += ("DCM includes a rectifier-off resonant interval: inductor current may reverse instead of being clipped to zero.",)
    if stress["Cout"]["voltage_pp_v"] > .1*inputs.vout_v:
        warnings += ("Output ripple exceeds 10% of the requested DC voltage; the dynamic capacitor is included, but confirm load behavior.",)
    metrics = {
        "input_current_mean_a": pin/inputs.vin_v, "input_power_w": pin, "output_power_w": pout,
        "output_voltage_signed_v": stress["Cout"]["voltage_mean_v"], "actual_switching_frequency_hz": inputs.fsw_hz,
        "inductor_ripple_pp_a": stress["L1"]["current_pp_a"], "output_ripple_pp_v": stress["Cout"]["voltage_pp_v"],
        "modeled_diode_loss_w": diode_loss, "modeled_other_loss_w": other_loss,
        "rectifiers_off_fraction": floating_fraction, "solved_mean_output_v": stress["Cout"]["voltage_mean_v"],
        "q1_channel_loss_w": losses["Q1"], "q2_channel_loss_w": losses["Q2"],
        "body_clamp_loss_w": losses["Dbody1"]+losses["Dbody2"], "winding_loss_w": losses["Rwinding"],
        "external_rectifier_loss_w": losses["D1"]+losses["D2"],
    }
    diagnostics = {
        "periodic_state_normalized_residual": refined_closure,
        "shooting_normalized_residual": error,
        "integration_refinement_max_normalized_error": refinement_error,
        "final_integrator_relative_tolerance": integration_tolerance,
        "waveform_constitutive_max_normalized_residual": ode_residual,
        "output_capacitor_mean_current_a": output_charge,
        "modeled_power_balance_w": balance,
        "periodic_storage_energy_change_w": storage_change_w,
        "trajectory_energy_balance_w": balance-storage_change_w,
        "inductor_mean_voltage_v": devices["L1"].voltage_v.statistics()["mean"],
        "cycle_evaluations": float(circuit.evaluations),
        "waveform_segment_count": float(len(input_current.segments)),
        **charge_errors,
    }
    return StageResult(inputs=inputs, design_id=inputs.design_id, topology_id="T05", mode=mode,
                       duty=float(solved[4]), waveforms=devices, stress=stress, metrics=metrics,
                       assumptions=("Both FET channels turn on simultaneously with their declared finite Ron; gates turn off simultaneously.",
                                    "State variables are signed inductor current, both switching-node voltages and the dynamic output voltage; all four states and output target are solved periodically.",
                                    "Input is a stiff voltage source; output is a positive constant-current load.",
                                    "Q1/Q2 currents are channel-only; each Coss and antiparallel body diode has its own branch, so capacitive charging loss is already included in channel dissipation.",
                                    "External and body diodes use max(0,(forward_voltage-Vf)/R); specified external diode_drop_v applies to both D1 and D2.",
                                    "Shared piecewise polynomials are recovered from adaptive Radau dense output; means, RMS and displayed samples use those same polynomials.",
                                    "This option is a bounded independent extension; it does not replace the default ideal CCM/BCM model."),
                       warnings=warnings, diagnostics=diagnostics,
                       provenance={"model": "Four-state switched nodal KCL/KVL with constant Coss and finite conductive resistances",
                                   "physics_revision": options.model_revision, "parameter_source": options.parameter_source,
                                   "options_json": json.dumps(asdict(options), sort_keys=True, allow_nan=False),
                                   "numerical_method": "Periodic shooting; implicit Radau; at least 10x tolerance refinement; exact polynomial integrals",
                                   "evidence": "Independent circuit equations and numerical convergence; no TI parity, measured-vendor-data or hardware claim"})
