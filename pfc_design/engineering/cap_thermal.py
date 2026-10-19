"""TTPL DC-bus capacitor-bank and semiconductor thermal screening.

This module is intentionally engineering-oriented rather than vendor-specific.
It turns the Phase-1 electrical requirements and Phase-2 semiconductor loss
model into explicit capacitor-bank sizing and lumped thermal estimates.

Neither the capacitor lifetime rule nor the thermal network is a release-grade
replacement for vendor lifetime tools, heatsink/airflow modelling or hardware
thermal validation.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .capacitor_ripple import PFCCapacitorRipple
    from .pfc_v3 import PFCLineCycleResult
    from .switched_cycle import PFCSwitchedCycleResult

from pfc_design.core.spec import MosfetSpec

from .device_loss import (
    TTPLHFDeviceLoss,
    TTPLSlowDeviceLoss,
    _design_for_line_cycle,
    evaluate_hf_device,
    evaluate_slow_device,
)
from .ttpl_design import TTPLDesignResult, analyze_ttpl_design


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
    estimated_hotspot_c: float | None
    estimated_life_h: float | None
    predicted_bus_ripple_pp_v: float
    capacitance_ok: bool
    voltage_ok: bool
    ripple_current_ok: bool | None
    warnings: tuple[str, ...]
    waveform_source: str = "ideal_sizing_half_cycle"
    capacitor_ripple: PFCCapacitorRipple | None = None
    bank_averaged_esr_loss_w: float = 0.0
    bank_known_switching_esr_loss_w: float = 0.0
    partial_model_hotspot_c: float | None = None
    line_cycle: PFCLineCycleResult | None = field(default=None, repr=False, compare=False)

    switched_cycle: PFCSwitchedCycleResult | None = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict:
        """Serialize the actual bank screen, source identity and unknown status."""
        if self.switched_cycle is not None:
            self.switched_cycle.require_accepted()
        partial = self.capacitor_ripple is not None and not self.capacitor_ripple.complete
        return {
            "waveform_source": self.waveform_source,
            "status": "PARTIAL" if partial else "COMPLETE_IN_STATED_MODEL",
            "source_config": (asdict(self.switched_cycle.config) if self.switched_cycle is not None else None if self.line_cycle is None else asdict(self.line_cycle.config)),
            "source_line_cycle": None if self.line_cycle is None else self.line_cycle.as_dict(),
            "source_switched_cycle": None if self.switched_cycle is None else self.switched_cycle.as_dict(include_waveforms=True),
            "current_decomposition_scope": "PWM-interval means plus within-PWM variance; same signed circuit source" if self.switched_cycle else "averaged producer plus local CCM reconstruction",
            "design_spec": asdict(self.design.spec),
            "unit": asdict(self.unit), "config": asdict(self.config),
            "series_count": self.series_count, "parallel_count": self.parallel_count,
            "bank_capacitance_f": self.bank_capacitance_f,
            "bank_esr_ohm": self.bank_esr_ohm,
            "known_model_current_rms_a": self.bank_ripple_current_rms_a,
            "known_model_esr_loss_w": self.bank_esr_loss_w,
            "bank_averaged_esr_loss_w": self.bank_averaged_esr_loss_w,
            "bank_known_switching_esr_loss_w": self.bank_known_switching_esr_loss_w,
            "known_model_per_cap_esr_loss_w": self.per_cap_esr_loss_w,
            "estimated_hotspot_c": self.estimated_hotspot_c,
            "estimated_life_h": self.estimated_life_h,
            "partial_model_hotspot_c": self.partial_model_hotspot_c,
            "averaged_bus_ripple_pp_v": self.predicted_bus_ripple_pp_v if self.switched_cycle is None else None,
            "projected_switching_charge_esr_ripple_pp_v": self.predicted_bus_ripple_pp_v if self.switched_cycle is not None else None,
            "ripple_current_ok": self.ripple_current_ok,
            "capacitor_ripple": None if self.capacitor_ripple is None else self.capacitor_ripple.as_dict(),
            "warnings": list(self.warnings),
        }

    @property
    def bank_capacitance_uf(self) -> float:
        return self.bank_capacitance_f * 1e6

    @property
    def estimated_life_years(self) -> float | None:
        return None if self.estimated_life_h is None else self.estimated_life_h / (365.0 * 24.0)


def design_bus_capacitor_bank(
    design: TTPLDesignResult,
    unit: CapacitorUnitSpec,
    config: CapacitorBankDesignConfig = CapacitorBankDesignConfig(),
    *,
    line_cycle: PFCLineCycleResult | None = None,
    switched_cycle: PFCSwitchedCycleResult | None = None,
) -> CapacitorBankResult:
    """Auto-size series/parallel capacitor count from voltage, C and ripple limits.

    For ``Ns`` identical capacitors in one string and ``Np`` strings in parallel:

    ``Cbank = Cunit * Np / Ns``
    ``ESRbank = ESRunit * Ns / Np``

    Without an accepted cycle the bank ripple is the Phase-1 twice-line estimate.
    With one, available local CCM PWM second moments enter sizing/ESR heating.
    If slices are unresolved, current and heating are explicitly partial-model
    values; complete hot-spot, lifetime and ripple PASS remain unavailable.
    Series capacitors in a branch carry the same branch current.  Parallel
    branches are assumed to share current equally.
    """

    unit.validate()
    config.validate()
    if line_cycle is not None and switched_cycle is not None:
        raise ValueError('select exactly one current source')
    if switched_cycle is not None:
        from .switched_loss import design_for_switched_cycle
        design = analyze_ttpl_design(design_for_switched_cycle(design, switched_cycle).spec)
    if line_cycle is not None:
        design = _design_for_line_cycle(design, line_cycle)
        stage = line_cycle.config.power_stage
        # Reuse the sizing kernel for the source's actual power/voltage while
        # preserving the user's ripple/hold-up/derating requirements.
        design = analyze_ttpl_design(replace(
            design.spec, vin_nom_rms_v=stage.vin_rms_v,
            vin_min_rms_v=min(design.spec.vin_min_rms_v, stage.vin_rms_v),
            vin_max_rms_v=max(design.spec.vin_max_rms_v, stage.vin_rms_v),
        ))
    spec = design.spec
    required_c = design.recommended_bus_capacitance_f
    from .capacitor_ripple import analyze_capacitor_ripple
    capacitor_ripple = None if line_cycle is None else analyze_capacitor_ripple(line_cycle)
    bank_i_rms = (design.bus_capacitor_ripple_current_rms_a if capacitor_ripple is None
                  else capacitor_ripple.known_model_current_rms_a)
    complete = capacitor_ripple is None or capacitor_ripple.complete
    if switched_cycle is not None:
        bank_i_rms = switched_cycle.capacitor_current_rms_a

    usable_voltage = unit.rated_voltage_v * config.voltage_derating
    bank_voltage = spec.bus_voltage_v if line_cycle is None else float(np.max(line_cycle.vbus_v))
    if switched_cycle is not None:
        bank_voltage = float(np.max(switched_cycle.signals["v(bus)"]))
    series_count = max(1, math.ceil(bank_voltage / usable_voltage))

    parallel_for_c = max(
        1,
        math.ceil(required_c * series_count / unit.capacitance_f),
    )
    usable_ripple_each = unit.rated_ripple_current_a * config.ripple_current_derating
    parallel_for_i = max(1, math.ceil(bank_i_rms / usable_ripple_each))
    parallel_count = max(parallel_for_c, parallel_for_i)

    bank_c = unit.capacitance_f * parallel_count / series_count
    bank_esr = unit.esr_ohm * series_count / parallel_count
    per_cap_voltage = bank_voltage / series_count
    per_cap_ripple = bank_i_rms / parallel_count
    per_cap_loss = per_cap_ripple**2 * unit.esr_ohm
    bank_loss = bank_i_rms**2 * bank_esr
    averaged_rms = bank_i_rms if capacitor_ripple is None else capacitor_ripple.averaged_current_rms_a
    averaged_loss = averaged_rms**2 * bank_esr
    switching_loss = (0.0 if capacitor_ripple is None else
                      float(np.mean(capacitor_ripple.known_switching_variance_a2)) * bank_esr)
    if switched_cycle is not None:
        moments = switched_cycle.capacitor_pwm_moments()
        averaged_loss = moments['pwm_interval_mean_square_a2'] * bank_esr
        switching_loss = moments['within_pwm_variance_a2'] * bank_esr
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

    if line_cycle is not None:
        dt = float(line_cycle.time_s[1] - line_cycle.time_s[0])
        ac_current = line_cycle.capacitor_current_a - np.mean(line_cycle.capacitor_current_a)
        voltage_ac = np.cumsum(ac_current) * dt / bank_c
        predicted_ripple = float(np.ptp(voltage_ac))

    if switched_cycle is not None:
        from .switched_cycle import time_mean
        t = switched_cycle.time_s
        current = switched_cycle.signals['i(vscap)']
        current = current-time_mean(t, current)
        charge = np.r_[0., np.cumsum(np.diff(t)*(current[:-1]+current[1:])/2)]
        predicted_ripple = float(np.ptp(charge/bank_c + current*bank_esr))
    capacitance_ok = bank_c + 1e-15 >= required_c
    voltage_ok = per_cap_voltage <= usable_voltage + 1e-12
    ripple_ok = (per_cap_ripple <= usable_ripple_each + 1e-12) if complete else None

    warnings: list[str] = [
        "Capacitor lifetime uses an empirical 10°C rule and lumped ESR heating; verify with the selected vendor model.",
    ]
    if line_cycle is not None:
        warnings.extend(capacitor_ripple.limitations)
        warnings.append("ESR is the user-entered constant value for both averaged and known switching terms; no vendor frequency correction is invented. Bus voltage ripple prediction uses averaged current only; rerun control after applying Cbank.")
        if not complete:
            warnings.append("Bank count and ESR heating use available partial-model current, not a complete ripple qualification. Complete hot-spot/lifetime and ripple derating PASS are unavailable.")
    if switched_cycle is not None:
        warnings.append('Full signed shared-ngspice capacitor branch RMS, including switching and zero-cross paths, feeds constant-ESR heating. PWM means/within-window variance are an integration decomposition, not spectral ESR bands. Generic diode/switch model; no vendor frequency correction. Bank changes require a circuit rerun; projected charge/ESR ripple uses this frozen current.')
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
    if ripple_ok is False:
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
        estimated_hotspot_c=hotspot if complete else None,
        estimated_life_h=life_h if complete else None,
        predicted_bus_ripple_pp_v=predicted_ripple,
        capacitance_ok=capacitance_ok,
        voltage_ok=voltage_ok,
        ripple_current_ok=ripple_ok,
        warnings=tuple(warnings),
        waveform_source=("shared_ngspice_signed_switching_cycle" if switched_cycle is not None else
                         "ideal_sizing_half_cycle" if line_cycle is None else "closed_loop_final_line_cycle"),
        capacitor_ripple=capacitor_ripple,
        bank_averaged_esr_loss_w=averaged_loss,
        bank_known_switching_esr_loss_w=switching_loss,
        partial_model_hotspot_c=hotspot if not complete else None,
        line_cycle=line_cycle, switched_cycle=switched_cycle,
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
    """Physical-device thermal solution with legacy role fields retained.

    active/sr_device_loss_w are aggregate PWM-role powers, not thermal sources.
    Deprecated active/sr_junction_temperature_c alias HF lower/upper respectively
    (their positive-half-cycle roles); slow_junction_temperature_c is the hotter
    LF device and slow_device_loss_each_w is the pair average. New consumers use
    the explicit physical upper/lower fields and hf_loss/slow_loss breakdowns.
    """

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

    hf_upper_device_loss_w: float | None = None
    hf_lower_device_loss_w: float | None = None
    hf_upper_junction_temperature_c: float | None = None
    hf_lower_junction_temperature_c: float | None = None
    slow_upper_device_loss_w: float | None = None
    slow_lower_device_loss_w: float | None = None
    slow_upper_junction_temperature_c: float | None = None
    slow_lower_junction_temperature_c: float | None = None
    temperature_residual_c: float | None = None
    waveform_source: str = "ideal_sizing_half_cycle"
    source_identity: str | None = None


def solve_ttpl_semiconductor_thermal(
    design: TTPLDesignResult,
    hf_device: MosfetSpec,
    slow_device: MosfetSpec,
    *,
    workpoint: str = "low",
    config: TTPLThermalConfig = TTPLThermalConfig(),
    line_cycle: PFCLineCycleResult | None = None,
    switched_cycle: PFCSwitchedCycleResult | None = None,
) -> TTPLThermalResult:
    """Solve four physical switches' loss↔temperature fixed points.

    Positive Vac makes HF lower active/HF upper SR and LF lower conducting;
    negative Vac reverses these roles. Full-cycle role powers are integrated
    into fixed upper/lower sources before applying their own Tj/Rds feedback.
    Legacy half-cycle inputs are explicitly mirrored. See evaluate_hf_device
    for the per-role switching/deadtime and per-device gate/Coss allocation.
    """

    config.validate()
    if line_cycle is not None and switched_cycle is not None:
        raise ValueError('select exactly one current source')
    if switched_cycle is not None:
        from .switched_loss import (
            design_for_switched_cycle,
            evaluate_switched_hf,
            evaluate_switched_slow,
        )
        design = design_for_switched_cycle(design, switched_cycle)
    if line_cycle is not None:
        design = _design_for_line_cycle(design, line_cycle)
    key = workpoint.strip().casefold()
    workpoints = {
        "low": design.spec.vin_min_rms_v,
        "nominal": design.spec.vin_nom_rms_v,
        "high": design.spec.vin_max_rms_v,
    }
    if key not in workpoints:
        raise ValueError("workpoint must be 'low', 'nominal' or 'high'")
    vin = workpoints[key] if line_cycle is None else line_cycle.config.power_stage.vin_rms_v
    if line_cycle is not None:
        key = "closed_loop"

    if switched_cycle is not None:
        key, vin = 'switching', switched_cycle.config.power_stage.vin_rms_v
    initial = max(config.initial_junction_temperature_c, config.ambient_temperature_c)
    # Fixed node order, not role order: HF upper/lower, LF upper/lower.
    temperatures = np.full(4, initial, dtype=float)
    rth = np.array([config.hf_rth_ja_k_per_w] * 2 + [config.slow_rth_ja_k_per_w] * 2)
    converged = False
    iterations = 0

    def evaluate_at(tj):
        if switched_cycle is not None:
            hf = evaluate_switched_hf(switched_cycle, hf_device, upper_temperature_c=float(tj[0]), lower_temperature_c=float(tj[1]), voltage_derating=config.voltage_derating)
            slow = evaluate_switched_slow(switched_cycle, slow_device, upper_temperature_c=float(tj[2]), lower_temperature_c=float(tj[3]), voltage_derating=config.voltage_derating)
            return hf, slow, np.array([hf.upper.total_w, hf.lower.total_w, slow.upper.total_w, slow.lower.total_w])
        hf = evaluate_hf_device(
            design, hf_device, vin_rms_v=vin,
            upper_junction_temperature_c=float(tj[0]),
            lower_junction_temperature_c=float(tj[1]),
            deadtime_s=config.deadtime_s, reverse_drop_v=config.reverse_drop_v,
            voltage_derating=config.voltage_derating, line_cycle=line_cycle)
        slow = evaluate_slow_device(
            design, slow_device, vin_rms_v=vin,
            upper_junction_temperature_c=float(tj[2]),
            lower_junction_temperature_c=float(tj[3]),
            voltage_derating=config.voltage_derating, line_cycle=line_cycle)
        powers = np.array([hf.upper.total_w, hf.lower.total_w,
                           slow.upper.total_w, slow.lower.total_w])
        return hf, slow, powers

    for iterations in range(1, config.max_iterations + 1):
        hf_loss, slow_loss, powers = evaluate_at(temperatures)
        proposed = config.ambient_temperature_c + powers * rth
        residual = float(np.max(np.abs(proposed - temperatures)))
        if residual <= config.convergence_tolerance_c:
            converged = True
            break
        # Each physical device's own temperature is damped independently.
        temperatures = 0.5 * (temperatures + proposed)

    # Losses use exactly the returned temperatures; the fixed-point residual
    # quantifies the remaining thermal-equation error (also on nonconvergence).
    hf_loss, slow_loss, powers = evaluate_at(temperatures)
    residual = float(np.max(np.abs(config.ambient_temperature_c + powers * rth - temperatures)))
    converged = residual <= config.convergence_tolerance_c
    upper_tj, lower_tj, slow_upper_tj, slow_lower_tj = map(float, temperatures)
    shared_role = 0.5 * (hf_loss.coss_w + hf_loss.gate_drive_w)
    active_power = hf_loss.active_conduction_w + hf_loss.active_switching_w + shared_role
    sr_power = hf_loss.sr_conduction_w + hf_loss.sr_switching_w + hf_loss.deadtime_reverse_w + shared_role
    slow_each = 0.5 * slow_loss.total_w
    slow_tj = max(slow_upper_tj, slow_lower_tj)
    thermal_ok = bool(max(temperatures) <= config.maximum_junction_temperature_c)
    electrical_ok = (
        hf_loss.voltage_ok
        and hf_loss.current_ok
        and slow_loss.voltage_ok
        and slow_loss.current_ok
    )

    warnings: list[str] = [
        "Semiconductor temperatures use lumped junction-to-ambient thermal resistances; heatsink spreading, interface, airflow and transient impedance are not modelled.",
        "Physical mapping: positive Vac => HF lower active, HF upper SR, LF lower on; negative Vac swaps roles. Each fixed device uses its own temperature/Rds.",
        "Active Eon(valley)/Eoff(peak) and SR Eoff(valley) follow their roles; both CCM deadtimes use SR reverse conduction; gate and the existing Coss proxy are per physical switch. Switching energies remain screening estimates and datasheet Eon/Coss may overlap.",
    ]
    if switched_cycle is not None:
        warnings = [warnings[0], 'Four physical branch sources use their own signed channel I², observed generic diode V*I, gate-edge switching energies and own temperature/Rds. Semiconductor screening overlays selected data on the frozen generic circuit; it is not a device-specific circuit rerun.']
    elif line_cycle is not None:
        warnings.append("All four physical switches consume the same accepted full-cycle trajectory and equal timestep weights, including series-inductor switching ripple in both HF/LF conduction.")
    else:
        warnings.append("The sizing half-cycle is mirrored: each fixed HF switch averages half the active and SR roles; LF switches share the symmetric cycle equally, including reconstructed inductor switching ripple.")
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
        active_junction_temperature_c=lower_tj,
        sr_junction_temperature_c=upper_tj,
        slow_junction_temperature_c=slow_tj,
        converged=converged,
        iterations=iterations,
        thermal_ok=thermal_ok,
        electrical_ok=electrical_ok,
        warnings=tuple(warnings),
        hf_upper_device_loss_w=float(powers[0]),
        hf_lower_device_loss_w=float(powers[1]),
        hf_upper_junction_temperature_c=upper_tj,
        hf_lower_junction_temperature_c=lower_tj,
        slow_upper_device_loss_w=float(powers[2]),
        slow_lower_device_loss_w=float(powers[3]),
        slow_upper_junction_temperature_c=slow_upper_tj,
        slow_lower_junction_temperature_c=slow_lower_tj,
        temperature_residual_c=residual,
        source_identity=switched_cycle.source_identity if switched_cycle is not None else None,
        waveform_source=("shared_ngspice_signed_switching_cycle" if switched_cycle is not None else
                         "ideal_sizing_half_cycle" if line_cycle is None else "closed_loop_final_line_cycle"),
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
