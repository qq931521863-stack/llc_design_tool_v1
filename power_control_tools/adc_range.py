"""Magnitude-only ADC headroom checks for the linear model's operating range.

These checks do not model bipolar offsets, clipping dynamics, overshoot or trips.
Passing one therefore does not certify a board-level measurement front end.
"""
from __future__ import annotations
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ADCMagnitudeCheck:
    name: str
    expected_peak: float
    input_voltage_v: float
    reference_voltage_v: float
    engineering_full_scale: float
    within_range: bool
    basis: str

    @property
    def detail(self) -> str:
        return (f"{self.basis}: peak={self.expected_peak:.7g} eng -> "
                f"{self.input_voltage_v:.7g} V; ADC Vref={self.reference_voltage_v:.7g} V, "
                f"engineering full-scale={self.engineering_full_scale:.7g}; "
                "magnitude-only, no offset/transient headroom certification")


def assess_adc_magnitude(name: str, peak: float, raw_gain: float,
                         vref_v: float, basis: str) -> ADCMagnitudeCheck:
    if not all(math.isfinite(v) for v in (peak, raw_gain, vref_v)):
        raise ValueError("ADC range inputs must be finite")
    if peak < 0 or raw_gain <= 0 or vref_v <= 0:
        raise ValueError("ADC range requires nonnegative magnitude and positive gain/reference")
    voltage = peak * raw_gain
    return ADCMagnitudeCheck(name, peak, voltage, vref_v, vref_v/raw_gain,
                             voltage < vref_v, basis)


def boost_input_current_peak_bound(*, output_power_w: float, efficiency: float,
                                   minimum_vin_rms_v: float, bus_voltage_v: float,
                                   inductance_h: float, switching_frequency_hz: float) -> float:
    """Conservative ideal-CCM sine peak + half worst-case switching ripple.

    D=1-v/Vbus gives v*D <= Vbus/4, so ΔIpp<=Vbus/(4*L*fsw).
    The two maxima need not occur at the same line phase. No inrush/overload.
    """
    return (math.sqrt(2)*output_power_w/(efficiency*minimum_vin_rms_v)
            + bus_voltage_v/(8*inductance_h*switching_frequency_hz))
