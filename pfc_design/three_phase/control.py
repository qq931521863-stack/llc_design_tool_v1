"""Sampled dq current loop and lifted voltage loop of the two-level rectifier.

Includes the current loop, PWM sample delay, inductor energy and actual
multi-rate hold. Grid feedforward and dq cross-coupling cancellation are ideal.
The nonlinear abc simulation is the separate time-domain validation.
"""

import math

import numpy as np
from scipy import signal

from llc_design.control.digital_loop import DigitalTransferFunction
from power_control_tools.physical_loop import discrete_loop, sampled_loop


def pi(kp, ki, rate):
    return DigitalTransferFunction(
        np.array([kp + ki / (2 * rate), -kp + ki / (2 * rate)]),
        np.array([1.0, -1.0]),
        1 / rate,
        "dq PI",
    )


def build_three_phase_loops(config, *, load_model="constant_power"):
    config.validate()
    if load_model not in ("constant_power", "resistance"):
        raise ValueError("Unknown load model")
    c = config
    vd = c.line_line_rms_v * math.sqrt(2 / 3)
    # Pconv = 1.5*(vd*Id-R*Id^2). Use low-current physical solution.
    discriminant = vd * vd - 4 * c.resistance_ohm * c.output_power_w / 1.5
    if discriminant <= 0:
        raise ValueError("No equilibrium at requested output power / resistance")
    id0 = (2 * c.output_power_w / 1.5) / (vd + math.sqrt(discriminant))
    ci = pi(c.current_kp, c.current_ki, c.control_hz)
    current = sampled_loop(
        [1], [c.inductance_h, c.resistance_ohm], ci, delay_ticks=c.pwm_delay_ticks
    )
    slope = 2 * c.output_power_w / c.bus_voltage_v if load_model == "resistance" else 0
    cv = c.bus_capacitance_f * c.bus_voltage_v
    # Small-signal energy balance including d(1.5*L*Id^2/2)/dt.
    aa = [
        [-c.resistance_ohm / c.inductance_h, 0],
        [1.5 * (vd - c.resistance_ohm * id0) / cv, -slope / cv],
    ]
    bb = [[1 / c.inductance_h], [-1.5 * id0 / cv]]
    ad, bd, _, _, _ = signal.cont2discrete(
        (np.asarray(aa), np.asarray(bb), np.eye(2), np.zeros((2, 1))), 1 / c.control_hz
    )
    n = 3 + c.pwm_delay_ticks  # i, V, controller DF2T state, delay queue
    b0, b1 = ci.numerator

    def advance(x, reference):
        error = reference - x[0]
        control = b0 * error + x[2]
        applied = x[3] if c.pwm_delay_ticks else control
        result = np.zeros(n)
        result[:2] = ad @ x[:2] + bd[:, 0] * applied
        result[2] = b1 * error + control
        if c.pwm_delay_ticks:
            result[3:] = np.r_[x[4:], control]
        return result

    fast_a = np.column_stack([advance(np.eye(n)[:, j], 0) for j in range(n)])
    fast_b = advance(np.zeros(n), 1)
    ratio = round(c.control_hz / c.voltage_loop_hz)
    lifted_a = np.linalg.matrix_power(fast_a, ratio)
    lifted_b = np.zeros(n)
    for _ in range(ratio):
        lifted_b = fast_a @ lifted_b + fast_b
    output = np.zeros((1, n))
    output[0, 1] = 1
    numerator, denominator = signal.ss2tf(lifted_a, lifted_b[:, None], output, [[0.0]])
    outer_controller = pi(c.voltage_kp, c.voltage_ki, c.voltage_loop_hz)
    outer = discrete_loop(numerator[0], denominator, outer_controller)
    return {
        "current": current,
        "voltage": outer,
        "operating_id_a": id0,
        "load_model": load_model,
        "current_transfer": "Gi(s)=1/(L*s+R); PI output is voltage [V]",
        "voltage_transfer": "Gvi(s)=[1.5*(Vd-2*R*Id)-1.5*L*Id*s]/[C*Vdc*s+dPload/dV]",
        "voltage_discrete": "Inner current loop and PWM delay lifted at voltage-loop sample rate",
        "scope": "Local unsaturated model, ideal feedforward/decoupling; excludes PLL/sensor dynamics",
    }
