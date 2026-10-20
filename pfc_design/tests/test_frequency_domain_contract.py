"""TTPL principal-band evidence must not contain aliased outer-loop samples."""
import json
from dataclasses import replace

import numpy as np
import pytest

from llc_design.control.design_session import (
    make_session,
    read_session,
    replay_ttpl,
    ttpl_inputs,
    write_session,
)
from llc_design.control.digital_loop import calculate_stability_margins
from pfc_design.control import PFCControlLabConfig, build_pfc_control_lab_analysis
from pfc_design.control.autotune import tune_pfc_current_loop
from pfc_design.control.export import export_pfc_control_lab
from pfc_design.control.sensing import sense_frequency_response
from pfc_design.engineering.pfc_v3 import build_pfc_smart_control_v3
from power_control_tools.research_export import build_pfc_research_export, render_python


def _rates(current=50000.0, voltage=10000.0):
    base = PFCControlLabConfig()
    return replace(
        base,
        firmware=replace(base.firmware, current_loop_rate_hz=current,
                         amc_rate_hz=min(base.firmware.amc_rate_hz, current),
                         voltage_loop_rate_hz=voltage),
        current_controller=replace(base.current_controller, sample_time_s=1/current),
        voltage_controller=replace(base.voltage_controller, sample_time_s=1/voltage),
        current_sense=replace(base.current_sense, timing=replace(base.current_sense.timing, sample_rate_hz=current)),
        vbus_sense=replace(base.vbus_sense, timing=replace(base.vbus_sense.timing, sample_rate_hz=voltage)),
    )


@pytest.mark.parametrize("current", [50000.0, 40000.0, 20000.0])
def test_actual_bands_are_exact_requested_samples_and_are_reported(current):
    config = _rates(current)
    result = build_pfc_control_lab_analysis(config)
    requested = np.geomspace(config.frequency_start_hz, config.frequency_stop_hz, config.frequency_points)
    assert result.config is config and config.frequency_stop_hz == 20000
    for key in ("current", "voltage"):
        loop = getattr(result, key + "_loop")
        np.testing.assert_array_equal(loop.frequencies_hz, requested[requested < loop.nyquist_hz])
        assert all(values.shape == loop.frequencies_hz.shape for values in loop.responses.values())
        assert all(np.all(np.isfinite(values)) for values in loop.responses.values())
        reported = result.summary_dict()["frequency_domain"][key]
        assert reported["stop_hz"] == loop.frequencies_hz[-1] < reported["nyquist_hz_exclusive"]
        assert reported["points"] == len(loop.frequencies_hz)
    assert result.voltage_loop.frequencies_hz[-1] < 5000
    assert any("Voltage-loop requested Bode range" in text for text in result.warnings)
    if current == 40000:
        assert len(result.current_loop.frequencies_hz) == 1599
        assert any("Current-loop requested Bode range" in text for text in result.warnings)


def test_outer_respects_amc_and_sense_rates_and_inner_band():
    base = _rates()
    cases = [
        (replace(base, firmware=replace(base.firmware, amc_rate_hz=4000)), 2000),
        (replace(base, vbus_sense=replace(base.vbus_sense,
                 timing=replace(base.vbus_sense.timing, sample_rate_hz=3000))), 1500),
        (replace(base, current_sense=replace(base.current_sense,
                 timing=replace(base.current_sense.timing, sample_rate_hz=2000))), 1000),
    ]
    for config, expected in cases:
        result = build_pfc_control_lab_analysis(config)
        assert result.voltage_loop.nyquist_hz == expected
        assert result.voltage_loop.frequencies_hz[-1] < expected
        np.testing.assert_array_equal(result.voltage_loop.responses["current_closed_for_outer"],
                                      result.current_loop.responses["closed_current_actual"][:len(result.voltage_loop.frequencies_hz)])


@pytest.mark.parametrize("start", [10000.0, 5000.0, 4990.0])
def test_empty_or_one_point_outer_band_rejected_before_analysis(start):
    with pytest.raises(ValueError, match="voltage loop needs at least two"):
        replace(_rates(), frequency_start_hz=start, frequency_points=100).validate()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, 0.0])
