"""DAB PI -> power command -> TPS -> capacitor/load voltage loop.

One-cycle steady-state power mapping + bus energy model. Not a transient
switching model of Cp/L. Controller coefficients are reused exactly from LLC.
"""

import math
from dataclasses import asdict, dataclass, field, replace

import numpy as np
from scipy import signal

from llc_design.control.digital_loop import PIControllerConfig
from power_control_tools.physical_loop import SampledLoop, discrete_loop, sampled_loop

from .core import tps_command
from .optimization import exact_tps_metrics
from .periodic_power import periodic_lc_power
from .sensing import DABSenseConfig


@dataclass(frozen=True)
class DABVoltageControlConfig:
    sample_hz: float = 5000
    kp_w_per_v: float = 100
    ti_s: float = 0.02
    delay_ticks: int = 1
    duration_s: float = 0.4
    disturbance_time_s: float = 0.15
    load_step_ratio: float = 1.2
    reference_step_v: float = 5
    load_model: str = "resistance"
    include_cp: bool = False
    actuator: str = "tps_power"
    sensing: DABSenseConfig = field(default_factory=DABSenseConfig)
    frequency_min_hz: float = 0.05
    frequency_max_hz: float = 20000.0

    def validate(self):
        if any(
            not math.isfinite(v)
            for v in [
                self.sample_hz,
                self.kp_w_per_v,
                self.ti_s,
                self.duration_s,
                self.disturbance_time_s,
                self.load_step_ratio,
                self.reference_step_v,
                self.frequency_min_hz,
                self.frequency_max_hz,
            ]
        ):
            raise ValueError("Finite control parameters required")
        if (
            min(
                self.sample_hz,
                self.kp_w_per_v,
                self.ti_s,
                self.duration_s,
                self.load_step_ratio,
            )
            <= 0
        ):
            raise ValueError("Positive sample rate/gains/duration/load ratio required")
        if self.actuator not in ("tps_power", "sps_power"):
            raise ValueError("Actuator must be tps_power or sps_power")
        self.sensing.validate(self.sample_hz)
        if (
            not 0
            < self.frequency_min_hz
            < min(self.frequency_max_hz, 0.49 * self.sample_hz)
        ):
            raise ValueError(
                "Frequency range must contain samples strictly below Nyquist"
            )
        if type(self.include_cp) is not bool:
            raise ValueError("include_cp must be boolean")
        if self.load_model not in ("resistance", "constant_power"):
            raise ValueError("Load model must be resistance or constant_power")
        if type(self.delay_ticks) is not int or not 0 <= self.delay_ticks <= 10:
            raise ValueError("Delay ticks must be 0..10")
        if (
            not 0 < self.disturbance_time_s < self.duration_s
            or self.sample_hz * self.duration_s > 50000
        ):
            raise ValueError("Disturbance must be inside duration; max 50000 samples")

    def controller(self):
        return PIControllerConfig(
            self.kp_w_per_v, self.ti_s, 1 / self.sample_hz, 0, 1e9
        ).transfer_function()


@dataclass(frozen=True)
class DABSampledLoop(SampledLoop):
    sense_b: np.ndarray
    sense_a: np.ndarray
    sense_response: np.ndarray
    output_reference_response: np.ndarray

    def summary(self):
        margins = asdict(self.margins)
        return {
            **super().summary(),
            **margins,
            "sense_b": self.sense_b.tolist(),
            "sense_a": self.sense_a.tolist(),
            "stability_scope": "Exact poles of the declared single-rate discrete linear model; not switching/hardware stability",
            "gain_margin_convention": "Scalar uses shared nonnegative-first convention; all signed gain_margins_db and phase_crossovers_hz are retained",
            "closed_response_definition": "T=L/(1+L) measured output; physical Vout/reference uses G*C/(1+L)",
        }


