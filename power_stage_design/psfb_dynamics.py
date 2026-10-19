"""Finite-Coss PSFB: an explicitly declared, seven-state passive circuit.

This independent enhancement is not the TI Power Stage Designer implementation.
A constant-capacitance MOSFET model and constant-Ron bidirectional channels are
used. Every capacitance and diode slope resistance is explicit. The transformer
shunt capacitance and rectifier-node capacitance make diode commutation a real
continuous circuit, rather than projecting/clamping inductor currents.

States are (Va, Vb, Ip, Im, Vp, Vr, Io): two bridge nodes, leakage current,
magnetizing current, transformer primary voltage, rectified node, and Lo current.
The center-tapped secondary has Np/Ns=n for EACH secondary half. The output is
stiff at the requested Vout; output ripple is reconstructed, not fed back.

Electrical mapping: Llk runs A -> P; Vp=P-B. Lm and Cprimary span P-B.
The center tap is ISO; secondary end voltages are +/-Vp/n relative to ISO.
D1/D2 run each secondary end -> RECT; Crectifier spans RECT-ISO and Lo runs
RECT -> Vout. Q1/Q3 run VIN+ -> A/B, Q2/Q4 run A/B -> GND; each body diode
runs source -> drain and each Coss has drain-to-source current/voltage reference.

The independently tested power identity is
  dE/dt = Vin*Isource - Vout*Io - sum(Ron*Ichannel**2)
          - sum((Vfbody+Rbody*Ibody)*Ibody)
          - sum((Vfsecondary+Rsecondary*Irectifier)*Irectifier).
Here E contains all seven dynamic states' capacitor/inductor stored energies;
Isource=Ichannel1+Ichannel3-Ibody1-Ibody3+Icoss1+Icoss3. Numerical waveform
charge/flux and power residuals are checked before returning a result.
"""
from __future__ import annotations

import itertools
import json
import math
import threading
from dataclasses import asdict, dataclass, replace
from numbers import Real

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares

from .models import DesignInputs, DeviceWaveforms, ModelDomainError, StageResult
from .waveforms import Waveform, WaveformSegment

MODEL_VERSION = "psfb-commutation-1"
_LSODA_LOCK = threading.Lock()


@dataclass(frozen=True)
class PSFBOptions:
    """All component values and their source are required; only numerics default."""

    coss_f: float  # each of Q1...Q4, constant over all voltages
    r_on_ohm: float
    body_diode_drop_v: float
    body_diode_resistance_ohm: float
    transformer_capacitance_f: float  # across the primary winding
    rectifier_capacitance_f: float  # rectified node to secondary center tap
    rectifier_resistance_ohm: float  # each secondary diode forward slope
    parameter_source: str
    relative_tolerance: float = 1e-8
    max_nfev: int = 80
    zvs_voltage_tolerance_v: float = 1.0
    model_revision: str = MODEL_VERSION


    def __post_init__(self):
        if not isinstance(self.parameter_source, str) or not self.parameter_source.strip():
            raise ModelDomainError("PSFB parameter_source must describe the component assumptions or source")
        if self.model_revision != MODEL_VERSION:
            raise ModelDomainError("Unsupported PSFB commutation model_revision")
        for name, value in asdict(self).items():
            if name in ("parameter_source", "model_revision"):
                continue
            if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
                raise ModelDomainError(f"PSFB {name} must be a finite real scalar")
            if name in ("body_diode_drop_v", "zvs_voltage_tolerance_v"):
                if value < 0:
                    raise ModelDomainError(f"PSFB {name} must be nonnegative")
            elif value <= 0:
                raise ModelDomainError(f"PSFB {name} must be positive")
        if not 1e-11 <= self.relative_tolerance <= 1e-6:
            raise ModelDomainError("PSFB relative_tolerance must be between 1e-11 and 1e-6")
        if not isinstance(self.max_nfev, int) or not 10 <= self.max_nfev <= 500:
            raise ModelDomainError("PSFB max_nfev must be an integer between 10 and 500")


def _validate(inputs, options):
    from .topologies import _topology_id
    from .topologies import _validate as validate_inputs
    validate_inputs(inputs)
    if _topology_id(inputs.topology) != "T18":
        raise ModelDomainError("PSFB dynamic model requires topology T18")
    if not isinstance(options, PSFBOptions):
        raise ModelDomainError("PSFB options must be a PSFBOptions instance")
    if inputs.magnetizing_inductance_h is None or inputs.leakage_inductance_h is None:
        raise ModelDomainError("PSFB dynamics require explicit Lm and Llk")
    if inputs.iout_a <= 0 or inputs.mode.casefold() not in ("auto", "ccm"):
        raise ModelDomainError("Finite-Coss PSFB currently supports positive-load CCM only")
    if not 0 < inputs.dead_time_s * inputs.fsw_hz < .25:
        raise ModelDomainError("PSFB dynamic dead time must be positive and less than T/4")
    fastest_rc = min(2*options.coss_f*options.r_on_ohm,
                     2*options.coss_f*options.body_diode_resistance_ohm,
                     options.transformer_capacitance_f*inputs.turns_ratio_np_ns**2*options.rectifier_resistance_ohm/2,
                     options.rectifier_capacitance_f*options.rectifier_resistance_ohm/2)
    if not math.isfinite(fastest_rc) or fastest_rc*inputs.fsw_hz < 1e-12:
        raise ModelDomainError("PSFB component RC time constant is below the numerical domain of 1e-12 switching periods")
    if options.zvs_voltage_tolerance_v >= .05 * inputs.vin_v:
        raise ModelDomainError("ZVS voltage tolerance must be less than 5% of Vin")


