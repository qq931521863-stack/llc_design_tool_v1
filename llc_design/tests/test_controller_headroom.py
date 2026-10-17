"""Static headroom uses the selected controller's absolute PCMD contract."""

from __future__ import annotations

from dataclasses import replace
import json

import numpy as np
import pytest

from llc_design.control.analysis import build_small_signal_analysis
from llc_design.control.digital_loop import (
    FMLUTMode,
    FrequencyModulatorLUT,
    PIControllerConfig,
    PIFControllerConfig,
    TwoP2ZControllerConfig,
    build_digital_loop_analysis,
)
from llc_design.control.linearize import ControlInputKind
from llc_design.control.smart_control import MetricStatus, build_loop_model, compute_headroom
from llc_design.core.spec import LLCDesignSpec
from llc_design.models.system import LLCSystemAnalyzer


CONTROLLERS = (PIControllerConfig, PIFControllerConfig, TwoP2ZControllerConfig)


@pytest.fixture(scope="module")
def small_signal():
    spec = LLCDesignSpec()
    system = LLCSystemAnalyzer().analyze(spec)
    return build_small_signal_analysis(spec, system_analysis=system, sample_time_s=20e-6)


def _analysis(small_signal, config_type=PIControllerConfig, **kwargs):
    config = kwargs.pop("controller_config", config_type(output_min=0.2, output_max=0.6))
    return build_digital_loop_analysis(
        small_signal,
        controller_config=config,
        command_pu=kwargs.pop("command_pu", 0.75),
        **kwargs,
    )


@pytest.mark.parametrize("config_type", CONTROLLERS)
def test_loop_model_uses_selected_controller_output_limits(small_signal, config_type):
    result = build_loop_model(_analysis(small_signal, config_type)).headroom
    assert result.command_min == 0.2
    assert result.command_max == 0.6
    assert result.command_pu == 0.75
    assert result.headroom_to_max == pytest.approx(-0.15)
    assert result.headroom_to_min == pytest.approx(0.55)
    assert result.saturated is True
    assert result.status is MetricStatus.WARN


@pytest.mark.parametrize(
    ("command", "saturated"),
    [(0.1, True), (0.2, True), (0.20001, False), (0.4, False), (0.59999, False), (0.6, True), (0.75, True)],
)
def test_signed_headroom_and_boundary_saturation(small_signal, command, saturated):
    result = compute_headroom(_analysis(small_signal, command_pu=command))
    assert result.headroom_to_max == pytest.approx(0.6 - command)
    assert result.headroom_to_min == pytest.approx(command - 0.2)
    assert result.saturated is saturated
    assert result.status is (MetricStatus.WARN if saturated else MetricStatus.APPROXIMATION)


@pytest.mark.parametrize("mode", list(FMLUTMode))
def test_effective_lut_input_range_includes_normalized_command_clipping(small_signal, mode):
    # LUT output units (timer counts or Hz) are not controller output units.
    values = [200, 300, 500, 800, 900] if mode is FMLUTMode.PCMD_TO_TBPRD else [300e3, 250e3, 120e3, 70e3, 60e3]
    lut = FrequencyModulatorLUT(np.array([-0.5, 0.0, 0.5, 1.0, 1.5]), np.array(values), mode=mode)
    analysis = _analysis(
        small_signal,
        controller_config=PIControllerConfig(output_min=-0.3, output_max=1.3),
        fm_lut=lut,
    )
    result = compute_headroom(analysis)
    assert (result.command_min, result.command_max) == (0.0, 1.0)
    assert result.headroom_to_max == pytest.approx(0.25)
    assert result.headroom_to_min == pytest.approx(0.75)
    assert result.saturated is False


@pytest.mark.parametrize(
    ("minimum", "maximum", "expected"),
    [(-0.3, 0.6, (0.0, 0.6)), (0.2, 1.3, (0.2, 1.0)), (0.2, 0.6, (0.2, 0.6))],
)
def test_intersection_keeps_tighter_controller_limit(small_signal, minimum, maximum, expected):
    analysis = _analysis(small_signal, controller_config=PIControllerConfig(output_min=minimum, output_max=maximum))
    result = compute_headroom(analysis)
    assert (result.command_min, result.command_max) == expected


