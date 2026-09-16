"""Canonical system-definition contract for the V9.3 guided design workflow.

The guided GUI and the expert workspaces must converge on one engineering
model.  This module deliberately contains no Qt imports and no topology solver
code.  It records what the user has defined: power stage, sensing, ADC,
modulator/PWM, timing and control architecture.  Existing LLC/PFC kernels remain
the numerical authorities.

V9.3 first supports LLC and single-phase TTPL PFC.  Other topology identifiers
are reserved so the launcher can show the future roadmap without pretending
that those guided adapters already exist.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SystemTopology(str, Enum):
    LLC = "llc"
    TTPL_PFC = "ttpl_pfc"
    VIENNA_PFC = "vienna_pfc"
    BUCK = "buck"
    BOOST = "boost"
    BUCK_BOOST = "buck_boost"
    FLYBACK = "flyback"
    GENERIC = "generic"


GUIDED_V93_SUPPORTED_TOPOLOGIES = frozenset(
    {SystemTopology.LLC, SystemTopology.TTPL_PFC}
)


class PlantSource(str, Enum):
    ANALYTICAL = "analytical"
    IMPORTED_FRA = "imported_fra"
    IDENTIFIED = "identified"
    CUSTOM_GS = "custom_gs"
    CUSTOM_GZ = "custom_gz"


class ControlArchitecture(str, Enum):
    LLC_FM_VOLTAGE = "llc_fm_voltage"
    TTPL_DUAL_LOOP = "ttpl_dual_loop"
    VOLTAGE_MODE = "voltage_mode"
    AVERAGE_CURRENT_MODE = "average_current_mode"
    PEAK_CURRENT_MODE = "peak_current_mode"
    CUSTOM = "custom"


@dataclass(frozen=True)
class LLCStageDefinition:
    vbus_min_v: float = 360.0
    vbus_nom_v: float = 400.0
    vbus_max_v: float = 420.0
    vout_v: float = 53.0
    pout_w: float = 3000.0
    resonant_frequency_hz: float = 100e3
    minimum_frequency_hz: float = 60e3
    maximum_frequency_hz: float = 180e3
    ln_ratio: float = 5.0
    q_full_load: float = 0.35
    primary_turns: int = 30
    secondary_turns: int = 4

    def validate(self) -> None:
        if not (0.0 < self.vbus_min_v <= self.vbus_nom_v <= self.vbus_max_v):
            raise ValueError("LLC bus voltage must satisfy min <= nominal <= max")
        if self.vout_v <= 0.0 or self.pout_w <= 0.0:
            raise ValueError("LLC output voltage/power must be positive")
        if not (
            0.0 < self.minimum_frequency_hz
            <= self.resonant_frequency_hz
            <= self.maximum_frequency_hz
        ):
            raise ValueError("LLC frequency ordering must satisfy fmin <= fr <= fmax")
        if self.ln_ratio <= 1.0 or self.q_full_load <= 0.0:
            raise ValueError("LLC requires Ln > 1 and positive full-load Q")
        if self.primary_turns < 1 or self.secondary_turns < 1:
            raise ValueError("LLC transformer turns must be positive integers")


@dataclass(frozen=True)
class TTPLStageDefinition:
    vin_min_rms_v: float = 176.0
    vin_nom_rms_v: float = 230.0
    vin_max_rms_v: float = 264.0
    line_frequency_hz: float = 50.0
    bus_voltage_v: float = 400.0
    output_power_w: float = 3300.0
    efficiency: float = 0.97
    switching_frequency_hz: float = 50e3
    boost_inductance_h: float = 220e-6
    inductor_dcr_ohm: float = 55e-3
    bus_capacitance_f: float = 1320e-6
    bus_cap_esr_ohm: float = 35e-3

    def validate(self) -> None:
        if not (0.0 < self.vin_min_rms_v <= self.vin_nom_rms_v <= self.vin_max_rms_v):
            raise ValueError("TTPL input voltage must satisfy min <= nominal <= max")
        for label, value in (
            ("line frequency", self.line_frequency_hz),
            ("bus voltage", self.bus_voltage_v),
            ("output power", self.output_power_w),
            ("switching frequency", self.switching_frequency_hz),
            ("boost inductance", self.boost_inductance_h),
            ("bus capacitance", self.bus_capacitance_f),
        ):
            if value <= 0.0:
                raise ValueError(f"TTPL {label} must be positive")
        if not 0.0 < self.efficiency <= 1.0:
            raise ValueError("TTPL efficiency must lie in (0, 1]")
        if self.inductor_dcr_ohm < 0.0 or self.bus_cap_esr_ohm < 0.0:
            raise ValueError("TTPL ESR/DCR cannot be negative")


@dataclass(frozen=True)
class SensorDefinition:
    """One explicit analog + sampled feedback path in engineering units."""

    name: str
    front_end_gain_v_per_unit: float
    amplifier_gain: float = 1.0
    amplifier_bandwidth_hz: float = 1e6
    source_resistance_ohm: float = 0.0
    source_capacitance_f: float = 0.0
    adc_series_resistance_ohm: float = 220.0
    adc_shunt_capacitance_f: float = 2e-9
    digital_filter_alpha: float = 1.0
    sample_rate_hz: float = 50e3
    # Optional physical divider fields.  They are useful for LLC where the
    # existing expert page exposes Rup/Rlow/Cdiv directly.
    divider_upper_ohm: float = 0.0
    divider_lower_ohm: float = 0.0

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("sensor name cannot be empty")
        if self.front_end_gain_v_per_unit <= 0.0 or self.amplifier_gain <= 0.0:
            raise ValueError(f"{self.name}: sensor gains must be positive")
        if self.amplifier_bandwidth_hz < 0.0 or self.sample_rate_hz <= 0.0:
            raise ValueError(f"{self.name}: invalid bandwidth/sample rate")
        for value in (
            self.source_resistance_ohm,
            self.source_capacitance_f,
            self.adc_series_resistance_ohm,
            self.adc_shunt_capacitance_f,
            self.divider_upper_ohm,
            self.divider_lower_ohm,
        ):
            if value < 0.0:
                raise ValueError(f"{self.name}: passive values cannot be negative")
        if not 0.0 < self.digital_filter_alpha <= 1.0:
            raise ValueError(f"{self.name}: digital LPF alpha must lie in (0, 1]")


@dataclass(frozen=True)
class ADCDefinition:
    vref_v: float = 3.3
    bits: int = 12
    clock_hz: float = 60e6
    acquisition_time_s: float = 300e-9
    conversion_cycles: float = 13.0
    soc_count: int = 1
    soc_spacing_s: float = 0.0
    recursive_previous_weight: float = 0.0

    def validate(self) -> None:
        if self.vref_v <= 0.0 or self.bits < 1:
            raise ValueError("ADC reference/resolution is invalid")
        if self.clock_hz <= 0.0 or self.acquisition_time_s < 0.0:
            raise ValueError("ADC clock/acquisition is invalid")
        if self.conversion_cycles < 0.0 or self.soc_count < 1 or self.soc_spacing_s < 0.0:
            raise ValueError("ADC conversion/SOC configuration is invalid")
        if not 0.0 <= self.recursive_previous_weight < 1.0:
            raise ValueError("ADC recursive previous weight must lie in [0, 1)")

    @property
    def conversion_time_s(self) -> float:
        return self.conversion_cycles / self.clock_hz

    @property
    def acquisition_to_ready_s(self) -> float:
        return (
            0.5 * self.acquisition_time_s
            + self.conversion_time_s
            + max(self.soc_count - 1, 0) * self.soc_spacing_s
        )


@dataclass(frozen=True)
class ModulatorDefinition:
    kind: str
    switching_frequency_hz: float
    timer_clock_hz: float = 120e6
    count_mode: str = "up_down"
    duty_min: float = 0.01
    duty_max: float = 0.98
    minimum_pulse_s: float = 0.0
    deadtime_s: float = 100e-9

    def validate(self) -> None:
        if not self.kind.strip() or self.switching_frequency_hz <= 0.0:
            raise ValueError("modulator type/switching frequency is invalid")
        if self.timer_clock_hz <= 0.0:
            raise ValueError("modulator timer clock must be positive")
        if self.count_mode not in {"up", "up_down"}:
            raise ValueError("count_mode must be 'up' or 'up_down'")
        if not 0.0 <= self.duty_min < self.duty_max <= 1.0:
            raise ValueError("modulator duty limits are invalid")
        if self.minimum_pulse_s < 0.0 or self.deadtime_s < 0.0:
            raise ValueError("minimum pulse/deadtime cannot be negative")


@dataclass(frozen=True)
class TimingDefinition:
    computation_delay_s: float = 1e-6
    pwm_update_delay_s: float = 0.0
    include_zero_order_hold: bool = True

    def validate(self) -> None:
        if self.computation_delay_s < 0.0 or self.pwm_update_delay_s < 0.0:
            raise ValueError("digital timing delays cannot be negative")

    @property
    def total_command_delay_s(self) -> float:
        return self.computation_delay_s + self.pwm_update_delay_s


@dataclass(frozen=True)
class ControllerIntent:
    structure: str = "PI"
    target_crossover_hz: float | None = None
    target_phase_margin_deg: float | None = None
    source: str = "existing_workspace_baseline"

    def validate(self) -> None:
        if not self.structure.strip():
            raise ValueError("controller structure cannot be empty")
        if self.target_crossover_hz is not None and self.target_crossover_hz <= 0.0:
            raise ValueError("target crossover must be positive when specified")
        if self.target_phase_margin_deg is not None and not 0.0 < self.target_phase_margin_deg < 180.0:
            raise ValueError("target phase margin must lie in (0, 180) degrees")


@dataclass(frozen=True)
class ControlSystemDefinition:
    """Canonical output of Guided System Modeling & Design."""

    topology: SystemTopology
    plant_source: PlantSource
    architecture: ControlArchitecture
    llc_stage: LLCStageDefinition | None = None
    ttpl_stage: TTPLStageDefinition | None = None
    sensors: tuple[SensorDefinition, ...] = field(default_factory=tuple)
    adc: ADCDefinition = field(default_factory=ADCDefinition)
    modulator: ModulatorDefinition = field(
        default_factory=lambda: ModulatorDefinition("PWM", 50e3)
    )
    timing: TimingDefinition = field(default_factory=TimingDefinition)
    controller: ControllerIntent = field(default_factory=ControllerIntent)
    schema_version: str = "1.0"

    @property
    def guided_adapter_available(self) -> bool:
        return self.topology in GUIDED_V93_SUPPORTED_TOPOLOGIES

    def validate(self) -> None:
        if not self.guided_adapter_available:
            raise NotImplementedError(
                f"V9.3 guided adapter is not implemented for {self.topology.value}"
            )
        if self.topology == SystemTopology.LLC:
            if self.llc_stage is None or self.ttpl_stage is not None:
                raise ValueError("LLC definition requires llc_stage only")
            if self.architecture != ControlArchitecture.LLC_FM_VOLTAGE:
                raise ValueError("LLC V9.3 guided path requires FM voltage-loop architecture")
            self.llc_stage.validate()
        elif self.topology == SystemTopology.TTPL_PFC:
            if self.ttpl_stage is None or self.llc_stage is not None:
                raise ValueError("TTPL definition requires ttpl_stage only")
            if self.architecture != ControlArchitecture.TTPL_DUAL_LOOP:
                raise ValueError("TTPL V9.3 guided path requires dual-loop architecture")
            self.ttpl_stage.validate()
        if not self.sensors:
            raise ValueError("at least one explicit sensing path is required")
        for sensor in self.sensors:
            sensor.validate()
        self.adc.validate()
        self.modulator.validate()
        self.timing.validate()
        self.controller.validate()

    def validation_checks(self) -> tuple[str, ...]:
        """Return human-readable setup checks after strict validation."""
        self.validate()
        stage = self.llc_stage if self.topology == SystemTopology.LLC else self.ttpl_stage
        return (
            f"Topology: {self.topology.value} adapter available",
            f"Plant source: {self.plant_source.value}",
            f"Power stage: {type(stage).__name__} valid",
            f"Sensing: {len(self.sensors)} explicit path(s) valid",
            f"ADC: {self.adc.bits}-bit / {self.adc.vref_v:g} V / {self.adc.clock_hz/1e6:g} MHz",
            f"Modulator: {self.modulator.kind} @ {self.modulator.switching_frequency_hz/1e3:g} kHz",
            f"Command delay: {self.timing.total_command_delay_s*1e6:g} us",
            f"Controller intent: {self.controller.structure}",
        )

    def as_manifest(self) -> dict[str, Any]:
        data = asdict(self)
        data["topology"] = self.topology.value
        data["plant_source"] = self.plant_source.value
        data["architecture"] = self.architecture.value
        return data


__all__ = [
    "ADCDefinition",
    "ControlArchitecture",
    "ControllerIntent",
    "ControlSystemDefinition",
    "GUIDED_V93_SUPPORTED_TOPOLOGIES",
    "LLCStageDefinition",
    "ModulatorDefinition",
    "PlantSource",
    "SensorDefinition",
    "SystemTopology",
    "TTPLStageDefinition",
    "TimingDefinition",
]