class _Circuit:
    """Only this circuit's explicit constitutive laws, all in SI units."""

    def __init__(self, inputs, options, cancelled=None):
        self.p, self.o, self.cancelled = inputs, options, cancelled
        self.half = .5 / inputs.fsw_hz
        self.dead = inputs.dead_time_s / self.half
        ipscale = max(inputs.iout_a / inputs.turns_ratio_np_ns,
                      inputs.vin_v * self.half / inputs.magnetizing_inductance_h)
        self.scale = np.array([inputs.vin_v, inputs.vin_v, ipscale, ipscale,
                               inputs.vin_v, inputs.vout_v, inputs.iout_a])

    def check_cancelled(self):
        if self.cancelled is not None and self.cancelled():
            raise InterruptedError("PSFB dynamic calculation cancelled")

    def branches(self, y, gates):
        va, vb, _ip, _im, vp, vr, _io = y
        p, o = self.p, self.o
        vds = np.array([p.vin_v-va, va, p.vin_v-vb, vb])
        channels = np.asarray(gates) * vds / o.r_on_ohm
        body = np.maximum(-vds-o.body_diode_drop_v, 0.) / o.body_diode_resistance_ohm
        sec_v = np.array([vp/p.turns_ratio_np_ns-vr, -vp/p.turns_ratio_np_ns-vr])
        sec_i = np.maximum(sec_v-p.diode_drop_v, 0.) / o.rectifier_resistance_ohm
        return vds, channels, body, sec_v, sec_i

    def derivative(self, y, gates):
        va, vb, ip, im, vp, vr, io = y
        p, o = self.p, self.o
        _, channel, body, _, diodes = self.branches(y, gates)
        cond = channel-body
        return np.array([
            (cond[0]-cond[1]-ip)/(2*o.coss_f),
            (cond[2]-cond[3]+ip)/(2*o.coss_f),
            (va-vb-vp)/p.leakage_inductance_h,
            vp/p.magnetizing_inductance_h,
            (ip-im-(diodes[0]-diodes[1])/p.turns_ratio_np_ns)/o.transformer_capacitance_f,
            (diodes.sum()-io)/o.rectifier_capacitance_f,
            (vr-p.vout_v)/p.inductance_h,
        ])

    def energy(self, y):
        va, vb, ip, im, vp, vr, io = y
        p, o = self.p, self.o
        return .5*(o.coss_f*((p.vin_v-va)**2+va**2+(p.vin_v-vb)**2+vb**2)
                   +p.leakage_inductance_h*ip**2+p.magnetizing_inductance_h*im**2
                   +o.transformer_capacitance_f*vp**2+o.rectifier_capacitance_f*vr**2
                   +p.inductance_h*io**2)

    def integrate(self, initial, duty, *, dense=False):
        self.check_cancelled()
        edges = (0., self.dead, duty, duty+self.dead, 1.)
        gates = ((0, 0, 0, 1), (1, 0, 0, 1), (1, 0, 0, 0), (1, 0, 1, 0))
        state = np.r_[np.asarray(initial)/self.scale, 0.]
        parts = []
        for left, right, gate in zip(edges, edges[1:], gates):
            def rhs(_t, z, gate=gate):
                self.check_cancelled()
                return np.r_[self.half*self.derivative(z[:7]*self.scale, gate)/self.scale, z[6]]
            # SciPy's legacy LSODA handle is process-global. Serialize calls
            # while retaining cancellation during lock waits and inside RHS.
            while not _LSODA_LOCK.acquire(timeout=.05):
                self.check_cancelled()
            try:
                result = solve_ivp(rhs, (left, right), state, method="LSODA",
                                   rtol=self.o.relative_tolerance,
                                   atol=self.o.relative_tolerance*1e-3,
                                   max_step=.04, dense_output=dense)
            except (ValueError, FloatingPointError, OverflowError) as exc:
                raise ModelDomainError(f"PSFB numerical integration is outside its domain: {exc}") from exc
            finally:
                _LSODA_LOCK.release()
            if not result.success or not np.isfinite(result.y).all():
                raise ModelDomainError(f"PSFB stiff integration failed: {result.message}")
            state = result.y[:, -1]
            if dense:
                parts.append((left, right, gate, result))
        return state[:7]*self.scale, state[7]*self.p.iout_a, parts


