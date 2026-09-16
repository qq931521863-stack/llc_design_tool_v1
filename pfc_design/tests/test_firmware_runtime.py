from __future__ import annotations

import math

import numpy as np
import pytest

from pfc_design.control import PFCControlLabConfig, build_pfc_control_handoff, build_pfc_control_lab_analysis
from pfc_design.control.firmware_runtime import (
    PFCExactFirmwareControllerRuntime,
    PFCSampledSenseRuntime,
    PFCZeroCrossRuntime,
)
from power_sim.digital_control import ControllerLimitConfig, DigitalTransferRuntime


def _handoff():
    analysis = build_pfc_control_lab_analysis(PFCControlLabConfig())
    return analysis, build_pfc_control_handoff(analysis)


def test_float32_pi_runtime_uses_frozen_hz_and_matches_unsaturated_linear_response():
    _, handoff = _handoff()
    artifact = handoff.current
    firmware = PFCExactFirmwareControllerRuntime(artifact)
    linear = DigitalTransferRuntime(
        artifact.runtime_transfer(),
        ControllerLimitConfig(-1.0e9, 1.0e9, False),
    )

    # Small excitation remains away from limits. The firmware-state execution
    # must reproduce the frozen exact H(z), allowing only float32 round-off.
    sequence = [0.001, -0.0003, 0.0007, 0.0, -0.0002, 0.0001]
    for error in sequence:
        fw = firmware.step(error)
        reference = linear.step(error)
        assert not fw.saturated
        assert fw.output == pytest.approx(reference.output, rel=3e-5, abs=2e-7)


def test_float32_pi_runtime_freezes_integrator_on_outward_saturation_and_recovers():
    _, handoff = _handoff()
    runtime = PFCExactFirmwareControllerRuntime(handoff.current)

    first = runtime.step(10_000.0)
    state_after_first = float(runtime.i_state)
    second = runtime.step(10_000.0)
    state_after_second = float(runtime.i_state)

    assert first.saturated
    assert second.saturated
    assert first.anti_windup_frozen
    assert second.anti_windup_frozen
    assert state_after_second == pytest.approx(state_after_first, rel=0.0, abs=0.0)

    recovered = runtime.step(-10_000.0)
    assert math.isfinite(recovered.output)
    assert handoff.current.output_min <= recovered.output <= handoff.current.output_max


def test_sampled_sense_runtime_quantizes_from_configured_adc_resolution_and_reports_latency():
    cfg = PFCControlLabConfig().current_sense
    sense = PFCSampledSenseRuntime(cfg, initial=1.0)
    sense.advance(1.0, 0.0)

    lsb = cfg.adc_vref_v / float(1 << cfg.adc_bits) / cfg.raw_dc_gain
    assert sense.lsb_engineering_units == pytest.approx(lsb)
    assert abs(sense.output - 1.0) <= 0.51 * lsb
    assert isinstance(sense.last_adc_code, int)

    expected_latency = (
        0.5 * cfg.timing.acquisition_time_s
        + cfg.timing.conversion_time_s
        + (cfg.timing.soc_count - 1) * cfg.timing.soc_spacing_s
    )
    assert sense.acquisition_to_ready_s == pytest.approx(expected_latency)


def test_sampled_sense_runtime_applies_digital_filter_in_float32_state():
    base = PFCControlLabConfig().current_sense
    from dataclasses import replace
    from pfc_design.control.config import DigitalFilterConfig

    cfg = replace(base, timing=replace(base.timing, digital_filter=DigitalFilterConfig(alpha=0.25)))
    sense = PFCSampledSenseRuntime(cfg, initial=0.0)
    sample_period = 1.0 / cfg.timing.sample_rate_hz

    # At t=0 no analog time has elapsed, so the first ADC event correctly
    # samples the initial analog state rather than an instantaneous input step.
    sense.advance(4.0, 0.0)
    first = sense.output
    assert first == pytest.approx(0.0, rel=0.0, abs=0.0)

    sense.advance(4.0, sample_period)
    second = sense.output
    sense.advance(4.0, 2.0 * sample_period)
    third = sense.output

    assert 0.0 < second < third < 4.0
    assert sense.snapshot.sample_time_s == pytest.approx(2.0 * sample_period)
    assert np.float32(third) == np.float32(sense.output)


def test_zero_cross_runtime_follows_eight_state_transition_and_pi_reset_contract():
    zc = PFCZeroCrossRuntime()
    zc.reset(positive_half=True)

    step = zc.step(14.0)
    assert step.state_code == 2
    assert step.reset_current_pi
    assert step.zero_cross_active
    assert step.lf_state == 0

    assert zc.step(-0.1).state_code == 3
    assert zc.step(-16.0).state_code == 4

    last = None
    for _ in range(10):
        last = zc.step(-20.0)
    assert last is not None
    assert last.state_code == 5
    assert not last.zero_cross_active
    assert last.lf_state == -1

    step = zc.step(-14.0)
    assert step.state_code == 6
    assert step.reset_current_pi
    assert zc.step(0.1).state_code == 7
    assert zc.step(16.0).state_code == 8
    for _ in range(10):
        last = zc.step(20.0)
    assert last is not None
    assert last.state_code == 1
    assert last.lf_state == 1
