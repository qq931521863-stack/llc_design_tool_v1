from __future__ import annotations

import math

import numpy as np

from power_sim.digital_control import LLCFMLUTConfig, LLCFMLUTRuntime, PWMCountMode


def test_fm_lut_runtime_preserves_bias_and_interpolates_tbprd():
    config = LLCFMLUTConfig(
        pcmd=(0.0, 0.5, 1.0),
        values=(240.0, 600.0, 857.0),
        command_bias_pu=0.5,
        values_are_tbprd=True,
        tbclk_hz=120e6,
        count_mode=PWMCountMode.UP_DOWN,
        quantize_tbprd=False,
    )
    runtime = LLCFMLUTRuntime(config)

    zero = runtime.map(0.0)
    assert math.isclose(zero.frequency_command_hz, 120e6 / (2.0 * 600.0), rel_tol=0.0, abs_tol=1e-9)
    assert zero.tbprd is None
    assert not zero.saturated

    up = runtime.map(0.1)
    expected_tbprd = 600.0 + 0.2 * (857.0 - 600.0)
    assert math.isclose(up.frequency_command_hz, 120e6 / (2.0 * expected_tbprd), rel_tol=1e-12)
    assert up.frequency_command_hz < zero.frequency_command_hz


def test_fm_lut_runtime_quantizes_like_pwm_timer_and_clamps_absolute_pcmd():
    config = LLCFMLUTConfig(
        pcmd=(0.0, 0.5, 1.0),
        values=(240.0, 600.2, 857.0),
        command_bias_pu=0.5,
        values_are_tbprd=True,
        tbclk_hz=120e6,
        count_mode=PWMCountMode.UP_DOWN,
        quantize_tbprd=True,
    )
    runtime = LLCFMLUTRuntime(config)

    result = runtime.map(0.0)
    assert result.tbprd == 600
    assert result.quantized
    assert math.isclose(result.actual_frequency_hz, 100_000.0, rel_tol=0.0, abs_tol=1e-9)

    high = runtime.map(0.8)  # bias + perturbation would be 1.3 -> clamp to 1.0
    assert high.saturated
    assert high.tbprd == 857
    assert math.isclose(high.actual_frequency_hz, 120e6 / (2.0 * 857.0), rel_tol=0.0, abs_tol=1e-9)


def test_fm_lut_frequency_limits_come_from_table_endpoints():
    config = LLCFMLUTConfig(
        pcmd=(0.0, 0.4, 1.0),
        values=(180_000.0, 100_000.0, 60_000.0),
        command_bias_pu=0.4,
        values_are_tbprd=False,
        quantize_tbprd=False,
    )
    config.validate()
    assert np.isclose(config.maximum_frequency_hz, 180_000.0)
    assert np.isclose(config.minimum_frequency_hz, 60_000.0)
    assert np.isclose(config.nominal_frequency_hz, 100_000.0)
