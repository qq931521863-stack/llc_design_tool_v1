"""TTPL DC-bus capacitor-bank and semiconductor thermal screening.

This module is intentionally engineering-oriented rather than vendor-specific.
It turns the Phase-1 electrical requirements and Phase-2 semiconductor loss
model into explicit capacitor-bank sizing and lumped thermal estimates.

Neither the capacitor lifetime rule nor the thermal network is a release-grade
replacement for vendor lifetime tools, heatsink/airflow modelling or hardware
thermal validation.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from pfc_design.core.spec import MosfetSpec
from .device_loss import (
    TTPLHFDeviceLoss,
    TTPLSlowDeviceLoss,
    evaluate_hf_device,
    evaluate_slow_device,
)
from .ttpl_design import TTPLDesignResult


@dataclass(frozen=True)
class CapacitorUnitSpec:
    """One capacitor used to construct the DC-bus bank."""

    name: str = "Generic 450 V / 470 uF electrolytic"
    capacitance_f: float = 470.0e-6
    rated_voltage_v: float = 450.0
    esr_ohm: float = 0.120
    rated_ripple_current_a: float = 3.5
    rated_temperature_c: float = 105.0
    rated_life_h: float = 5000.0
    thermal_resistance_k_per_w: float = 18.0

    def validate(self) -> None:
        values = {
            "unit capacitance": self.capacitance_f,
            "rated voltage": self.rated_voltage_v,
            "ESR": self.esr_ohm,
            "rated ripple current": self.rated_ripple_current_a,
            "rated life": self.rated_life_h,
            "thermal resistance": self.thermal_resistance_k_per_w,
        }
        for label, value in values.items():
            if value <= 0.0:
                raise ValueError(f"{label} must be positive")
        if self.rated_temperature_c <= -40.0:
            raise ValueError("rated temperature is not physically meaningful")


@dataclass(frozen=True)
class CapacitorBankDesignConfig:
    """Derating and ambient assumptions for capacitor-bank auto sizing."""

    voltage_derating: float = 0.90
    ripple_current_derating: float = 0.80
    ambient_temperature_c: float = 45.0

    def validate(self) -> None:
        if not 0.1 <= self.voltage_derating <= 1.0:
            raise ValueError("capacitor voltage derating must lie in [0.1,1]")
        if not 0.1 <= self.ripple_current_derating <= 1.0:
            raise ValueError("capacitor ripple-current derating must lie in [0.1,1]")
        if not -40.0 <= self.ambient_temperature_c <= 150.0:
            raise ValueError("capacitor ambient temperature is outside the screening range")


@dataclass(frozen=True)
class CapacitorBankResult:
    design: TTPLDesignResult
    unit: CapacitorUnitSpec
    config: CapacitorBankDesignConfig
    series_count: int
    parallel_count: int
    total_count: int
    bank_capacitance_f: float
    bank_esr_ohm: float
    bank_ripple_current_rms_a: float
    per_cap_voltage_v: float
    per_cap_ripple_current_rms_a: float
    per_cap_esr_loss_w: float
    bank_esr_loss_w: float
    estimated_hotspot_c: float
    estimated_life_h: float
    predicted_bus_ripple_pp_v: float
    capacitance_ok: bool
    voltage_ok: bool
    ripple_current_ok: bool
    warnings: tuple[str, ...]

    @property
    def bank_capacitance_uf(self) -> float:
        return self.bank_capacitance_f * 1e6

    @property
    def estimated_life_years(self) -> float:
        return self.estimated_life_h / (365.0 * 24.0)


def design_bus_capacitor_bank(
    design: TTPLDesignResult,
    unit: CapacitorUnitSpec,
    config: CapacitorBankDesignConfig = CapacitorBankDesignConfig(),
) -> CapacitorBankResult:
    """Auto-size series/parallel capacitor count from voltage, C and ripple limits.

    For ``Ns`` identical capacitors in one string and ``Np`` strings in parallel:

    ``Cbank = Cunit * Np / Ns``
    ``ESRbank = ESRunit * Ns / Np``

    The low-frequency bank ripple current is the Phase-1 twice-line estimate.
    Series capacitors in a branch carry the same branch current.  Parallel
    branches are assumed to share current equally.
    """

    unit.validate()
    config.validate()
    spec = design.spec
    required_c = design.recommended_bus_capacitance_f
    bank_i_rms = design.bus_capacitor_ripple_current_rms_a

    usable_voltage = unit.rated_voltage_v * config.voltage_derating
    series_count = max(1, math.ceil(spec.bus_voltage_v / usable_voltage))

    parallel_for_c = max(
        1,
        math.ceil(required_c * series_count / unit.capacitance_f),
    )
    usable_ripple_each = unit.rated_ripple_current_a * config.ripple_current_derating
    parallel_for_i = max(1, math.ceil(bank_i_rms / usable_ripple_each))
    parallel_count = max(parallel_for_c, parallel_for_i)

    bank_c = unit.capacitance_f * parallel_count / series_count
    bank_esr = unit.esr_ohm * series_count / parallel_count
    per_cap_voltage = spec.bus_voltage_v / series_count
    per_cap_ripple = bank_i_rms / parallel_count
    per_cap_loss = per_cap_ripple**2 * unit.esr_ohm
    bank_loss = bank_i_rms**2 * bank_esr
    hotspot = config.ambient_temperature_c + per_cap_loss * unit.thermal_resistance_k_per_w

    # Common electrolytic 10 °C lifetime rule.  This is a screening estimate,
    # not a substitute for the selected vendor's ripple/frequency/life model.
    if hotspot >= unit.rated_temperature_c:
        life_h = 0.0
    else:
        life_h = unit.rated_life_h * 2.0 ** (
            (unit.rated_temperature_c - hotspot) / 10.0
        )
        if per_cap_ripple > unit.rated_ripple_current_a:
            life_h *= (
                unit.rated_ripple_current_a / max(per_cap_ripple, 1e-12)
            ) ** 3.0

    omega_line = 2.0 * math.pi * spec.line_frequency_hz
    predicted_ripple = spec.output_power_w / (
        omega_line * spec.bus_voltage_v * bank_c
    )

    capacitance_ok = bank_c + 1e-15 >= required_c
    voltage_ok = per_cap_voltage <= usable_voltage + 1e-12
    ripple_ok = per_cap_ripple <= usable_ripple_each + 1e-12

    warnings: list[str] = [
        "Capacitor lifetime uses an empirical 10°C rule and lumped ESR heating; verify with the selected vendor model.",
    ]
    if series_count > 1:
        warnings.append(
            "Series capacitor strings require voltage-sharing/balancing design; ideal equal voltage sharing is assumed here."
        )
    if hotspot >= unit.rated_temperature_c:
        warnings.append(
            "Estimated capacitor hot-spot reaches or exceeds the rated temperature; lifetime estimate is invalid/zero."
        )
    if not capacitance_ok:
        warnings.append("Auto-sized bank does not meet required capacitance.")
    if not voltage_ok:
        warnings.append("Auto-sized bank violates the configured voltage derating.")
    if not ripple_ok:
        warnings.append("Auto-sized bank violates the configured ripple-current derating.")

    return CapacitorBankResult(
        design=design,
        unit=unit,
        config=config,
        series_count=series_count,
        parallel_count=parallel_count,
        total_count=series_count * parallel_count,
        bank_capacitance_f=bank_c,
        bank_esr_ohm=bank_esr,
        bank_ripple_current_rms_a=bank_i_rms,
        per_cap_voltage_v=per_cap_voltage,
        per_cap_ripple_current_rms_a=per_cap_ripple,
        per_cap_esr_loss_w=per_cap_loss,
        bank_esr_loss_w=bank_loss,
        estimated_hotspot_c=hotspot,
        estimated_life_h=life_h,
        predicted_bus_ripple_pp_v=predicted_ripple,
        capacitance_ok=capacitance_ok,
        voltage_ok=voltage_ok,
        ripple_current_ok=ripple_ok,
        warnings=tuple(warnings),
    )


@dataclass(frozen=True)
class TTPLThermalConfig:
    """Lumped semiconductor thermal-network assumptions."""

    ambient_temperature_c: float = 45.0
    hf_rth_ja_k_per_w: float = 2.0
    slow_rth_ja_k_per_w: float = 3.0
    maximum_junction_temperature_c: float = 150.0
    initial_junction_temperature_c: float = 80.0
    convergence_tolerance_c: float = 0.05
    max_iterations: int = 50
    voltage_derating: float = 0.80
    deadtime_s: float = 100.0e-9
    reverse_drop_v: float = 2.0

    def validate(self) -> None:
        if self.hf_rth_ja_k_per_w <= 0.0 or self.slow_rth_ja_k_per_w <= 0.0:
            raise ValueError("thermal resistances must be positive")
        if self.maximum_junction_temperature_c <= self.ambient_temperature_c:
            raise ValueError("maximum junction temperature must exceed ambient")
        if self.convergence_tolerance_c <= 0.0 or self.max_iterations < 1:
            raise ValueError("invalid thermal convergence settings")
        if not 0.0 < self.voltage_derating <= 1.0:
            raise ValueError("VDS derating must lie in (0,1]")
        if self.deadtime_s < 0.0 or self.reverse_drop_v < 0.0:
            raise ValueError("deadtime and reverse drop cannot be negative")


@dataclass(frozen=True)
class TTPLThermalResult:
    design: TTPLDesignResult
    hf_device: MosfetSpec
    slow_device: MosfetSpec
    workpoint: str
    vin_rms_v: float
    config: TTPLThermalConfig
    hf_loss: TTPLHFDeviceLoss
    slow_loss: TTPLSlowDeviceLoss
    active_device_loss_w: float
    sr_device_loss_w: float
    slow_device_loss_each_w: float
    active_junction_temperature_c: float
    sr_junction_temperature_c: float
    slow_junction_temperature_c: float
    converged: bool
    iterations: int
    thermal_ok: bool
    electrical_ok: bool
    warnings: tuple[str, ...]


def solve_ttpl_semiconductor_thermal(
    design: TTPLDesignResult,
    hf_device: MosfetSpec,
    slow_device: MosfetSpec,
    *,
    workpoint: str = "low",
    config: TTPLThermalConfig = TTPLThermalConfig(),
) -> TTPLThermalResult:
    """Solve a self-consistent lumped loss↔temperature fixed point.

    The HF loss model returns the complete two-device fast leg.  Coss, gate and
    deadtime terms are therefore divided equally between the active and SR
    devices for the thermal estimate.  The line-frequency-leg total is divided
    between its two physical devices.  This allocation is explicit and should
    be replaced by measured/device-specific commutation data when available.
    """

    config.validate()
    key = workpoint.strip().casefold()
    workpoints = {
        "low": design.spec.vin_min_rms_v,
        "nominal": design.spec.vin_nom_rms_v,
        "high": design.spec.vin_max_rms_v,
    }
    if key not in workpoints:
        raise ValueError("workpoint must be 'low', 'nominal' or 'high'")
    vin = workpoints[key]

    hf_tj = max(config.initial_junction_temperature_c, config.ambient_temperature_c)
    slow_tj = hf_tj
    converged = False
    iterations = 0
    active_tj = hf_tj
    sr_tj = hf_tj

    for iterations in range(1, config.max_iterations + 1):
        hf_loss = evaluate_hf_device(
            design,
            hf_device,
            vin_rms_v=vin,
            junction_temperature_c=hf_tj,
            deadtime_s=config.deadtime_s,
            reverse_drop_v=config.reverse_drop_v,
            voltage_derating=config.voltage_derating,
        )
        shared_hf = hf_loss.deadtime_reverse_w + hf_loss.coss_w + hf_loss.gate_drive_w
        active_power = hf_loss.active_conduction_w + hf_loss.active_switching_w + 0.5 * shared_hf
        sr_power = hf_loss.sr_conduction_w + hf_loss.sr_switching_w + 0.5 * shared_hf
        active_new = config.ambient_temperature_c + active_power * config.hf_rth_ja_k_per_w
        sr_new = config.ambient_temperature_c + sr_power * config.hf_rth_ja_k_per_w
        hf_new = max(active_new, sr_new)

        slow_loss = evaluate_slow_device(
            design,
            slow_device,
            vin_rms_v=vin,
            junction_temperature_c=slow_tj,
            voltage_derating=config.voltage_derating,
        )
        slow_each = 0.5 * slow_loss.total_w
        slow_new = config.ambient_temperature_c + slow_each * config.slow_rth_ja_k_per_w

        if (
            abs(hf_new - hf_tj) <= config.convergence_tolerance_c
            and abs(slow_new - slow_tj) <= config.convergence_tolerance_c
        ):
            active_tj, sr_tj, slow_tj = active_new, sr_new, slow_new
            hf_tj = hf_new
            converged = True
            break

        # Damped fixed-point iteration is more robust with strongly
        # temperature-dependent Si superjunction RDS(on).
        hf_tj = 0.5 * hf_tj + 0.5 * hf_new
        slow_tj = 0.5 * slow_tj + 0.5 * slow_new
        active_tj, sr_tj = active_new, sr_new

    # Re-evaluate at the final temperatures so the returned loss values match
    # the reported thermal operating point.
    hf_loss = evaluate_hf_device(
        design,
        hf_device,
        vin_rms_v=vin,
        junction_temperature_c=max(active_tj, sr_tj),
        deadtime_s=config.deadtime_s,
        reverse_drop_v=config.reverse_drop_v,
        voltage_derating=config.voltage_derating,
    )
    shared_hf = hf_loss.deadtime_reverse_w + hf_loss.coss_w + hf_loss.gate_drive_w
    active_power = hf_loss.active_conduction_w + hf_loss.active_switching_w + 0.5 * shared_hf
    sr_power = hf_loss.sr_conduction_w + hf_loss.sr_switching_w + 0.5 * shared_hf
    active_tj = config.ambient_temperature_c + active_power * config.hf_rth_ja_k_per_w
    sr_tj = config.ambient_temperature_c + sr_power * config.hf_rth_ja_k_per_w

    slow_loss = evaluate_slow_device(
        design,
        slow_device,
        vin_rms_v=vin,
        junction_temperature_c=slow_tj,
        voltage_derating=config.voltage_derating,
    )
    slow_each = 0.5 * slow_loss.total_w
    slow_tj = config.ambient_temperature_c + slow_each * config.slow_rth_ja_k_per_w

    thermal_ok = max(active_tj, sr_tj, slow_tj) <= config.maximum_junction_temperature_c
    electrical_ok = (
        hf_loss.voltage_ok
        and hf_loss.current_ok
        and slow_loss.voltage_ok
        and slow_loss.current_ok
    )

    warnings: list[str] = [
        "Semiconductor temperatures use lumped junction-to-ambient thermal resistances; heatsink spreading, interface, airflow and transient impedance are not modelled.",
    ]
    if not converged:
        warnings.append("Loss-temperature fixed-point iteration did not converge within the configured iteration limit.")
    if not thermal_ok:
        warnings.append("Estimated semiconductor junction temperature exceeds the configured thermal limit.")
    if not electrical_ok:
        warnings.append("At least one selected semiconductor fails the screening VDS or current-rating check.")

    return TTPLThermalResult(
        design=design,
        hf_device=hf_device,
        slow_device=slow_device,
        workpoint=key,
        vin_rms_v=vin,
        config=config,
        hf_loss=hf_loss,
        slow_loss=slow_loss,
        active_device_loss_w=active_power,
        sr_device_loss_w=sr_power,
        slow_device_loss_each_w=slow_each,
        active_junction_temperature_c=active_tj,
        sr_junction_temperature_c=sr_tj,
        slow_junction_temperature_c=slow_tj,
        converged=converged,
        iterations=iterations,
        thermal_ok=thermal_ok,
        electrical_ok=electrical_ok,
        warnings=tuple(warnings),
    )


__all__ = [
    "CapacitorBankDesignConfig",
    "CapacitorBankResult",
    "CapacitorUnitSpec",
    "TTPLThermalConfig",
    "TTPLThermalResult",
    "design_bus_capacitor_bank",
    "solve_ttpl_semiconductor_thermal",
]
