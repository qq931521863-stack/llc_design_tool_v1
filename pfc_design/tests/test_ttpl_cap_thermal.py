from __future__ import annotations

import pytest

from pfc_design.engineering import (
    CapacitorBankDesignConfig,
    CapacitorUnitSpec,
    PFCDeviceDatabase,
    TTPLDesignSpec,
    TTPLThermalConfig,
    analyze_ttpl_design,
    design_bus_capacitor_bank,
    solve_ttpl_semiconductor_thermal,
)


def _design():
    return analyze_ttpl_design(TTPLDesignSpec())


def test_capacitor_auto_size_meets_capacitance_voltage_and_ripple_constraints():
    design = _design()
    unit = CapacitorUnitSpec(
        capacitance_f=470e-6,
        rated_voltage_v=450.0,
        esr_ohm=0.12,
        rated_ripple_current_a=3.5,
        rated_temperature_c=105.0,
        rated_life_h=5000.0,
        thermal_resistance_k_per_w=18.0,
    )
    config = CapacitorBankDesignConfig(
        voltage_derating=0.90,
        ripple_current_derating=0.80,
        ambient_temperature_c=45.0,
    )
    result = design_bus_capacitor_bank(design, unit, config)

    assert result.capacitance_ok
    assert result.voltage_ok
    assert result.ripple_current_ok
    assert result.bank_capacitance_f >= design.recommended_bus_capacitance_f
    assert result.per_cap_voltage_v <= unit.rated_voltage_v * config.voltage_derating
    assert result.per_cap_ripple_current_rms_a <= (
        unit.rated_ripple_current_a * config.ripple_current_derating
    )
    assert result.bank_esr_loss_w == pytest.approx(
        result.bank_ripple_current_rms_a**2 * result.bank_esr_ohm
    )


def test_capacitor_series_count_and_bank_equations_are_explicit():
    design = _design()
    unit = CapacitorUnitSpec(
        name="Low-voltage unit",
        capacitance_f=1000e-6,
        rated_voltage_v=200.0,
        esr_ohm=0.05,
        rated_ripple_current_a=10.0,
        rated_temperature_c=105.0,
        rated_life_h=5000.0,
        thermal_resistance_k_per_w=10.0,
    )
    config = CapacitorBankDesignConfig(voltage_derating=0.80, ripple_current_derating=0.80)
    result = design_bus_capacitor_bank(design, unit, config)

    assert result.series_count == 3
    assert result.bank_capacitance_f == pytest.approx(
        unit.capacitance_f * result.parallel_count / result.series_count
    )
    assert result.bank_esr_ohm == pytest.approx(
        unit.esr_ohm * result.series_count / result.parallel_count
    )
    assert any("balancing" in warning for warning in result.warnings)


def test_capacitor_life_screen_decreases_with_hotter_ambient():
    design = _design()
    unit = CapacitorUnitSpec()
    cool = design_bus_capacitor_bank(
        design,
        unit,
        CapacitorBankDesignConfig(ambient_temperature_c=35.0),
    )
    hot = design_bus_capacitor_bank(
        design,
        unit,
        CapacitorBankDesignConfig(ambient_temperature_c=75.0),
    )
    assert cool.estimated_hotspot_c < hot.estimated_hotspot_c
    assert cool.estimated_life_h > hot.estimated_life_h


def test_semiconductor_thermal_fixed_point_responds_to_rth():
    design = _design()
    db = PFCDeviceDatabase(include_user=False)
    hf = db.get("GS66516T")
    slow = db.get("IPW65R032M8")

    cool = solve_ttpl_semiconductor_thermal(
        design,
        hf,
        slow,
        config=TTPLThermalConfig(hf_rth_ja_k_per_w=0.5, slow_rth_ja_k_per_w=1.0),
    )
    hot = solve_ttpl_semiconductor_thermal(
        design,
        hf,
        slow,
        config=TTPLThermalConfig(hf_rth_ja_k_per_w=3.0, slow_rth_ja_k_per_w=5.0),
    )

    assert cool.converged
    assert hot.converged
    assert hot.active_junction_temperature_c > cool.active_junction_temperature_c
    assert hot.sr_junction_temperature_c > cool.sr_junction_temperature_c
    assert hot.slow_junction_temperature_c > cool.slow_junction_temperature_c
    assert cool.active_device_loss_w > 0.0
    assert cool.sr_device_loss_w > 0.0
    assert cool.slow_device_loss_each_w > 0.0


def test_semiconductor_thermal_limit_can_fail_without_hiding_electrical_checks():
    design = _design()
    db = PFCDeviceDatabase(include_user=False)
    result = solve_ttpl_semiconductor_thermal(
        design,
        db.get("GS66516T"),
        db.get("IPW65R032M8"),
        config=TTPLThermalConfig(
            ambient_temperature_c=80.0,
            hf_rth_ja_k_per_w=8.0,
            slow_rth_ja_k_per_w=8.0,
            maximum_junction_temperature_c=90.0,
        ),
    )
    assert result.thermal_ok is False
    assert any("thermal limit" in warning for warning in result.warnings)
