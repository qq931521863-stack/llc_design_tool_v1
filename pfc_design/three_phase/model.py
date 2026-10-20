"""Averaged two-level AFE model; current positive from grid into converter.

Amplitude-invariant, cosine-aligned Park convention:
  d = alpha*cos(theta)+beta*sin(theta), q = -alpha*sin(theta)+beta*cos(theta)
  L*did = vd-vcd-R*id+omega*L*iq
  L*diq = vq-vcq-R*iq-omega*L*id
The plant is integrated in stationary abc; only sampled PLL angle drives control.
No Vienna midpoint, no direct ideal-grid-angle feed to the controller.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .exact_control import (
    DF2T,
    ExactController,
    SampledSense,
    adc_headroom,
    controller_for,
)


def abc_to_dq(abc, theta: float) -> tuple[float, float]:
    a, b, c = abc
    alpha = (2 * a - b - c) / 3
    beta = (b - c) / math.sqrt(3)
    cs, sn = math.cos(theta), math.sin(theta)
    return alpha * cs + beta * sn, -alpha * sn + beta * cs


def dq_to_abc(d: float, q: float, theta: float) -> np.ndarray:
    cs, sn = math.cos(theta), math.sin(theta)
    alpha, beta = d * cs - q * sn, d * sn + q * cs
    return np.array(
        (
            alpha,
            -0.5 * alpha + math.sqrt(3) * beta / 2,
            -0.5 * alpha - math.sqrt(3) * beta / 2,
        )
    )


@dataclass(frozen=True)
class ThreePhaseConfig:
    line_line_rms_v: float = 400.0
    grid_hz: float = 50.0
    nominal_hz: float = 50.0
    bus_voltage_v: float = 700.0
    output_power_w: float = 10000.0
    inductance_h: float = 600e-6
    resistance_ohm: float = 0.08
    bus_capacitance_f: float = 2200e-6
    control_hz: float = 20000.0
    voltage_loop_hz: float = 2000.0
    current_kp: float = 3.77
    current_ki: float = 503.0
    voltage_kp: float = 0.5
    voltage_ki: float = 20.0
    current_controller: ExactController | None = None
    voltage_controller: ExactController | None = None
    current_sense: SampledSense = field(default_factory=SampledSense)
    voltage_sense: SampledSense = field(default_factory=SampledSense)
    voltage_delay_ticks: int = 0
    load_model: str = "constant_power"
    analysis_min_hz: float = 0.05
    analysis_max_hz: float = 20000.0
    analysis_points: int = 1200
    current_limit_a: float = 60.0
    iq_reference_a: float = 0.0
    current_slew_a_per_s: float = 2000.0
    modulation_limit: float = 0.98
    pwm_delay_ticks: int = 1
    pll_kind: str = "SRF"
    pll_bandwidth_hz: float = 20.0  # natural frequency, not closed-loop -3dB bandwidth
    pll_damping: float = 0.707
    pll_min_hz: float = 40.0
    pll_max_hz: float = 70.0
    pll_initial_deg: float = -30.0
    pll_lock_error: float = 0.02
    pll_lock_time_s: float = 0.01
    duration_s: float = 0.3
    integration_substeps: int = 4
    load_ramp_s: float = 0.05
    disturbance_time_s: float = 0.15
    grid_after_hz: float = 50.0
    phase_step_deg: float = 0.0
    load_step_ratio: float = 1.0

    def validate(self):
        if (
            type(self.analysis_points) is not int
            or not 100 <= self.analysis_points <= 10000
        ):
            raise ValueError("analysis points must be integer 100..10000")
        if not 0 < self.analysis_min_hz < self.analysis_max_hz:
            raise ValueError(
                "analysis frequency bounds must be positive and increasing"
            )
        if self.pll_kind not in ("SRF", "DDSRF", "DDSOGI"):
            raise ValueError("Choose SRF, DDSRF or DDSOGI PLL")
        if self.load_model not in ("constant_power", "resistance"):
            raise ValueError("unknown load model")
        for loop, rate in (
            ("current", self.control_hz),
            ("voltage", self.voltage_loop_hz),
        ):
            exact = getattr(self, loop + "_controller")
            if exact is not None and (
                not isinstance(exact, ExactController) or exact.sample_rate_hz != rate
            ):
                raise ValueError(f"{loop} H(z) sample rate mismatch")
            sense = getattr(self, loop + "_sense")
            if not isinstance(sense, SampledSense):
                raise TypeError("sense must be SampledSense")
            sense.validate()
        if (
            type(self.voltage_delay_ticks) is not int
            or not 0 <= self.voltage_delay_ticks <= 5
        ):
            raise ValueError("voltage delay ticks must be integer 0..5")
        for name, value in asdict(self).items():
            if name in (
                "pll_kind",
                "load_model",
                "current_controller",
                "voltage_controller",
                "current_sense",
                "voltage_sense",
            ):
                continue
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        for name in (
            "line_line_rms_v",
            "grid_hz",
            "nominal_hz",
            "bus_voltage_v",
            "inductance_h",
            "bus_capacitance_f",
            "control_hz",
            "voltage_loop_hz",
            "current_limit_a",
            "current_slew_a_per_s",
            "pll_bandwidth_hz",
            "pll_damping",
            "pll_min_hz",
            "pll_max_hz",
            "pll_lock_error",
            "pll_lock_time_s",
            "duration_s",
            "load_ramp_s",
            "grid_after_hz",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in (
            "output_power_w",
            "resistance_ohm",
            "current_kp",
            "current_ki",
            "voltage_kp",
            "voltage_ki",
            "load_step_ratio",
            "disturbance_time_s",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        ratio = self.control_hz / self.voltage_loop_hz
        if ratio < 1 or abs(ratio - round(ratio)) > 1e-9:
            raise ValueError("voltage-loop rate must divide current-loop rate")
        if not 0 < self.modulation_limit <= 1:
            raise ValueError("modulation limit must be in (0,1]")
        if not self.pll_min_hz < self.nominal_hz < self.pll_max_hz:
            raise ValueError("PLL nominal frequency must be inside frequency limits")
        if abs(self.iq_reference_a) > self.current_limit_a:
            raise ValueError("Iq reference exceeds current-vector limit")
        if (
            type(self.integration_substeps) is not int
            or not 2 <= self.integration_substeps <= 32
            or type(self.pwm_delay_ticks) is not int
            or not 0 <= self.pwm_delay_ticks <= 5
        ):
            raise ValueError(
                "integration_substeps must be 2..32; delay ticks must be 0..5"
            )
        if self.control_hz < 20 * max(
            self.grid_hz, self.grid_after_hz, self.pll_bandwidth_hz
        ):
            raise ValueError("control sample rate too low for grid / PLL")
        if self.duration_s < 3 / min(self.grid_hz, self.grid_after_hz):
            raise ValueError("simulate at least three line cycles")
        if self.duration_s * self.control_hz * self.integration_substeps > 2000000:
            raise ValueError("simulation exceeds two million integration steps")
        if (
            self.bus_voltage_v
            <= math.sqrt(2) * self.line_line_rms_v / self.modulation_limit
        ):
            raise ValueError("DC bus lacks two-level boost/SVPWM voltage headroom")


class SRFPLL:
    """Normalized SRF PI/VCO with frequency clamp and qualified lock.

    Public step consumes measured abc only. Returns the angle at the sample;
    internal theta advances to the next sample. Low voltage invalidates lock.
    """

    def __init__(self, config: ThreePhaseConfig):
        config.validate()
        self.config = config
        self.theta = math.radians(config.pll_initial_deg) % (2 * math.pi)
        self.integral = 0.0
        self.frequency_hz = config.nominal_hz
        self.error = 0.0
        self.lock_count = 0
        self.locked = False

    def step(self, voltage_abc) -> float:
        cfg = self.config
        theta = self.theta
        vd, vq = abc_to_dq(voltage_abc, theta)
        amplitude = math.hypot(vd, vq)
        valid = math.isfinite(
            amplitude
        ) and amplitude > 0.2 * cfg.line_line_rms_v * math.sqrt(2 / 3)
        dt = 1 / cfg.control_hz
        if valid:
            self.error = vq / amplitude
            wn = 2 * math.pi * cfg.pll_bandwidth_hz
            kp, ki = 2 * cfg.pll_damping * wn, wn * wn
            candidate = self.integral + ki * self.error * dt
            raw = 2 * math.pi * cfg.nominal_hz + kp * self.error + candidate
            omega = float(
                np.clip(raw, 2 * math.pi * cfg.pll_min_hz, 2 * math.pi * cfg.pll_max_hz)
            )
            self.integral = candidate + (omega - raw)
            self.frequency_hz = omega / (2 * math.pi)
            qualifies = vd > 0 and abs(self.error) < cfg.pll_lock_error
            self.lock_count = self.lock_count + 1 if qualifies else 0
        else:
            self.error = 0.0
            self.lock_count = 0
        self.locked = self.lock_count >= math.ceil(cfg.pll_lock_time_s * cfg.control_hz)
        self.theta = (theta + 2 * math.pi * self.frequency_hz * dt) % (2 * math.pi)
        return theta


def svpwm(
    voltage_abc, bus_v: float, limit: float = 0.98
) -> tuple[np.ndarray, np.ndarray, bool]:
    """Centered carrier-equivalent SVPWM with circular linear-region limiting.

    Returns upper-switch duties and actual floating-neutral average voltage.
    Lower ideal duties are complementary. Deadtime is not represented.
    """
    if not math.isfinite(bus_v) or bus_v <= 0 or not 0 < limit <= 1:
        raise ValueError("SVPWM requires positive Vbus and limit in (0,1]")
    abc = np.asarray(voltage_abc, dtype=float)
    if abc.shape != (3,) or not np.all(np.isfinite(abc)):
        raise ValueError("SVPWM expects three finite voltage commands")
    abc = abc - np.mean(abc)
    magnitude = math.sqrt(2 / 3 * float(np.dot(abc, abc)))
    max_magnitude = limit * bus_v / math.sqrt(3)
    scale = min(1.0, max_magnitude / max(magnitude, 1e-12))
    abc = abc * scale
    common = -0.5 * (np.max(abc) + np.min(abc))
    duty = np.clip(0.5 + (abc + common) / bus_v, 0, 1)
    actual = bus_v * (duty - np.mean(duty))
    return duty, actual, scale < 1 - 1e-12


@dataclass(frozen=True)
class ThreePhaseResult:
    config: ThreePhaseConfig
    time_s: np.ndarray
    signals: dict[str, np.ndarray]
    metrics: dict
    warnings: tuple[str, ...]


def simulate_three_phase(
    config: ThreePhaseConfig, *, cancel_event=None
) -> ThreePhaseResult:
    config.validate()
    dt = 1 / config.control_hz
    h = dt / config.integration_substeps
    time = np.arange(round(config.duration_s / dt) + 1) * dt
    names = [
        f"{prefix}_{p}" for prefix in ("vac", "current", "duty", "vconv") for p in "abc"
    ]
    names += [
        "current_error_d_a",
        "current_error_q_a",
        "current_tracking_d_v",
        "current_tracking_q_v",
        "voltage_error_v",
        "voltage_controller_raw_a",
        "controller_d_v",
        "controller_q_v",
        "id_command_a",
        "id_feedback_a",
        "iq_feedback_a",
        "vbus_feedback_v",
        "id_a",
        "iq_a",
        "id_ref_a",
        "iq_ref_a",
        "vd_v",
        "vq_v",
        "vbus_v",
        "pll_theta_rad",
        "grid_theta_rad",
        "pll_hz",
        "pll_error",
        "pll_locked",
        "modulation_limited",
        "pin_w",
        "pconv_w",
        "pload_w",
        "resistive_loss_w",
        "total_energy_j",
        "energy_balance_error_j",
    ]
    signals = {name: np.zeros(len(time)) for name in names}
    from .pll import GridPLL, PLLConfig

    pll = GridPLL(
        PLLConfig(
            sample_hz=config.control_hz,
            nominal_hz=config.nominal_hz,
            bandwidth_hz=config.pll_bandwidth_hz,
            damping=config.pll_damping,
            minimum_hz=config.pll_min_hz,
            maximum_hz=config.pll_max_hz,
            nominal_peak_v=config.line_line_rms_v * math.sqrt(2 / 3),
            initial_angle_deg=config.pll_initial_deg,
            lock_error=config.pll_lock_error,
            lock_time_s=config.pll_lock_time_s,
        ),
        config.pll_kind,
    )
    vpk = config.line_line_rms_v * math.sqrt(2 / 3)
    current = np.zeros(3)
    bus_energy = 0.5 * config.bus_capacitance_f * config.bus_voltage_v**2
    initial_energy = bus_energy
    energy_input = 0.0
    bus_v = config.bus_voltage_v
    ci, cv = controller_for(config, "current"), controller_for(config, "voltage")
    current_controllers = [DF2T(ci), DF2T(ci)]
    voltage_controller = DF2T(
        cv,
        config.output_power_w / (1.5 * vpk)
        if config.voltage_controller is None
        else 0.0,
    )
    current_sensors = [
        DF2T(config.current_sense.controller(config.control_hz)) for _ in range(2)
    ]
    voltage_sensor = DF2T(config.voltage_sense.controller(config.voltage_loop_hz))
    # Initialize bus sensing at the precharged operating point, without a fictitious filter startup.
    if voltage_sensor.state.size:
        from scipy.signal import lfilter_zi

        sense_tf = voltage_sensor.controller
        voltage_sensor.state = lfilter_zi(sense_tf.b, sense_tf.a) * bus_v
    voltage_queue = [0.0] * config.voltage_delay_ticks
    id_command = id_reference = 0.0
    error_v = raw_v_command = 0.0
    sensed_bus = bus_v * config.voltage_sense.gain
    outer_divider = round(config.control_hz / config.voltage_loop_hz)
    initial_duty, _, _ = svpwm(dq_to_abc(vpk, 0, 0), bus_v, config.modulation_limit)
    queue = [initial_duty.copy() for _ in range(config.pwm_delay_ticks)]

    def grid(t):
        after = max(0.0, t - config.disturbance_time_s)
        phase = (
            2
            * math.pi
            * (config.grid_hz * t + (config.grid_after_hz - config.grid_hz) * after)
        )
        if t >= config.disturbance_time_s:
            phase += math.radians(config.phase_step_deg)
        return dq_to_abc(vpk, 0, phase), phase

    def load(t, voltage=None):
        factor = config.load_step_ratio if t >= config.disturbance_time_s else 1.0
        voltage = bus_v if voltage is None else voltage
        load_factor = (
            (voltage / config.bus_voltage_v) ** 2
            if config.load_model == "resistance"
            else 1.0
        )
        return (
            config.output_power_w
            * min(t / config.load_ramp_s, 1)
            * factor
            * load_factor
        )

    for k, t in enumerate(time):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("dq simulation cancelled")
        voltage, grid_theta = grid(t)
        theta = pll.step(voltage)
        vd, vq = abc_to_dq(voltage, theta)
        idq = np.array(abc_to_dq(current, theta))
        sensed_idq = np.array(
            [sensor.advance(value) for sensor, value in zip(current_sensors, idq)]
        )
        if k % outer_divider == 0:
            sensed_bus = voltage_sensor.advance(bus_v)
            error_v = config.bus_voltage_v - sensed_bus
            raw = voltage_controller.output(error_v)
            raw_v_command = raw
            d_limit = math.sqrt(
                max(0, config.current_limit_a**2 - config.iq_reference_a**2)
            )
            command = float(np.clip(raw, 0, d_limit)) if pll.locked else 0.0
            voltage_controller.advance(error_v, command - raw, enabled=pll.locked)
            voltage_queue.append(command)
            id_command = voltage_queue.pop(0)
        target = id_command if pll.locked else 0.0
        delta = config.current_slew_a_per_s * dt
        id_reference += float(np.clip(target - id_reference, -delta, delta))
        iq_reference = config.iq_reference_a if pll.locked else 0.0
        error_i = np.array((id_reference, iq_reference)) - sensed_idq
        u = np.array(
            [
                controller.output(error)
                for controller, error in zip(current_controllers, error_i)
            ]
        )
        omega_l = 2 * math.pi * pll.frequency_hz * config.inductance_h
        raw_dq = np.array((vd + omega_l * idq[1], vq - omega_l * idq[0])) - u
        duty, actual, limited = svpwm(
            dq_to_abc(*raw_dq, theta), bus_v, config.modulation_limit
        )
        actual_dq = np.array(abc_to_dq(actual, theta))
        # Back-calculation sign follows u = feedforward - converter voltage.
        correction = min(1.0, 1000 * dt) * (raw_dq - actual_dq)
        for controller, error, tracking in zip(
            current_controllers, error_i, correction
        ):
            controller.advance(error, tracking)
        queue.append(duty)
        applied = queue.pop(0)
        vconv = bus_v * (applied - np.mean(applied))
        pin, pconv = float(voltage @ current), float(vconv @ current)
        loss = config.resistance_ohm * float(current @ current)
        energy = bus_energy + 0.5 * config.inductance_h * float(current @ current)
        for j, phase in enumerate("abc"):
            for prefix, value in (
                ("vac", voltage[j]),
                ("current", current[j]),
                ("duty", applied[j]),
                ("vconv", vconv[j]),
            ):
                signals[f"{prefix}_{phase}"][k] = value
        values = {
            "current_error_d_a": error_i[0],
            "current_error_q_a": error_i[1],
            "current_tracking_d_v": correction[0],
            "current_tracking_q_v": correction[1],
            "voltage_error_v": error_v,
            "voltage_controller_raw_a": raw_v_command,
            "controller_d_v": u[0],
            "controller_q_v": u[1],
            "id_command_a": id_command,
            "id_feedback_a": sensed_idq[0],
            "iq_feedback_a": sensed_idq[1],
            "vbus_feedback_v": sensed_bus,
            "id_a": idq[0],
            "iq_a": idq[1],
            "id_ref_a": id_reference,
            "iq_ref_a": iq_reference,
            "vd_v": vd,
            "vq_v": vq,
            "vbus_v": bus_v,
            "pll_theta_rad": theta,
            "grid_theta_rad": grid_theta % (2 * math.pi),
            "pll_hz": pll.frequency_hz,
            "pll_error": pll.error,
            "pll_locked": float(pll.locked),
            "modulation_limited": float(limited),
            "pin_w": pin,
            "pconv_w": pconv,
            "pload_w": load(t),
            "resistive_loss_w": loss,
            "total_energy_j": energy,
            "energy_balance_error_j": energy - initial_energy - energy_input,
        }
        for key, value in values.items():
            signals[key][k] = value
        if k == len(time) - 1:
            break
        # Midpoint integration of phase currents AND physical capacitor energy.
        for step in range(config.integration_substeps):
            ts = t + step * h
            volts, _ = grid(ts)
            conv = bus_v * (applied - np.mean(applied))
            di = (volts - conv - config.resistance_ohm * current) / config.inductance_h
            de = float(conv @ current) - load(ts)
            imid = current + 0.5 * h * di
            emid = bus_energy + 0.5 * h * de
            if emid <= 0:
                raise ValueError(
                    "DC bus energy collapsed: reduce load / retune / increase capacitance"
                )
            vmid = math.sqrt(2 * emid / config.bus_capacitance_f)
            vgrid_mid, _ = grid(ts + 0.5 * h)
            conv_mid = vmid * (applied - np.mean(applied))
            current += (
                h
                * (vgrid_mid - conv_mid - config.resistance_ohm * imid)
                / config.inductance_h
            )
            current -= np.mean(current)
            bus_energy += h * (float(conv_mid @ imid) - load(ts + 0.5 * h, vmid))
            energy_input += h * (
                float(vgrid_mid @ imid)
                - config.resistance_ohm * float(imid @ imid)
                - load(ts + 0.5 * h, vmid)
            )
            if (
                not math.isfinite(bus_energy)
                or bus_energy <= 0
                or np.max(np.abs(current)) > 1e5
            ):
                raise ValueError(
                    "unbounded simulation / DC bus collapse; result withheld"
                )
            bus_v = math.sqrt(2 * bus_energy / config.bus_capacitance_f)

    tail_hz = (
        config.grid_after_hz if time[-1] > config.disturbance_time_s else config.grid_hz
    )
    count = round(config.control_hz / tail_hz)
    # End-exclusive last complete sample interval; enough duration is validated.
    sl = slice(len(time) - 1 - count, len(time) - 1)
    previous = slice(len(time) - 1 - 2 * count, len(time) - 1 - count)
    vrms = np.array([np.sqrt(np.mean(signals[f"vac_{p}"][sl] ** 2)) for p in "abc"])
    irms = np.array([np.sqrt(np.mean(signals[f"current_{p}"][sl] ** 2)) for p in "abc"])
    pin_mean = float(np.mean(signals["pin_w"][sl]))
    vbus_mean = float(np.mean(signals["vbus_v"][sl]))
    phase_error = np.angle(
        np.exp(1j * (signals["grid_theta_rad"] - signals["pll_theta_rad"]))
    )
    regulated_target_v = config.bus_voltage_v / config.voltage_sense.gain
    settled = (
        np.all(signals["pll_locked"][sl] > 0.5)
        and abs(vbus_mean - regulated_target_v) < 0.02 * regulated_target_v
        and abs(pin_mean - np.mean(signals["pin_w"][previous]))
        < 0.02 * max(abs(pin_mean), 1)
    )
    warnings = [
        "Averaged ideal six-switch model; no deadtime, device losses, ADC quantization/clipping or hardware verification.",
        "Exact DF2T controllers; tracking correction enters first state, voltage state freezes on PLL unlock. Generic H(z) antiwindup is not a stability guarantee.",
        "Sense: calibrated sample-domain dq/bus gain, matched-z filter and integer latency; no analog anti-alias/aperture or PLL sensor coupling.",
        f"PLL={config.pll_kind}; separate comparison experiment covers negative sequence and harmonics.",
        "Starts with precharged DC bus; no rectifier diode/precharge/startup protection model.",
    ]
    if not settled:
        warnings.append(
            "Final line cycles are not settled: PF and current errors are transient diagnostics."
        )
    if np.any(signals["modulation_limited"][sl]):
        warnings.append("Final-cycle voltage command reaches SVPWM limit.")
    from .control import request_identity

    metrics = {
        "source_identity": request_identity(config),
        "evidence": "SOFTWARE_MODEL_ONLY",
        "exact_controllers": {
            key: asdict(controller_for(config, key)) for key in ("current", "voltage")
        },
        "adc_headroom": adc_headroom(config),
        "settled": bool(settled),
        "pll_locked": bool(np.all(signals["pll_locked"][sl] > 0.5)),
        "pll_frequency_hz": float(np.mean(signals["pll_hz"][sl])),
        "pll_phase_error_peak_deg": float(
            np.max(np.abs(phase_error[sl])) * 180 / math.pi
        ),
        "vbus_average_v": vbus_mean,
        "vbus_regulated_target_v": regulated_target_v,
        "vbus_feedback_average_v": float(np.mean(signals["vbus_feedback_v"][sl])),
        "tracking_error_domain": "reference minus calibrated sampled feedback; physical Id/Iq and Vbus remain separate signals",
        "vbus_ripple_pp_v": float(np.ptp(signals["vbus_v"][sl])),
        "pin_w": pin_mean,
        "load_w": float(np.mean(signals["pload_w"][sl])),
        "power_factor": pin_mean / max(float(vrms @ irms), 1e-12),
        "phase_current_rms_a": irms.tolist(),
        "id_error_rms_a": float(
            np.sqrt(
                np.mean((signals["id_ref_a"][sl] - signals["id_feedback_a"][sl]) ** 2)
            )
        ),
        "iq_error_rms_a": float(
            np.sqrt(
                np.mean((signals["iq_ref_a"][sl] - signals["iq_feedback_a"][sl]) ** 2)
            )
        ),
        "modulation_limited_fraction": float(
            np.mean(signals["modulation_limited"][sl])
        ),
        "energy_balance_error_j": float(signals["energy_balance_error_j"][-1]),
    }
    return ThreePhaseResult(config, time, signals, metrics, tuple(warnings))


def switching_snapshot(
    result: ThreePhaseResult, cycles: int = 3, samples_per_cycle: int = 400
):
    """Ideal complementary six gates at one frozen average workpoint."""
    if cycles < 1 or samples_per_cycle < 20:
        raise ValueError("positive cycles and >=20 samples per cycle required")
    t = np.arange(cycles * samples_per_cycle) / (
        samples_per_cycle * result.config.control_hz
    )
    carrier = 1 - np.abs(2 * ((t * result.config.control_hz) % 1) - 1)
    duty = np.array([result.signals[f"duty_{p}"][-1] for p in "abc"])
    high = (duty[:, None] > carrier).astype(float)
    low = 1 - high
    pole = result.signals["vbus_v"][-1] * (high - 0.5)
    return t, high, low, pole, pole - np.mean(pole, axis=0)


def export_result(result: ThreePhaseResult, path: str | Path):
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time_s", *result.signals])
        writer.writerows(zip(result.time_s, *result.signals.values()))
    path.with_suffix(".json").write_text(
        json.dumps(
            {
                "schema": "three-phase-dq-result-v2",
                "config": asdict(result.config),
                "metrics": result.metrics,
                "warnings": result.warnings,
            },
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
