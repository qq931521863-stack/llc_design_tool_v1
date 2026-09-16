"""Exact-H(z) TTPL PFC shared-ngspice closed-loop co-simulation.

ngspice owns the continuous four-switch power stage.  Power Design Toolkit owns
multi-rate digital control and gate scheduling.  The current and bus-voltage
controllers are consumed from :class:`PFCControlHandoff` exactly as normalized
b/a coefficients; this module never rebuilds them from Kp/Ti and never performs
an S2Z conversion.

V1 deliberately keeps the surrounding nonlinear implementation contract
explicit.  Exact H(z) is authoritative for the *linear* controller.  The
shared-ngspice runtime uses direct-form output limiting; firmware-specific
PI/PIF conditional-integrator anti-windup remains a separate implementation
semantic and is reported in result metadata rather than silently inferred from
H(z).
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from pfc_design.control import PFCControlLabAnalysis
from pfc_design.control.handoff import (
    PFCControlHandoff,
    assert_handoff_matches_analysis,
    build_pfc_control_handoff,
)
from power_sim.digital_control import ControllerLimitConfig, DigitalTransferRuntime

from .netlist import render_netlist
from .pfc import DutyTimeline, TTPLGateScheduler, TTPLSpiceConfig, build_ttpl_pfc_circuit
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
    bus_reference_v: float
    vac_rms_estimate_v: float
    current_reference_a: float
    current_error_a: float
    current_controller_raw: float
    current_controller_output: float
    current_controller_saturated: bool
    voltage_error_v: float
    voltage_controller_delta_raw: float
    voltage_controller_absolute: float
    gcmd_a_per_v: float
    duty_feedforward: float
    indu_comp: float
    duty_unclamped: float
    duty_command: float
    duty_apply_time_s: float


@dataclass(frozen=True)
class TTPLSharedNgSpiceMetrics:
    final_bus_voltage_v: float
    bus_ripple_pp_v: float
    peak_inductor_current_a: float
    input_current_rms_a: float | None
    real_input_power_w: float | None
    power_factor: float | None
    current_controller_saturation_fraction: float
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
    duties = [sample.duty_command for sample in samples] or [0.0]
    return TTPLSharedNgSpiceMetrics(
        final_bus_voltage_v=final_bus,
        bus_ripple_pp_v=ripple,
        peak_inductor_current_a=peak_i,
        input_current_rms_a=i_rms,
        real_input_power_w=pin,
        power_factor=pf,
        current_controller_saturation_fraction=saturation_fraction,
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
    """Run a four-switch TTPL PFC loop using the frozen exact-H(z) handoff."""
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

    current_rt = DigitalTransferRuntime(
        exact.current.runtime_transfer(),
        ControllerLimitConfig(exact.current.output_min, exact.current.output_max, True),
    )
    # The outer-loop exact H(z) runs as a perturbation around the analytical
    # steady-state bias.  This avoids reconstructing PI state from Kp/Ti while
    # preserving exact b/a dynamics for every perturbation.
    voltage_rt = DigitalTransferRuntime(exact.voltage.runtime_transfer())

    required_gcmd = stage.output_power_w / (stage.efficiency * stage.vin_rms_v**2)
    if fw.vff_bypass:
        voltage_bias = required_gcmd / max(fw.vac_rms_feedforward_gain, 1e-30)
    else:
        voltage_bias = required_gcmd * fw.vac_rms_feedforward_gain * stage.vin_rms_v**2
    voltage_absolute = _clip(voltage_bias, exact.voltage.output_min, exact.voltage.output_max)
    vac_rms_sq = stage.vin_rms_v**2
    vac_rms = stage.vin_rms_v
    gcmd = required_gcmd

    phase = math.radians(simulation.input_phase_deg)
    vac0 = stage.line_peak_v * math.sin(phase)
    i_ref = gcmd * abs(vac0)
    duty_ff = _clip(1.0 - abs(vac0) / max(scenario.bus_reference_v, 1.0), stage.duty_min, stage.duty_max)
    indu_comp = _clip(fw.indu_comp_gain * i_ref, fw.indu_comp_min, fw.indu_comp_max)
    effective_duty_min = max(stage.duty_min, stage.minimum_effective_pulse_s * stage.switching_frequency_hz)
    duty0 = _clip(duty_ff, effective_duty_min, stage.duty_max)
    duty_timeline = DutyTimeline(duty0)
    gates = TTPLGateScheduler(spice, duty_timeline)

    current_period = 1.0 / exact.current.sample_rate_hz
    voltage_period = 1.0 / exact.voltage.sample_rate_hz
    amc_period = 1.0 / exact.amc_rate_hz
    pwm_period = 1.0 / stage.switching_frequency_hz
    next_current = 0.0
    next_voltage = 0.0
    next_amc = 0.0
    latest_vac = vac0
    latest_current = 0.0
    latest_bus = spice.initial_bus_voltage_v or stage.bus_voltage_v
    latest_current_raw = 0.0
    latest_current_out = 0.0
    latest_current_sat = False
    latest_voltage_error = scenario.bus_reference_v - latest_bus
    latest_voltage_delta = 0.0
    latest_duty_unclamped = duty0
    previous_polarity = 1 if vac0 >= 0.0 else -1
    tolerance = max(1e-12, current_period * 1e-7)
    samples: list[TTPLControlSample] = []

    record_names = tuple(dict.fromkeys(name.lower() for name in simulation.record_vectors))
    vector_lists: dict[str, list[float]] = {name: [] for name in record_names}
    last_time = -math.inf

    def update_voltage(time_s: float) -> None:
        nonlocal vac_rms_sq, vac_rms, voltage_absolute, gcmd, latest_voltage_error, latest_voltage_delta
        vac_rms_sq += fw.vac_rms_lpf_alpha * (latest_vac * latest_vac - vac_rms_sq)
        vac_rms = math.sqrt(max(vac_rms_sq, 1.0))
        latest_voltage_error = scenario.reference_at(time_s) - latest_bus
        vstep = voltage_rt.step(latest_voltage_error)
        latest_voltage_delta = vstep.raw_output
        voltage_absolute = _clip(voltage_bias + vstep.output, exact.voltage.output_min, exact.voltage.output_max)
        if fw.vff_bypass:
            vloop = voltage_absolute
        else:
            denom = max((fw.vac_rms_feedforward_gain * vac_rms) ** 2, 1.0)
            vloop = voltage_absolute / denom
        gcmd = _clip(vloop * fw.vac_rms_feedforward_gain, 0.0, fw.gcmd_max_a_per_v)

    def update_amc(time_s: float) -> None:
        nonlocal i_ref, duty_ff, indu_comp
        i_ref = gcmd * abs(latest_vac)
        vref = max(scenario.reference_at(time_s), 1.0)
        duty_ff = _clip(1.0 - abs(latest_vac) / vref, stage.duty_min, stage.duty_max)
        indu_comp = _clip(fw.indu_comp_gain * i_ref, fw.indu_comp_min, fw.indu_comp_max)

    def update_current(time_s: float) -> None:
        nonlocal latest_current_raw, latest_current_out, latest_current_sat, latest_duty_unclamped, previous_polarity
        polarity = 1 if latest_vac >= 0.0 else -1
        if polarity != previous_polarity:
            current_rt.reset()
            previous_polarity = polarity
        error = i_ref - abs(latest_current)
        cstep = current_rt.step(error)
        latest_current_raw = cstep.raw_output
        latest_current_out = cstep.output
        latest_current_sat = cstep.saturated
        latest_duty_unclamped = duty_ff + cstep.output * indu_comp
        duty = _clip(latest_duty_unclamped, effective_duty_min, stage.duty_max)
        requested = time_s + simulation.additional_pwm_delay_s
        apply_time = _next_pwm_boundary(requested, pwm_period) if simulation.shadow_update else requested
        duty_timeline.set_duty(apply_time, duty)
        samples.append(
            TTPLControlSample(
                time_s=float(time_s),
                vac_v=float(latest_vac),
                inductor_current_a=float(latest_current),
                bus_voltage_v=float(latest_bus),
                bus_reference_v=float(scenario.reference_at(time_s)),
                vac_rms_estimate_v=float(vac_rms),
                current_reference_a=float(i_ref),
                current_error_a=float(error),
                current_controller_raw=float(cstep.raw_output),
                current_controller_output=float(cstep.output),
                current_controller_saturated=bool(cstep.saturated),
                voltage_error_v=float(latest_voltage_error),
                voltage_controller_delta_raw=float(latest_voltage_delta),
                voltage_controller_absolute=float(voltage_absolute),
                gcmd_a_per_v=float(gcmd),
                duty_feedforward=float(duty_ff),
                indu_comp=float(indu_comp),
                duty_unclamped=float(latest_duty_unclamped),
                duty_command=float(duty),
                duty_apply_time_s=float(apply_time),
            )
        )

    def external_voltage(time_s: float, source_name: str) -> float:
        return gates.gate_value(time_s, source_name)

    def sync_callback(time_s: float, proposed_delta_s: float, old_delta_s: float, redostep: int, location: int) -> float | None:
        del old_delta_s, redostep, location
        candidates = [gates.next_pwm_event_after(time_s), next_current, next_voltage, next_amc]
        future = [value for value in candidates if value > time_s + tolerance]
        if not future:
            return proposed_delta_s
        return min(float(proposed_delta_s), max(min(future) - time_s, tolerance))

    def data_callback(index: int, point: dict[str, complex]) -> None:
        nonlocal last_time, latest_vac, latest_current, latest_bus, next_current, next_voltage, next_amc
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

        for name in record_names:
            value = _callback_vector(point, name)
            vector_lists[name].append(float(value.real) if value is not None else math.nan)

        # Deterministic multi-rate ordering at coincident ticks: voltage -> AMC -> current.
        while time_s + tolerance >= next_voltage and next_voltage <= simulation.duration_s + tolerance:
            update_voltage(next_voltage)
            next_voltage += voltage_period
        while time_s + tolerance >= next_amc and next_amc <= simulation.duration_s + tolerance:
            update_amc(next_amc)
            next_amc += amc_period
        while time_s + tolerance >= next_current and next_current <= simulation.duration_s + tolerance:
            update_current(next_current)
            next_current += current_period

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
        "model_level": "ideal_switching_correlation",
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
        "outer_loop_bias": voltage_bias,
        "pwm_shadow_update": simulation.shadow_update,
        "nonlinear_controller_semantics": (
            "Exact H(z) b/a are authoritative. Shared-ngspice V1 executes direct-form limiting; "
            "firmware PI/PIF conditional-integrator anti-windup remains an explicit separate semantic."
        ),
        "sense_model": "engineering-unit sampling; analog sensing remains owned by the PFC small-signal/sensing model in V1",
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
