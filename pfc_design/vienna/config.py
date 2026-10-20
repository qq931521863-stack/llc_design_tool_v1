"""Three-phase Vienna PFC control-lab configuration."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass, replace
import math
import types
from functools import lru_cache
from typing import get_args, get_origin, get_type_hints, Union

import numpy as np

from llc_design.control.digital_loop import PIControllerConfig
from pfc_design.control.config import (
    ADCTimingConfig,
    ControllerConfig,
    DigitalFilterConfig,
    ExternalSenseConfig,
    LoadModel,
)


@lru_cache(maxsize=32)
def _hints(cls):
    return get_type_hints(cls)


def _matches_type(value, hint):
    origin=get_origin(hint)
    if origin in (Union,types.UnionType):return any(_matches_type(value,item) for item in get_args(hint))
    if origin is tuple:
        args=get_args(hint)
        return type(value) is tuple and len(value)==len(args) and all(_matches_type(v,t) for v,t in zip(value,args))
    if hint is float:return type(value) in (int,float)
    if hint in (bool,int,str):return type(value) is hint
    return isinstance(value,hint)


def _finite_model(value, path="Vienna") -> None:
    """Reject NaN/Inf before arithmetic or NumPy silently propagates them."""
    if is_dataclass(value):
        for item in fields(value):
            member=getattr(value,item.name)
            if not _matches_type(member,_hints(type(value))[item.name]):
                raise ValueError(f"{path}.{item.name} has invalid type")
            _finite_model(member, f"{path}.{item.name}")
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _finite_model(item, f"{path}[{index}]")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")


@dataclass(frozen=True)
class ViennaPowerStageConfig:
    line_line_rms_v: float = 400.0
    line_frequency_hz: float = 50.0
    bus_voltage_v: float = 700.0
    output_power_w: float = 10_000.0
    switching_frequency_hz: float = 65.0e3
    boost_inductance_h: float = 600.0e-6
    phase_series_resistance_ohm: float = 0.08
    upper_bus_capacitance_f: float = 680.0e-6
    lower_bus_capacitance_f: float = 680.0e-6
    efficiency: float = 0.98
    load_model: LoadModel = LoadModel.CONSTANT_POWER
    modulation_limit: float = 0.96
    minimum_effective_pulse_s: float = 0.0
    deadtime_s: float = 100.0e-9

    @property
    def phase_rms_v(self) -> float:
        return self.line_line_rms_v / math.sqrt(3.0)

    @property
    def phase_peak_v(self) -> float:
        return math.sqrt(2.0) * self.phase_rms_v

    @property
    def input_conductance_a_per_v(self) -> float:
        return self.output_power_w / (3.0 * self.efficiency * self.phase_rms_v**2)

    @property
    def phase_current_rms_a(self) -> float:
        return self.input_conductance_a_per_v * self.phase_rms_v

    @property
    def series_bus_capacitance_f(self) -> float:
        cp, cn = self.upper_bus_capacitance_f, self.lower_bus_capacitance_f
        return cp * cn / (cp + cn)

    def validate(self) -> None:
        _finite_model(self)
        values = [
            self.line_line_rms_v, self.line_frequency_hz, self.bus_voltage_v,
            self.output_power_w, self.switching_frequency_hz,
            self.boost_inductance_h, self.upper_bus_capacitance_f,
            self.lower_bus_capacitance_f,
        ]
        if min(values) <= 0.0:
            raise ValueError("Vienna stage positive parameters must be > 0")
        if self.phase_series_resistance_ohm < 0.0:
            raise ValueError("phase series resistance cannot be negative")
        if not 0.0 < self.efficiency <= 1.0:
            raise ValueError("efficiency must lie in (0,1]")
        if not 0.1 <= self.modulation_limit <= 1.0:
            raise ValueError("modulation limit must lie in [0.1,1]")
        if self.minimum_effective_pulse_s < 0.0 or self.deadtime_s < 0.0:
            raise ValueError("pulse/deadtime cannot be negative")


@dataclass(frozen=True)
class ViennaFirmwareConfig:
    current_loop_rate_hz: float = 50.0e3
    reference_rate_hz: float = 25.0e3
    voltage_loop_rate_hz: float = 10.0e3
    balance_loop_rate_hz: float = 10.0e3
    current_computation_delay_s: float = 2.0e-6
    pwm_update_delay_s: float = 7.0e-6
    reference_update_delay_s: float = 20.0e-6
    voltage_computation_delay_s: float = 4.0e-6
    balance_injection_limit: float = 0.08
    midpoint_current_gain_a_per_pu: float = 30.0
    third_harmonic_injection_enabled: bool = True
    inductor_voltage_drop_feedforward_enabled: bool = True

    def validate(self) -> None:
        _finite_model(self)
        rates = [self.current_loop_rate_hz, self.reference_rate_hz, self.voltage_loop_rate_hz, self.balance_loop_rate_hz]
        if min(rates) <= 0.0:
            raise ValueError("Vienna firmware rates must be positive")
        if self.current_loop_rate_hz < max(self.reference_rate_hz, self.voltage_loop_rate_hz, self.balance_loop_rate_hz):
            raise ValueError("current loop must be the fastest Vienna control rate")
        if self.balance_injection_limit <= 0.0 or self.midpoint_current_gain_a_per_pu <= 0.0:
            raise ValueError("balance injection limit and midpoint gain must be positive")
        if min(self.current_computation_delay_s, self.pwm_update_delay_s,
               self.reference_update_delay_s, self.voltage_computation_delay_s) < 0.0:
            raise ValueError("Vienna firmware delays cannot be negative")


def default_vienna_current_controller() -> PIControllerConfig:
    return PIControllerConfig(
        kp=0.02,
        ti_s=250.0e-6,
        sample_time_s=20.0e-6,
        output_min=-0.45,
        output_max=0.45,
    )


def default_vienna_voltage_controller() -> PIControllerConfig:
    # Output is conductance command (A/V).
    return PIControllerConfig(
        kp=2.0e-5,
        # Conservative default: keeps the outer loop well separated from the
        # 50/60 Hz line dynamics and gives >50° PM with the default plant.
        ti_s=80.0e-3,
        sample_time_s=100.0e-6,
        output_min=0.0,
        output_max=0.30,
    )


def default_vienna_balance_controller() -> PIControllerConfig:
    return PIControllerConfig(
        kp=1.0e-3,
        ti_s=20.0e-3,
        sample_time_s=100.0e-6,
        output_min=-0.08,
        output_max=0.08,
    )


def _sense(name: str, gain: float, sample_hz: float) -> ExternalSenseConfig:
    return ExternalSenseConfig(
        name=name,
        front_end_gain_v_per_unit=gain,
        amplifier_gain=1.0,
        amplifier_bandwidth_hz=1.5e6,
        output_resistance_ohm=220.0,
        adc_capacitance_f=2.0e-9,
        normalize_to_engineering_units=True,
        timing=ADCTimingConfig(
            sample_rate_hz=sample_hz,
            adc_clock_hz=60.0e6,
            acquisition_time_s=300e-9,
            computation_delay_s=0.0,
            pwm_update_delay_s=0.0,
            digital_filter=DigitalFilterConfig(alpha=1.0),
        ),
    )


def default_phase_current_sense() -> ExternalSenseConfig:
    return _sense("Vienna phase current", 0.03, 50.0e3)


def default_phase_voltage_sense() -> ExternalSenseConfig:
    return _sense("Vienna phase voltage", 1.0 / 180.0, 50.0e3)


def default_split_bus_sense() -> ExternalSenseConfig:
    return ExternalSenseConfig(
        name="Vienna split bus voltage",
        front_end_gain_v_per_unit=1.6e3/(117e3+1.6e3),
        amplifier_gain=1.0,
        amplifier_bandwidth_hz=1.0e6,
        source_resistance_ohm=(117e3*1.6e3)/(117e3+1.6e3),
        shunt_capacitance_f=1.0e-9,
        output_resistance_ohm=220.0,
        adc_capacitance_f=2.0e-9,
        normalize_to_engineering_units=True,
        timing=ADCTimingConfig(
            sample_rate_hz=10.0e3,
            computation_delay_s=0.0,
            pwm_update_delay_s=0.0,
        ),
    )


@dataclass(frozen=True)
class ViennaControlLabConfig:
    power_stage: ViennaPowerStageConfig = field(default_factory=ViennaPowerStageConfig)
    firmware: ViennaFirmwareConfig = field(default_factory=ViennaFirmwareConfig)
    current_controller: ControllerConfig = field(default_factory=default_vienna_current_controller)
    voltage_controller: ControllerConfig = field(default_factory=default_vienna_voltage_controller)
    balance_controller: ControllerConfig = field(default_factory=default_vienna_balance_controller)
    phase_current_sense: ExternalSenseConfig = field(default_factory=default_phase_current_sense)
    phase_voltage_sense: ExternalSenseConfig = field(default_factory=default_phase_voltage_sense)
    split_bus_sense: ExternalSenseConfig = field(default_factory=default_split_bus_sense)
    frequency_start_hz: float = 0.5
    frequency_stop_hz: float = 20.0e3
    frequency_points: int = 1600
    waveform_line_cycles: int = 12
    waveform_integration_rate_hz: float = 500.0e3
    switching_cycles: int = 2
    switching_samples_per_cycle: int = 800
    switching_line_angle_deg: float = 30.0
    initial_midpoint_imbalance_v: float = 4.0
    # Optional sensor mismatch diagnostics.  The baseline GUI keeps ABC locked;
    # when unlocked these scale/offset tuples model channel-to-channel errors
    # without duplicating the complete analog transfer-function definition.
    phase_current_gain_scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    phase_current_offset_a: tuple[float, float, float] = (0.0, 0.0, 0.0)
    phase_voltage_gain_scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    phase_voltage_offset_v: tuple[float, float, float] = (0.0, 0.0, 0.0)
    split_bus_gain_scale: tuple[float, float] = (1.0, 1.0)
    split_bus_offset_v: tuple[float, float] = (0.0, 0.0)

    def loop_nyquist_hz(self, loop: str) -> float:
        fw = self.firmware
        if loop == "current":
            rates = [fw.current_loop_rate_hz, self.phase_current_sense.timing.sample_rate_hz]
            times = [self.current_controller.sample_time_s]
        elif loop == "voltage":
            rates = [fw.current_loop_rate_hz, self.phase_current_sense.timing.sample_rate_hz,
                     fw.voltage_loop_rate_hz, fw.reference_rate_hz,
                     self.phase_voltage_sense.timing.sample_rate_hz,
                     self.split_bus_sense.timing.sample_rate_hz]
            times = [self.current_controller.sample_time_s, self.voltage_controller.sample_time_s]
        elif loop == "balance":
            rates = [fw.balance_loop_rate_hz, self.split_bus_sense.timing.sample_rate_hz]
            times = [self.balance_controller.sample_time_s]
        else:
            raise ValueError("Vienna loop must be current, voltage or balance")
        if any(not math.isfinite(v) or v <= 0 for v in rates + times):
            raise ValueError("Vienna sample rates and controller sample times must be finite and positive")
        return min([0.5 * r for r in rates] + [0.5 / ts for ts in times])

    def loop_frequencies_hz(self, loop: str) -> np.ndarray:
        f = np.geomspace(self.frequency_start_hz, self.frequency_stop_hz, self.frequency_points)
        f = f[f < self.loop_nyquist_hz(loop)]
        if len(f) < 2 or np.any(np.diff(f) <= 0):
            raise ValueError(f"Vienna {loop} loop needs at least two requested samples below Nyquist")
        return f

    def sensing_frequencies_hz(self) -> np.ndarray:
        f = np.geomspace(self.frequency_start_hz, self.frequency_stop_hz, self.frequency_points)
        limit = 0.5 * min(s.timing.sample_rate_hz for s in (
            self.phase_current_sense, self.phase_voltage_sense, self.split_bus_sense))
        f = f[f < limit]
        if len(f) < 2:
            raise ValueError("Vienna sensing panel needs at least two samples below all sensor Nyquist limits")
        return f

    def validate(self) -> None:
        _finite_model(self)
        self.power_stage.validate(); self.firmware.validate()
        self.phase_current_sense.validate(); self.phase_voltage_sense.validate(); self.split_bus_sense.validate()
        if not all(sense.normalize_to_engineering_units for sense in (
                self.phase_current_sense,self.phase_voltage_sense,self.split_bus_sense)):
            raise ValueError("Vienna supported controller units require calibrated engineering-unit sensors")
        if not isinstance(self.power_stage.load_model, LoadModel):
            raise ValueError("Invalid Vienna load model")
        if self.frequency_start_hz <= 0 or self.frequency_stop_hz <= self.frequency_start_hz:
            raise ValueError("invalid Vienna Bode frequency range")
        if not isinstance(self.frequency_points, int) or isinstance(self.frequency_points, bool) or self.frequency_points < 100:
            raise ValueError("Vienna Bode requires >=100 points")
        for loop in ("current", "voltage", "balance"):
            self.loop_frequencies_hz(loop)
            ctrl = getattr(self, loop + "_controller")
            rate = getattr(self.firmware, loop + "_loop_rate_hz")
            if not math.isclose(ctrl.sample_time_s * rate, 1.0, rel_tol=1e-9, abs_tol=0):
                raise ValueError(f"Vienna {loop} controller sample time must match its firmware loop rate")
            if ctrl.output_min >= ctrl.output_max:
                raise ValueError("Vienna controller output minimum must be below maximum")
            ctrl.transfer_function()
        self.sensing_frequencies_hz()
        for name in ("waveform_line_cycles", "switching_cycles", "switching_samples_per_cycle"):
            if not isinstance(getattr(self, name), int) or isinstance(getattr(self, name), bool):
                raise ValueError(f"Vienna {name} must be an integer")
        if not 1 <= self.switching_cycles <= 50 or self.switching_samples_per_cycle < 100:
            raise ValueError("Vienna switching requires 1..50 cycles and >=100 samples/cycle")
        if self.waveform_line_cycles < 3:
            raise ValueError("Vienna line-cycle simulation needs >=3 cycles")
        if abs(self.initial_midpoint_imbalance_v) >= 0.5*self.power_stage.bus_voltage_v:
            raise ValueError("initial midpoint imbalance is unrealistically large")
        if self.waveform_integration_rate_hz < 4*self.firmware.current_loop_rate_hz:
            raise ValueError("Vienna integration rate must be >=4x current loop")
        triples = (
            (self.phase_current_gain_scale, self.phase_current_offset_a, "phase current"),
            (self.phase_voltage_gain_scale, self.phase_voltage_offset_v, "phase voltage"),
        )
        for gain, offset, label in triples:
            if len(gain) != 3 or len(offset) != 3:
                raise ValueError(f"{label} mismatch tuple must have three values")
            if min(gain) <= 0.0:
                raise ValueError(f"{label} gain scales must be positive")
        if len(self.split_bus_gain_scale) != 2 or len(self.split_bus_offset_v) != 2:
            raise ValueError("split-bus mismatch tuple must have two values")
        if min(self.split_bus_gain_scale) <= 0.0:
            raise ValueError("split-bus gain scales must be positive")


def illustrative_vienna_config() -> ViennaControlLabConfig:
    """New opt-in divider example; never migrate loaded legacy numbers.

    249 kΩ / 1.6 kΩ yields 2.235 V at 350 V; 1/250 Vabc gain fits
    the nominal bipolar span inside 3.3 V. Bipolar sensor bias is
    deliberately still unknown: this example is not a hardware qualification.
    """
    base = ViennaControlLabConfig()
    return replace(base, phase_voltage_sense=replace(base.phase_voltage_sense, front_end_gain_v_per_unit=1/250),
        split_bus_sense=replace(base.split_bus_sense,
        front_end_gain_v_per_unit=1600.0 / (249000.0 + 1600.0),
        source_resistance_ohm=249000.0 * 1600.0 / (249000.0 + 1600.0)))


__all__ = [
    "illustrative_vienna_config", "ViennaControlLabConfig", "ViennaFirmwareConfig", "ViennaPowerStageConfig",
    "default_phase_current_sense", "default_phase_voltage_sense", "default_split_bus_sense",
    "default_vienna_current_controller", "default_vienna_voltage_controller", "default_vienna_balance_controller",
]
