"""Single-frame ADC consumers reject spillover before analysis or persistence."""
from dataclasses import replace
import math

import pytest

from llc_design.control.design_session import decode_model, encode_model
from llc_design.control.digital_loop import ADCSamplingConfig, CommandTimingConfig
from pfc_design.control.config import ADCTimingConfig

_UNIT = 2.0 ** -20


def _llc(*, explicit):
    frame = 4 * _UNIT
    return ADCSamplingConfig(
        control_sample_time_s=frame, adc_clock_hz=1 / _UNIT,
        acquisition_time_s=_UNIT, conversion_cycles=1., soc_count=2,
        # Unsorted offsets also require the maximum, not the final tuple entry.
        soc_sample_offsets_s=(2.5 * _UNIT, .5 * _UNIT) if explicit else None,
    )


@pytest.mark.parametrize("explicit", [False, True])
def test_llc_eoc_exactly_on_frame_boundary_is_valid_and_serializable(explicit):
    adc = _llc(explicit=explicit)
    adc.validate()
    assert adc.eoc_delay_s == adc.control_sample_time_s
    assert decode_model(encode_model(adc)) == adc
    # Command/PWM delays are different owners and may extend beyond a frame.
    CommandTimingConfig(computation_delay_s=.01, pwm_update_delay_s=.02).validate()


@pytest.mark.parametrize("explicit", [False, True])
def test_llc_one_ulp_past_frame_is_rejected_by_core_and_session(explicit):
    adc = _llc(explicit=explicit)
    invalid = replace(adc, control_sample_time_s=math.nextafter(adc.control_sample_time_s, 0.))
    with pytest.raises(ValueError, match="single-frame model"):
        invalid.validate()
    with pytest.raises(ValueError, match="single-frame model"):
        encode_model(invalid)
    payload = encode_model(adc)
    payload["fields"]["control_sample_time_s"] = invalid.control_sample_time_s
    with pytest.raises(ValueError, match="single-frame model"):
        decode_model(payload)


def test_llc_centres_inside_frame_are_insufficient_if_conversion_finishes_late():
    adc = ADCSamplingConfig(soc_count=1, soc_sample_offsets_s=(19.9e-6,))
    assert adc.soc_sample_offsets_s[0] < adc.control_sample_time_s
    with pytest.raises(ValueError, match="single-frame model"):
        adc.frequency_response([1000.])
    with pytest.raises(ValueError, match="single-frame model"):
        encode_model(adc)


@pytest.mark.parametrize("count,spacing", [(1, 1000.), (3, 7 * _UNIT)])
def test_ttpl_exact_boundary_and_command_delays_are_valid(count, spacing):
    acquisition = 15 * _UNIT if count == 1 else _UNIT
    adc = ADCTimingConfig(sample_rate_hz=1 / (16 * _UNIT), adc_clock_hz=1 / _UNIT,
                          acquisition_time_s=acquisition, conversion_cycles=1.,
                          soc_count=count, soc_spacing_s=spacing,
                          computation_delay_s=.01, pwm_update_delay_s=.02)
    adc.validate()
    assert decode_model(encode_model(adc)) == adc
    invalid = replace(adc, acquisition_time_s=acquisition + _UNIT)
    with pytest.raises(ValueError, match="single-frame model"):
        invalid.validate()
    with pytest.raises(ValueError, match="single-frame model"):
        encode_model(invalid)
    payload = encode_model(adc)
    payload["fields"]["acquisition_time_s"] = invalid.acquisition_time_s
    with pytest.raises(ValueError, match="single-frame model"):
        decode_model(payload)


@pytest.mark.parametrize("explicit", [False, True])
def test_llc_actual_expert_run_and_capture_reject_over_frame(desktop_qapp, monkeypatch, explicit):
    from llc_design.core.spec import LLCDesignSpec
    from llc_design.gui import main_window
    from llc_design.gui.widgets import digital_loop_view
    from llc_design.gui.solution_map_install import install_llc_solution_map
    monkeypatch.setattr(main_window, "check_for_updates", lambda *_a, **_k: None)
    errors, requests = [], []
    monkeypatch.setattr(digital_loop_view, "show_operation_issue", lambda _p, _t, message, **_k: errors.append(message))
    host = main_window.LLCMainWindow(LLCDesignSpec())
    try:
        install_llc_solution_map(host)
        view = host.digital_loop_view
        view.analysis_requested.connect(requests.append)
        if explicit:
            view.soc_count.setValue(1)
            view.soc_offsets.setPlainText("0.0000199")
        else:
            view.soc_count.setValue(16)
            view.acq_ns.setValue(2000.)
        view.run_button.click()
        assert errors and "single-frame model" in errors[-1]
        assert requests == []
        assert view.result is None and not view.codegen_button.isEnabled()
        assert not view.solution_map_view.sources
        with pytest.raises(ValueError, match="single-frame model"):
            view._session_capture()
    finally:
        host.close()


def test_ttpl_actual_expert_run_and_capture_reject_preserved_over_frame_config(desktop_qapp, monkeypatch):
    from pfc_design.gui import main_window, control_lab_view
    from pfc_design.gui.solution_map_install import install_ttpl_solution_map
    monkeypatch.setattr(main_window, "check_for_updates", lambda *_a, **_k: None)
    errors, requests = [], []
    monkeypatch.setattr(control_lab_view, "show_operation_issue", lambda _p, _t, message, **_k: errors.append(message))
    host = main_window.PFCMainWindow()
    try:
        install_ttpl_solution_map(host)
        view = host.control_lab_view.control_lab
        config = view._config()
        bad_sense = replace(config.current_sense, timing=replace(config.current_sense.timing,
                            soc_count=4, soc_spacing_s=10e-6))
        view._solution_map_restore_config(replace(config, current_sense=bad_sense))
        view.analysis_requested.connect(requests.append)
        view.run_button.click()
        assert errors and "single-frame model" in errors[-1]
        assert requests == []
        assert view.result is None and not view.codegen_button.isEnabled()
        assert not view.solution_map_view.sources
        with pytest.raises(ValueError, match="single-frame model"):
            view._session_capture()
    finally:
        host.close()


@pytest.mark.parametrize("kind,changes", [
    ("llc", {"control_sample_time_s": float("nan")}),
    ("llc", {"control_sample_time_s": float("inf")}),
    ("llc", {"acquisition_time_s": float("nan")}),
    ("ttpl", {"sample_rate_hz": float("nan")}),
    ("ttpl", {"sample_rate_hz": float("inf")}),
    ("ttpl", {"soc_spacing_s": float("nan")}),
])
def test_adc_frame_bound_cannot_be_bypassed_with_nonfinite_timing(kind, changes):
    base = ADCSamplingConfig() if kind == "llc" else ADCTimingConfig(sample_rate_hz=50e3)
    expected = ("ADC and control sample rates must be positive"
                if kind == "ttpl" and "sample_rate_hz" in changes
                else "single-frame model")
    with pytest.raises(ValueError, match=expected):
        replace(base, **changes).validate()