def power_map_jacobian(stage, vb, power, include_cp=False, actuator="tps_power"):
    """Two-sided Richardson checks: mode changes alone do not imply a kink.

    h,h/2,h/4 one-sided slopes extrapolate to zero; absolute and natural-scale
    relative tolerances distinguish smooth ideal-L boundaries from RLC kinks.
    A local derivative does not certify operation throughout nonlinear excursions.
    """
    center = power_command(stage, vb, power, actuator)
    if power <= 0 or center.limited or center.p >= stage.p_limit:
        raise ValueError("Operating point must be inside forward command limits")
    f0 = mapped_power(stage, vb, power, include_cp, actuator)
    evidence = {
        "actuator": actuator,
        "mode_id": center.mode_id,
        "m": center.m,
        "p": center.p,
        "p_boundary": center.p_boundary,
        "axes": {},
    }
    derivatives = []
    for axis, x, h, scale in (
        ("power", power, min(power / 4, max(0.01, power * 1e-4)), 1.0),
        ("voltage", vb, min(vb / 4, max(0.001, vb * 1e-4)), abs(f0 / vb)),
    ):
        sides = [[], []]
        modes = []
        for step in (h, h / 2, h / 4):
            for index, sign in enumerate((-1, 1)):
                voltage, command = (
                    (vb, x + sign * step)
                    if axis == "power"
                    else (x + sign * step, power)
                )
                point = power_command(stage, voltage, command, actuator)
                if point.limited or point.p >= stage.p_limit:
                    raise ValueError(
                        "Linearization stencil reaches command clipping; choose an interior operating point"
                    )
                modes.append(point.mode_id)
                value = mapped_power(stage, voltage, command, include_cp, actuator)
                sides[index].append((value - f0) / (sign * step))
        left, right = [2 * side[2] - side[1] for side in sides]
        previous = [2 * side[1] - side[0] for side in sides]
        tolerance = 1e-6 + 2e-5 * max(scale, abs(left), abs(right))
        converged = max(abs(left - previous[0]), abs(right - previous[1])) <= tolerance
        smooth = abs(left - right) <= tolerance
        evidence["axes"][axis] = {
            "steps": [h, h / 2, h / 4],
            "left_slopes": sides[0],
            "right_slopes": sides[1],
            "left_limit": left,
            "right_limit": right,
            "tolerance": tolerance,
            "converged": bool(converged),
            "smooth": bool(smooth),
            "stencil_modes": modes,
        }
        if not converged or not smooth:
            raise ValueError(
                f"Nonsmooth or unconverged TPS {axis} Jacobian: left={left:.9g}, right={right:.9g}; no unique local linear model"
            )
        derivatives.append((left + right) / 2)
    return *derivatives, evidence


def forward_sps_power(stage, vb_v, phase_pu):
    """Diagnostic SPS contract D=phi/pi, phases=(0,D,D), forward D in [0,.5]."""
    stage.validate()
    if (
        not math.isfinite(vb_v)
        or vb_v <= 0
        or not math.isfinite(phase_pu)
        or not 0 <= phase_pu <= 0.5
    ):
        raise ValueError("Forward SPS requires positive Vout and phase D in [0, 0.5]")
    coefficient = (
        stage.va_v
        * (vb_v / stage.turns_ratio)
        / (2 * stage.switching_hz * stage.inductance_h)
    )
    return {
        "power_w": coefficient * phase_pu * (1 - phase_pu),
        "dp_dd_w_per_pu": coefficient * (1 - 2 * phase_pu),
        "phases": (0.0, phase_pu, phase_pu),
        "interior_authority": bool(0 < phase_pu < 0.5),
        "boundary": "Diagnostic ideal-L raw-phase derivative; selectable SPS Pcmd [W] actuator uses inverse power mapping. Reverse power unsupported.",
    }


def power_command(stage, vb, pref, actuator="tps_power"):
    command = tps_command(stage, vb, pref)
    if actuator == "tps_power":
        return command
    if actuator != "sps_power":
        raise ValueError("Unknown DAB actuator")
    d = 0.5 * (1 - math.sqrt(1 - command.p))
    return replace(command, d1=0.0, d2=d, d3=d, mode_id=0, p_boundary=0.0)


def mapped_power(stage, vb, pref, include_cp=False, actuator="tps_power"):
    command = power_command(stage, vb, pref, actuator)
    if include_cp:
        return periodic_lc_power(stage, vb, (command.d1, command.d2, command.d3))
    return exact_tps_metrics(stage, vb, (command.d1, command.d2, command.d3)).power_w


