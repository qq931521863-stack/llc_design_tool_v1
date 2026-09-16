from __future__ import annotations

import math

import pytest

from pfc_design.engineering import TTPLDesignSpec, analyze_ttpl_design


def test_default_ttpl_inductor_is_sized_from_low_line_peak_ripple():
    spec = TTPLDesignSpec()
    result = analyze_ttpl_design(spec)

    i_rms_low = spec.output_power_w / (spec.efficiency * spec.vin_min_rms_v)
    i_peak_low = math.sqrt(2.0) * i_rms_low
    delta_i_target = spec.inductor_ripple_ratio_peak * i_peak_low
    # Low-line peak is above Vbus/2, therefore max[V*(1-V/Vbus)] = Vbus/4.
    expected_l = (spec.bus_voltage_v / 4.0) / (
        spec.switching_frequency_hz * delta_i_target
    )

    assert result.required_inductance_h == pytest.approx(expected_l, rel=2e-4)
    assert result.ripple_max_pp_a == pytest.approx(delta_i_target, rel=2e-4)
    assert result.ripple_target_pp_a == pytest.approx(delta_i_target)
    assert 50.0 < result.ripple_max_angle_deg < 60.0
    assert result.estimated_inductor_peak_a > result.low_line_input_current_peak_a


def test_bus_capacitor_uses_max_of_twice_line_ripple_and_hold_up():
    spec = TTPLDesignSpec()
    result = analyze_ttpl_design(spec)

    omega = 2.0 * math.pi * spec.line_frequency_hz
    c_ripple = spec.output_power_w / (
        omega * spec.bus_voltage_v * spec.bus_ripple_pp_v
    )
    c_hold = 2.0 * spec.output_power_w * spec.hold_up_time_s / (
        spec.bus_voltage_v**2 - spec.hold_up_end_voltage_v**2
    )

    assert result.required_bus_cap_ripple_f == pytest.approx(c_ripple)
    assert result.required_bus_cap_hold_up_f == pytest.approx(c_hold)
    assert result.recommended_bus_capacitance_f == pytest.approx(max(c_ripple, c_hold))
    assert result.predicted_bus_ripple_pp_v <= spec.bus_ripple_pp_v * (1.0 + 1e-12)


def test_input_workpoints_capture_low_nominal_high_line_current_stress():
    result = analyze_ttpl_design(TTPLDesignSpec())
    low, nominal, high = result.work_points

    assert low.label == "Low line"
    assert nominal.label == "Nominal"
    assert high.label == "High line"
    assert low.input_current_rms_a > nominal.input_current_rms_a > high.input_current_rms_a
    assert low.input_current_peak_a > nominal.input_current_peak_a > high.input_current_peak_a
    assert low.duty_at_line_peak > nominal.duty_at_line_peak > high.duty_at_line_peak


def test_insufficient_high_line_boost_headroom_is_reported_not_hidden():
    spec = TTPLDesignSpec(bus_voltage_v=360.0, hold_up_end_voltage_v=300.0)
    result = analyze_ttpl_design(spec)

    assert result.boost_headroom_v < 0.0
    assert any("boost-only PFC cannot regulate" in warning for warning in result.warnings)


def test_invalid_voltage_range_is_rejected():
    with pytest.raises(ValueError, match="Vin range"):
        analyze_ttpl_design(
            TTPLDesignSpec(vin_min_rms_v=230.0, vin_nom_rms_v=220.0, vin_max_rms_v=264.0)
        )
