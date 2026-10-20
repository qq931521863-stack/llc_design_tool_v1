"""Guided timing must reach the exact analysis, visible edits and saved sessions."""
from dataclasses import replace

import numpy as np
import pytest

from llc_design.control.digital_loop import ADCSamplingConfig
from llc_design.control.design_session import decode_model, encode_model


@pytest.fixture
def wizard_host(desktop_qapp, monkeypatch):
    from llc_design.gui import main_window
    from llc_design.gui.system_modeling import SystemModelingDesignDialog
    from llc_design.core.spec import LLCDesignSpec
    monkeypatch.setattr(main_window, "check_for_updates", lambda *_args, **_kwargs: None)
    wizard = SystemModelingDesignDialog()
    window = main_window.LLCMainWindow(LLCDesignSpec())
    from llc_design.gui.solution_map_install import install_llc_solution_map
    install_llc_solution_map(window)
    yield wizard, window
    wizard.close()
    window.close()


@pytest.mark.parametrize("count", [1, 4])
@pytest.mark.parametrize("spacing_us", [0.0, 5.0, 10.0])
def test_guided_spacing_has_literal_centres_and_eoc(wizard_host, count, spacing_us):
    from llc_design.gui.system_modeling import apply_definition_to_llc_window
    wizard, host = wizard_host
    # Keep all spacing vectors inside the model frame, including N=4 / 10 us.
    wizard.llc_sample.setValue(25.0)  # 40 us frame
    wizard.adc_soc.setValue(count)
    wizard.adc_soc_spacing.setValue(spacing_us)
    definition = wizard.build_definition()
    apply_definition_to_llc_window(host, definition)
    adc = host.digital_loop_view._analysis_options()["loop"]["adc_sampling"]
    expected = tuple(.5 * definition.adc.acquisition_time_s + index * definition.adc.soc_spacing_s
                     for index in range(count))
    assert adc.soc_sample_offsets_s == expected
    assert adc.eoc_delay_s == pytest.approx(
        (count - 1) * spacing_us * 1e-6 + adc.acquisition_time_s + adc.conversion_time_s)
    assert adc.eoc_delay_s - definition.adc.acquisition_to_ready_s == pytest.approx(.5 * adc.acquisition_time_s)
    # Independent rectangular-aperture / averaging / conversion timing formula.
    f = np.array([1000., 5000.])
    phase_samples = np.exp(2j * np.pi * f[:, None] * np.array(expected))
    w = adc.recursive_previous_weight
    expected_response = (np.sinc(f * adc.acquisition_time_s) * (1-w) * phase_samples.mean(axis=1)
                         / (1-w*np.exp(-2j*np.pi*f*adc.control_sample_time_s)))
    np.testing.assert_allclose(adc.frequency_response(f), expected_response, rtol=2e-14)


def test_llc_offsets_restore_exactly_edit_invalidate_and_blank_restores_sequential(wizard_host):
    from llc_design.gui.system_modeling import apply_definition_to_llc_window
    from llc_design.gui.design_session_install import validate_map_state
    wizard, host = wizard_host
    definition = wizard.build_definition()
    definition = replace(definition, adc=replace(definition.adc, soc_count=4, soc_spacing_s=1.234567891234e-6))
    apply_definition_to_llc_window(host, definition)
    loop = host.digital_loop_view
    exact = loop._analysis_options()["loop"]["adc_sampling"]
    document = loop._session_capture()
    saved = decode_model(document["inputs"])["digital_loop"]["adc_sampling"]
    # Session capture owns the exact controller clock, avoiding display rounding.
    assert saved == replace(exact, control_sample_time_s=1 / definition.sensors[0].sample_rate_hz)
    exact = saved
    loop.soc_offsets.clear()
    assert loop._analysis_options()["loop"]["adc_sampling"].soc_sample_offsets_s is None
    prepared = validate_map_state(loop.solution_map_view, document["solution_map"], "llc")
    loop._session_restore(document, prepared)
    assert loop._analysis_options()["loop"]["adc_sampling"] == exact
    assert tuple(map(float, loop.soc_offsets.toPlainText().split(","))) == exact.soc_sample_offsets_s
    revision = loop.input_revision
    loop.soc_offsets.setPlainText("1e-7, 2e-7, 3e-7, 4e-7")
    assert loop.input_revision > revision
    assert loop.result is None and not loop.codegen_button.isEnabled()
    edited = loop._analysis_options()["loop"]["adc_sampling"]
    assert edited.soc_sample_offsets_s == (1e-7, 2e-7, 3e-7, 4e-7)
    assert edited != exact
    loop.soc_offsets.clear()
    assert loop._analysis_options()["loop"]["adc_sampling"].soc_sample_offsets_s is None


