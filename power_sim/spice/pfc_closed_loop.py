"""Exact-H(z) TTPL PFC shared-ngspice closed-loop co-simulation.

ngspice owns the continuous four-switch power stage. Power Design Toolkit owns
the sampled sensing chain, float32 digital control, multi-rate scheduling,
firmware delays, zero-cross commutation state and gate generation.

The current and bus-voltage controllers are consumed from
:class:`PFCControlHandoff` exactly as normalized b/a coefficients. PI/PIF
anti-windup state parameters are recovered algebraically from those frozen
coefficients; this module never rebuilds H(z) from Kp/Ti and never performs an
S2Z conversion.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from pfc_design.control import PFCControlLabAnalysis
from pfc_design.control.firmware_runtime import (
    PFCExactFirmwareControllerRuntime,
    PFCSampledSenseRuntime,
    PFCZeroCrossRuntime,
)
from pfc_design.control.handoff import (
    PFCControlHandoff,
    assert_handoff_matches_analysis,
    build_pfc_control_handoff,
)

from .netlist import render_netlist
from .pfc import DutyTimeline, TTPLSpiceConfig, build_ttpl_pfc_circuit
from .pfc_gate_runtime import TTPLCommutationTimeline, TTPLFirmwareGateScheduler
from .shared import NgSpiceSharedLibrary


@dataclass(frozen=True)
class TTPLClosedLoopScenario:
    bus_reference_v: float
    bus_reference_final_v: float | None = None
    reference_step_time_s: float | None = None

    def validate(self) -> None:
        if self.bus_reference_v <= 0.0:
            raise ValueError("TTPL bus reference must be positive")
        if self.bus_reference_final_v is not None and self.bus_reference_final_v <= 0.0:
            raise ValueError("TTPL final bus reference must be positive")
        if self.reference_step_time_s is not None and self.reference_step_time_s < 0.0:
            raise ValueError("TTPL reference step time cannot be negative")
        if self.reference_step_time_s is not None and self.bus_reference_final_v is None:
            raise ValueError("TTPL reference step requires bus_reference_final_v")

    def reference_at(self, time_s: float) -> float:
        if (
            self.reference_step_time_s is not None
            and self.bus_reference_final_v is not None
            and float(time_s) >= self.reference_step_time_s
        ):
            return float(self.bus_reference_final_v)
        return float(self.bus_reference_v)


@dataclass(frozen=True)
class TTPLSharedNgSpiceConfig:
    duration_s: float
    input_phase_deg: float = 90.0
    output_step_s: float | None = None
    max_step_s: float | None = None
    wall_timeout_s: float = 120.0
    use_initial_conditions: bool = True
    additional_pwm_delay_s: float = 0.0
    shadow_update: bool = True
    record_vectors: tuple[str, ...] = (
        "time",
        "v(line)",
        "v(neutral)",
        "v(sw)",
        "v(bus)",
        "v(g_hf_h)",
        "v(g_hf_l)",
        "v(g_lf_p)",
        "v(g_lf_n)",
        "i(lboost)",
        "i(vac)",
    )

    def validate(self, switching_frequency_hz: float, current_rate_hz: float) -> tuple[float, float]:
        if self.duration_s <= 0.0:
            raise ValueError("TTPL shared-ngspice duration must be positive")
        if switching_frequency_hz <= 0.0 or current_rate_hz <= 0.0:
            raise ValueError("TTPL switching/control rates must be positive")
        if self.wall_timeout_s <= 0.0:
            raise ValueError("TTPL wall timeout must be positive")
        if self.additional_pwm_delay_s < 0.0:
            raise ValueError("TTPL additional PWM delay cannot be negative")
        period = 1.0 / switching_frequency_hz
        sample = 1.0 / current_rate_hz
        output_step = self.output_step_s or min(period / 24.0, sample / 8.0)
        max_step = self.max_step_s or min(period / 80.0, sample / 20.0)
        if output_step <= 0.0 or max_step <= 0.0:
            raise ValueError("TTPL transient steps must be positive")
        return float(output_step), float(max_step)


@dataclass(frozen=True)
class TTPLControlSample:
    time_s: float
    vac_v: float
    inductor_current_a: float
    bus_voltage_v: float
    vac_measured_v: float
    inductor_current_measured_a: float
    bus_voltage_measured_v: float
    vac_adc_code: int
    current_adc_code: int
    vbus_adc_code: int
    bus_reference_v: float
    vac_rms_estimate_v: float
    current_reference_a: float
    current_error_a: float
    current_controller_raw: float
    current_controller_output: float
    current_controller_saturated: bool
    current_anti_windup_frozen: bool
    voltage_error_v: float
    voltage_controller_delta_raw: float
    voltage_controller_absolute: float
    gcmd_a_per_v: float
    duty_feedforward: float
    indu_comp: float
    duty_unclamped: float
    duty_command: float
    duty_apply_time_s: float
    pwm_state_code: int
    zero_cross_active: bool
    current_pi_reset: bool


@dataclass(frozen=True)
class TTPLSharedNgSpiceMetrics:
    final_bus_voltage_v: float
    bus_ripple_pp_v: float
    peak_inductor_current_a: float
    input_current_rms_a: float | None
    real_input_power_w: float | None
    power_factor: float | None
    current_controller_saturation_fraction: float
    current_anti_windup_fraction: float
    zero_cross_fraction: float
    duty_min: float
    duty_max: float
    line_cycle_covered: bool


@dataclass(frozen=True)
class TTPLSharedNgSpiceResult:
    samples: tuple[TTPLControlSample, ...]
    vectors: dict[str, np.ndarray]
    metrics: TTPLSharedNgSpiceMetrics
    metadata: dict[str, object]
    simulator_messages: tuple[str, ...]
    circuit_netlist: str


def _callback_vector(point: dict[str, complex], name: str) -> complex | None:
    lowered = {str(key).lower(): value for key, value in point.items()}
    key = str(name).strip().lower()
    candidates = [key]
    if key.startswith("v(") and key.endswith(")"):
        node = key[2:-1].strip()
        if node:
            candidates.append(node)
    elif key.startswith("i(") and key.endswith(")"):
        refdes = key[2:-1].strip()
        if refdes:
            candidates.extend((f"{refdes}#branch", f"@{refdes}[i]"))
    for candidate in candidates:
        value = lowered.get(candidate)
        if value is not None:
            return value
    return None


def _lookup(point: dict[str, complex], *names: str) -> float | None:
    for name in names:
        value = _callback_vector(point, name)
        if value is not None:
            return float(value.real)
    return None


def _clip(value: float, minimum: float, maximum: float) -> float:
    return min(max(float(value), float(minimum)), float(maximum))


def _next_pwm_boundary(time_s: float, period_s: float, *, epsilon_s: float = 1e-15) -> float:
    index = math.floor((float(time_s) + epsilon_s) / period_s) + 1
    return index * period_s


def _metrics(
    vectors: dict[str, np.ndarray],
    samples: tuple[TTPLControlSample, ...],
    *,
    line_frequency_hz: float,
    duration_s: float,
) -> TTPLSharedNgSpiceMetrics:
    bus = np.asarray(vectors.get("v(bus)", []), dtype=float)
    line = np.asarray(vectors.get("v(line)", []), dtype=float)
    neutral = np.asarray(vectors.get("v(neutral)", []), dtype=float)
    current = np.asarray(vectors.get("i(lboost)", []), dtype=float)
    n = min(len(bus), len(line), len(neutral), len(current))
    if n == 0:
        raise RuntimeError("TTPL shared-ngspice did not return required power-stage vectors")
    bus = bus[:n]
    vac = line[:n] - neutral[:n]
    current = current[:n]
    finite = np.isfinite(bus) & np.isfinite(vac) & np.isfinite(current)
    if not np.any(finite):
        raise RuntimeError("TTPL shared-ngspice returned no finite electrical samples")
    bus_f = bus[finite]
    vac_f = vac[finite]
    current_f = current[finite]
    tail = max(8, int(0.1 * len(bus_f)))
    final_bus = float(np.mean(bus_f[-tail:]))
    ripple = float(np.ptp(bus_f[-tail:]))
    peak_i = float(np.max(np.abs(current_f)))
    covered = duration_s >= 0.95 / line_frequency_hz
    i_rms = pin = pf = None
    if covered and len(vac_f) >= 32:
        count = max(16, int(round(len(vac_f) * min(1.0, (1.0 / line_frequency_hz) / duration_s))))
        vv = vac_f[-count:]
        ii = current_f[-count:]
        vrms = float(np.sqrt(np.mean(vv * vv)))
        i_rms = float(np.sqrt(np.mean(ii * ii)))
        pin = float(np.mean(vv * ii))
        apparent = vrms * i_rms
        pf = pin / apparent if apparent > 1e-12 else None
    saturation_fraction = float(np.mean([sample.current_controller_saturated for sample in samples])) if samples else 0.0
    aw_fraction = float(np.mean([sample.current_anti_windup_frozen for sample in samples])) if samples else 0.0
    zc_fraction = float(np.mean([sample.zero_cross_active for sample in samples])) if samples else 0.0
    duties = [sample.duty_command for sample in samples] or [0.0]
    return TTPLSharedNgSpiceMetrics(
        final_bus_voltage_v=final_bus,
        bus_ripple_pp_v=ripple,
        peak_inductor_current_a=peak_i,
        input_current_rms_a=i_rms,
        real_input_power_w=pin,
        power_factor=pf,
        current_controller_saturation_fraction=saturation_fraction,
        current_anti_windup_fraction=aw_fraction,
        zero_cross_fraction=zc_fraction,
        duty_min=float(min(duties)),
        duty_max=float(max(duties)),
        line_cycle_covered=covered,
    )


def run_ttpl_shared_closed_loop(
    analysis: PFCControlLabAnalysis,
    *,
    scenario: TTPLClosedLoopScenario | None = None,
    simulation: TTPLSharedNgSpiceConfig,
    handoff: PFCControlHandoff | None = None,
    library: str | None = None,
) -> TTPLSharedNgSpiceResult:
    """Run TTPL PFC with exact H(z) plus firmware-faithful nonlinear semantics."""
    exact = handoff or build_pfc_control_handoff(analysis)
    exact.validate()
    assert_handoff_matches_analysis(analysis, exact)
    cfg = analysis.config
    stage = cfg.power_stage
    fw = cfg.firmware
    scenario = scenario or TTPLClosedLoopScenario(stage.bus_voltage_v)
    scenario.validate()
    output_step, max_step = simulation.validate(stage.switching_frequency_hz, exact.current.sample_rate_hz)

    spice = TTPLSpiceConfig.from_power_stage(stage, input_phase_deg=simulation.input_phase_deg)
    circuit = build_ttpl_pfc_circuit(spice)
    circuit.save_vectors = list(dict.fromkeys(simulation.record_vectors))
    uic = " uic" if simulation.use_initial_conditions else ""
    circuit.directives.append(
        f".tran {output_step:.12e} {simulation.duration_s:.12e} 0 {max_step:.12e}{uic}"
    )
    netlist = render_netlist(circuit)
    circuit_lines = [line for line in netlist.splitlines() if line.strip()]

    required_gcmd = stage.output_power_w / (stage.efficiency * stage.vin_rms_v**2)
    if fw.vff_bypass:
        voltage_bias = required_gcmd / max(fw.vac_rms_feedforward_gain, 1e-30)
    else:
        voltage_bias = required_gcmd * fw.vac_rms_feedforward_gain * stage.vin_rms_v**2
    voltage_bias = _clip(voltage_bias, exact.voltage.output_min, exact.voltage.output_max)

    current_rt = PFCExactFirmwareControllerRuntime(exact.current, initial_output=0.0)
    voltage_rt = PFCExactFirmwareControllerRuntime(exact.voltage, initial_output=voltage_bias)

    phase = math.radians(simulation.input_phase_deg)
    vac0 = stage.line_peak_v * math.sin(phase)
    initial_bus = spice.initial_bus_voltage_v or stage.bus_voltage_v
    vac_sense = PFCSampledSenseRuntime(cfg.vac_sense, vac0)
    current_sense = PFCSampledSenseRuntime(cfg.current_sense, 0.0)
    vbus_sense = PFCSampledSenseRuntime(cfg.vbus_sense, initial_bus)

    vac_rms_sq = np.float32(stage.vin_rms_v**2)
    vac_rms = stage.vin_rms_v
    voltage_absolute = voltage_bias
    gcmd = required_gcmd
    i_ref = gcmd * abs(vac0)
    duty_ff = _clip(1.0 - abs(vac0) / max(scenario.bus_reference_v, 1.0), stage.duty_min, stage.duty_max)
    indu_comp = _clip(fw.indu_comp_gain * i_ref, fw.indu_comp_min, fw.indu_comp_max)
    effective_duty_min = max(stage.duty_min, stage.minimum_effective_pulse_s * stage.switching_frequency_hz)
    duty0 = _clip(duty_ff, effective_duty_min, stage.duty_max)
    duty_timeline = DutyTimeline(duty0)

    zero_cross = PFCZeroCrossRuntime()
    zero_cross.reset(positive_half=vac0 >= 0.0)
    initial_state = 1 if vac0 >= 0.0 else 5
    commutation_timeline = TTPLCommutationTimeline(
        initial_state_code=initial_state,
        initial_lf_state=1 if initial_state == 1 else -1,
        initial_hf_softstart_fraction=1.0,
        initial_target_polarity=1 if initial_state == 1 else -1,
    )
    gates = TTPLFirmwareGateScheduler(spice, duty_timeline, commutation_timeline)

    current_period = 1.0 / exact.current.sample_rate_hz
    voltage_period = 1.0 / exact.voltage.sample_rate_hz
    amc_period = 1.0 / exact.amc_rate_hz
    pwm_period = 1.0 / stage.switching_frequency_hz

    # Controller execution begins after the configured acquisition/conversion
    # latency.  Thereafter each loop keeps its frozen sample rate.
    next_current = current_sense.acquisition_to_ready_s
    next_voltage = vbus_sense.acquisition_to_ready_s
    next_amc = vac_sense.acquisition_to_ready_s

    latest_vac = vac0
    latest_current = 0.0
    latest_bus = initial_bus
    latest_current_raw = 0.0
    latest_current_out = 0.0
    latest_current_sat = False
    latest_current_aw = False
    latest_voltage_error = scenario.bus_reference_v - initial_bus
    latest_voltage_raw = voltage_bias
    latest_duty_unclamped = duty0
    latest_zc_state = initial_state
    latest_zc_active = False
    tolerance = max(1e-12, current_period * 1e-7)
    samples: list[TTPLControlSample] = []

    pending_voltage: list[tuple[float, float, float, float, float]] = []
    pending_amc: list[tuple[float, float, float, float]] = []

    record_names = tuple(dict.fromkeys(name.lower() for name in simulation.record_vectors))
    vector_lists: dict[str, list[float]] = {name: [] for name in record_names}
    last_time = -math.inf

    def schedule_sorted(queue: list, item: tuple) -> None:
        queue.append(item)
        queue.sort(key=lambda entry: entry[0])

    def apply_pending(time_s: float) -> None:
        nonlocal voltage_absolute, gcmd, latest_voltage_error, latest_voltage_raw
        nonlocal i_ref, duty_ff, indu_comp
        while pending_voltage and pending_voltage[0][0] <= time_s + tolerance:
            _, voltage_absolute, gcmd, latest_voltage_error, latest_voltage_raw = pending_voltage.pop(0)
        while pending_amc and pending_amc[0][0] <= time_s + tolerance:
            _, i_ref, duty_ff, indu_comp = pending_amc.pop(0)

    def update_voltage(time_s: float) -> None:
        nonlocal vac_rms_sq, vac_rms
        vac_measured = vac_sense.output
        bus_measured = vbus_sense.output
        vac_sq = np.float32(vac_measured * vac_measured)
        alpha = np.float32(fw.vac_rms_lpf_alpha)
        vac_rms_sq = np.float32(vac_rms_sq + np.float32(alpha * np.float32(vac_sq - vac_rms_sq)))
        vac_rms = math.sqrt(max(float(vac_rms_sq), 1.0))
        error = scenario.reference_at(time_s) - bus_measured
        vstep = voltage_rt.step(error)
        absolute = vstep.output
        if fw.vff_bypass:
            vloop = absolute
        else:
            denom = max((fw.vac_rms_feedforward_gain * vac_rms) ** 2, 1.0)
            vloop = absolute / denom
        gcmd_target = _clip(vloop * fw.vac_rms_feedforward_gain, 0.0, fw.gcmd_max_a_per_v)
        apply_time = time_s + fw.voltage_computation_delay_s
        schedule_sorted(pending_voltage, (apply_time, absolute, gcmd_target, error, vstep.raw_output))
        apply_pending(time_s)

    def update_amc(time_s: float) -> None:
        vac_abs = abs(vac_sense.output)
        iref_target = gcmd * vac_abs
        vref = max(scenario.reference_at(time_s), 1.0)
        ff_target = _clip(1.0 - vac_abs / vref, stage.duty_min, stage.duty_max)
        comp_target = _clip(fw.indu_comp_gain * iref_target, fw.indu_comp_min, fw.indu_comp_max)
        apply_time = time_s + fw.amc_update_delay_s
        schedule_sorted(pending_amc, (apply_time, iref_target, ff_target, comp_target))
        apply_pending(time_s)

    def update_current(time_s: float) -> None:
        nonlocal latest_current_raw, latest_current_out, latest_current_sat, latest_current_aw
        nonlocal latest_duty_unclamped, latest_zc_state, latest_zc_active
        apply_pending(time_s)
        zc = zero_cross.step(vac_sense.output)
        latest_zc_state = zc.state_code
        latest_zc_active = zc.zero_cross_active
        commutation_timeline.set_command(
            time_s,
            state_code=zc.state_code,
            lf_state=zc.lf_state,
            hf_softstart_fraction=zc.hf_softstart_fraction,
            target_polarity=zc.target_polarity,
        )
        if zc.reset_current_pi:
            current_rt.reset(initial_output=0.0)

        error = i_ref - abs(current_sense.output)
        cstep = current_rt.step(error)
        latest_current_raw = cstep.raw_output
        latest_current_out = cstep.output
        latest_current_sat = cstep.saturated
        latest_current_aw = cstep.anti_windup_frozen
        latest_duty_unclamped = duty_ff + cstep.output * indu_comp
        duty = _clip(latest_duty_unclamped, effective_duty_min, stage.duty_max)
        # Firmware zero-cross states clamp duty while dead-band is used to
        # suppress/soft-start the HF leg.
        if zc.zero_cross_active:
            duty = effective_duty_min

        requested = (
            time_s
            + fw.current_computation_delay_s
            + fw.current_pwm_update_delay_s
            + simulation.additional_pwm_delay_s
        )
        apply_time = _next_pwm_boundary(requested, pwm_period) if simulation.shadow_update else requested
        duty_timeline.set_duty(apply_time, duty)
        samples.append(
            TTPLControlSample(
                time_s=float(time_s),
                vac_v=float(latest_vac),
                inductor_current_a=float(latest_current),
                bus_voltage_v=float(latest_bus),
                vac_measured_v=float(vac_sense.output),
                inductor_current_measured_a=float(current_sense.output),
                bus_voltage_measured_v=float(vbus_sense.output),
                vac_adc_code=int(vac_sense.last_adc_code),
                current_adc_code=int(current_sense.last_adc_code),
                vbus_adc_code=int(vbus_sense.last_adc_code),
                bus_reference_v=float(scenario.reference_at(time_s)),
                vac_rms_estimate_v=float(vac_rms),
                current_reference_a=float(i_ref),
                current_error_a=float(error),
                current_controller_raw=float(cstep.raw_output),
                current_controller_output=float(cstep.output),
                current_controller_saturated=bool(cstep.saturated),
                current_anti_windup_frozen=bool(cstep.anti_windup_frozen),
                voltage_error_v=float(latest_voltage_error),
                voltage_controller_delta_raw=float(latest_voltage_raw - voltage_bias),
                voltage_controller_absolute=float(voltage_absolute),
                gcmd_a_per_v=float(gcmd),
                duty_feedforward=float(duty_ff),
                indu_comp=float(indu_comp),
                duty_unclamped=float(latest_duty_unclamped),
                duty_command=float(duty),
                duty_apply_time_s=float(apply_time),
                pwm_state_code=int(zc.state_code),
                zero_cross_active=bool(zc.zero_cross_active),
                current_pi_reset=bool(zc.reset_current_pi),
            )
        )

    def external_voltage(time_s: float, source_name: str) -> float:
        return gates.gate_value(time_s, source_name)

    def sync_callback(
        time_s: float,
        proposed_delta_s: float,
        old_delta_s: float,
        redostep: int,
        location: int,
    ) -> float | None:
        del old_delta_s, redostep, location
        candidates = [
            gates.next_pwm_event_after(time_s),
            next_current,
            next_voltage,
            next_amc,
            vac_sense.next_event_after(time_s),
            current_sense.next_event_after(time_s),
            vbus_sense.next_event_after(time_s),
        ]
        if pending_voltage:
            candidates.append(pending_voltage[0][0])
        if pending_amc:
            candidates.append(pending_amc[0][0])
        future = [value for value in candidates if value > time_s + tolerance]
        if not future:
            return proposed_delta_s
        return min(float(proposed_delta_s), max(min(future) - time_s, tolerance))

    def data_callback(index: int, point: dict[str, complex]) -> None:
        nonlocal last_time, latest_vac, latest_current, latest_bus
        nonlocal next_current, next_voltage, next_amc
        del index
        time_s = _lookup(point, "time")
        if time_s is None or time_s + tolerance < last_time:
            return
        last_time = max(last_time, time_s)
        line = _lookup(point, "v(line)")
        neutral = _lookup(point, "v(neutral)")
        bus = _lookup(point, "v(bus)")
        current = _lookup(point, "i(lboost)")
        if line is not None and neutral is not None:
            latest_vac = line - neutral
        if bus is not None and math.isfinite(bus):
            latest_bus = bus
        if current is not None and math.isfinite(current):
            latest_current = current

        vac_sense.advance(latest_vac, time_s)
        current_sense.advance(latest_current, time_s)
        vbus_sense.advance(latest_bus, time_s)
        apply_pending(time_s)

        for name in record_names:
            value = _callback_vector(point, name)
            vector_lists[name].append(float(value.real) if value is not None else math.nan)

        # Execute due tasks chronologically. Coincident tasks use the firmware
        # order voltage -> AMC/reference -> current.
        while True:
            due = min(next_voltage, next_amc, next_current)
            if due > time_s + tolerance or due > simulation.duration_s + tolerance:
                break
            if next_voltage <= due + tolerance:
                event = next_voltage
                update_voltage(event)
                next_voltage += voltage_period
                apply_pending(event)
            if next_amc <= due + tolerance:
                event = next_amc
                update_amc(event)
                next_amc += amc_period
                apply_pending(event)
            if next_current <= due + tolerance:
                event = next_current
                update_current(event)
                next_current += current_period
                apply_pending(event)

    session = NgSpiceSharedLibrary(library)
    session.initialize(data_callback=data_callback, external_voltage=external_voltage, sync_callback=sync_callback)
    rc = session.load_circuit(circuit_lines)
    if rc != 0:
        raise RuntimeError(f"ngSpice_Circ failed with status {rc}: {' | '.join(session.messages[-20:])}")
    session.run_background(timeout_s=simulation.wall_timeout_s)
    if session.exit_status not in (None, 0):
        raise RuntimeError(
            f"TTPL shared ngspice transient failed: exit={session.exit_status}; "
            + " | ".join(session.messages[-30:])
        )
    if not samples:
        raise RuntimeError("TTPL shared ngspice produced no digital control samples")

    vectors = {name: np.asarray(values, dtype=float) for name, values in vector_lists.items()}
    sample_tuple = tuple(samples)
    result_metrics = _metrics(
        vectors,
        sample_tuple,
        line_frequency_hz=stage.line_frequency_hz,
        duration_s=simulation.duration_s,
    )
    metadata: dict[str, object] = {
        "engine": "shared-ngspice",
        "topology": "single_phase_ttpl_pfc",
        "model_level": "ideal_switching_correlation_firmware_faithful_control",
        "exact_hz_schema": exact.as_dict()["schema"],
        "exact_hz_match_checked": True,
        "current_hz_source": exact.current.source,
        "voltage_hz_source": exact.voltage.source,
        "current_b": exact.current.b,
        "current_a": exact.current.a,
        "voltage_b": exact.voltage.b,
        "voltage_a": exact.voltage.a,
        "coefficient_convention": exact.current.coefficient_convention,
        "controller_re_discretized": False,
        "controller_runtime": "float32 exact-H(z)-derived PI/PIF/2P2Z state runtime",
        "pi_anti_windup": "conditional integrator freeze on outward saturation",
        "outer_loop_initial_output": voltage_bias,
        "pwm_shadow_update": simulation.shadow_update,
        "current_total_command_delay_s": (
            fw.current_computation_delay_s
            + fw.current_pwm_update_delay_s
            + simulation.additional_pwm_delay_s
        ),
        "voltage_computation_delay_s": fw.voltage_computation_delay_s,
        "amc_update_delay_s": fw.amc_update_delay_s,
        "sense_model": (
            "configured analog poles + float32 sampled filtering + calibrated signed ADC quantization; "
            "board-specific ADC offset/rail clipping not yet present in PFC schema"
        ),
        "current_adc_lsb_a": current_sense.lsb_engineering_units,
        "vac_adc_lsb_v": vac_sense.lsb_engineering_units,
        "vbus_adc_lsb_v": vbus_sense.lsb_engineering_units,
        "current_adc_ready_latency_s": current_sense.acquisition_to_ready_s,
        "vac_adc_ready_latency_s": vac_sense.acquisition_to_ready_s,
        "vbus_adc_ready_latency_s": vbus_sense.acquisition_to_ready_s,
        "zero_cross_runtime": "eight-state control-rate TTPL commutation with PI reset and HF deadband soft-start",
        "zero_cross_gate_boundary": (
            "state logic is firmware-faithful; detailed ePWM DBRED/DBFED/TBCLK register timing remains a later hardware-fidelity layer"
        ),
        "shared_senddata_points": session.data_callback_count,
    }
    return TTPLSharedNgSpiceResult(
        sample_tuple,
        vectors,
        result_metrics,
        metadata,
        tuple(session.messages),
        netlist,
    )


__all__ = [
    "TTPLClosedLoopScenario",
    "TTPLControlSample",
    "TTPLSharedNgSpiceConfig",
    "TTPLSharedNgSpiceMetrics",
    "TTPLSharedNgSpiceResult",
    "run_ttpl_shared_closed_loop",
]