def build_dab_voltage_loop(stage, vb_v, target_w, control, controller=None):
    stage.validate()
    control.validate()
    if vb_v <= 0 or target_w <= 0:
        raise ValueError("Positive operating voltage/power required")
    if control.sample_hz > stage.switching_hz:
        raise ValueError("Voltage-loop sample rate cannot exceed switching frequency")
    if power_command(stage, vb_v, target_w, control.actuator).limited:
        raise ValueError("Operating command is clipped; no local linear model")
    kp, kv, derivative_evidence = power_map_jacobian(
        stage, vb_v, target_w, control.include_cp, control.actuator
    )
    equilibrium = mapped_power(
        stage, vb_v, target_w, control.include_cp, control.actuator
    )
    if (
        not all(math.isfinite(value) for value in (kp, kv, equilibrium))
        or equilibrium <= 0
    ):
        raise ValueError("Finite positive operating output power required")
    if kp <= 1e-9:
        raise ValueError(
            "No local power-command control authority at this operating point"
        )
    load_slope = 2 * equilibrium / vb_v if control.load_model == "resistance" else 0
    pole = (load_slope - kv) / (stage.output_capacitance_f * vb_v)
    gain = kp / (stage.output_capacitance_f * vb_v)
    ctrl = controller or control.controller()
    if not np.isclose(ctrl.sample_time_s, 1 / control.sample_hz, rtol=1e-12, atol=0):
        raise ValueError("Controller sample period must match voltage-loop sample rate")
    physical = sampled_loop(
        [gain], [1, pole], ctrl, delay_ticks=control.delay_ticks, method="euler"
    )
    sense_b, sense_a = control.sensing.coefficients(control.sample_hz)
    composite = discrete_loop(
        np.convolve(physical.plant_b, sense_b),
        np.convolve(physical.plant_a, sense_a),
        ctrl,
    )
    f = np.geomspace(
        control.frequency_min_hz,
        min(control.frequency_max_hz, 0.49 * control.sample_hz),
        1200,
    )
    w = 2 * np.pi * f / control.sample_hz
    g = signal.freqz(physical.plant_b, physical.plant_a, worN=w)[1]
    h = signal.freqz(sense_b, sense_a, worN=w)[1]
    c = signal.freqz(composite.controller_b, composite.controller_a, worN=w)[1]
    op = g * h * c
    from llc_design.control.digital_loop import calculate_stability_margins

    loop = DABSampledLoop(
        **{
            **composite.__dict__,
            "frequency_hz": f,
            "plant_b": physical.plant_b,
            "plant_a": physical.plant_a,
            "plant_response": g,
            "controller_response": c,
            "open_response": op,
            "closed_response": op / (1 + op),
            "sensitivity": 1 / (1 + op),
            "margins": calculate_stability_margins(f, op),
        },
        sense_b=sense_b,
        sense_a=sense_a,
        sense_response=h,
        output_reference_response=g * c / (1 + op),
    )
    headroom = control.sensing.headroom(vb_v)
    if not headroom.within_range:
        raise ValueError("Nominal output exceeds ADC range: " + headroom.detail)
    return loop, {
        "operating_voltage_v": vb_v,
        "power_command_w": target_w,
        "operating_output_w": equilibrium,
        "load_resistance_ohm": vb_v**2 / equilibrium,
        "dpout_dpcmd": kp,
        "derivative_evidence": derivative_evidence,
        "sensing": asdict(control.sensing),
        "adc_headroom": asdict(headroom),
        "adc_lsb_output_v": control.sensing.adc_reference_v
        / (2**control.sensing.adc_bits * control.sensing.divider_gain),
        "measurement_model": "Incremental engineering V about nominal trim; exponential digital low-pass and integer measurement delay. ADC quantization/aperture/aliasing excluded.",
        "timing": {
            "command_delay_ticks": control.delay_ticks,
            "measurement_delay_ticks": control.sensing.delay_ticks,
            "command_hold": "Pcmd held for one energy-update interval; included once in Euler plant",
        },
        "frequency_requested_hz": [control.frequency_min_hz, control.frequency_max_hz],
        "frequency_actual_hz": [float(f[0]), float(f[-1])],
        "dpout_dvout_w_per_v": kv,
        "plant_numerator": [gain],
        "plant_denominator": [1, pole],
        "plant_discretization": "Forward Euler: Jacobian of the same capacitor-energy update used in time simulation",
        "power_map": "Event-exact periodic RLC"
        if control.include_cp
        else "Exact ideal-L segments",
        "transfer": "GvP(s) = (dPout/dPcmd)/(C*V0*s + dPload/dV - dPout/dV)",
        "actuator": control.actuator,
        "actuator_contract": "Controller Pcmd [W] -> p=Pcmd/Pbase(Vout); TPS branches or forward SPS D=(1-sqrt(1-p))/2, phases=(0,D,D); reverse power unsupported",
        "boundary": "Averaged bus + periodic steady-state power; no Cp transient states",
    }