@pytest.mark.parametrize("offsets", [(0.,), (-1., 0.), (float("nan"), 0.), (float("inf"), 0.)])
def test_invalid_explicit_adc_offsets_are_rejected(offsets):
    with pytest.raises(ValueError, match="SOC sample offsets"):
        ADCSamplingConfig(soc_count=2, soc_sample_offsets_s=offsets).validate()


def test_ttpl_command_zoh_is_independent_of_adc_hold_and_replays(wizard_host):
    from llc_design.gui.system_modeling import ttpl_config_from_system_definition
    from power_control_tools.system_definition import SystemTopology
    from pfc_design.control.analysis import build_pfc_control_lab_analysis
    wizard, _ = wizard_host
    wizard.select_topology(SystemTopology.TTPL_PFC)
    definition = wizard.build_definition()
    with_hold = ttpl_config_from_system_definition(definition)
    without_hold = ttpl_config_from_system_definition(replace(
        definition, timing=replace(definition.timing, include_zero_order_hold=False)))
    assert without_hold.firmware.include_command_zero_order_hold is False
    for name in ("current_sense", "vac_sense", "vbus_sense"):
        assert getattr(without_hold, name) == getattr(with_hold, name)
        assert getattr(without_hold, name).timing.include_zero_order_hold
    assert decode_model(encode_model(without_hold)) == without_hold
    on, off = map(build_pfc_control_lab_analysis, (with_hold, without_hold))
    f = on.frequencies_hz
    pwm_hold = np.sinc(f / with_hold.firmware.current_loop_rate_hz) * np.exp(-1j*np.pi*f/with_hold.firmware.current_loop_rate_hz)
    fv = on.voltage_loop.frequencies_hz
    np.testing.assert_array_equal(fv, f[:len(fv)])
    amc_hold = np.sinc(fv / with_hold.firmware.amc_rate_hz) * np.exp(-1j*np.pi*fv/with_hold.firmware.amc_rate_hz)
    np.testing.assert_allclose(on.current_loop.responses["open_current"] / off.current_loop.responses["open_current"], pwm_hold, rtol=2e-14)
    np.testing.assert_allclose(on.voltage_loop.responses["amc_vff"] / off.voltage_loop.responses["amc_vff"], amc_hold, rtol=2e-14)
    inner_ratio = on.current_loop.responses["closed_current_actual"] / off.current_loop.responses["closed_current_actual"]
    np.testing.assert_allclose(on.voltage_loop.responses["open_voltage"] / off.voltage_loop.responses["open_voltage"], amc_hold * inner_ratio[:len(fv)], rtol=2e-14)
    assert np.max(np.abs(on.voltage_loop.responses["open_voltage"] - off.voltage_loop.responses["open_voltage"])) > 0


def test_old_ttpl_sessions_restore_the_previous_always_on_command_hold():
    from pfc_design.control.config import PFCFirmwareAlgorithmConfig
    payload = encode_model(PFCFirmwareAlgorithmConfig())
    del payload["fields"]["include_command_zero_order_hold"]
    assert decode_model(payload).include_command_zero_order_hold is True


def test_ttpl_command_zoh_visible_state_restore_dirty_and_summary(desktop_qapp, monkeypatch):
    from llc_design.gui.system_modeling import SystemModelingDesignDialog, apply_definition_to_ttpl_window
    from llc_design.gui.design_session_install import validate_map_state
    from pfc_design.gui import main_window
    from pfc_design.gui.solution_map_install import install_ttpl_solution_map
    from power_control_tools.system_definition import SystemTopology
    monkeypatch.setattr(main_window, "check_for_updates", lambda *_args, **_kwargs: None)
    wizard = SystemModelingDesignDialog()
    host = main_window.PFCMainWindow()
    try:
        wizard.select_topology(SystemTopology.TTPL_PFC)
        wizard.include_zoh.setChecked(False)
        control = host.control_lab_view.control_lab
        install_ttpl_solution_map(host)
        config = apply_definition_to_ttpl_window(host, wizard.build_definition())
        control._solution_map_restore_config(config)
        assert not control.include_command_zoh.isChecked()
        assert not control._config().firmware.include_command_zero_order_hold
        document = control._session_capture()
        control.include_command_zoh.setChecked(True)
        assert control._config().firmware.include_command_zero_order_hold
        assert not control.codegen_button.isEnabled()
        prepared = validate_map_state(control.solution_map_view, document["solution_map"], "ttpl")
        control._session_restore(document, prepared)
        assert not control.include_command_zoh.isChecked()
        assert control._config() == config
        assert not control.codegen_button.isEnabled()
    finally:
        wizard.close()
        host.close()
