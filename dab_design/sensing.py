"""Explicit single-rate engineering-voltage measurement model for DAB.

The filter is an exponential *digital* low-pass, not an undocumented analog ADC
aperture model. ADC divider/reference/bits document headroom and LSB only;
quantization, acquisition aperture and switching ripple aliasing are not modeled.
"""

import math
from dataclasses import dataclass

import numpy as np

from power_control_tools.adc_range import assess_adc_magnitude


@dataclass(frozen=True)
class DABSenseConfig:
    calibration_gain: float = 1.0
    lowpass_hz: float = 0.0
    delay_ticks: int = 0
    divider_gain: float = 0.006
    adc_reference_v: float = 3.3
    adc_bits: int = 12

    def validate(self, sample_hz):
        numbers = (
            self.calibration_gain,
            self.lowpass_hz,
            self.divider_gain,
            self.adc_reference_v,
        )
        if not all(math.isfinite(x) for x in numbers):
            raise ValueError("Finite sensing parameters required")
        if min(self.calibration_gain, self.divider_gain, self.adc_reference_v) <= 0:
            raise ValueError("Positive sensing gains and ADC reference required")
        if self.lowpass_hz < 0 or self.lowpass_hz >= sample_hz / 2:
            raise ValueError(
                "Sense low-pass must be disabled (0) or below sample Nyquist"
            )
        if type(self.delay_ticks) is not int or not 0 <= self.delay_ticks <= 10:
            raise ValueError("Sense delay ticks must be an integer from 0 to 10")
        if type(self.adc_bits) is not int or not 1 <= self.adc_bits <= 32:
            raise ValueError("ADC bits must be an integer from 1 to 32")

    def coefficients(self, sample_hz):
        self.validate(sample_hz)
        alpha = (
            math.exp(-2 * math.pi * self.lowpass_hz / sample_hz)
            if self.lowpass_hz
            else 0.0
        )
        b = np.r_[np.zeros(self.delay_ticks), self.calibration_gain * (1 - alpha)]
        a = np.array([1.0, -alpha]) if alpha else np.array([1.0])
        return b, a

    def headroom(self, voltage_v):
        return assess_adc_magnitude(
            "DAB output voltage",
            voltage_v,
            self.divider_gain,
            self.adc_reference_v,
            "Nominal DC output voltage",
        )