@pytest.mark.parametrize("field", ["frequency_start_hz", "frequency_stop_hz"])
def test_invalid_requested_frequency_endpoints_rejected(field, value):
    with pytest.raises(ValueError, match="Bode frequency range"):
        replace(_rates(), **{field: value}).validate()


@pytest.mark.parametrize("key", ["current", "voltage"])
def test_controller_clock_mismatch_is_not_guessed(key):
    base = _rates()
    with pytest.raises(ValueError, match="controller sample time must match"):
        replace(base, **{key + "_controller": replace(getattr(base, key + "_controller"), sample_time_s=1/17000)}).validate()


def _legacy_outer(config, analysis):
    """Independent formula over the old grid, deliberately exposing aliases."""
    f = analysis.current_loop.frequencies_hz
    s = 2j * np.pi * f
    stage, fw = config.power_stage, config.firmware
    controller = analysis.voltage_loop.controller
    z = np.exp(s * controller.sample_time_s)
    cv = sum(b * z**(-k) for k, b in enumerate(controller.numerator)) / sum(a * z**(-k) for k, a in enumerate(controller.denominator))
    amc = np.sinc(f/fw.amc_rate_hz) * np.exp(-s * (0.5/fw.amc_rate_hz + fw.amc_update_delay_s + fw.voltage_computation_delay_s))
    amc *= 1 / (fw.vac_rms_feedforward_gain * stage.vin_rms_v**2)
    bus = stage.vin_rms_v**2 * analysis.current_loop.responses["closed_current_actual"]
    bus = bus * (1+s*stage.bus_capacitance_f*stage.bus_cap_esr_ohm) / (stage.bus_capacitance_f*stage.bus_voltage_v*s)
    return f, cv * amc * bus * sense_frequency_response(config.vbus_sense, f).total


def test_alias_crossover_cannot_qualify_margin_or_phase_budget():
    base = _rates()
    config = replace(base, voltage_controller=replace(base.voltage_controller, kp=1000))
    analysis = build_pfc_control_lab_analysis(config)
    f, old_response = _legacy_outer(config, analysis)
    legacy = calculate_stability_margins(f, old_response)
    assert legacy.critical_gain_crossover_hz > 5000
    assert legacy.gain_margin_db > 0
    assert min(legacy.gain_margins_db) < 0  # Scalar positive-first policy hides a negative crossing.
    assert any(crossing >= 5000 for crossing in legacy.phase_crossovers_hz)
    band = f < 5000
    np.testing.assert_allclose(analysis.voltage_loop.responses["open_voltage"], old_response[band], rtol=2e-14)
    expected = calculate_stability_margins(f[band], old_response[band])
    actual = analysis.voltage_loop.margins
    assert actual.critical_gain_crossover_hz is None
    assert actual.gain_margin_db == pytest.approx(expected.gain_margin_db, abs=1e-11)
    assert actual.gain_margin_db < 0
    summary = analysis.summary_dict()["voltage_loop"]
    assert summary["phase_crossovers_hz"] == actual.phase_crossovers_hz
    assert summary["gain_margins_db"] == actual.gain_margins_db
    assert all(crossing < 5000 for crossing in actual.phase_crossovers_hz)
    np.testing.assert_allclose(actual.phase_crossovers_hz, expected.phase_crossovers_hz, rtol=1e-12)
    np.testing.assert_allclose(actual.gain_margins_db, expected.gain_margins_db, atol=1e-11)
    smart = build_pfc_smart_control_v3(config, analysis=analysis)
    assert smart.voltage.fc_hz is None and smart.voltage.phase_budget is None
    assert smart.current.phase_budget is not None