def _shoot(circuit):
    """Solve half-period symmetry AND requested mean Lo current, including phase."""
    from .topologies import _phase_shifted_bridge
    p = circuit.p
    ideal = _phase_shifted_bridge(replace(p, dead_time_s=0.))
    if ideal.mode != "CCM":
        raise ModelDomainError("Finite-Coss PSFB initial operating point is not CCM; use the ideal DCM/BCM model")
    first = lambda key: ideal.waveforms[key].current_a.segments[0].evaluate(0.)
    lastv = ideal.waveforms["Lm"].voltage_v.segments[-1].evaluate(1.)
    initial = np.array([0., 0., first("Lleak"), first("Lm"), lastv,
                        abs(lastv)/p.turns_ratio_np_ns-p.diode_drop_v,
                        first("L1")])
    lower_d, upper_d = circuit.dead+1e-6, 1-circuit.dead-1e-6
    if not lower_d < ideal.duty < upper_d:
        raise ModelDomainError("Requested PSFB output requires phase outside separated dead-time windows")
    guess = np.r_[initial/circuit.scale, ideal.duty]
    sign = np.array([-1., -1., -1., -1., -1., 1., 1.])
    offset = np.array([p.vin_v, p.vin_v, 0., 0., 0., 0., 0.])

    def residual(z):
        terminal, mean, _ = circuit.integrate(z[:7]*circuit.scale, z[7])
        target = sign*z[:7]*circuit.scale+offset
        return np.r_[(terminal-target)/circuit.scale, (mean-p.iout_a)/p.iout_a]

    def jacobian(z):
        # Absolute normalized perturbations are essential: bridge-node initial
        # values are close to zero, where relative finite differences fall
        # beneath the ODE's error floor and can manufacture a singular Jacobian.
        base = residual(z)
        columns = []
        for index in range(8):
            step = 2e-4 if index < 7 else 1e-5
            if index == 7 and z[index]+step >= upper_d:
                step = -step
            shifted = z.copy()
            shifted[index] += step
            columns.append((residual(shifted)-base)/step)
        return np.asarray(columns).T

    solved = least_squares(residual, guess, jac=jacobian,
                           bounds=(np.r_[np.full(7, -np.inf), lower_d],
                                   np.r_[np.full(7, np.inf), upper_d]),
                           xtol=1e-11, ftol=1e-11, gtol=1e-11,
                           max_nfev=circuit.o.max_nfev)
    error = float(np.max(np.abs(residual(solved.x))))
    if not solved.success or error > max(2e-8, 5*circuit.o.relative_tolerance):
        raise ModelDomainError(f"PSFB periodic/load/phase solve did not close (normalized residual {error:.3g})")
    half_flow = solved.jac[:7,:7]+np.diag(sign)
    # Fixed-gate orbital stability, not closed-loop control stability. The
    # second half is the sign-conjugate first-half map for the balanced circuit.
    full_flow = np.diag(sign)@half_flow@np.diag(sign)@half_flow
    radius = float(np.max(np.abs(np.linalg.eigvals(full_flow))))
    if not math.isfinite(radius) or radius >= 1.:
        raise ModelDomainError(f"PSFB fixed-gate periodic orbit is unstable or numerically unresolved (spectral radius {radius:.6g})")
    circuit.stability_radius = radius
    return solved.x[:7]*circuit.scale, float(solved.x[7]), error


def calculate_psfb_dynamic(inputs: DesignInputs, options: PSFBOptions,
                           cancelled=None) -> StageResult:
    """Solve the declared finite-Coss circuit; no fallback to the ideal model."""
    inputs = replace(inputs, psfb_options=options)
    _validate(inputs, options)
    circuit = _Circuit(inputs, options, cancelled)
    initial, duty, error = _shoot(circuit)
    terminal, mean, parts = circuit.integrate(initial, duty, dense=True)
    return _make_result(circuit, initial, terminal, duty, mean, parts, error)


def _fit_state(solution, left, right, scales, *, single_step=True):
    """Recover dense-output polynomials through public evaluation, with a check.

    LSODA's dense extension has order <= 12. No private SciPy coefficients are
    consumed. A withheld grid verifies the interpolation before it is used.
    """
    probes = np.linspace(.037, .963, 17)
    truth = solution(left+(right-left)*probes)[:7].T*scales
    for degree in ((3, 5, 7, 12) if single_step else (5, 7)):
        z = np.cos(np.arange(degree+1)*math.pi/degree)
        u = (z+1)/2
        values = solution(left+(right-left)*u)[:7].T*scales
        cheb = np.polynomial.chebyshev.chebfit(z, values, degree)
        coeff = []
        for index in range(7):
            polynomial = np.polynomial.Chebyshev(cheb[:, index], domain=[0., 1.]).convert(kind=np.polynomial.Polynomial)
            coeff.append(polynomial.coef)
        matrix = np.asarray(coeff)
        check = np.polynomial.polynomial.polyval(probes, matrix.T).T
        if np.max(np.abs(check-truth)/scales) < 2e-11:
            return matrix
    if not single_step:
        return None
    raise ModelDomainError("PSFB dense-output polynomial reconstruction failed its independent sample check")


