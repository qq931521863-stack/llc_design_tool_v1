"""Explicit QR controller extension with a charge-based drain-node state.

This is an independently defined controller, not a reconstruction of TI firmware.
The state is (primary-referred magnetizing current, drain-node charge). At each
zero-current secondary release it is (0, Q(Vin + n*(Vo+Vf))). The off-state
trajectory then determines the selected valley and delayed gate event. An ideal
zero-drop body diode holds Vds=0 until negative magnetizing current reaches zero;
release and subsequent valleys are real state transitions.

Q(V) is piecewise linear for charge tables. Differential-C tables are piecewise
linear C(V), integrated analytically for Q and energy. No accepted trajectory
uses data outside the supplied [0, maximum drain voltage] coverage. With finite
Ron, dQ/dt=im-Vds/Ron physically discharges Cnode and its current contributes to
channel peak/RMS. Without Ron the voltage reset is an explicit ideal impulse,
with energy accounted separately and no claimed finite impulse peak/RMS.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, replace
from numbers import Real
from typing import TYPE_CHECKING

import numpy as np
from scipy.integrate import quad, solve_ivp
from scipy.optimize import brentq, minimize_scalar

from .models import DeviceWaveforms, ModelDomainError, StageResult
from .waveforms import Waveform, WaveformSegment, capacitor_voltage

if TYPE_CHECKING:
    from .models import DesignInputs


@dataclass(frozen=True)
class QROptions:
    model_revision: str = "qr-dynamic-1"
    capacitance_source: str = "User-supplied; not independently verified"
    valley_policy: str = "first_feasible"
    valley_index: int = 1
    maximum_valley_index: int = 100
    detector_delay_s: float = 0.0
    gate_delay_s: float = 0.0
    on_resistance_ohm: float | None = None
    resistive_root_policy: str = "lower_peak"
    capacitance_model: str = "constant"
    capacitance_table: tuple[tuple[float, float], ...] = ()

    def __post_init__(self):
        if self.model_revision != "qr-dynamic-1":
            raise ModelDomainError("Unsupported QR model_revision; explicit migration is required")
        if not isinstance(self.capacitance_source, str) or not self.capacitance_source.strip() or len(self.capacitance_source) > 2000:
            raise ModelDomainError("QR capacitance_source must be a nonempty string of at most 2000 characters")
        if self.valley_policy not in ("first_feasible", "selected"):
            raise ModelDomainError("QR valley_policy must be first_feasible or selected")
        for name in ("valley_index", "maximum_valley_index"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
                raise ModelDomainError(f"QR {name} must be an integer from 1 to 100")
        if self.valley_index > self.maximum_valley_index:
            raise ModelDomainError("QR valley_index cannot exceed maximum_valley_index")
        for name in ("detector_delay_s", "gate_delay_s", "on_resistance_ohm"):
            value = getattr(self, name)
            if name == "on_resistance_ohm" and value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value < 0:
                raise ModelDomainError(f"QR {name} must be finite and nonnegative")
            if name == "on_resistance_ohm" and value == 0:
                raise ModelDomainError("QR on_resistance_ohm must be positive; use None for an ideal switch")
        if self.resistive_root_policy not in ("lower_peak", "higher_peak"):
            raise ModelDomainError("QR resistive_root_policy must be lower_peak or higher_peak")
        if self.resistive_root_policy == "higher_peak" and self.on_resistance_ohm is None:
            raise ModelDomainError("QR higher_peak root policy requires finite on_resistance_ohm")
        if self.capacitance_model not in ("constant", "charge", "differential"):
            raise ModelDomainError("QR capacitance_model must be constant, charge or differential")
        try:
            table = tuple(tuple(row) for row in self.capacitance_table)
        except TypeError as exc:
            raise ModelDomainError("QR capacitance table requires (voltage, charge or capacitance) pairs") from exc
        if any(len(row) != 2 or any(isinstance(x, bool) or not isinstance(x, Real) or not math.isfinite(x) for x in row) for row in table):
            raise ModelDomainError("QR capacitance table requires finite numeric pairs")
        table = tuple((float(v), float(y)) for v, y in table)
        object.__setattr__(self, "capacitance_table", table)
        if self.capacitance_model == "constant":
            if table:
                raise ModelDomainError("A QR table requires charge or differential capacitance_model")
            return
        if len(table) < 2 or table[0][0] != 0 or any(b[0] <= a[0] for a, b in itertools.pairwise(table)):
            raise ModelDomainError("QR table voltages must start at zero and strictly increase")
        if self.capacitance_model == "charge":
            if table[0][1] != 0 or any(b[1] <= a[1] for a, b in itertools.pairwise(table)):
                raise ModelDomainError("QR charge table must start at Q(0)=0 and strictly increase (positive differential C)")
        elif any(c <= 0 for _, c in table):
            raise ModelDomainError("QR differential capacitance must be strictly positive at every table point")


class _Capacitance:
    """Analytical Q(V), E(V)=integral V dQ and inverse Q; SI-unit storage."""

    def __init__(self, inputs, options, maximum_voltage):
        if options.capacitance_model == "constant":
            if inputs.resonant_capacitance_f is None:
                raise ModelDomainError("Constant QR capacitance requires resonant_capacitance_f")
            self.v = np.array([0.0, maximum_voltage])
            self.c = np.array([inputs.resonant_capacitance_f])
            self.slope = np.array([0.0])
        else:
            table = np.asarray(options.capacitance_table)
            self.v = table[:, 0]
            if self.v[-1] < maximum_voltage:
                raise ModelDomainError(f"QR capacitance table must cover 0 through {maximum_voltage:.12g} V; extrapolation is forbidden")
            self.c = np.diff(table[:, 1]) / np.diff(self.v) if options.capacitance_model == "charge" else table[:-1, 1]
            self.slope = np.zeros_like(self.c) if options.capacitance_model == "charge" else np.diff(table[:, 1]) / np.diff(self.v)
        dv = np.diff(self.v)
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            self.q = np.r_[0.0, np.cumsum(self.c * dv + .5 * self.slope * dv**2)]
            self.e = np.r_[0.0, np.cumsum(self.c * (self.v[:-1]*dv + .5*dv**2) + self.slope*(.5*self.v[:-1]*dv**2 + dv**3/3))]
        if (not all(np.all(np.isfinite(a)) for a in (self.c, self.slope, self.q, self.e))
                or np.any(self.c <= 0) or np.any(np.diff(self.q) <= 0)):
            raise ModelDomainError("QR capacitance table exceeds the finite positive numerical domain")
        self.reference_c = self.charge(maximum_voltage) / maximum_voltage

    def _voltage_cell(self, voltage):
        if not math.isfinite(voltage) or voltage < -1e-12 or voltage > self.v[-1]*(1+1e-12):
            raise ModelDomainError("QR capacitance evaluation outside supplied voltage coverage")
        voltage = min(self.v[-1], max(0.0, voltage))
        k = min(len(self.c)-1, max(0, int(np.searchsorted(self.v, voltage, side="right"))-1))
        return k, voltage-self.v[k]

    def charge(self, voltage):
        k, d = self._voltage_cell(voltage)
        return float(self.q[k] + self.c[k]*d + .5*self.slope[k]*d*d)

    def energy(self, voltage):
        k, d = self._voltage_cell(voltage)
        return float(self.e[k] + self.c[k]*(self.v[k]*d+.5*d*d) + self.slope[k]*(.5*self.v[k]*d*d+d*d*d/3))

    def voltage(self, charge):
        # The integrator may evaluate an event-bracketing trial beyond a hard
        # clamp. Continuation is clipped only in the RHS; retained trajectories
        # end exactly at the clamp event, and physical samples are checked.
        charge = min(self.q[-1], max(0.0, charge))
        k = min(len(self.c)-1, max(0, int(np.searchsorted(self.q, charge, side="right"))-1))
        dq = charge-self.q[k]
        if self.slope[k] == 0:
            d = dq/self.c[k]
        else:
            width = self.v[k+1]-self.v[k]
            fraction = dq/(self.c[k]*width)
            slope = self.slope[k]*width/self.c[k]
            d = width*2*fraction/(1+math.sqrt(max(0.0, 1+2*slope*fraction)))
        return float(self.v[k]+d)


@dataclass
class _Piece:
    duration: float
    state: object
    kind: str
    knots: tuple[float, ...] = ()


def calculate_qr_dynamic(inputs: DesignInputs, options: QROptions, cancelled=None) -> StageResult:
    """Solve a periodic QR cycle with an explicitly selected controller policy.

    fsw_hz remains a ceiling. 'selected' requires exactly valley_index;
    'first_feasible' starts at valley_index and searches through the maximum.
    Valley one is the first local minimum or first entry to the zero clamp.
    Each later valley is the next minimum after clamp release. Detector and
    gate delays add after the selected event; no device-specific compensation
    or undocumented valley lockout is inferred.
    """
    from .topologies import _finish, _topology_id, _validate

    _validate(inputs)
    if not isinstance(options, QROptions):
        raise ModelDomainError("QR options must be a QROptions value")
    if inputs.qr_options != options:
        inputs = replace(inputs, qr_options=options)
    if _topology_id(inputs.topology) != "T11":
        raise ModelDomainError("QR dynamics options apply only to QR Flyback / T11")
    if inputs.mode.casefold() not in ("auto", "dcm") or inputs.iout_a <= 0:
        raise ModelDomainError("QR dynamics requires positive output power and auto/DCM mode")
    if inputs.dead_time_s:
        raise ModelDomainError("Use explicit QR detector/gate delays, not generic dead_time_s")
    if inputs.leakage_inductance_h is not None:
        raise ModelDomainError("QR dynamics does not model leakage inductance; remove the unused leakage input")
    vin, vo, vf, n, lm = inputs.vin_v, inputs.vout_v, inputs.diode_drop_v, inputs.turns_ratio_np_ns, inputs.inductance_h
    vr = n*(vo+vf)
    cap = _Capacitance(inputs, options, vin+vr)
    c = cap.reference_c
    tscale, iscale, qscale = math.sqrt(lm*c), vin*math.sqrt(c/lm), c*vin
    escale = c*vin*vin
    if not all(math.isfinite(value) and value > 0 for value in (tscale, iscale, qscale, escale)):
        raise ModelDomainError("QR L/C/voltage scales exceed the finite positive numerical domain")
    zmax = cap.charge(vin+vr)/qscale
    ratio = vr/vin
    power = (vo+vf)*inputs.iout_a/(vin*iscale)
    resistance = options.on_resistance_ohm
    conductance = 0.0 if resistance is None else math.sqrt(lm/c)/resistance
    if resistance is not None and (not math.isfinite(conductance) or conductance <= 0):
        raise ModelDomainError("QR Ron exceeds the finite positive numerical domain")
    delay = (options.detector_delay_s+options.gate_delay_s)/tscale
    maximum_ring_time = 40*math.pi*math.sqrt(float(np.max(cap.c+np.maximum(0, cap.slope)*np.diff(cap.v)))/c)
    if not math.isfinite(delay) or delay > maximum_ring_time*100:
        raise ModelDomainError("QR delay exceeds 100 bounded resonant cycles; use an explicit burst model")
    evaluations = 0

    def check_cancel():
        if cancelled is not None and cancelled():
            raise InterruptedError("QR dynamics calculation cancelled")

    check_cancel()

    def voltage(z):
        return cap.voltage(float(z)*qscale)/vin

    def potential(z):
        return cap.energy(cap.voltage(float(z)*qscale))/escale-z

    def rhs(gate):
        def f(t, y):
            check_cancel()
            v = voltage(y[1])
            return (1-v, y[0]-(conductance*v if gate else 0.0))
        return f

    def integrate(state, kind, horizon, event=None, direction=0):
        check_cancel()
        if event is not None:
            event.terminal = True
            event.direction = direction
        gate = kind == "on_resistive"
        sol = solve_ivp(rhs(gate), (0, horizon), state, method="Radau" if gate else "DOP853",
                        rtol=5e-13, atol=5e-14, dense_output=True, events=event,
                        max_step=(max(.2, horizon/64) if gate else .2))
        if not sol.success:
            raise ModelDomainError(f"QR {kind} integration failed: {sol.message}")
        if event is not None and not sol.t_events[0].size:
            raise ModelDomainError(f"QR {kind} event was not reached; revise operating point or Ron")
        return _Piece(float(sol.t[-1]), sol.sol, kind, tuple(sol.t)), np.asarray(sol.y[:, -1])

    def linear(start, derivative, duration, kind):
        state = np.array(start, dtype=float)
        slope = np.array(derivative, dtype=float)
        return _Piece(float(duration), lambda t, a=state, b=slope: a+b*np.asarray(t)[..., None] if np.ndim(t) else a+b*t, kind)

    def body_hold(state, duration=None, kind="body"):
        full = max(0.0, -float(state[0]))
        dt = full if duration is None else min(full, duration)
        piece = linear((state[0], 0), (1, 0), dt, kind)
        return piece, np.array([state[0]+dt, 0.0])

    # First valley includes a true body-clamp event whenever its conserved
    # energy reaches Vds=0, including the nonlinear-C boundary.
    umax = potential(zmax)
    clamped = umax >= -2e-12
    if clamped and umax > 2e-12:
        first, first_state = integrate((0, zmax), "ring", maximum_ring_time, lambda t, y: y[1], -1)
        first_state[1] = 0
    else:
        first, first_state = integrate((0, zmax), "ring", maximum_ring_time, lambda t, y: y[0], 1)
        first_state[0] = 0
        if clamped:
            first_state[1] = 0
    valley_pieces = [first]
    valley_state = first_state.copy()
    valley_time = first.duration

    def next_valley(pieces, state):
        state = state.copy()
        additions = []
        if state[0] < -1e-11 and state[1] <= 2e-11:
            hold, state = body_hold(state)
            additions.append(hold)
        crest, state = integrate(state, "ring", maximum_ring_time, lambda t, y: y[0], -1)
        state[0] = 0
        trough, state = integrate(state, "ring", maximum_ring_time, lambda t, y: y[0], 1)
        state[0] = 0
        if clamped:
            if abs(state[1]) > 2e-9:
                raise ModelDomainError("QR released-clamp orbit failed its zero-charge return check")
            state[1] = 0
        additions.extend((crest, trough))
        return pieces+additions, state

    def delayed_gate(pieces, state):
        additions = []
        remaining = delay
        state = state.copy()
        if remaining and state[0] < 0 and state[1] <= 2e-11:
            hold, state = body_hold(state, remaining)
            additions.append(hold)
            remaining -= hold.duration
        if remaining > 1e-13:
            # After first-clamp release subsequent minima touch zero with no
            # negative current. Free off-state integration is consequently
            # sufficient; no second body-hold event is hidden in this interval.
            extra, state = integrate(state, "ring", remaining)
            additions.append(extra)
        return pieces+additions, state

    qknots = cap.q/qscale

    def charge_time(peak, zstart):
        transfer2 = peak*peak+2*(potential(zstart)-umax)
        if transfer2 <= 0 or zstart >= zmax:
            return None
        transfer = math.sqrt(transfer2)
        def integrand(u):
            # z=zmax-u² regularizes the zero-transfer-current boundary.
            z = zmax-u*u
            delta = 2*(umax-potential(z))
            if u*u < 1e-12*max(1.0, zmax):
                delta = 2*ratio*u*u
            square = transfer2+delta
            if square <= 0:
                raise ModelDomainError("QR turn-off charge trajectory cannot reach the secondary clamp")
            return 2*u/math.sqrt(square)
        points = sorted(math.sqrt(zmax-z) for z in qknots if zstart < z < zmax)
        time = quad(integrand, 0, math.sqrt(zmax-zstart), points=points, epsabs=2e-11, epsrel=2e-11, limit=200)[0]
        return time, transfer

    # Finite Ron has a finite maximum transferable power. Its two load roots
    # cannot be bracketed by assuming power increases to Vin/Ron. Cache a full
    # first-rise orbit so both the maximum and all selected roots consume the
    # same stiff integration. Its endpoint is the actual first current maximum
    # (Vds crossing Vin upward). Only an asymptotic orbit uses a stated 1e-10
    # distance from equilibrium; Vin/Ron is NOT an underdamped current bound.
    on_orbits = {}
    resolved_peak_fraction = 1-1e-10

    def resistive_orbit(initial):
        key = tuple(initial)
        if key in on_orbits:
            return on_orbits[key]
        state = initial.copy()
        pieces = []
        if state[1] <= 2e-11 and state[0] < 0:
            hold, state = body_hold(state, kind="on_body")
            pieces.append(hold)
        def crest(t, y): return voltage(y[1])-1
        crest.direction = 1
        crest.terminal = True
        def zero(t, y): return y[1]
        zero.direction = -1
        zero.terminal = True
        # Resolve 50 dominant L/R or RC time constants. Underdamped orbits
        # stop at the real first current crest, normally much earlier.
        horizon = max(100.0, 50*(conductance+1/conductance))
        if not math.isfinite(horizon):
            raise ModelDomainError("QR resistive orbit time scale exceeds the finite numerical domain")
        for _ in range(2):
            sol = solve_ivp(rhs(True), (0, horizon), state, method="Radau", rtol=2e-11, atol=2e-12,
                            dense_output=True, events=(crest, zero), max_step=max(.2, horizon/64))
            if not sol.success:
                raise ModelDomainError(f"QR resistive turn-on integration failed: {sol.message}")
            pieces.append(_Piece(float(sol.t[-1]), sol.sol, "on_resistive", tuple(sol.t)))
            state = sol.y[:, -1]
            if sol.t_events[0].size and state[0] > conductance*(1+5e-9):
                on_orbits[key] = pieces, float(state[0]), "finite_current_crest"
                return on_orbits[key]
            if abs(state[0]-conductance) <= 5e-9*max(1.0, conductance) and abs(voltage(state[1])-1) <= 5e-9:
                on_orbits[key] = pieces, conductance*resolved_peak_fraction, "asymptotic_equilibrium"
                return on_orbits[key]
            if sol.t_events[1].size and state[0] < -1e-10:
                hold, state = body_hold(state, kind="on_body")
                pieces.append(hold)
            else:
                raise ModelDomainError("QR resistive first-rise orbit reached neither a current crest nor its equilibrium within the explicit 50-time-constant numerical bound")
        raise ModelDomainError("QR resistive turn-on has unresolved body-clamp events")

    def on_state(peak, initial, retain):
        if resistance is None:
            duration = peak-initial[0]
            if duration <= 0:
                return None
            pieces = [linear((initial[0], 0), (1, 0), duration, "on_ideal")] if retain else []
            return duration, np.array([peak, 0.0]), pieces
        orbit, upper_peak, boundary = resistive_orbit(initial)
        if peak > upper_peak or (boundary == "asymptotic_equilibrium" and peak >= upper_peak) or peak <= max(0.0, initial[0]):
            return None
        pieces = []
        elapsed = 0.0
        for piece in orbit:
            grid = piece.knots or (0.0, piece.duration)
            for a, b in itertools.pairwise(grid):
                if piece.state(a)[0] <= peak <= piece.state(b)[0]:
                    stop = brentq(lambda t, piece=piece: piece.state(t)[0]-peak, a, b, xtol=2e-13, rtol=1e-14)
                    state = np.asarray(piece.state(stop))
                    if voltage(state[1]) >= 1+ratio:
                        return None
                    if retain:
                        knots = tuple(t for t in grid if t < stop)+(float(stop),)
                        pieces.append(_Piece(float(stop), piece.state, piece.kind, knots))
                    return elapsed+stop, state, pieces
            elapsed += piece.duration
            if retain:
                pieces.append(piece)
        raise ModelDomainError("QR peak root is outside the resolved first-rise current orbit")

    chosen = None
    for valley in range(1, options.maximum_valley_index+1):
        check_cancel()
        if valley > 1:
            valley_pieces, valley_state = next_valley(valley_pieces, valley_state)
            valley_time = sum(p.duration for p in valley_pieces)
        if valley < options.valley_index:
            continue
        waiting, gate_state = delayed_gate(valley_pieces, valley_state)
        wait = sum(p.duration for p in waiting)
        if gate_state[1] < -2e-9 or gate_state[1] > zmax+2e-9:
            raise ModelDomainError(f"Delayed QR gate trajectory left the physical capacitance domain: {gate_state}, Qmax={zmax}")
        gate_state[1] = max(0.0, gate_state[1])

        def cycle(peak, retain=False, gate_state=gate_state, wait=wait):
            nonlocal evaluations
            evaluations += 1
            check_cancel()
            on = on_state(peak, gate_state, retain)
            if on is None:
                return None
            ton, cutoff, onpieces = on
            charged = charge_time(float(cutoff[0]), float(cutoff[1]))
            if charged is None:
                return None
            tc, transfer = charged
            period = ton+tc+transfer/ratio+wait
            return .5*transfer*transfer/period, period, cutoff, transfer, ton, tc, onpieces

        lower = max(0.0, gate_state[0], math.sqrt(max(0.0, 2*umax)) if resistance is None else 0.0)+1e-9
        maximum_power = None
        maximum_peak = None
        maximum_tolerance = 0.0
        if resistance is not None:
            # Search the finite admissible current interval, refine each sampled
            # interior maximum, then bracket the requested lower/higher root.
            # Including both the refined maxima and near-equilibrium samples
            # prevents a doubling step from skipping the feasible-power lobe.
            _orbit, orbit_peak, orbit_boundary = resistive_orbit(gate_state)
            upper = orbit_peak if orbit_boundary == "finite_current_crest" else orbit_peak*(1-1e-10)
            if lower >= upper:
                if options.valley_policy == "selected":
                    raise ModelDomainError("Selected QR valley has no admissible finite-Ron first-rise peak")
                continue
            cache = {}
            def delivered(peak, cycle=cycle, cache=cache):
                if peak not in cache:
                    candidate = cycle(peak)
                    cache[peak] = 0.0 if candidate is None else candidate[0]
                return cache[peak]
            fractions = np.r_[np.linspace(lower/upper, 1.0, 65), 1-np.logspace(-2, -9, 15)]
            grid = sorted({float(f*upper) for f in fractions if lower <= f*upper <= upper})
            grid = sorted({lower, upper, *grid})
            values = [delivered(p) for p in grid]
            refined = []
            for k in range(1, len(grid)-1):
                if values[k] > 0 and values[k] >= values[k-1] and values[k] >= values[k+1]:
                    solved_max = minimize_scalar(lambda f: -delivered(f*conductance),
                                                 bounds=(grid[k-1]/conductance, grid[k+1]/conductance),
                                                 method="bounded", options={"xatol": 2e-12, "maxiter": 100})
                    if not solved_max.success:
                        raise ModelDomainError("QR finite-Ron power maximum search did not converge")
                    refined.append(float(solved_max.x*conductance))
            grid = sorted(set(grid+refined))
            maximum_peak = max(grid, key=delivered)
            maximum_power = delivered(maximum_peak)
            maximum_tolerance = 2e-10*max(maximum_power, 1e-10)
            if power > maximum_power+maximum_tolerance:
                if options.valley_policy == "selected":
                    limit_w = maximum_power*vin*iscale*vo/(vo+vf)
                    raise ModelDomainError(f"Selected QR valley requested output exceeds finite-Ron first-upward-current controller maximum output power {limit_w:.12g} W")
                continue
            if abs(power-maximum_power) <= maximum_tolerance:
                peak = maximum_peak
            else:
                intervals = [(a, b) for a, b in itertools.pairwise(grid)
                             if (delivered(a)-power)*(delivered(b)-power) <= 0]
                if orbit_boundary == "finite_current_crest" and abs(delivered(upper)-power) <= maximum_tolerance:
                    intervals.append((upper, upper))
                if not intervals:
                    raise ModelDomainError("QR finite-Ron load root is outside the resolved first-rise current interval")
                if options.resistive_root_policy == "higher_peak" and orbit_boundary == "asymptotic_equilibrium" and delivered(upper) > power:
                    if options.valley_policy == "selected":
                        raise ModelDomainError("QR higher_peak root is closer than 2e-10 to Vin/Ron; its asymptotic on-time is outside the explicitly resolved current/time domain")
                    continue
                a, b = intervals[0] if options.resistive_root_policy == "lower_peak" else intervals[-1]
                peak = a if a == b else brentq(lambda p: delivered(p)-power, a, b, xtol=2e-12, rtol=2e-13)
        else:
            upper = max(1.0, lower*2, 2*math.sqrt(power/(inputs.fsw_hz*tscale)))
            low_result = cycle(lower)
            high_result = cycle(upper)
            for _ in range(80):
                if high_result is not None and high_result[0] >= power:
                    break
                lower = upper
                low_result = high_result
                upper *= 2
                if upper > 1e8:
                    break
                high_result = cycle(upper)
            if high_result is None or high_result[0] < power or (low_result is not None and low_result[0] > power):
                if options.valley_policy == "selected":
                    raise ModelDomainError("Selected QR valley cannot deliver the requested load with the specified gate delay")
                continue
            def residual(peak, cycle=cycle):
                data = cycle(peak)
                return -power if data is None else data[0]-power
            peak = brentq(residual, lower, upper, xtol=2e-12, rtol=2e-13)
        data = cycle(peak, True)
        if data is None:
            raise ModelDomainError("QR selected periodic solution lost the turn-off charge event")
        frequency = 1/(data[1]*tscale)
        if frequency <= inputs.fsw_hz*(1+2e-11):
            chosen = valley, peak, data, waiting, gate_state, valley_time, maximum_power, maximum_peak, maximum_tolerance, orbit_boundary if resistance is not None else "ideal_channel"
            break
        if options.valley_policy == "selected":
            raise ModelDomainError(f"Selected QR valley {valley} frequency {frequency:.12g} Hz exceeds ceiling {inputs.fsw_hz:.12g} Hz")
    if chosen is None:
        raise ModelDomainError("No feasible QR valley within maximum_valley_index; revise the ceiling/load or use a burst-mode model")

    valley, peak, data, waiting, gate_state, selected_time, maximum_power, maximum_peak, maximum_tolerance, orbit_boundary = chosen
    _, period, cutoff, transfer, ton, tc, onpieces = data
    charging, charged_state = integrate(cutoff, "charge", max(maximum_ring_time, tc*2), lambda t, y: y[1]-zmax, 1)
    # Event integration and independent energy/quadrature solve must agree;
    # do not paper over a discrepancy by changing output-current tolerances.
    if abs(charging.duration-tc) > 2e-8*max(1.0, tc) or abs(charged_state[0]-transfer) > 2e-8*max(1.0, transfer):
        raise ModelDomainError("QR turn-off charge ODE disagrees with the independent energy/quadrature solution")
    demag = linear((transfer, zmax), (-ratio, 0), transfer/ratio, "demag")
    pieces = onpieces+[charging, demag]+waiting
    actual_period = sum(p.duration for p in pieces)
    if abs(actual_period-period) > 2e-9*max(1.0, period):
        raise ModelDomainError("QR retained waveform period disagrees with solved period")
    period = actual_period
    period_s = period*tscale
    signals = {key: [] for key in ("im", "v", "primary", "channel", "body", "displacement", "secondary")}
    offset = 0.0
    nodes = .5*(1-np.cos(np.linspace(0, math.pi, 8)))
    verify_nodes = np.array([.07, .19, .37, .57, .73, .91])

    def build_interval(piece, a, b, depth=0):
        check_cancel()
        values = np.array([piece.state(a+(b-a)*u) for u in nodes])
        if np.min(values[:, 1]) < -2e-9 or np.max(values[:, 1]) > zmax+2e-9:
            raise ModelDomainError("QR retained trajectory left its zero/secondary voltage clamps")
        im = values[:, 0]*iscale
        volts = np.array([voltage(z)*vin for z in values[:, 1]])
        icoeff = np.polynomial.polynomial.polyfit(nodes, im, 7)
        vcoeff = np.polynomial.polynomial.polyfit(nodes, volts, 7)
        probes = np.array([piece.state(a+(b-a)*u) for u in verify_nodes])
        iv = np.polynomial.polynomial.polyval(verify_nodes, icoeff)
        vv = np.polynomial.polynomial.polyval(verify_nodes, vcoeff)
        error = max(np.max(np.abs(iv-probes[:, 0]*iscale))/max(1.0, float(np.max(np.abs(im)))), np.max(np.abs(vv-np.array([voltage(z)*vin for z in probes[:, 1]])))/max(1.0, vin+vr))
        if error > 2e-11 and depth < 18:
            middle = (a+b)/2
            build_interval(piece, a, middle, depth+1)
            build_interval(piece, middle, b, depth+1)
            return
        if error > 2e-9:
            raise ModelDomainError("QR waveform interpolation did not meet its local accuracy budget")
        zero = np.zeros(8)
        primary = zero if piece.kind == "demag" else icoeff
        channel = icoeff if piece.kind == "on_ideal" else vcoeff/resistance if piece.kind == "on_resistive" else zero
        body = -icoeff if piece.kind in ("body", "on_body") else zero
        displacement = icoeff-channel if piece.kind in ("ring", "charge", "on_resistive") else zero
        secondary = n*icoeff if piece.kind == "demag" else zero
        coefficients = (icoeff, vcoeff, primary, channel, body, displacement, secondary)
        start, end = (offset+a)/period, min(1.0, (offset+b)/period)
        for key, coeff in zip(signals, coefficients):
            signals[key].append(WaveformSegment(start, end, tuple(float(v) for v in coeff)))

    for piece in pieces:
        if piece.duration <= 1e-14:
            offset += piece.duration
            continue
        grid = list(piece.knots) if piece.knots else [0.0, piece.duration]
        # Split interpolation exactly at Q-table knots: V(Q) can have a
        # derivative jump, even though the integrated state is continuous.
        extra = []
        for a, b in itertools.pairwise(grid):
            za, zb = piece.state(a)[1], piece.state(b)[1]
            for knot in qknots[1:-1]:
                if (za-knot)*(zb-knot) < 0:
                    extra.append(brentq(lambda t, knot=knot, piece=piece: piece.state(t)[1]-knot, a, b, xtol=2e-14))
        grid = sorted(set(grid+extra))
        for a, b in itertools.pairwise(grid):
            build_interval(piece, a, b)
        offset += piece.duration

    def wave(key, unit, reference):
        return Waveform(period_s, unit, reference, tuple(signals[key]))

    im = wave("im", "A", "primary-referred magnetizing state; signed")
    vq = wave("v", "V", "drain to source")
    vp = vq.affine(scale=-1, offset=vin, reference="primary dot minus undotted terminal")
    ip = wave("primary", "A", "actual primary winding current into dot; signed")
    diode = wave("secondary", "A", "secondary rectifier anode to cathode")
    channel = wave("channel", "A", "MOS channel only; body diode is a separate branch")
    cout = diode.affine(offset=-inputs.iout_a, reference="into output capacitor positive plate")
    devices = {
        "Q1": DeviceWaveforms("QR main switch channel", channel, vq),
        "Dbody": DeviceWaveforms("Ideal zero-drop primary body diode", wave("body", "A", "body diode source to drain"), vq),
        "D1": DeviceWaveforms("Secondary rectifier", diode, vp.affine(scale=1/n, offset=vo, reference="cathode to anode")),
        "Lm": DeviceWaveforms("Primary-referred magnetizing state", im, vp),
        "primary": DeviceWaveforms("Actual primary winding", ip, vp),
        "secondary": DeviceWaveforms("Actual secondary winding", diode, vp.affine(scale=-1/n)),
        "Cnode": DeviceWaveforms("Charge-based drain-node capacitance", wave("displacement", "A", "into drain-node positive plate; ideal reset impulse excluded" if resistance is None else "into drain-node positive plate"), vq),
        "Cout": DeviceWaveforms("Output capacitor", cout, capacitor_voltage(cout, inputs.capacitance_f, vo)),
    }
    gate_v = voltage(gate_state[1])*vin
    stored = cap.energy(gate_v)
    impulse_loss = stored/period_s if resistance is None else 0.0
    resistive_loss = resistance*channel.statistics()["rms"]**2 if resistance is not None else 0.0
    closure = np.asarray(pieces[-1].state(pieces[-1].duration))-gate_state
    warnings = ("Independent QR controller extension; no TI executable or hardware correlation is implied.",
                "Leakage, body-diode forward drop/recovery, gate-charge dynamics and device thermal dependence are excluded.")
    if resistance is None:
        warnings += ("Ideal turn-on Cnode reset is an impulse: channel/displacement peak and RMS omit that impulse and are not total switching-current stresses.",)
    root_metrics = {} if resistance is None else {
        "controller_maximum_output_power_w": maximum_power*vin*iscale*vo/(vo+vf),
        "maximum_power_peak_current_a": maximum_peak*iscale,
        "maximum_power_search_tolerance_w": maximum_tolerance*vin*iscale*vo/(vo+vf),
        "resistive_equilibrium_current_a": conductance*iscale,
    }
    result = _finish(inputs, "T11", "QR-DYNAMIC-DCM", ton/period, devices, ip, im, vf*inputs.iout_a,
                     modeled_other_loss_w=impulse_loss+resistive_loss,
                     assumptions=("Fixed positive output power; fsw_hz is the ceiling, with actual switching frequency solved from one periodic cycle.",
                                  "Valley one is the first drain minimum or entry to the zero clamp. Later valleys occur after body-clamp hold/release; detector and gate delays add after the selected event.",
                                  "With the gate off or finite Ron, the ideal zero-drop body diode holds Vds=0 while im<0, releasing exactly at im=0. Secondary conduction clamps Vds=Vin+n*(Vo+Vf).",
                                  "Charge-state ODE, exact capacitor-energy integrals, independent turn-off quadrature and output-charge balance determine the cycle.",
                                  f"Capacitance model: {options.capacitance_model}; Q(V) is monotonic, with no trajectory extrapolation beyond supplied voltage coverage.",
                                  f"Finite Ron includes physical capacitor discharge without separate Eoss double counting. The {options.resistive_root_policy} load root is selected on the first upward current trajectory, ending at its actual finite current maximum or asymptotic equilibrium; frequency limits never silently change the root. Only asymptotic high roots closer than 2e-10 to Vin/Ron are explicitly unresolved." if resistance is not None else "Ideal channel carries signed magnetizing current, including reverse conduction; its instantaneous capacitor-charge reset dissipates E(Vgate), separately accounted without finite impulse-current claims."),
                     warnings=warnings,
                     diagnostics={"inductor_mean_voltage_v": vp.statistics()["mean"],
                                  "periodic_current_residual_a": float(closure[0]*iscale),
                                  "periodic_charge_residual_c": float(closure[1]*qscale),
                                  "turn_off_energy_current_residual_a": float((charged_state[0]-transfer)*iscale),
                                  "turn_off_quadrature_time_residual_s": (charging.duration-tc)*tscale},
                     metrics={**root_metrics, "valley_index": float(valley), "drain_valley_voltage_v": voltage(valley_state[1])*vin,
                              "gate_turn_on_voltage_v": gate_v, "turn_on_magnetizing_current_a": gate_state[0]*iscale,
                              "turn_off_magnetizing_current_a": float(cutoff[0]*iscale),
                              "turn_on_capacitive_energy_j": stored, "turn_on_capacitive_loss_w": impulse_loss,
                              "channel_resistive_loss_w": resistive_loss, "turn_off_charge_time_s": charging.duration*tscale,
                              "demagnetization_time_s": transfer/ratio*tscale, "resonant_wait_time_s": sum(p.duration for p in waiting)*tscale,
                              "selected_valley_time_s": selected_time*tscale,
                              "body_clamp_hold_time_s": sum(p.duration for p in pieces if p.kind in ("body", "on_body"))*tscale,
                              "detector_delay_s": options.detector_delay_s, "gate_delay_s": options.gate_delay_s,
                              "ideal_turn_on_charge_impulse_c": -cap.charge(gate_v) if resistance is None else 0.0,
                              "periodic_solver_evaluations": float(evaluations)})
    return replace(result, provenance={**result.provenance, "physics_revision": "qr-charge-controller-1",
                                      "controller_policy": options.valley_policy,
                                      "resistive_root_policy": options.resistive_root_policy,
                                      "resistive_search_domain": orbit_boundary,
                                      "maximum_power_scope": "selected-valley first-upward-current controller domain",
                                      "capacitance_model": options.capacitance_model,
                                      "capacitance_source": options.capacitance_source,
                                      "model_revision": options.model_revision,
                                      "input_frequency_role": "maximum switching-frequency ceiling",
                                      "evidence": "Independent hybrid charge-state model; TI executable equivalence and hardware correlation UNKNOWN"})