def test_explicit_absolute_pcmd_limits_override_only_requested_bound(small_signal):
    analysis = _analysis(small_signal)
    result = compute_headroom(analysis, command_max=0.9)
    assert (result.command_min, result.command_max) == (0.2, 0.9)
    assert result.headroom_to_max == pytest.approx(0.15)
    assert result.saturated is False
    assert compute_headroom(analysis, command_min=0.1).command_min == 0.1


@pytest.mark.parametrize("inactive_config", [None, PIControllerConfig(output_min=0.2, output_max=0.6)])
def test_external_hz_never_borrows_inactive_local_limits(small_signal, inactive_config):
    external = PIFControllerConfig(kp=0.003).transfer_function()
    analysis = _analysis(small_signal, controller_config=inactive_config, controller_transfer_function=external)
    assert analysis.controller is external
    assert analysis.controller_config is None
    result = build_loop_model(analysis).headroom
    assert result.status is MetricStatus.UNKNOWN
    assert result.saturated is None
    assert result.command_min is None and result.command_max is None
    assert result.headroom_to_max is None and result.headroom_to_min is None
    assert "external" in result.notes
    assert "absolute" in result.notes
    # Unknown evidence serializes as null, not a false healthy result or NaN.
    assert json.loads(json.dumps(result.as_dict(), allow_nan=False))["saturated"] is None


def test_external_hz_accepts_explicit_absolute_pcmd_contract(small_signal):
    external = PIControllerConfig().transfer_function()
    analysis = _analysis(small_signal, controller_transfer_function=external)
    result = compute_headroom(analysis, command_min=0.2, command_max=0.6)
    assert result.saturated is True
    assert result.headroom_to_max == pytest.approx(-0.15)
    assert compute_headroom(analysis, command_max=0.6).status is MetricStatus.UNKNOWN


def test_control_tools_hz_without_absolute_limit_metadata_is_unknown(small_signal):
    from power_control_tools import ControllerKind, DiscretizationMethod, design_controller, discretize_transfer_function

    analog = design_controller(ControllerKind.PI, kp=0.003, ti_s=0.002)
    external = discretize_transfer_function(analog, 50_000.0, DiscretizationMethod.TUSTIN)
    analysis = _analysis(small_signal, controller_transfer_function=external)
    assert compute_headroom(analysis).status is MetricStatus.UNKNOWN


@pytest.mark.parametrize("output_name", ["frequency_hz", "timer_counts", "output_voltage_v", "delta_pcmd"])
def test_non_absolute_pcmd_controller_coordinate_is_unknown(small_signal, output_name):
    external = replace(PIControllerConfig().transfer_function(), output_name=output_name)
    analysis = _analysis(small_signal, controller_transfer_function=external)
    result = compute_headroom(analysis, command_min=0.2, command_max=0.6)
    assert result.status is MetricStatus.UNKNOWN
    assert result.saturated is None
    assert result.headroom_to_max is None
    assert "coordinate" in result.notes


@pytest.mark.parametrize("kind", [ControlInputKind.FREQUENCY_KHZ, ControlInputKind.PERIOD_S, ControlInputKind.TIMER_COUNTS])
def test_incompatible_plant_control_coordinate_is_unknown(small_signal, kind):
    analysis = _analysis(replace(small_signal, control_input_kind=kind))
    result = compute_headroom(analysis)
    assert result.status is MetricStatus.UNKNOWN
    assert result.command_min is None
    assert result.headroom_to_max is None


@pytest.mark.parametrize("unit", ["kHz", "s", "count", "pu"])
def test_plant_unit_must_match_fm_hz_per_pu_gain(small_signal, unit):
    plant = replace(small_signal.continuous_transfer, input_unit=unit)
    analysis = _analysis(replace(small_signal, continuous_transfer=plant))
    assert compute_headroom(analysis).status is MetricStatus.UNKNOWN


INVALID_LIMITS = [
    (0.6, 0.2), (0.5, 0.5),
    (float("nan"), 0.8), (0.2, float("nan")),
    (float("inf"), 0.8), (float("-inf"), 0.8),
    (0.2, float("inf")), (0.2, float("-inf")),
]