def _compose(coefficients, left, right):
    """Restrict a polynomial in u to u=left+(right-left)*v."""
    return np.polynomial.Polynomial(coefficients)(np.polynomial.Polynomial([left, right-left])).coef


def _roots_inside(coefficients):
    roots = np.polynomial.polynomial.polyroots(coefficients)
    return [float(root.real) for root in roots if abs(root.imag) < 1e-8 and 1e-9 < root.real < 1-1e-9]


def _dense_blocks(result, scales):
    """Coalesce smooth solver steps only after withheld-point error checks."""
    def block(first, last):
        left, right = result.t[first], result.t[last]
        coefficients = _fit_state(result.sol,left,right,scales,single_step=last-first == 1)
        if coefficients is not None:
            # Include original solver-step boundaries to reject an aliased fit.
            u=(result.t[first:last+1]-left)/(right-left)
            truth=result.sol(result.t[first:last+1])[:7]*scales[:,None]
            predicted=np.polynomial.polynomial.polyval(u,coefficients.T)
            if np.max(np.abs(predicted-truth)/scales[:,None]) < 2e-11:
                yield left,right,coefficients
                return
        if last-first == 1:
            raise ModelDomainError("PSFB polynomial interval failed solver-boundary verification")
        middle=(first+last)//2
        yield from block(first,middle)
        yield from block(middle,last)
    for first in range(0,len(result.t)-1,8):
        yield from block(first,min(first+8,len(result.t)-1))


def _state_pieces(circuit, parts):
    p, o = circuit.p, circuit.o
    for _, _, gates, result in parts:
        for left, right, coeff in _dense_blocks(result, circuit.scale):
            va, vb, _, _, vp, vr, _ = coeff
            # Split at all body/rectifier conducting-state changes. Then every
            # constitutive branch is a polynomial over each retained interval.
            thresholds = [va.copy(), -va.copy(), vb.copy(), -vb.copy(),
                          vp/p.turns_ratio_np_ns-vr, -vp/p.turns_ratio_np_ns-vr]
            thresholds[0][0] -= p.vin_v+o.body_diode_drop_v
            thresholds[1][0] -= o.body_diode_drop_v
            thresholds[2][0] -= p.vin_v+o.body_diode_drop_v
            thresholds[3][0] -= o.body_diode_drop_v
            thresholds[4][0] -= p.diode_drop_v
            thresholds[5][0] -= p.diode_drop_v
            roots = sorted({0., 1., *(r for threshold in thresholds for r in _roots_inside(threshold))})
            for a, b in itertools.pairwise(roots):
                if b-a <= 1e-13:
                    continue
                restricted = [c.copy() for c in coeff] if a == 0. and b == 1. else [_compose(c, a, b) for c in coeff]
                yield .5*(left+(right-left)*a), .5*(left+(right-left)*b), restricted, gates


def _trim(coefficients):
    coefficients = np.asarray(coefficients, dtype=float)
    last = len(coefficients)
    tolerance = max(1., float(np.max(np.abs(coefficients))))*2e-13
    while last > 1 and abs(coefficients[last-1]) < tolerance:
        last -= 1
    return tuple(float(value) for value in coefficients[:last])


def _mean_product(left: Waveform, right: Waveform) -> float:
    return math.fsum((a.end_fraction-a.start_fraction)*sum(value/(i+1) for i, value in enumerate(
        np.polynomial.polynomial.polymul(a.coefficients, b.coefficients)))
        for a, b in zip(left.segments, right.segments))


def _numerical_capacitor_voltage(current, capacitance, voltage, tolerance):
    """Integrate actual current without shifting it; retain numerical closure error."""
    mean = math.fsum(segment.integral() for segment in current.segments)
    if abs(mean) > tolerance:
        raise ValueError(f"mean current {mean:.6g} A exceeds numerical tolerance {tolerance:.6g} A")
    segments, value = [], 0.
    for segment in current.segments:
        factor = current.period_s*segment.duration_fraction/capacitance
        coefficients = (value, *(factor*c/(k+1) for k,c in enumerate(segment.coefficients)))
        integrated = WaveformSegment(segment.start_fraction, segment.end_fraction, coefficients)
        segments.append(integrated)
        value = float(integrated.evaluate(1.))
    result = Waveform(current.period_s, "V", "output capacitor positive to return", tuple(segments))
    return result.affine(offset=voltage-math.fsum(s.integral() for s in segments))


