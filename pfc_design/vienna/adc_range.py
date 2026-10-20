"""Pure operating-point checks; never infer a bipolar ADC bias."""
import math
from power_control_tools.adc_range import assess_adc_magnitude


def operating_adc_range_warnings(config) -> tuple[str, ...]:
    config.validate()
    stage = config.power_stage
    warnings = []
    for index, sign in enumerate((1, -1)):
        peak = max(stage.bus_voltage_v/2, stage.bus_voltage_v/2 + sign*config.initial_midpoint_imbalance_v/2)
        peak = abs(peak*config.split_bus_gain_scale[index] + config.split_bus_offset_v[index])
        check = assess_adc_magnitude(f"Vdc{'+' if sign == 1 else '-'}", peak,
            config.split_bus_sense.raw_dc_gain, config.split_bus_sense.adc_vref_v,
            "Nominal/initial split-bus level; ADC clipping omitted")
        if not check.within_range:
            warnings.append(f"ADC_OVERRANGE {check.name}: {check.detail}")
    # A bipolar span wider than Vref cannot fit at ANY unknown bias. This
    # necessary condition does not invent a midpoint bias or assert headroom.
    current_bound = (math.sqrt(2)*stage.phase_current_rms_a
                     + stage.bus_voltage_v/(8*stage.boost_inductance_h*stage.switching_frequency_hz))
    for label, peak, sense, scales in (
        ("Iabc", current_bound, config.phase_current_sense, config.phase_current_gain_scale),
        ("Vabc", stage.phase_peak_v, config.phase_voltage_sense, config.phase_voltage_gain_scale),
    ):
        span = 2*peak*sense.raw_dc_gain*max(scales)
        if span >= sense.adc_vref_v:
            warnings.append(f"ADC_OVERRANGE {label}: required bipolar span {span:.7g} V >= Vref {sense.adc_vref_v:.7g} V; no unknown DC bias can fit both polarities. Clipping omitted.")
    return tuple(warnings)
