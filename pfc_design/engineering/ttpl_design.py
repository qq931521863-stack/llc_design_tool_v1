"""Single-phase totem-pole PFC engineering power-stage sizing.

This module answers the design questions that should come *before* the control
laboratory: input current stress, boost-inductor target, line-cycle ripple,
duty/pulse envelope and DC-bus capacitance from twice-line ripple and hold-up.

The equations are deliberately explicit and conservative.  They are not a
replacement for switching simulation or component datasheet correlation.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class TTPLDesignSpec:
    """User-facing single-phase TTPL design requirements.

    ``inductor_ripple_ratio_peak`` is the allowed maximum boost-inductor ripple
    (peak-to-peak) as a fraction of the low-line sinusoidal *peak* input current.
    ``bus_ripple_pp_v`` is the allowed twice-line DC-bus ripple, peak-to-peak.
    """

    vin_min_rms_v: float = 176.0
    vin_nom_rms_v: float = 230.0
    vin_max_rms_v: float = 264.0
    line_frequency_hz: float = 50.0
    bus_voltage_v: float = 400.0
    output_power_w: float = 3300.0
    efficiency: float = 0.97
    switching_frequency_hz: float = 65_000.0
    inductor_ripple_ratio_peak: float = 0.30
    bus_ripple_pp_v: float = 12.0
    hold_up_time_s: float = 10.0e-3
    hold_up_end_voltage_v: float = 320.0
    duty_min: float = 0.01
    duty_max: float = 0.98
    minimum_effective_pulse_s: float = 0.0
    trace_points: int = 721

    def validate(self) -> None:
        positive = {
            "Vin min": self.vin_min_rms_v,
            "Vin nominal": self.vin_nom_rms_v,
            "Vin max": self.vin_max_rms_v,
            "line frequency": self.line_frequency_hz,
            "bus voltage": self.bus_voltage_v,
            "output power": self.output_power_w,
            "switching frequency": self.switching_frequency_hz,
            "bus ripple": self.bus_ripple_pp_v,
        }
        for label, value in positive.items():
            if value <= 0.0:
                raise ValueError(f"{label} must be positive")
        if not self.vin_min_rms_v <= self.vin_nom_rms_v <= self.vin_max_rms_v:
            raise ValueError("Vin range must satisfy Vin_min <= Vin_nom <= Vin_max")
        if not 0.0 < self.efficiency <= 1.0:
            raise ValueError("efficiency must lie in (0, 1]")
        if not 0.01 <= self.inductor_ripple_ratio_peak <= 2.0:
            raise ValueError("inductor ripple ratio must lie in [0.01, 2]")
        if self.hold_up_time_s < 0.0:
            raise ValueError("hold-up time cannot be negative")
        if not 0.0 < self.hold_up_end_voltage_v < self.bus_voltage_v:
            raise ValueError("hold-up end voltage must lie between zero and Vbus")
        if not 0.0 <= self.duty_min < self.duty_max <= 1.0:
            raise ValueError("invalid duty limits")
        if self.minimum_effective_pulse_s < 0.0:
            raise ValueError("minimum effective pulse cannot be negative")
        if self.trace_points < 181:
            raise ValueError("trace_points must be at least 181")


@dataclass(frozen=True)
class TTPLInputWorkPoint:
    label: str
    vin_rms_v: float
    input_current_rms_a: float
    input_current_peak_a: float
    peak_line_voltage_v: float
    duty_at_line_peak: float
    minimum_on_time_s: float


@dataclass(frozen=True)
class TTPLLineTrace:
    angle_deg: np.ndarray
    vin_abs_v: np.ndarray
    duty: np.ndarray
    average_inductor_current_a: np.ndarray
    ripple_pp_a: np.ndarray
    inductor_peak_a: np.ndarray
    inductor_valley_a: np.ndarray
    on_time_s: np.ndarray


@dataclass(frozen=True)
class TTPLDesignResult:
    spec: TTPLDesignSpec
    required_inductance_h: float
    ripple_target_pp_a: float
    ripple_max_pp_a: float
    ripple_max_angle_deg: float
    low_line_input_current_rms_a: float
    low_line_input_current_peak_a: float
    estimated_inductor_peak_a: float
    required_bus_cap_ripple_f: float
    required_bus_cap_hold_up_f: float
    recommended_bus_capacitance_f: float
    bus_capacitor_ripple_current_rms_a: float
    predicted_bus_ripple_pp_v: float
    boost_headroom_v: float
    work_points: tuple[TTPLInputWorkPoint, ...]
    nominal_trace: TTPLLineTrace
    warnings: tuple[str, ...]

    @property
    def required_inductance_uh(self) -> float:
        return self.required_inductance_h * 1e6

    @property
    def recommended_bus_capacitance_uf(self) -> float:
        return self.recommended_bus_capacitance_f * 1e6


def _input_current(spec: TTPLDesignSpec, vin_rms_v: float) -> tuple[float, float]:
    i_rms = spec.output_power_w / (spec.efficiency * vin_rms_v)
    return i_rms, math.sqrt(2.0) * i_rms


def _ideal_duty(vin_abs_v: np.ndarray, spec: TTPLDesignSpec) -> np.ndarray:
    raw = 1.0 - vin_abs_v / spec.bus_voltage_v
    return np.clip(raw, spec.duty_min, spec.duty_max)


def _inductor_numerator(vin_abs_v: np.ndarray, spec: TTPLDesignSpec) -> np.ndarray:
    """Return V*D for the CCM boost ripple equation ΔI=V*D/(L*fs)."""
    raw_duty = np.maximum(1.0 - vin_abs_v / spec.bus_voltage_v, 0.0)
    return vin_abs_v * raw_duty


def _required_inductance(spec: TTPLDesignSpec) -> tuple[float, float, float]:
    _, i_peak_low = _input_current(spec, spec.vin_min_rms_v)
    ripple_target = spec.inductor_ripple_ratio_peak * i_peak_low
    angle = np.linspace(0.0, math.pi, spec.trace_points)
    vin_abs = math.sqrt(2.0) * spec.vin_min_rms_v * np.sin(angle)
    numerator = _inductor_numerator(vin_abs, spec)
    index = int(np.argmax(numerator))
    maximum = float(numerator[index])
    inductance = maximum / (spec.switching_frequency_hz * ripple_target)
    return inductance, ripple_target, math.degrees(float(angle[index]))


def _line_trace(spec: TTPLDesignSpec, vin_rms_v: float, inductance_h: float) -> TTPLLineTrace:
    angle = np.linspace(0.0, math.pi, spec.trace_points)
    sin_angle = np.sin(angle)
    vin_abs = math.sqrt(2.0) * vin_rms_v * sin_angle
    duty = _ideal_duty(vin_abs, spec)
    _, i_peak = _input_current(spec, vin_rms_v)
    current_average = i_peak * sin_angle
    ripple = _inductor_numerator(vin_abs, spec) / (
        max(inductance_h, 1e-15) * spec.switching_frequency_hz
    )
    current_peak = current_average + 0.5 * ripple
    current_valley = np.maximum(current_average - 0.5 * ripple, 0.0)
    on_time = duty / spec.switching_frequency_hz
    return TTPLLineTrace(
        angle_deg=np.degrees(angle),
        vin_abs_v=vin_abs,
        duty=duty,
        average_inductor_current_a=current_average,
        ripple_pp_a=ripple,
        inductor_peak_a=current_peak,
        inductor_valley_a=current_valley,
        on_time_s=on_time,
    )


def _input_work_point(spec: TTPLDesignSpec, label: str, vin_rms_v: float) -> TTPLInputWorkPoint:
    i_rms, i_peak = _input_current(spec, vin_rms_v)
    vpk = math.sqrt(2.0) * vin_rms_v
    duty_peak = float(np.clip(1.0 - vpk / spec.bus_voltage_v, spec.duty_min, spec.duty_max))
    return TTPLInputWorkPoint(
        label=label,
        vin_rms_v=vin_rms_v,
        input_current_rms_a=i_rms,
        input_current_peak_a=i_peak,
        peak_line_voltage_v=vpk,
        duty_at_line_peak=duty_peak,
        minimum_on_time_s=duty_peak / spec.switching_frequency_hz,
    )


def _bus_capacitance(
    spec: TTPLDesignSpec,
) -> tuple[float, float, float, float, float]:
    # Twice-line energy pulsation: ΔVpp ~= P/(ω_line*C*Vbus).
    omega_line = 2.0 * math.pi * spec.line_frequency_hz
    c_ripple = spec.output_power_w / (
        omega_line * spec.bus_voltage_v * spec.bus_ripple_pp_v
    )
    if spec.hold_up_time_s <= 0.0:
        c_hold = 0.0
    else:
        denominator = spec.bus_voltage_v**2 - spec.hold_up_end_voltage_v**2
        c_hold = 2.0 * spec.output_power_w * spec.hold_up_time_s / denominator
    recommended = max(c_ripple, c_hold)
    predicted_ripple = spec.output_power_w / (
        omega_line * spec.bus_voltage_v * recommended
    )
    # First-order twice-line capacitor-current estimate with Vbus treated stiff.
    i_cap_rms = spec.output_power_w / (math.sqrt(2.0) * spec.bus_voltage_v)
    return c_ripple, c_hold, recommended, predicted_ripple, i_cap_rms


def analyze_ttpl_design(spec: TTPLDesignSpec) -> TTPLDesignResult:
    """Size the first-order TTPL boost stage from electrical requirements."""
    spec.validate()
    inductance, ripple_target, _ripple_design_angle = _required_inductance(spec)
    nominal = _line_trace(spec, spec.vin_nom_rms_v, inductance)
    low_line = _line_trace(spec, spec.vin_min_rms_v, inductance)
    ripple_index = int(np.argmax(low_line.ripple_pp_a))
    ripple_max = float(low_line.ripple_pp_a[ripple_index])
    low_i_rms, low_i_peak = _input_current(spec, spec.vin_min_rms_v)
    inductor_peak = float(np.max(low_line.inductor_peak_a))
    c_ripple, c_hold, c_bus, predicted_ripple, i_cap_rms = _bus_capacitance(spec)

    vpk_high = math.sqrt(2.0) * spec.vin_max_rms_v
    headroom = spec.bus_voltage_v - vpk_high
    work_points = (
        _input_work_point(spec, "Low line", spec.vin_min_rms_v),
        _input_work_point(spec, "Nominal", spec.vin_nom_rms_v),
        _input_work_point(spec, "High line", spec.vin_max_rms_v),
    )

    warnings: list[str] = []
    if headroom <= 0.0:
        warnings.append(
            "High-line peak input voltage is at or above the requested DC bus; a boost-only PFC cannot regulate this point."
        )
    elif headroom < 0.05 * spec.bus_voltage_v:
        warnings.append(
            "High-line boost headroom is below 5% of Vbus; duty resolution/minimum-pulse behaviour needs explicit switching validation."
        )
    if c_hold > c_ripple * 1.25:
        warnings.append("DC-bus capacitance is hold-up dominated rather than twice-line-ripple dominated.")
    if spec.minimum_effective_pulse_s > 0.0:
        min_on = min(point.minimum_on_time_s for point in work_points)
        if min_on < spec.minimum_effective_pulse_s:
            warnings.append(
                "Requested minimum effective pulse exceeds the high-line on-time at the line peak; minimum-pulse parking/distortion must be modelled."
            )
    if float(np.min(low_line.inductor_valley_a[1:-1])) <= 0.0:
        warnings.append(
            "The first-order ripple envelope touches zero current near the line crossing; CCM-only assumptions are not valid there."
        )

    return TTPLDesignResult(
        spec=spec,
        required_inductance_h=inductance,
        ripple_target_pp_a=ripple_target,
        ripple_max_pp_a=ripple_max,
        ripple_max_angle_deg=float(low_line.angle_deg[ripple_index]),
        low_line_input_current_rms_a=low_i_rms,
        low_line_input_current_peak_a=low_i_peak,
        estimated_inductor_peak_a=inductor_peak,
        required_bus_cap_ripple_f=c_ripple,
        required_bus_cap_hold_up_f=c_hold,
        recommended_bus_capacitance_f=c_bus,
        bus_capacitor_ripple_current_rms_a=i_cap_rms,
        predicted_bus_ripple_pp_v=predicted_ripple,
        boost_headroom_v=headroom,
        work_points=work_points,
        nominal_trace=nominal,
        warnings=tuple(warnings),
    )


__all__ = [
    "TTPLDesignResult",
    "TTPLDesignSpec",
    "TTPLInputWorkPoint",
    "TTPLLineTrace",
    "analyze_ttpl_design",
]