def test_save_reopen_preserves_request_and_recomputes_identical_valid_bands(tmp_path):
    config = replace(_rates(40000), frequency_start_hz=0.37, frequency_stop_hz=30000, frequency_points=1801)
    first = build_pfc_control_lab_analysis(config)
    path = write_session(make_session("ttpl", ttpl_inputs(first)), tmp_path / "session.json")
    second = replay_ttpl(read_session(path)["inputs"])
    assert second.config == config
    assert second.config.frequency_stop_hz == 30000
    assert first.frequency_domain_dict() == second.frequency_domain_dict()
    for key in ("current", "voltage"):
        left, right = getattr(first, key+"_loop"), getattr(second, key+"_loop")
        np.testing.assert_array_equal(left.frequencies_hz, right.frequencies_hz)
        for name in left.responses:
            np.testing.assert_array_equal(left.responses[name], right.responses[name])


@pytest.mark.parametrize("selection", ["current", "voltage", "both"])
def test_research_script_exports_only_valid_exact_samples(selection):
    analysis = build_pfc_control_lab_analysis(_rates(40000))
    bundle = build_pfc_research_export(analysis, selection)
    count = len(bundle.frequencies_hz)
    namespace = {"__name__": "frequency_contract_test"}
    exec(compile(render_python(bundle), "frequency_contract_export.py", "exec"), namespace)  # noqa: S102 - generated-script self-check
    assert namespace["coefficient_verification"]
    assert all(value == "PASS_AT_WELL_CONDITIONED_SAMPLES" for value in namespace["coefficient_verification"].values())
    models = {model.name: model for model in bundle.models}
    for key, short in (("current", "i"), ("voltage", "v")):
        if selection in (key, "both"):
            loop = getattr(analysis, key + "_loop")
            assert max(bundle.frequencies_hz) < loop.nyquist_hz
            np.testing.assert_array_equal(models["L"+short].response, loop.responses["open_"+key][:count])
    metadata = json.loads(bundle.metadata_json)
    assert metadata["frequency_domain"]["requested_stop_hz"] == 20000
    assert metadata["exported_frequency_domain"]["common_band"] == (selection == "both")
    for key, margins in metadata["loop_margins"].items():
        expected = getattr(analysis, key + "_loop").margins
        assert margins["phase_crossovers_hz"] == list(expected.phase_crossovers_hz)
        assert margins["gain_margins_db"] == list(expected.gain_margins_db)


def test_current_tuner_respects_low_user_maximum_and_requested_band():
    result = tune_pfc_current_loop(_rates(), maximum_crossover_hz=100)
    assert 0 < result.target_crossover_hz <= 100
    with pytest.raises(ValueError, match="no valid tuning interval"):
        tune_pfc_current_loop(replace(_rates(), frequency_start_hz=500), maximum_crossover_hz=100)


@pytest.mark.parametrize("maximum", [float("nan"), float("inf"), 0.0, -1.0])
def test_current_tuner_rejects_invalid_explicit_maximum(maximum):
    with pytest.raises(ValueError, match="maximum crossover"):
        tune_pfc_current_loop(_rates(), maximum_crossover_hz=maximum)


def test_csv_and_plot_export_use_each_loop_band(tmp_path):
    from pfc_design.control import (
        build_pfc_switching_waveforms,
        simulate_pfc_line_cycle,
    )
    config = replace(_rates(40000), waveform_line_cycles=3, waveform_integration_rate_hz=200000)
    analysis = build_pfc_control_lab_analysis(config)
    line = simulate_pfc_line_cycle(config)
    switching = build_pfc_switching_waveforms(config, line_cycle=line)
    paths = export_pfc_control_lab(analysis, line, switching, tmp_path)
    for key in ("current", "voltage"):
        exported = np.genfromtxt(paths[key + "_bode"], delimiter=",", names=True)
        loop = getattr(analysis, key + "_loop")
        np.testing.assert_allclose(exported["frequency_hz"], loop.frequencies_hz, rtol=1e-15)
        assert exported["frequency_hz"][-1] < loop.nyquist_hz
        assert paths[key + "_plot"].stat().st_size > 1000
    summary = json.loads(paths["summary"].read_text())
    assert summary["frequency_domain"] == analysis.frequency_domain_dict()
