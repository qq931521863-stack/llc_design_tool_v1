"""Operating range, not merely positive gain, determines ADC headroom."""
from dataclasses import replace
import math
import pytest
from power_control_tools.adc_range import assess_adc_magnitude, boost_input_current_peak_bound
from power_control_tools.system_definition import (
    ADCDefinition, ControlArchitecture, ControlSystemDefinition, PlantSource,
    SensorDefinition, SystemTopology, TTPLStageDefinition,
)


def _definition():
    return ControlSystemDefinition(
        topology=SystemTopology.TTPL_PFC, plant_source=PlantSource.ANALYTICAL,
        architecture=ControlArchitecture.TTPL_DUAL_LOOP, ttpl_stage=TTPLStageDefinition(),
        sensors=(SensorDefinition("PFC inductor current", .03),
                 SensorDefinition("AC input voltage", 1/150),
                 SensorDefinition("PFC bus voltage", 1600/250600, sample_rate_hz=10000)),
    )


def _adc_rows(definition):
    return [row for row in definition.engineering_checklist() if "full-scale" in row.label]


def test_legacy_bus_gain_cannot_pass_guided_review_at_400v():
    definition = _definition()
    legacy = replace(definition.sensors[2], front_end_gain_v_per_unit=1600/118600)
    definition = replace(definition, sensors=(*definition.sensors[:2], legacy))
    rows = _adc_rows(definition)
    assert [row.ok for row in rows] == [True, True, False]
    assert "5.39629" in rows[2].detail and "244.6125" in rows[2].detail
    assert rows[2].step == 2  # Link back to the actual sensing gain, not a read-only summary.


def test_magnitude_check_respects_amplifier_gain_adc_reference_and_exact_full_scale():
    assert assess_adc_magnitude("bus", 400, .005, 3.3, "DC").within_range
    assert not assess_adc_magnitude("bus", 400, .005*2, 3.3, "DC").within_range
    assert not assess_adc_magnitude("bus", 400, .005, 2.0, "DC").within_range
    for invalid in [float("nan"), float("inf")]:
        with pytest.raises(ValueError, match="finite"):
            assess_adc_magnitude("bus", invalid, .005, 3.3, "DC")


def test_high_line_and_low_line_current_are_checked_against_range():
    good = _definition()
    assert all(row.ok for row in _adc_rows(good))
    high_line = replace(good, ttpl_stage=replace(good.ttpl_stage, vin_max_rms_v=400))
    assert not _adc_rows(high_line)[1].ok
    high_current = replace(good, ttpl_stage=replace(good.ttpl_stage, output_power_w=20000))
    assert not _adc_rows(high_current)[0].ok


def test_boost_bound_exceeds_independent_phase_grid_current():
    stage = TTPLStageDefinition()
    bound = boost_input_current_peak_bound(
        output_power_w=stage.output_power_w, efficiency=stage.efficiency,
        minimum_vin_rms_v=stage.vin_min_rms_v, bus_voltage_v=stage.bus_voltage_v,
        inductance_h=stage.boost_inductance_h, switching_frequency_hz=stage.switching_frequency_hz)
    for phase in range(181):
        sine = math.sin(math.radians(phase))
        v = math.sqrt(2)*stage.vin_min_rms_v*sine
        duty = 1-v/stage.bus_voltage_v
        current = math.sqrt(2)*stage.output_power_w/(stage.efficiency*stage.vin_min_rms_v)*sine
        half_ripple = v*duty/(2*stage.boost_inductance_h*stage.switching_frequency_hz)
        assert current+half_ripple <= bound


def test_missing_or_duplicate_sensor_paths_fail_before_adapter_key_lookup():
    good = _definition()
    with pytest.raises(ValueError, match="distinct"):
        replace(good, sensors=(good.sensors[0],)*3).validate()
    with pytest.raises(ValueError, match="distinct"):
        replace(good, sensors=good.sensors[:2]).validate()


def test_expert_legacy_configuration_has_exported_clipping_warning_without_mutation():
    from pfc_design.control import PFCControlLabConfig, build_pfc_control_lab_analysis
    config = PFCControlLabConfig()
    original = config.vbus_sense.front_end_gain_v_per_unit
    result = build_pfc_control_lab_analysis(config)
    assert any("ADC RANGE EXCEEDED" in warning and "bus voltage" in warning
               for warning in result.warnings)
    assert any("omit ADC clipping" in warning for warning in result.summary_dict()["warnings"])
    assert result.config.vbus_sense.front_end_gain_v_per_unit == original
    valid = replace(config, vbus_sense=replace(config.vbus_sense, front_end_gain_v_per_unit=1/150))
    assert not any("ADC RANGE EXCEEDED" in warning
                   for warning in build_pfc_control_lab_analysis(valid).warnings)


@pytest.mark.parametrize("count,spacing,acquisition,ok", [
    (4, 5e-6, 300e-9, True), (4, 10e-6, 300e-9, False),
    (1, 0., 30e-6, False), (4, 0., 300e-9, True),
])
def test_adc_sequence_domain_checked_before_analyze(count, spacing, acquisition, ok):
    definition = replace(_definition(), adc=ADCDefinition(
        soc_count=count, soc_spacing_s=spacing, acquisition_time_s=acquisition))
    row = next(row for row in definition.engineering_checklist()
               if "sequence completes" in row.label)
    assert row.ok is ok
    assert row.step == 3
    assert "trigger-to-EOC" in row.detail
