from __future__ import annotations

from dataclasses import replace

import pytest

from llc_design.core.spec import LLCDesignSpec
from llc_design.gui.system_modeling import (
    llc_spec_from_system_definition,
    ttpl_config_from_system_definition,
)
from pfc_design.control import PFCControlLabConfig
from power_control_tools.system_definition import (
    ADCDefinition,
    ControlArchitecture,
    ControlSystemDefinition,
    LLCStageDefinition,
    ModulatorDefinition,
    PlantSource,
    SensorDefinition,
    SystemTopology,
    TTPLStageDefinition,
    TimingDefinition,
)


def _llc_definition() -> ControlSystemDefinition:
    rup = 117e3
    rlow = 1.6e3
    return ControlSystemDefinition(
        topology=SystemTopology.LLC,
        plant_source=PlantSource.ANALYTICAL,
        architecture=ControlArchitecture.LLC_FM_VOLTAGE,
        llc_stage=LLCStageDefinition(),
        sensors=(SensorDefinition(
            name="LLC output voltage",
            front_end_gain_v_per_unit=rlow/(rup+rlow),
            amplifier_bandwidth_hz=1e6,
            source_resistance_ohm=(rup*rlow)/(rup+rlow),
            source_capacitance_f=1e-9,
            adc_series_resistance_ohm=220.0,
            adc_shunt_capacitance_f=2e-9,
            sample_rate_hz=50e3,
            divider_upper_ohm=rup,
            divider_lower_ohm=rlow,
        ),),
        adc=ADCDefinition(vref_v=3.3, bits=12, soc_count=3, recursive_previous_weight=0.25),
        modulator=ModulatorDefinition("FM/TBPRD", 100e3, timer_clock_hz=120e6),
        timing=TimingDefinition(1e-6, 5e-6, True),
    )


def _ttpl_definition() -> ControlSystemDefinition:
    sensors = (
        SensorDefinition("PFC inductor current", 0.03, amplifier_bandwidth_hz=2e6,
                         adc_series_resistance_ohm=220.0, adc_shunt_capacitance_f=2e-9,
                         digital_filter_alpha=0.8, sample_rate_hz=50e3),
        SensorDefinition("AC input voltage", 1/150, amplifier_bandwidth_hz=1e6,
                         source_resistance_ohm=2000.0, source_capacitance_f=1e-9,
                         adc_series_resistance_ohm=220.0, adc_shunt_capacitance_f=2e-9,
                         sample_rate_hz=50e3),
        SensorDefinition("PFC bus voltage", 1600/(117000+1600), amplifier_bandwidth_hz=1e6,
                         source_resistance_ohm=117000*1600/(117000+1600), source_capacitance_f=1e-9,
                         adc_series_resistance_ohm=220.0, adc_shunt_capacitance_f=2e-9,
                         digital_filter_alpha=0.5, sample_rate_hz=10e3),
    )
    return ControlSystemDefinition(
        topology=SystemTopology.TTPL_PFC,
        plant_source=PlantSource.ANALYTICAL,
        architecture=ControlArchitecture.TTPL_DUAL_LOOP,
        ttpl_stage=TTPLStageDefinition(
            vin_min_rms_v=176.0, vin_nom_rms_v=230.0, vin_max_rms_v=264.0,
            line_frequency_hz=50.0, bus_voltage_v=400.0, output_power_w=3300.0,
            efficiency=0.965, switching_frequency_hz=65e3,
            boost_inductance_h=350e-6, inductor_dcr_ohm=45e-3,
            bus_capacitance_f=470e-6, bus_cap_esr_ohm=30e-3,
        ),
        sensors=sensors,
        adc=ADCDefinition(
            vref_v=3.0, bits=14, clock_hz=80e6, acquisition_time_s=250e-9,
            conversion_cycles=14.0, soc_count=2, soc_spacing_s=0.5e-6,
            recursive_previous_weight=0.2,
        ),
        modulator=ModulatorDefinition(
            "TTPL PWM", 65e3, timer_clock_hz=120e6,
            duty_min=0.02, duty_max=0.97, minimum_pulse_s=0.4e-6, deadtime_s=120e-9,
        ),
        timing=TimingDefinition(1.2e-6, 7.5e-6, True),
    )


def test_llc_definition_validates_and_maps_to_existing_spec():
    definition = _llc_definition()
    checks = definition.validation_checks()
    assert len(checks) >= 8
    spec = llc_spec_from_system_definition(definition, LLCDesignSpec())
    assert spec.vbus_nom_v == 400.0
    assert spec.vout_v == 53.0
    assert spec.resonant_frequency_hz == 100e3
    assert spec.primary_deadtime_s == pytest.approx(100e-9)


def test_ttpl_definition_maps_adc_sensing_and_split_timing_without_losing_semantics():
    definition = _ttpl_definition()
    config = ttpl_config_from_system_definition(definition, PFCControlLabConfig())
    assert config.power_stage.switching_frequency_hz == 65e3
    assert config.power_stage.boost_inductance_h == pytest.approx(350e-6)
    assert config.power_stage.bus_capacitance_f == pytest.approx(470e-6)
    assert config.power_stage.efficiency == pytest.approx(0.965)
    assert config.current_sense.adc_bits == 14
    assert config.current_sense.adc_vref_v == pytest.approx(3.0)
    assert config.current_sense.timing.adc_clock_hz == 80e6
    assert config.current_sense.timing.soc_count == 2
    assert config.current_sense.timing.digital_filter.alpha == pytest.approx(0.8)
    assert config.vbus_sense.timing.digital_filter.alpha == pytest.approx(0.5)
    assert config.firmware.current_computation_delay_s == pytest.approx(1.2e-6)
    assert config.firmware.current_pwm_update_delay_s == pytest.approx(7.5e-6)
    assert config.current_controller.sample_time_s == pytest.approx(1/50e3)
    assert config.voltage_controller.sample_time_s == pytest.approx(1/10e3)


def test_unsupported_guided_topology_fails_closed():
    definition = replace(
        _llc_definition(),
        topology=SystemTopology.VIENNA_PFC,
        architecture=ControlArchitecture.CUSTOM,
        llc_stage=None,
    )
    with pytest.raises(NotImplementedError, match="not implemented"):
        definition.validate()


def test_manifest_uses_stable_string_enum_values():
    manifest = _ttpl_definition().as_manifest()
    assert manifest["topology"] == "ttpl_pfc"
    assert manifest["plant_source"] == "analytical"
    assert manifest["architecture"] == "ttpl_dual_loop"
    assert manifest["adc"]["bits"] == 14
