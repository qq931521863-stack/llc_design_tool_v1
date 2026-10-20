"""Exact sampled dq current loop and state-lifted multirate voltage loop.

Local unsaturated model only: ideal grid feedforward/dq decoupling. Explicit
sample-domain sensing is included. PLL, ABC sensor mixing, negative sequence,
clipping, current-vector limiting and slew are NOT in this local stability proof.
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace

import numpy as np
from scipy import signal

from llc_design.control.digital_loop import calculate_stability_margins
from power_control_tools.physical_loop import SampledLoop

from .exact_control import ExactController, adc_headroom, controller_for, df2t_advance

MODEL_VERSION = "dq-lifted-exact-v2"


def pi(kp, ki, rate, *, method="forward_euler"):
    """Explicit native method; old implicit Tustin analysis was not runtime."""
    if method == "forward_euler":
        b = (kp, -kp + ki / rate)
    elif method == "backward_euler":
        b = (kp + ki / rate, -kp)
    elif method == "tustin":
        b = (kp + ki / (2 * rate), -kp + ki / (2 * rate))
    else:
        raise ValueError("unknown PI recurrence")
    return ExactController(b, (1.0, -1.0), rate, method).loop_transfer()


def request_identity(config):
    payload = {
        "model": MODEL_VERSION,
        "config": asdict(config),
        "controllers": {
            loop: asdict(controller_for(config, loop))
            for loop in ("current", "voltage")
        },
    }
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _state_response(a, b, c, f, ts):
    z = np.exp(2j * np.pi * f * ts)
    # State-space evaluation avoids catastrophic polynomial cancellation in lifted models.
    return np.asarray(
        [c @ np.linalg.solve(zi * np.eye(len(a)) - a, b) for zi in z], dtype=complex
    ).reshape(-1)


def _closed_state(a, b, output, controller, sensor, delay):
    """Complete physical + DF2T controller/sensor + command FIFO state matrix."""
    np_ = len(a)
    nc, ns = controller.order, sensor.order
    ic, ish, iq = np_, np_ + nc, np_ + nc + ns
    n = iq + delay

    def advance(x, reference):
        y = float(output @ x[:np_])
        measured, zs = df2t_advance(sensor, x[ish:iq], y)
        command, zc = df2t_advance(controller, x[ic:ish], reference - measured)
        applied = x[iq] if delay else command
        result = np.empty(n)
        result[:np_] = a @ x[:np_] + b * applied
        result[ic:ish], result[ish:iq] = zc, zs
        if delay:
            result[iq:] = np.r_[x[iq + 1 :], command]
        return result

    ac = np.column_stack([advance(np.eye(n)[:, j], 0.0) for j in range(n)])
    bc = advance(np.zeros(n), 1.0)
    return ac, bc


@dataclass(frozen=True)
class DQLoop(SampledLoop):
    physical_plant_response: np.ndarray
    sense_response: np.ndarray
    command_response: np.ndarray
    forward_response: np.ndarray
    closed_state_a: np.ndarray
    plant_state_a: np.ndarray
    plant_state_b: np.ndarray
    plant_state_c: np.ndarray

    def summary(self):
        result = super().summary()
        m = self.margins
        result.update(
            {
                "stability_scope": "all eigenvalues of the declared unsaturated sampled state model; not nonlinear/PLL/hardware proof",
                "closed_loop_stability": "STABLE" if self.stable else "UNSTABLE",
                "gain_crossovers_hz": list(m.gain_crossovers_hz),
                "phase_crossovers_hz": list(m.phase_crossovers_hz),
                "phase_margins_deg": list(m.phase_margins_deg),
                "signed_gain_margins_db": list(m.gain_margins_db),
                "minimum_signed_gain_margin_db": min(m.gain_margins_db, default=None),
                "ms": float(np.max(abs(self.sensitivity))),
                "mt": float(np.max(abs(self.closed_response))),
                "frequency_domain_hz": [
                    float(self.frequency_hz[0]),
                    float(self.frequency_hz[-1]),
                ],
            }
        )
        return result

    def phase_budget(self, frequency_hz):
        if not self.frequency_hz[0] <= frequency_hz <= self.frequency_hz[-1]:
            raise ValueError("cursor outside this loop's valid frequency domain")
        f = np.array([frequency_hz])
        g = _state_response(
            self.plant_state_a,
            self.plant_state_b,
            self.plant_state_c,
            f,
            self.sample_time_s,
        )[0]
        # Remaining rational factors evaluated exactly, not by interpolated phase.
        _, c = signal.freqz(
            self.controller_b,
            self.controller_a,
            worN=2 * np.pi * f * self.sample_time_s,
        )
        # Sensor/command definitions are retained in the response arrays; log complex interpolation
        # is deliberately not used for the authoritative full return ratio.
        sh = self._sense_exact(f)[0]
        dh = self._command_exact(f)[0]
        values = {
            "controller": c[0],
            "command FIFO": dh,
            "physical/lifted plant": g,
            "sampled sense": sh,
        }
        sampled = {
            "controller": self.controller_response,
            "command FIFO": self.command_response,
            "physical/lifted plant": self.physical_plant_response,
            "sampled sense": self.sense_response,
        }
        blocks = {}
        for name, value in values.items():
            wrapped = float(np.degrees(np.angle(value)))
            branch = float(
                np.interp(
                    np.log(frequency_hz),
                    np.log(self.frequency_hz),
                    np.degrees(np.unwrap(np.angle(sampled[name]))),
                )
            )
            blocks[name] = {
                "gain_db": float(20 * np.log10(max(abs(value), 1e-300))),
                "phase_deg": wrapped + 360 * round((branch - wrapped) / 360),
            }
        product = np.prod(list(values.values()))
        return {
            "frequency_hz": frequency_hz,
            "blocks": blocks,
            "return_ratio": [float(product.real), float(product.imag)],
            "gain_db": float(20 * np.log10(max(abs(product), 1e-300))),
            "phase_deg": sum(block["phase_deg"] for block in blocks.values()),
            "wrapped_phase_deg": float(np.degrees(np.angle(product))),
        }


def _loop(a, b, output, controller, sensor, delay, *, fmin, fmax, points):
    ts = 1 / controller.sample_rate_hz
    high = min(fmax, 0.49 / ts)
    if not 0 < fmin < high:
        raise ValueError(
            "requested frequency range is empty below this loop's Nyquist boundary"
        )
    f = np.geomspace(fmin, high, points)
    if not all(np.all(np.isfinite(v)) for v in (a, b, output)):
        raise ValueError(
            "nonfinite local state model; controller/rate combination exceeds numerical domain"
        )
    physical = _state_response(a, b, output, f, ts)
    sense = sensor.loop_transfer().frequency_response(f)
    command = np.exp(-2j * np.pi * f * ts * delay)
    cr = controller.loop_transfer().frequency_response(f)
    fixed = physical * sense * command
    forward = physical * command * cr
    op = fixed * cr
    closed_a, _ = _closed_state(a, b, output, controller, sensor, delay)
    if not all(np.all(np.isfinite(v)) for v in (closed_a, physical, sense, cr, op)):
        raise ValueError("nonfinite loop response or closed-loop state matrix")
    poles = np.linalg.eigvals(closed_a)
    pb, pa = signal.ss2tf(a, b[:, None], output[None, :], [[0.0]])
    pb = np.r_[np.zeros(delay), np.convolve(pb[0], sensor.b)]
    pa = np.convolve(pa, sensor.a)
    result = DQLoop(
        ts,
        f,
        fixed,
        cr,
        op,
        op / (1 + op),
        1 / (1 + op),
        poles,
        pb,
        pa,
        np.asarray(controller.b),
        np.asarray(controller.a),
        calculate_stability_margins(f, op),
        bool(np.all(abs(poles) < 1.0)),
        physical,
        sense,
        command,
        forward,
        closed_a,
        a,
        b,
        output,
    )
    object.__setattr__(
        result, "_sense_exact", sensor.loop_transfer().frequency_response
    )
    object.__setattr__(
        result,
        "_command_exact",
        lambda f: np.exp(-2j * np.pi * np.asarray(f) * ts * delay),
    )
    return result


def build_three_phase_loops(config, *, load_model=None):
    config.validate()
    c = config if load_model is None else replace(config, load_model=load_model)
    c.validate()
    load_model = c.load_model
    if load_model not in ("constant_power", "resistance"):
        raise ValueError("unknown load model")
    if abs(c.iq_reference_a) > 1e-12:
        raise ValueError(
            "local scalar dq energy model requires Iq=0; nonlinear ABC simulation remains available"
        )
    vd = c.line_line_rms_v * math.sqrt(2 / 3)
    operating_bus = c.bus_voltage_v / c.voltage_sense.gain
    operating_power = c.output_power_w * (
        (operating_bus / c.bus_voltage_v) ** 2 if load_model == "resistance" else 1.0
    )
    if operating_bus <= math.sqrt(2) * c.line_line_rms_v / c.modulation_limit:
        raise ValueError("sensed-voltage target lacks physical SVPWM headroom")
    discriminant = vd * vd - 4 * c.resistance_ohm * operating_power / 1.5
    if discriminant <= 0:
        raise ValueError("No equilibrium at requested output power / resistance")
    id0 = (2 * operating_power / 1.5) / (vd + math.sqrt(discriminant))
    if id0 <= 0 or id0 * c.current_sense.gain >= c.current_limit_a:
        raise ValueError(
            "local operating Id must be strictly inside the forward current limit"
        )
    ci, outer_controller = controller_for(c, "current"), controller_for(c, "voltage")
    si = c.current_sense.controller(c.control_hz)
    sv = c.voltage_sense.controller(c.voltage_loop_hz)
    # Sense gain shifts the regulated physical bus/Id reference. Imported finite-DC-gain
    # controllers require a matching steady-state bias to realize this operating point.
    options = {
        "fmin": c.analysis_min_hz,
        "fmax": c.analysis_max_hz,
        "points": c.analysis_points,
    }
    ai, bi, _, _, _ = signal.cont2discrete(
        (
            np.array([[-c.resistance_ohm / c.inductance_h]]),
            np.array([[1 / c.inductance_h]]),
            np.array([[1.0]]),
            np.array([[0.0]]),
        ),
        1 / c.control_hz,
    )
    current = _loop(ai, bi[:, 0], np.array([1.0]), ci, si, c.pwm_delay_ticks, **options)
    slope = 2 * operating_power / operating_bus if load_model == "resistance" else 0.0
    cv = c.bus_capacitance_f * operating_bus
    aa = np.array(
        [
            [-c.resistance_ohm / c.inductance_h, 0.0],
            [1.5 * (vd - c.resistance_ohm * id0) / cv, -slope / cv],
        ]
    )
    bb = np.array([[1 / c.inductance_h], [-1.5 * id0 / cv]])
    ad, bd, _, _, _ = signal.cont2discrete(
        (aa, bb, np.eye(2), np.zeros((2, 1))), 1 / c.control_hz
    )
    fast_a, fast_b = _closed_state(
        ad, bd[:, 0], np.array([1.0, 0.0]), ci, si, c.pwm_delay_ticks
    )
    ratio = round(c.control_hz / c.voltage_loop_hz)
    # One augmented matrix power preserves the exact held-input sum without an
    # unbounded Python loop at extreme (but integral) multirate ratios.
    augmented = np.eye(len(fast_a) + 1)
    augmented[:-1, :-1], augmented[:-1, -1] = fast_a, fast_b
    with np.errstate(over="ignore", invalid="ignore"):
        lifted = np.linalg.matrix_power(augmented, ratio)
    if not np.all(np.isfinite(lifted)):
        raise ValueError(
            "lifted state model is nonfinite for this controller/rate combination"
        )
    lifted_a, lifted_b = lifted[:-1, :-1], lifted[:-1, -1]
    output = np.zeros(len(fast_a))
    output[1] = 1.0
    outer = _loop(
        lifted_a,
        lifted_b,
        output,
        outer_controller,
        sv,
        c.voltage_delay_ticks,
        **options,
    )
    return {
        "current": current,
        "voltage": outer,
        "source_identity": request_identity(c),
        "model_version": MODEL_VERSION,
        "operating_id_a": id0,
        "operating_bus_v": operating_bus,
        "operating_power_w": operating_power,
        "operating_current_reference_a": id0 * c.current_sense.gain,
        "equilibrium_scope": "regulated sense target; requires integral action or matching steady-state bias. Imported finite-DC-gain controllers are not claimed to settle at this point.",
        "load_model": load_model,
        "controllers": {
            key: asdict(controller_for(c, key)) for key in ("current", "voltage")
        },
        "frequency_request_hz": [c.analysis_min_hz, c.analysis_max_hz],
        "current_transfer": "Gi(s)=1/(L*s+R); error A -> controller voltage V",
        "voltage_transfer": "Gvi(s)=[1.5*(Vd-2*R*Id)-1.5*L*Id*s]/[C*Vdc*s+dPload/dV]; voltage error V -> Id* A",
        "voltage_discrete": f"complete inner/controller/sense/PWM states lifted over {ratio} current samples; outer command held for that interval",
        "sensing": "sample-domain dq/bus gain, matched-z filter, integer latency; calibrated nominal point; no analog aperture/anti-alias or ADC clipping/quantization",
        "adc_headroom": adc_headroom(c),
        "scope": "Local unsaturated Iq=0 model, ideal feedforward/decoupling; excludes PLL/ABC sensor coupling, limiter/slew and switching dynamics. Nonlinear runtime integrates ABC plant with midpoint substeps; local plant uses exact ZOH.",
    }
