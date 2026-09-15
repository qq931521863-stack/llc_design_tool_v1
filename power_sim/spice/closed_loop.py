"""Shared-ngspice LLC closed-loop co-simulation.

This is the first continuous-state controller-in-the-loop path. ngspice owns
the switching circuit state; Power Design Toolkit owns ADC sampling, exact H(z),
limits and FM/TBPRD logic. Gate voltage sources are supplied through
shared-ngspice EXTERNAL callbacks and solver timesteps are synchronized to PWM
and control events.

V1 scope is deliberately narrow: FULL_BRIDGE LLC, fixed input bus/load and a
reference step. Vin/load steps are added after this path is numerically proven.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

import numpy as np

from llc_design.core.spec import LLCDesignSpec
from llc_design.core.tank import TankDesign
from power_control_tools.models import DigitalTransferFunction
from power_sim.closed_loop import (
    ClosedLoopDiagnostics,
    ClosedLoopResult,
    ClosedLoopSample,
    ClosedLoopScenario,
    _diagnose,
)
from power_sim.digital_control import (
    ControllerLimitConfig,
    DigitalTransferRuntime,
    LLCFMConfig,
    LLCFMRuntime,
    SamplerConfig,
    SamplerRuntime,
)

from .gate import FrequencyTimeline, LLCFullBridgeGateScheduler
from .llc import LLCSpiceConfig, build_ideal_llc_circuit
from .netlist import render_netlist
from .shared import NgSpiceSharedLibrary
from .sync import LLCCoSimulationSynchronizer


@dataclass(frozen=True)
class NgSpiceClosedLoopConfig:
    duration_s: float
    computation_delay_s: float = 0.0
    pwm_update_delay_s: float = 0.0
    output_step_s: float | None = None
    max_step_s: float | None = None
    wall_timeout_s: float = 120.0
    record_vectors: tuple[str, ...] = (
        "time",
        "v(out)",
        "i(lr)",
        "i(lpri)",
        "v(a)",
        "v(b)",
        "v(g_ah)",
        "v(g_al)",
        "v(g_bh)",
        "v(g_bl)",
    )

    def validate(self, sample_time_s: float, maximum_frequency_hz: float) -> tuple[float, float]:
        if self.duration_s <= 0.0:
            raise ValueError("duration_s must be positive")
        if self.computation_delay_s < 0.0 or self.pwm_update_delay_s < 0.0:
            raise ValueError("control delays cannot be negative")
        if self.wall_timeout_s <= 0.0:
            raise ValueError("wall_timeout_s must be positive")
        if sample_time_s <= 0.0 or maximum_frequency_hz <= 0.0:
            raise ValueError("sample time and maximum switching frequency must be positive")
        switching_period_min = 1.0 / maximum_frequency_hz
        output_step = self.output_step_s or min(sample_time_s / 8.0, switching_period_min / 40.0)
        max_step = self.max_step_s or min(sample_time_s / 12.0, switching_period_min / 60.0)
        if output_step <= 0.0 or max_step <= 0.0:
            raise ValueError("ngspice transient step sizes must be positive")
        return float(output_step), float(max_step)


@dataclass(frozen=True)
class NgSpiceClosedLoopResult:
    control: ClosedLoopResult
    vectors: dict[str, np.ndarray]
    simulator_messages: tuple[str, ...]
    circuit_netlist: str


def _lookup(point: dict[str, complex], *names: str) -> float | None:
    lowered = {key.lower(): value for key, value in point.items()}
    for name in names:
        if name.lower() in lowered:
            return float(lowered[name.lower()].real)
    return None


def run_llc_shared_closed_loop(
    spec: LLCDesignSpec,
    tank: TankDesign,
    spice_config: LLCSpiceConfig,
    *,
    controller: DigitalTransferFunction,
    controller_limits: ControllerLimitConfig,
    sampler: SamplerConfig,
    modulator: LLCFMConfig,
    scenario: ClosedLoopScenario,
    simulation: NgSpiceClosedLoopConfig,
    library: str | None = None,
) -> NgSpiceClosedLoopResult:
    """Run the first real digital-controller/shared-ngspice LLC loop.

    ``controller`` is used directly; no controller re-discretization occurs.
    This preserves coefficient identity with Control Tools/FRA/C99.
    """
    sampler.validate()
    controller_limits.validate()
    modulator.validate()
    if not math.isclose(controller.sample_rate_hz, sampler.sample_rate_hz, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("controller and sampler sample rates must match")
    if scenario.bus_voltage_v is not None:
        if scenario.bus_voltage_v.step_time_s is not None:
            raise NotImplementedError("shared-ngspice V1 supports fixed bus voltage only")
        if not math.isclose(scenario.bus_voltage_v.initial, spice_config.bus_voltage_v, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("scenario initial bus voltage must match LLCSpiceConfig in V1")
    if scenario.load_fraction is not None:
        if scenario.load_fraction.step_time_s is not None:
            raise NotImplementedError("shared-ngspice V1 supports fixed load only")
        if not math.isclose(scenario.load_fraction.initial, spice_config.load_fraction, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("scenario initial load must match LLCSpiceConfig in V1")

    output_step, max_step = simulation.validate(sampler.sample_time_s, modulator.maximum_frequency_hz)
    shared_spice = replace(spice_config, gate_drive_mode="external")
    circuit = build_ideal_llc_circuit(spec, tank, shared_spice)
    circuit.save_vectors = list(dict.fromkeys(simulation.record_vectors))
    circuit.directives.append(
        f".tran {output_step:.12e} {simulation.duration_s:.12e} 0 {max_step:.12e}"
    )
    netlist = render_netlist(circuit)
    circuit_lines = [line for line in netlist.splitlines() if line.strip()]

    sampler_rt = SamplerRuntime(sampler)
    initial_output = spec.vout_v if spice_config.initial_output_v is None else float(spice_config.initial_output_v)
    sampler_rt.reset(initial_output)
    controller_rt = DigitalTransferRuntime(controller, controller_limits)
    modulator_rt = LLCFMRuntime(modulator)
    initial_mod = modulator_rt.map(0.0)
    timeline = FrequencyTimeline(initial_mod.actual_frequency_hz)
    gates = LLCFullBridgeGateScheduler(
        timeline,
        deadtime_s=spec.primary_deadtime_s,
        gate_high_v=spice_config.gate_high_v,
    )
    synchronizer = LLCCoSimulationSynchronizer(
        timeline,
        sample_time_s=sampler.sample_time_s,
        sample_phase_s=sampler.sample_phase_s,
        deadtime_s=spec.primary_deadtime_s,
    )

    record_names = {name.lower() for name in simulation.record_vectors}
    vector_lists: dict[str, list[float]] = {name: [] for name in record_names}
    samples: list[ClosedLoopSample] = []
    next_sample_s = sampler.sample_phase_s
    if next_sample_s <= 1e-15:
        next_sample_s = 0.0
    sample_index = 0
    last_time = -math.inf
    latest_vout = initial_output
    total_delay = simulation.computation_delay_s + simulation.pwm_update_delay_s
    tolerance = max(1e-12, sampler.sample_time_s * 1e-7)

    def execute_controller(time_s: float, vout_v: float) -> None:
        nonlocal sample_index, next_sample_s
        feedback = sampler_rt.sample(vout_v)
        reference = scenario.reference_v.value_at(time_s)
        error = reference - feedback
        ctrl = controller_rt.step(error)
        mapped = modulator_rt.map(ctrl.output)
        applied_now = timeline.frequency_at(time_s)
        apply_time = time_s + total_delay
        timeline.set_frequency(apply_time, mapped.actual_frequency_hz)
        samples.append(
            ClosedLoopSample(
                time_s=float(time_s),
                reference_v=float(reference),
                plant_output_v=float(vout_v),
                feedback_v=float(feedback),
                error_v=float(error),
                controller_raw=float(ctrl.raw_output),
                controller_output=float(ctrl.output),
                controller_saturated=bool(ctrl.saturated),
                frequency_command_hz=float(mapped.frequency_command_hz),
                frequency_actual_hz=float(mapped.actual_frequency_hz),
                frequency_applied_hz=float(applied_now),
                tbprd=mapped.tbprd,
                modulator_saturated=bool(mapped.saturated),
                modulator_quantized=bool(mapped.quantized),
            )
        )
        sample_index += 1
        next_sample_s = sampler.sample_phase_s + sample_index * sampler.sample_time_s
        if next_sample_s <= time_s + tolerance:
            next_sample_s = time_s + sampler.sample_time_s

    if sampler.sample_phase_s <= tolerance:
        execute_controller(0.0, initial_output)

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
        return synchronizer.constrain_delta(time_s, proposed_delta_s)

    def data_callback(index: int, point: dict[str, complex]) -> None:
        nonlocal last_time, latest_vout
        del index
        time_s = _lookup(point, "time")
        if time_s is None:
            return
        if time_s + tolerance < last_time:
            return
        last_time = max(last_time, time_s)
        vout = _lookup(point, "v(out)")
        if vout is not None and math.isfinite(vout):
            latest_vout = vout

        lowered = {key.lower(): value for key, value in point.items()}
        for name in record_names:
            value = lowered.get(name)
            if value is not None:
                vector_lists[name].append(float(value.real))

        while time_s + tolerance >= next_sample_s and next_sample_s <= simulation.duration_s + tolerance:
            execute_controller(next_sample_s, latest_vout)

    session = NgSpiceSharedLibrary(library)
    session.initialize(
        data_callback=data_callback,
        external_voltage=external_voltage,
        sync_callback=sync_callback,
    )
    rc = session.load_circuit(circuit_lines)
    if rc != 0:
        raise RuntimeError(f"ngSpice_Circ failed with status {rc}: {' | '.join(session.messages[-20:])}")

    # sharedspice streams SendData from its background analysis path. Waiting on
    # the explicit worker start/stop callbacks avoids returning before the first
    # transient point is accepted on fast simulations.
    session.run_background(timeout_s=simulation.wall_timeout_s)

    if session.exit_status not in (None, 0):
        raise RuntimeError(
            f"shared ngspice transient failed: exit={session.exit_status}; "
            + " | ".join(session.messages[-30:])
        )
    if not samples:
        raise RuntimeError("shared ngspice produced no digital control samples")

    sample_tuple = tuple(samples)
    diagnostics: ClosedLoopDiagnostics = _diagnose(
        sample_tuple,
        sampler.sample_rate_hz,
        scenario.reference_v.value_at(simulation.duration_s),
    )
    control_result = ClosedLoopResult(
        sample_tuple,
        diagnostics,
        metadata={
            "engine": "shared-ngspice",
            "model_level": "ideal_switching_correlation",
            "controller_source": controller.source,
            "controller_name": controller.name,
            "sample_rate_hz": controller.sample_rate_hz,
            "computation_delay_s": simulation.computation_delay_s,
            "pwm_update_delay_s": simulation.pwm_update_delay_s,
            "final_switching_frequency_hz": timeline.frequency_at(simulation.duration_s),
        },
    )
    vectors = {name: np.asarray(values, dtype=float) for name, values in vector_lists.items()}
    return NgSpiceClosedLoopResult(control_result, vectors, tuple(session.messages), netlist)


__all__ = ["NgSpiceClosedLoopConfig", "NgSpiceClosedLoopResult", "run_llc_shared_closed_loop"]