def simulate_dab_voltage_loop(
    stage, vb_v, target_w, control, controller=None, *, cancelled=None
):
    loop, model = build_dab_voltage_loop(stage, vb_v, target_w, control, controller)
    ts = 1 / control.sample_hz
    count = round(control.duration_s / ts) + 1
    t = np.arange(count) * ts
    names = [
        "vout_v",
        "vref_v",
        "measured_v",
        "pcmd_w",
        "pout_w",
        "pload_w",
        "d1",
        "d2",
        "d3",
        "saturated",
        "energy_error_j",
    ]
    data = {name: np.zeros(count) for name in names}
    b, a = loop.controller_b, loop.controller_a
    # DFII transposed incremental controller operates around Pcmd0.
    order = max(len(a), len(b)) - 1
    b = np.pad(b, (0, order + 1 - len(b)))
    a = np.pad(a, (0, order + 1 - len(a)))
    state = np.zeros(order)
    energy = 0.5 * stage.output_capacitance_f * vb_v**2
    initial = energy
    net = 0.0
    voltage = vb_v
    queue = [target_w] * control.delay_ticks
    load0 = model["operating_output_w"]
    sb, sa = loop.sense_b, loop.sense_a
    sense_state = np.zeros(max(len(sb), len(sa)) - 1)
    for i, time_s in enumerate(t):
        if cancelled is not None and cancelled():
            raise ValueError("DAB analysis cancelled")
        after = time_s >= control.disturbance_time_s
        reference = vb_v + (control.reference_step_v if after else 0)
        measured_delta, sense_state = signal.lfilter(
            sb, sa, [voltage - vb_v], zi=sense_state
        )
        measured = vb_v + float(measured_delta[0])
        error = reference - measured
        increment = b[0] * error + (state[0] if order else 0)
        raw = target_w + increment
        maximum = tps_command(stage, voltage, 0).pbase_w * stage.p_limit
        command = float(np.clip(raw, 0, maximum))
        # Conditional state update, with error*command direction, avoids frozen recovery.
        saturated = command != raw
        if not saturated or error * (raw - command) <= 0:
            next_state = np.zeros(order)
            for j in range(order):
                next_state[j] = (
                    (state[j + 1] if j + 1 < order else 0)
                    + b[j + 1] * error
                    - a[j + 1] * increment
                )
            state = next_state
        applied = queue.pop(0) if queue else command
        if control.delay_ticks:
            queue.append(command)
        delivered = mapped_power(
            stage, voltage, applied, control.include_cp, control.actuator
        )
        factor = control.load_step_ratio if after else 1
        consumed = (
            load0
            * factor
            * ((voltage / vb_v) ** 2 if control.load_model == "resistance" else 1)
        )
        phase = power_command(stage, voltage, applied, control.actuator)
        values = [
            voltage,
            reference,
            measured,
            command,
            delivered,
            consumed,
            phase.d1,
            phase.d2,
            phase.d3,
            float(saturated),
            energy - initial - net,
        ]
        for key, value in zip(names, values):
            data[key][i] = value
        if i < count - 1:
            change = ts * (delivered - consumed)
            energy += change
            net += change
            if not math.isfinite(energy) or energy <= 0:
                raise ValueError("DAB bus energy collapsed")
            voltage = math.sqrt(2 * energy / stage.output_capacitance_f)
    metrics = {
        "loop": loop.summary(),
        "model": model,
        "final_error_v": float(data["vout_v"][-1] - data["vref_v"][-1]),
        "max_energy_error_j": float(np.max(abs(data["energy_error_j"]))),
        "saturation_fraction": float(np.mean(data["saturated"])),
    }
    return t, data, metrics, loop
