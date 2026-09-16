"""Single-phase TTPL PFC switching circuit and deterministic gate scheduling.

This module is the circuit-level counterpart of the averaged PFC line-cycle
model.  ngspice owns the continuous electrical state while gate commands are
supplied through shared-ngspice EXTERNAL voltage sources.

V1 is an engineering correlation model.  It intentionally uses ideal voltage-
controlled switches, antiparallel diode models and small physical damping.  It
does not claim vendor-accurate Coss/Qrr/switching-loss/ringing behaviour.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math

from pfc_design.control import PFCPowerStageConfig

from .ir import CircuitIR, ElementKind, SpiceElement


@dataclass(frozen=True)
class TTPLSpiceConfig:
    vin_rms_v: float
    line_frequency_hz: float
    bus_voltage_v: float
    output_power_w: float
    switching_frequency_hz: float
    boost_inductance_h: float
    boost_dcr_ohm: float
    bus_capacitance_f: float
    bus_cap_esr_ohm: float
    input_phase_deg: float = 90.0
    gate_high_v: float = 5.0
    deadtime_s: float = 100e-9
    zero_cross_voltage_v: float = 7.5
    switch_ron_ohm: float = 12e-3
    switch_roff_ohm: float = 1e8
    diode_is_a: float = 1e-9
    diode_rs_ohm: float = 40e-3
    diode_cjo_f: float = 0.0
    bleeder_ohm: float = 10e6
    initial_bus_voltage_v: float | None = None

    @classmethod
    def from_power_stage(cls, stage: PFCPowerStageConfig, *, input_phase_deg: float = 90.0) -> "TTPLSpiceConfig":
        return cls(
            vin_rms_v=float(stage.vin_rms_v),
            line_frequency_hz=float(stage.line_frequency_hz),
            bus_voltage_v=float(stage.bus_voltage_v),
            output_power_w=float(stage.output_power_w),
            switching_frequency_hz=float(stage.switching_frequency_hz),
            boost_inductance_h=float(stage.boost_inductance_h),
            boost_dcr_ohm=float(stage.equivalent_series_resistance_ohm),
            bus_capacitance_f=float(stage.bus_capacitance_f),
            bus_cap_esr_ohm=float(stage.bus_cap_esr_ohm),
            input_phase_deg=float(input_phase_deg),
            deadtime_s=float(stage.deadtime_s),
            initial_bus_voltage_v=float(stage.bus_voltage_v),
        )

    def validate(self) -> None:
        positive = {
            "vin_rms_v": self.vin_rms_v,
            "line_frequency_hz": self.line_frequency_hz,
            "bus_voltage_v": self.bus_voltage_v,
            "output_power_w": self.output_power_w,
            "switching_frequency_hz": self.switching_frequency_hz,
            "boost_inductance_h": self.boost_inductance_h,
            "bus_capacitance_f": self.bus_capacitance_f,
            "gate_high_v": self.gate_high_v,
            "switch_ron_ohm": self.switch_ron_ohm,
            "switch_roff_ohm": self.switch_roff_ohm,
            "diode_is_a": self.diode_is_a,
            "diode_rs_ohm": self.diode_rs_ohm,
            "bleeder_ohm": self.bleeder_ohm,
        }
        for name, value in positive.items():
            if value <= 0.0:
                raise ValueError(f"{name} must be positive")
        if self.boost_dcr_ohm < 0.0 or self.bus_cap_esr_ohm < 0.0:
            raise ValueError("PFC DCR/ESR cannot be negative")
        if self.deadtime_s < 0.0 or self.zero_cross_voltage_v < 0.0:
            raise ValueError("PFC deadtime/zero-cross threshold cannot be negative")
        if self.switch_roff_ohm <= self.switch_ron_ohm:
            raise ValueError("PFC switch Roff must exceed Ron")
        if self.diode_cjo_f < 0.0:
            raise ValueError("PFC diode Cjo cannot be negative")

    @property
    def line_peak_v(self) -> float:
        return math.sqrt(2.0) * self.vin_rms_v

    @property
    def switching_period_s(self) -> float:
        return 1.0 / self.switching_frequency_hz


@dataclass(frozen=True)
class DutySegment:
    start_time_s: float
    duty: float


class DutyTimeline:
    """Piecewise duty timeline addressed by absolute simulation time."""

    def __init__(self, initial_duty: float, *, initial_time_s: float = 0.0):
        self._segments = [DutySegment(float(initial_time_s), self._clip(initial_duty))]

    @staticmethod
    def _clip(value: float) -> float:
        return min(max(float(value), 0.0), 1.0)

    @property
    def segments(self) -> tuple[DutySegment, ...]:
        return tuple(self._segments)

    def duty_at(self, time_s: float) -> float:
        starts = [segment.start_time_s for segment in self._segments]
        index = max(0, bisect_right(starts, float(time_s)) - 1)
        return self._segments[index].duty

    def set_duty(self, time_s: float, duty: float) -> None:
        t = float(time_s)
        value = self._clip(duty)
        last = self._segments[-1]
        if t < last.start_time_s - 1e-15:
            raise ValueError("duty commands must be appended in nondecreasing time order")
        if abs(t - last.start_time_s) <= 1e-15:
            self._segments[-1] = DutySegment(t, value)
        elif math.isclose(value, last.duty, rel_tol=0.0, abs_tol=1e-15):
            return
        else:
            self._segments.append(DutySegment(t, value))


class TTPLGateScheduler:
    """Deterministic four-switch totem-pole gate scheduler.

    Positive line half-cycle:
      - LF negative/bottom device is held on.
      - HF low device is the boost switch for ``duty``.
      - HF high device is the synchronous device.

    Negative line half-cycle swaps the HF roles and holds the LF positive/top
    device on.  Around line zero crossing all commanded gates are off and the
    antiparallel diode network provides a numerically continuous commutation
    path.
    """

    _HF_HIGH = {"vghfh", "ghfh", "vhfhigh", "hfhigh"}
    _HF_LOW = {"vghfl", "ghfl", "vhflow", "hflow"}
    _LF_POS = {"vglfp", "glfp", "vlfpos", "lfpos"}
    _LF_NEG = {"vglfn", "glfn", "vlfneg", "lfneg"}

    def __init__(self, config: TTPLSpiceConfig, timeline: DutyTimeline):
        config.validate()
        self.config = config
        self.timeline = timeline

    def line_voltage(self, time_s: float) -> float:
        phase = math.radians(self.config.input_phase_deg)
        return self.config.line_peak_v * math.sin(2.0 * math.pi * self.config.line_frequency_hz * float(time_s) + phase)

    def _pwm_states(self, time_s: float) -> tuple[bool, bool]:
        period = self.config.switching_period_s
        tau = float(time_s) % period
        duty = self.timeline.duty_at(time_s)
        dead = min(self.config.deadtime_s, 0.2 * period)
        half_dead = 0.5 * dead
        transition = duty * period
        active_on = half_dead <= tau < max(transition - half_dead, half_dead)
        sync_on = min(transition + half_dead, period) <= tau < period - half_dead
        return active_on, sync_on

    def gate_value(self, time_s: float, source_name: str) -> float:
        name = str(source_name).strip().lower()
        known = self._HF_HIGH | self._HF_LOW | self._LF_POS | self._LF_NEG
        if name not in known:
            raise KeyError(f"unknown TTPL gate external source {source_name!r}")

        vac = self.line_voltage(time_s)
        if abs(vac) <= self.config.zero_cross_voltage_v:
            return 0.0

        active_on, sync_on = self._pwm_states(time_s)
        positive = vac > 0.0
        state = False
        if positive:
            if name in self._HF_LOW:
                state = active_on
            elif name in self._HF_HIGH:
                state = sync_on
            elif name in self._LF_NEG:
                state = True
        else:
            if name in self._HF_HIGH:
                state = active_on
            elif name in self._HF_LOW:
                state = sync_on
            elif name in self._LF_POS:
                state = True
        return self.config.gate_high_v if state else 0.0

    def next_pwm_event_after(self, time_s: float) -> float:
        """Return the next gate-discontinuity time after ``time_s``."""
        t = float(time_s)
        period = self.config.switching_period_s
        base = math.floor(t / period) * period
        duty = self.timeline.duty_at(t)
        dead = min(self.config.deadtime_s, 0.2 * period)
        half_dead = 0.5 * dead
        edges = (
            base + half_dead,
            base + max(duty * period - half_dead, half_dead),
            base + min(duty * period + half_dead, period),
            base + period - half_dead,
            base + period,
        )
        eps = max(1e-15, period * 1e-10)
        future = [edge for edge in edges if edge > t + eps]
        if future:
            return min(future)
        return base + period + half_dead


def _s(value: float) -> str:
    return f"{float(value):.12e}"


def _external_gate(refdes: str, node: str) -> SpiceElement:
    return SpiceElement(refdes, ElementKind.VOLTAGE_SOURCE, (node, "0"), "DC 0 EXTERNAL")


def build_ttpl_pfc_circuit(config: TTPLSpiceConfig) -> CircuitIR:
    """Build a full four-switch totem-pole PFC switching-correlation circuit."""
    config.validate()
    load_ohm = config.bus_voltage_v * config.bus_voltage_v / config.output_power_w
    initial_bus = config.bus_voltage_v if config.initial_bus_voltage_v is None else float(config.initial_bus_voltage_v)
    source = (
        f"SIN(0 {_s(config.line_peak_v)} {_s(config.line_frequency_hz)} 0 0 "
        f"{_s(config.input_phase_deg)})"
    )
    circuit = CircuitIR(
        name="Power Design Toolkit TTPL PFC shared-ngspice switching correlation",
        metadata={
            "model_level": "ideal_switching_correlation_with_physical_damping",
            "topology": "single_phase_totem_pole_pfc",
            "vin_rms_v": config.vin_rms_v,
            "line_frequency_hz": config.line_frequency_hz,
            "vbus_v": config.bus_voltage_v,
            "pout_w": config.output_power_w,
            "fsw_hz": config.switching_frequency_hz,
            "boost_l_h": config.boost_inductance_h,
            "bus_c_f": config.bus_capacitance_f,
        },
    )
    circuit.extend(
        [
            SpiceElement("VAC", ElementKind.VOLTAGE_SOURCE, ("LINE", "NEUTRAL"), source),
            SpiceElement("RBOOST", ElementKind.RESISTOR, ("LINE", "L_IN"), _s(max(config.boost_dcr_ohm, 1e-6))),
            SpiceElement("LBOOST", ElementKind.INDUCTOR, ("L_IN", "SW"), _s(config.boost_inductance_h)),
            _external_gate("VGHFH", "G_HF_H"),
            _external_gate("VGHFL", "G_HF_L"),
            _external_gate("VGLFP", "G_LF_P"),
            _external_gate("VGLFN", "G_LF_N"),
            SpiceElement("SHFH", ElementKind.SWITCH, ("BUS", "SW", "G_HF_H", "0"), model="SWPFC"),
            SpiceElement("SHFL", ElementKind.SWITCH, ("SW", "0", "G_HF_L", "0"), model="SWPFC"),
            SpiceElement("SLFP", ElementKind.SWITCH, ("BUS", "NEUTRAL", "G_LF_P", "0"), model="SWPFC"),
            SpiceElement("SLFN", ElementKind.SWITCH, ("NEUTRAL", "0", "G_LF_N", "0"), model="SWPFC"),
            # Antiparallel diode paths are required during deadtime/zero crossing.
            SpiceElement("DHFH", ElementKind.DIODE, ("SW", "BUS"), model="DPFC"),
            SpiceElement("DHFL", ElementKind.DIODE, ("0", "SW"), model="DPFC"),
            SpiceElement("DLFP", ElementKind.DIODE, ("NEUTRAL", "BUS"), model="DPFC"),
            SpiceElement("DLFN", ElementKind.DIODE, ("0", "NEUTRAL"), model="DPFC"),
            SpiceElement("RCBUS", ElementKind.RESISTOR, ("BUS", "C_INT"), _s(max(config.bus_cap_esr_ohm, 1e-6))),
            SpiceElement("CBUS", ElementKind.CAPACITOR, ("C_INT", "0"), _s(config.bus_capacitance_f)),
            SpiceElement("RLOAD", ElementKind.RESISTOR, ("BUS", "0"), _s(load_ohm)),
            SpiceElement("RSWBLEED", ElementKind.RESISTOR, ("SW", "0"), _s(config.bleeder_ohm)),
            SpiceElement("RNBLEED", ElementKind.RESISTOR, ("NEUTRAL", "0"), _s(config.bleeder_ohm)),
        ]
    )
    circuit.models.extend(
        [
            f".model SWPFC SW(Ron={_s(config.switch_ron_ohm)} Roff={_s(config.switch_roff_ohm)} Vt=2.5 Vh=0.1)",
            f".model DPFC D(Is={_s(config.diode_is_a)} N=1 Rs={_s(config.diode_rs_ohm)} Cjo={_s(config.diode_cjo_f)})",
        ]
    )
    circuit.directives.extend(
        [
            ".options method=gear reltol=3e-4 abstol=1e-7 vntol=1e-5 gmin=1e-10",
            f".ic V(BUS)={_s(initial_bus)} V(C_INT)={_s(initial_bus)}",
        ]
    )
    circuit.save_vectors.extend(
        [
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
        ]
    )
    return circuit


__all__ = [
    "DutySegment",
    "DutyTimeline",
    "TTPLGateScheduler",
    "TTPLSpiceConfig",
    "build_ttpl_pfc_circuit",
]