def _make_result(circuit, initial, terminal, duty, mean, parts, shooting_error):
    p, o = circuit.p, circuit.o
    period = 1/p.fsw_hz
    pieces = list(_state_pieces(circuit, parts))
    all_pieces = list(pieces)
    for left, right, coeff, gates in pieces:
        mirrored = [(-np.asarray(c) if index < 5 else np.asarray(c).copy()) for index, c in enumerate(coeff)]
        mirrored[0][0] += p.vin_v
        mirrored[1][0] += p.vin_v
        all_pieces.append((left+.5, right+.5, mirrored, (gates[1], gates[0], gates[3], gates[2])))
    segments = {}
    labels = {}
    units = {}
    references = {}
    max_kcl = 0.
    max_ode = 0.
    circulating_square = 0.

    def append(key, label, current, voltage, left, right,
               current_ref="passive branch current", voltage_ref="passive branch voltage"):
        labels[key] = label
        for field, values, unit, ref in (("i", current, "A", current_ref), ("v", voltage, "V", voltage_ref)):
            name = (key, field)
            segments.setdefault(name, []).append(WaveformSegment(float(left), float(right), _trim(values)))
            units[name], references[name] = unit, ref

    for left, right, coeff, gates in all_pieces:
        circuit.check_cancelled()
        # Pad to common order for direct circuit algebra.
        width = max(len(c) for c in coeff)
        va, vb, ip, im, vp, vr, io = [np.pad(c, (0, width-len(c))) for c in coeff]
        if (gates[0] and gates[2]) or (gates[1] and gates[3]):
            squared = np.polynomial.polynomial.polymul(ip, ip)
            circulating_square += (right-left)*sum(c/(k+1) for k,c in enumerate(squared))
        zero = np.zeros(width)
        const = lambda value, width=width: np.r_[float(value), np.zeros(width-1)]
        plus = lambda a, b: np.polynomial.polynomial.polyadd(a, b)
        vds = (const(p.vin_v)-va, va, const(p.vin_v)-vb, vb)
        channel = [g*v/o.r_on_ohm for g, v in zip(gates, vds)]
        body = []
        for voltage in vds:
            threshold = -voltage-const(o.body_diode_drop_v)
            body.append(threshold/o.body_diode_resistance_ohm
                        if np.polynomial.polynomial.polyval(.5, threshold) > 0 else zero.copy())
        sec_voltage = (vp/p.turns_ratio_np_ns-vr, -vp/p.turns_ratio_np_ns-vr)
        sec = []
        for voltage in sec_voltage:
            threshold = voltage-const(p.diode_drop_v)
            sec.append(threshold/o.rectifier_resistance_ohm
                       if np.polynomial.polynomial.polyval(.5, threshold) > 0 else zero.copy())
        duration = (right-left)*period
        deriv = lambda c, duration=duration: np.polynomial.polynomial.polyder(c)/duration
        # Use the ODE's physical branch currents, not differentiation of a
        # dense interpolant across very fast RC layers. Their integral-versus-
        # voltage constitutive residual is checked independently below.
        node_current = (channel[0]-body[0]-channel[1]+body[1]-ip,
                        channel[2]-body[2]-channel[3]+body[3]+ip)
        coss = [-node_current[0]/2, node_current[0]/2,
                -node_current[1]/2, node_current[1]/2]
        cp_current = ip-im-(sec[0]-sec[1])/p.turns_ratio_np_ns
        cr_current = sec[0]+sec[1]-io
        source = plus(channel[0]-body[0]+channel[2]-body[2], coss[0]+coss[2])
        winding = im+(sec[0]-sec[1])/p.turns_ratio_np_ns
        append("L1", "Output inductor", io, vr-const(p.vout_v), left, right, "RECT to OUT", "RECT minus OUT")
        append("Lleak", "Primary leakage/series inductance", ip, va-vb-vp, left, right, "A to P", "A minus P")
        append("Lm", "Primary magnetizing inductance", im, vp, left, right, "P to B (magnetizing state)", "P minus B")
        append("primary", "Transformer winding (excludes shunt capacitance)", winding, vp, left, right,
               "P to B (into primary dot; excludes shunt capacitance)", "P minus B")
        append("secondary1", "Positive secondary half", sec[0], vp/p.turns_ratio_np_ns, left, right,
               "ISO to S1 (center tap to upper outer terminal)", "S1 minus ISO")
        append("secondary2", "Negative secondary half", sec[1], -vp/p.turns_ratio_np_ns, left, right,
               "ISO to S2 (center tap to lower outer terminal)", "S2 minus ISO")
        for k in range(4):
            append(f"Q{k+1}", f"Q{k+1} bidirectional channel", channel[k], vds[k], left, right,
                   "drain to source; channel only, excludes Coss/body diode", "drain to source")
            append(f"Dbody{k+1}", f"Q{k+1} body diode", body[k], vds[k], left, right,
                   "source to drain (anode to cathode)", "cathode to anode")
            append(f"Coss{k+1}", f"Q{k+1} constant output capacitance", coss[k], vds[k], left, right,
                   "drain to source displacement current", "drain to source")
        for k in range(2):
            append(f"D{k+1}", f"Secondary rectifier {k+1}", sec[k], -sec_voltage[k], left, right,
                   "anode to cathode", "cathode to anode")
        append("Cprimary", "Declared transformer shunt capacitance", cp_current, vp, left, right, "P to B", "P minus B")
        append("Crectifier", "Declared rectified-node capacitance", cr_current, vr, left, right, "RECT to ISO", "RECT minus ISO")
        append("node_A", "Bridge node A (ideal high-impedance voltage probe)", zero, va, left, right,
               "ideal voltage probe draws zero current", "node A to DC return")
        append("node_B", "Bridge node B (ideal high-impedance voltage probe)", zero, vb, left, right,
               "ideal voltage probe draws zero current", "node B to DC return")
        append("source", "DC source (probe)", source, const(p.vin_v), left, right,
               "from positive DC rail, including Coss displacement")
        # A pointwise differential check is separate from mean power closure.
        for u in (.2113248654, .5, .7886751346):
            state = np.array([np.polynomial.polynomial.polyval(u, c) for c in (va,vb,ip,im,vp,vr,io)])
            derivative = np.array([np.polynomial.polynomial.polyval(u, deriv(c)) for c in (va,vb,ip,im,vp,vr,io)])
            rhs = circuit.derivative(state, gates)
            max_ode = max(max_ode, float(np.max(np.abs(derivative-rhs)/(circuit.scale/circuit.half+np.abs(rhs)))))
            cond = np.array([np.polynomial.polynomial.polyval(u, channel[k]-body[k]) for k in range(4)])
            kcl = np.array([cond[0]-cond[1]-state[2]-2*o.coss_f*derivative[0],
                            cond[2]-cond[3]+state[2]-2*o.coss_f*derivative[1]])
            max_kcl = max(max_kcl, float(np.max(np.abs(kcl))))
    waves = {name: Waveform(period, units[name], references[name], tuple(items)) for name, items in segments.items()}
    devices = {key: DeviceWaveforms(labels[key], waves[(key,"i")], waves[(key,"v")]) for key in labels if key != "source"}
    for k in range(1,5):
        for prefix in ("Dbody", "Coss"):
            key=f"{prefix}{k}"
            devices[key]=replace(devices[key],voltage_v=replace(devices[f"Q{k}"].voltage_v, reference=devices[key].voltage_v.reference))
    for key in ("primary", "Cprimary"):
        devices[key]=replace(devices[key],voltage_v=replace(devices["Lm"].voltage_v, reference=devices[key].voltage_v.reference))
    for key, other in (("node_A","Q2"),("node_B","Q4")):
        devices[key]=replace(devices[key],voltage_v=replace(devices[other].voltage_v, reference=devices[key].voltage_v.reference))
    for k in (1,2):
        devices[f"secondary{k}"]=replace(devices[f"secondary{k}"],current_a=replace(devices[f"D{k}"].current_a, reference=devices[f"secondary{k}"].current_a.reference))
    source = waves[("source", "i")]
    output = devices["L1"].current_a
    actual_mean = math.fsum(segment.integral() for segment in output.segments)
    cap_current = output.affine(offset=-p.iout_a, reference="OUT to ISO (into Cout positive plate)")
    try:
        cap_voltage = _numerical_capacitor_voltage(cap_current, p.capacitance_f, p.vout_v,
                                                  max(2e-8, 20*o.relative_tolerance)*max(1., p.iout_a))
    except ValueError as exc:
        raise ModelDomainError(f"PSFB output charge does not close: {exc}") from exc
    devices["Cout"] = DeviceWaveforms("Output capacitor (ripple reconstruction)", cap_current,
                                      replace(cap_voltage, reference="OUT minus ISO"))
    devices["Cin"] = DeviceWaveforms("Stiff DC bus input capacitor", source.affine(scale=-1, offset=math.fsum(segment.integral() for segment in source.segments),
                                      reference="VIN to GND (into Cin positive plate)"),
                                      source.affine(scale=0, offset=p.vin_v, unit="V", reference="VIN minus GND"))
    channel_loss = sum(_mean_product(devices[f"Q{k}"].current_a, devices[f"Q{k}"].voltage_v) for k in range(1,5))
    body_loss = -sum(_mean_product(devices[f"Dbody{k}"].current_a, devices[f"Dbody{k}"].voltage_v) for k in range(1,5))
    rectifier_loss = -sum(_mean_product(devices[f"D{k}"].current_a, devices[f"D{k}"].voltage_v) for k in (1,2))
    pin = p.vin_v*math.fsum(segment.integral() for segment in source.segments)
    pout = p.vout_v*p.iout_a
    balance = pin-pout-channel_loss-body_loss-rectifier_loss
    tolerance = max(2e-6, 200*o.relative_tolerance)
    if abs(balance) > tolerance*max(1., pin, pout):
        raise ModelDomainError(f"PSFB energy/power balance failed ({balance:.6g} W)")
    output_stats, cap_stats = output.statistics(), cap_voltage.statistics()
    if output_stats["min"] <= 0:
        raise ModelDomainError("Finite-Coss PSFB output current reaches zero; dynamic DCM/BCM is not yet qualified")
    if cap_stats["min"] <= 0:
        raise ModelDomainError("PSFB reconstructed output voltage reaches zero; stiff-output model is invalid")
    diagnostics = {
        "output_capacitor_mean_current_a": actual_mean-p.iout_a,
        "shooting_mean_output_current_a": float(mean),
        "displayed_mean_current_interpolation_residual_a": actual_mean-float(mean),
        "modeled_power_balance_w": balance,
        "periodic_shooting_normalized_residual": shooting_error,
        "fixed_gate_orbit_spectral_radius": circuit.stability_radius,
        "half_period_stored_energy_residual_j": float(circuit.energy(terminal)-circuit.energy(initial)),
        "dense_output_ode_relative_defect": max_ode,
        "dense_derivative_bridge_kcl_defect_a": max_kcl,
        "inductor_mean_voltage_v": math.fsum(s.integral() for s in devices["L1"].voltage_v.segments),
        "transformer_mean_voltage_v": math.fsum(s.integral() for s in devices["Lm"].voltage_v.segments),
        "leakage_mean_voltage_v": math.fsum(s.integral() for s in devices["Lleak"].voltage_v.segments),
    }
    constitutive = {}
    for key, parameter, capacitor in (
        *((f"Coss{k}",o.coss_f,True) for k in range(1,5)),
        ("Cprimary",o.transformer_capacitance_f,True),
        ("Crectifier",o.rectifier_capacitance_f,True),
        ("Lleak",p.leakage_inductance_h,False),
        ("Lm",p.magnetizing_inductance_h,False),
        ("L1",p.inductance_h,False),
    ):
        device = devices[key]
        integrated = device.current_a if capacitor else device.voltage_v
        state = device.voltage_v if capacitor else device.current_a
        start = state.segments[0].evaluate(0.)
        accumulated, residual = 0., 0.
        for current_segment, state_segment in zip(integrated.segments,state.segments):
            accumulated += period*current_segment.integral()
            residual = max(residual,abs(accumulated-parameter*(state_segment.evaluate(1.)-start)))
        normalization = parameter*max(1.,p.vin_v if capacitor else p.iout_a)
        constitutive[key] = float(residual/normalization)
        diagnostics[f"{key}_constitutive_relative_residual"] = constitutive[key]
    if max(constitutive.values()) > max(2e-5,1000*o.relative_tolerance):
        raise ModelDomainError("PSFB displayed branch charge/flux constitutive residual exceeds numerical tolerance")
    metrics = {
        "phase_shift_deg": 180*duty, "dead_time_s": p.dead_time_s,
        "circulating_current_rms_a": math.sqrt(circulating_square),
        "per_switch_duty": .5-p.dead_time_s*p.fsw_hz,
        "actual_switching_frequency_hz": p.fsw_hz,
        "rectified_ripple_frequency_hz": 2*p.fsw_hz,
        "input_current_mean_a": math.fsum(segment.integral() for segment in source.segments),
        "input_power_w": pin, "output_power_w": pout,
        "output_voltage_signed_v": p.vout_v,
        "modeled_channel_loss_w": channel_loss,
        "modeled_body_diode_loss_w": body_loss,
        "modeled_rectifier_loss_w": rectifier_loss,
        "modeled_diode_loss_w": body_loss+rectifier_loss,
        "modeled_other_loss_w": channel_loss,
        "output_ripple_pp_v": cap_stats["peak_to_peak"],
        "inductor_ripple_pp_a": output_stats["peak_to_peak"],
        "zvs_voltage_tolerance_v": o.zvs_voltage_tolerance_v,
    }
    for part_index, switch, gate_time in ((0, 1, circuit.dead), (2, 3, duty+circuit.dead)):
        part = parts[part_index]
        state = part[3].sol(gate_time)[:7]*circuit.scale
        vds, _, body, _, _ = circuit.branches(state, part[2])
        rhs = circuit.derivative(state, part[2])
        index = switch-1
        voltage = float(vds[index])
        start_state = part[3].sol(part[0])[:7]*circuit.scale
        start_vds = float(circuit.branches(start_state,part[2])[0][index])
        falling = -rhs[0 if switch == 1 else 1] <= 0.
        qualified = voltage <= o.zvs_voltage_tolerance_v and (body[index] > 0 or falling)
        for q in (switch, switch+1):
            metrics[f"Q{q}_gate_on_time_s"] = gate_time*circuit.half+(0 if q == switch else circuit.half)
            metrics[f"Q{q}_gate_on_vds_v"] = voltage
            metrics[f"Q{q}_dead_time_s"] = p.dead_time_s
            metrics[f"Q{q}_dead_time_start_vds_v"] = start_vds
            metrics[f"Q{q}_dead_time_incoming_coss_charge_c"] = o.coss_f*(voltage-start_vds)
            metrics[f"Q{q}_gate_on_primary_current_a"] = float(state[2])*(1 if q == switch else -1)
            metrics[f"Q{q}_gate_on_vds_slew_v_per_s"] = float(-rhs[0 if switch == 1 else 1])
            metrics[f"Q{q}_commutation_start_leakage_energy_j"] = .5*p.leakage_inductance_h*float(start_state[2])**2
            metrics[f"Q{q}_gate_on_body_current_a"] = float(body[index])
            metrics[f"Q{q}_gate_on_coss_energy_j"] = .5*o.coss_f*voltage**2
            metrics[f"Q{q}_gate_on_residual_coss_energy_j"] = .5*o.coss_f*max(voltage,0.)**2
            metrics[f"Q{q}_zvs_qualified"] = float(qualified)
    metrics["all_switches_zvs_qualified"] = float(all(metrics[f"Q{k}_zvs_qualified"] for k in range(1,5)))
    stresses = {}
    statistic_cache = {id(output):output_stats,id(cap_voltage):cap_stats}
    def stats(wave):
        if id(wave) not in statistic_cache:
            statistic_cache[id(wave)]=wave.statistics()
        return statistic_cache[id(wave)]
    for key, device in devices.items():
        circuit.check_cancelled()
        current, voltage = stats(device.current_a), stats(device.voltage_v)
        stresses[key] = {
            **{f"current_{suffix}_a": current[field] for suffix,field in (("min","min"),("max","max"),("mean","mean"),("rms","rms"),("ac_rms","ac_rms"),("pp","peak_to_peak"))},
            **{f"voltage_{suffix}_v": voltage[field] for suffix,field in (("min","min"),("max","max"),("mean","mean"),("rms","rms"),("ac_rms","ac_rms"),("pp","peak_to_peak"))},
        }
    warnings = ("Constant-Coss, instantaneous-gate, finite-Ron circuit model; nonlinear charge, reverse recovery, gate-driver dynamics, thermal/core/winding losses are not modeled.",
                "ZVS qualification is model- and tolerance-specific evidence at gate-on; it is not hardware validation.",
                "Hard-turn-on current peaks depend strongly on the instantaneous-gate assumption; package/rail inductance and gate slew are omitted.")
    if not metrics["all_switches_zvs_qualified"]:
        warnings += ("At least one bridge leg does not satisfy the declared gate-on ZVS criterion.",)
    if metrics["output_ripple_pp_v"] > .1*p.vout_v:
        warnings += ("Output ripple exceeds 10% of Vout; the stiff-output approximation may be inaccurate.",)
    return StageResult(
        inputs=p, design_id=p.design_id,
        topology_id="T18", mode="CCM", duty=duty, waveforms=devices, stress=stresses,
        metrics=metrics, diagnostics=diagnostics, warnings=warnings,
        assumptions=(
            "Independent seven-state periodic circuit with constant per-switch Coss, bidirectional Ron channels and passive finite-slope body/secondary diodes.",
            "Explicit transformer shunt capacitance and rectified-node capacitance are physical assumptions, not numerical clamps.",
            "Center-tapped secondary; Np/Ns is measured to each half winding. Input and output are stiff DC voltages; Cout ripple is reconstructed.",
            "Phase shift and initial states are solved together for half-period symmetry and requested mean output current. Dynamic qualification is CCM only; fixed-gate orbital stability is checked separately from any controller.",
            "Q1...Q4 currents are channel-only. Body-diode and Coss displacement currents are separate branches; node_A/node_B are probes, not additional components.",
            "Gate-on ZVS requires Vds <= the declared tolerance with body-diode conduction or nonincreasing Vds immediately before gate-on. Positive-Vds residual Coss energy and actual capacitor energy are reported separately; neither is an independently added loss.",
            "Reported integrals and RMS use the complete displayed numerical polynomial period, including dead-time and resonant intervals.",
        ),
        provenance={"model": "Independent finite-Coss PSFB ODE and periodic shooting",
                    "model_revision": MODEL_VERSION, "parameter_source": o.parameter_source,
                    "options_json": json.dumps(asdict(o), sort_keys=True, allow_nan=False),
                    "reference_url": "https://www.ti.com/lit/an/slua560d/slua560d.pdf",
                    "reference_scope": "SLUA560D sections 6 and 13 motivate energy/timing checks only; no TI solver or hardware parity is claimed.",
                    "numerics": "LSODA stiff/nonstiff ODE; exact gate events; bounded phase shooting; verified polynomial dense reconstruction"},
        model_version=MODEL_VERSION,
    )
