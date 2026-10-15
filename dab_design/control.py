"""DAB PI -> power command -> TPS -> capacitor/load voltage loop.

One-cycle steady-state power mapping + bus energy model. Not a transient
switching model of Cp/L. Controller coefficients are reused exactly from LLC.
"""

import math
from dataclasses import dataclass, replace

import numpy as np

from llc_design.control.digital_loop import PIControllerConfig
from power_control_tools.physical_loop import sampled_loop

from .core import periodic_waveform, tps_command
from .optimization import exact_tps_metrics


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


def mapped_power(stage, vb, pref, include_cp=False):
    command = tps_command(stage, vb, max(0, pref))
    if include_cp:
        return periodic_waveform(
            replace(stage, wave_samples=min(stage.wave_samples, 2048)),
            vb,
            command,
            include_cp=True,
        ).metrics["pout_w"]
    return exact_tps_metrics(stage, vb, (command.d1, command.d2, command.d3)).power_w


def build_dab_voltage_loop(stage, vb_v, target_w, control, controller=None):
    stage.validate()
    control.validate()
    if vb_v <= 0 or target_w <= 0:
        raise ValueError("Positive operating voltage/power required")
    if control.sample_hz > stage.switching_hz:
        raise ValueError("Voltage-loop sample rate cannot exceed switching frequency")
    if tps_command(stage, vb_v, target_w).limited:
        raise ValueError("Operating command is clipped; no local linear model")
    dp = min(target_w / 4, max(0.01, target_w * 1e-4))
    dv = min(vb_v / 4, max(0.001, vb_v * 1e-4))
    kp = (
        mapped_power(stage, vb_v, target_w + dp, control.include_cp)
        - mapped_power(stage, vb_v, target_w - dp, control.include_cp)
    ) / (2 * dp)
    kv = (
        mapped_power(stage, vb_v + dv, target_w, control.include_cp)
        - mapped_power(stage, vb_v - dv, target_w, control.include_cp)
    ) / (2 * dv)
    equilibrium = mapped_power(stage, vb_v, target_w, control.include_cp)
    load_slope = 2 * equilibrium / vb_v if control.load_model == "resistance" else 0
    pole = (load_slope - kv) / (stage.output_capacitance_f * vb_v)
    gain = kp / (stage.output_capacitance_f * vb_v)
    ctrl = controller or control.controller()
    if not np.isclose(ctrl.sample_time_s, 1 / control.sample_hz, rtol=1e-9):
        raise ValueError("Controller sample period must match voltage-loop sample rate")
    loop = sampled_loop([gain], [1, pole], ctrl, delay_ticks=control.delay_ticks)
    return loop, {
        "operating_voltage_v": vb_v,
        "power_command_w": target_w,
        "operating_output_w": equilibrium,
        "load_resistance_ohm": vb_v**2 / equilibrium,
        "dpout_dpcmd": kp,
        "dpout_dvout_w_per_v": kv,
        "plant_numerator": [gain],
        "plant_denominator": [1, pole],
        "transfer": "GvP(s) = (dPout/dPcmd)/(C*V0*s + dPload/dV - dPout/dV)",
        "actuator": "PI output Pcmd [W] -> p=Pcmd/Pbase(Vout) -> TPS(D1,D2,D3)",
        "boundary": "Averaged bus + periodic steady-state power; no Cp transient states",
    }


def simulate_dab_voltage_loop(stage, vb_v, target_w, control, controller=None):
    loop, model = build_dab_voltage_loop(stage, vb_v, target_w, control, controller)
    ts = 1 / control.sample_hz
    count = round(control.duration_s / ts) + 1
    t = np.arange(count) * ts
    names = [
        "vout_v",
        "vref_v",
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
    for i, time_s in enumerate(t):
        after = time_s >= control.disturbance_time_s
        reference = vb_v + (control.reference_step_v if after else 0)
        error = reference - voltage
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
        delivered = mapped_power(stage, voltage, applied, control.include_cp)
        factor = control.load_step_ratio if after else 1
        consumed = (
            load0
            * factor
            * ((voltage / vb_v) ** 2 if control.load_model == "resistance" else 1)
        )
        phase = tps_command(stage, voltage, applied)
        values = [
            voltage,
            reference,
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