@pytest.mark.parametrize("config_type", CONTROLLERS)
@pytest.mark.parametrize(("minimum", "maximum"), INVALID_LIMITS)
def test_invalid_selected_controller_limits_fail_closed(small_signal, config_type, minimum, maximum):
    analysis = _analysis(small_signal, controller_config=config_type(output_min=minimum, output_max=maximum))
    with pytest.raises(ValueError, match="headroom command"):
        build_loop_model(analysis)


@pytest.mark.parametrize(("minimum", "maximum"), INVALID_LIMITS)
def test_invalid_explicit_limits_fail_closed(small_signal, minimum, maximum):
    analysis = _analysis(small_signal)
    with pytest.raises(ValueError, match="headroom command"):
        compute_headroom(analysis, command_min=minimum, command_max=maximum)


@pytest.mark.parametrize("command", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_operating_command_fails_closed(small_signal, command):
    analysis = _analysis(small_signal)
    analysis = replace(analysis, fm_operating_point=replace(analysis.fm_operating_point, command_pu=command))
    with pytest.raises(ValueError, match="operating command must be finite"):
        compute_headroom(analysis)


@pytest.mark.parametrize(("minimum", "maximum"), [(1.1, 1.2), (-0.2, -0.1)])
def test_disjoint_controller_and_fm_ranges_fail_closed(small_signal, minimum, maximum):
    analysis = _analysis(small_signal, controller_config=PIControllerConfig(output_min=minimum, output_max=maximum))
    with pytest.raises(ValueError, match="do not overlap"):
        compute_headroom(analysis)


@pytest.mark.parametrize(("minimum", "maximum", "command"), [(-0.2, 0.0, 0.0), (1.0, 1.2, 1.0)])
def test_single_reachable_command_has_zero_authority(small_signal, minimum, maximum, command):
    analysis = _analysis(
        small_signal,
        controller_config=PIControllerConfig(output_min=minimum, output_max=maximum),
        command_pu=command,
    )
    result = compute_headroom(analysis)
    assert result.command_min == result.command_max == command
    assert result.headroom_to_min == result.headroom_to_max == 0.0
    assert result.saturated is True
    assert result.status is MetricStatus.WARN


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 0.0])
def test_invalid_mutable_lut_axis_fails_closed(small_signal, value):
    lut = FrequencyModulatorLUT.firmware_default()
    analysis = _analysis(small_signal, fm_lut=lut)
    lut.pcmd[1] = value
    with pytest.raises(ValueError, match="FM LUT PCMD axis"):
        compute_headroom(analysis)


def test_headroom_does_not_change_exact_hz_or_linear_loop(small_signal):
    wide = _analysis(small_signal, controller_config=PIControllerConfig())
    narrow = _analysis(small_signal)
    assert np.array_equal(wide.controller.numerator, narrow.controller.numerator)
    assert np.array_equal(wide.controller.denominator, narrow.controller.denominator)
    assert np.array_equal(wide.nominal_open_loop, narrow.nominal_open_loop)
    assert compute_headroom(wide).saturated is False
    assert compute_headroom(narrow).saturated is True


def test_external_selection_is_not_inferred_from_matching_coefficients(small_signal):
    inactive = PIControllerConfig(output_min=float("nan"), output_max=0.6)
    external = inactive.transfer_function()
    analysis = _analysis(small_signal, controller_config=inactive, controller_transfer_function=external)
    assert analysis.controller_config is None
    assert compute_headroom(analysis).status is MetricStatus.UNKNOWN


def test_reachable_lut_axis_is_used_even_if_changed_after_analysis(small_signal):
    lut = FrequencyModulatorLUT.firmware_default()
    analysis = _analysis(small_signal, controller_config=PIControllerConfig(), fm_lut=lut)
    # Arrays in the frozen LUT remain mutable; do not assume original endpoints.
    lut.pcmd[:] = np.linspace(0.1, 0.7, len(lut.pcmd))
    result = compute_headroom(analysis)
    assert (result.command_min, result.command_max) == (0.1, 0.7)
    assert result.headroom_to_max == pytest.approx(-0.05)
    assert result.saturated is True
