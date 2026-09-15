from __future__ import annotations

import math

import numpy as np
import pytest

from llc_design.core.spec import LLCDesignSpec
from llc_design.core.tank import design_tank
from power_control_tools.models import DigitalTransferFunction
from power_sim.closed_loop import ClosedLoopScenario, StepProfile
from power_sim.digital_control import (
    ControllerLimitConfig,
    LLCFMConfig,
    LLCFMLUTConfig,
    LLCFMMode,
    PWMCountMode,
    SamplerConfig,
)
from power_sim.spice import (
    LLCSpiceConfig,
    NgSpiceClosedLoopConfig,
    find_ngspice_shared_library,
    ngspice_closed_loop_waveform_bundle,
    run_llc_shared_closed_loop,
)


def _default_runtime(spec: LLCDesignSpec, fs_ctrl: float):
    sampler = SamplerConfig(sample_rate_hz=fs_ctrl)
    modulator = LLCFMConfig(
        mode=LLCFMMode.LINEAR_FM,
        nominal_frequency_hz=spec.resonant_frequency_hz,
        kfm_hz_per_unit=-50_000.0,
        minimum_frequency_hz=spec.minimum_frequency_hz,
        maximum_frequency_hz=spec.maximum_frequency_hz,
        tbclk_hz=120e6,
        count_mode=PWMCountMode.UP_DOWN,
        quantize_tbprd=False,
    )
    spice = LLCSpiceConfig(
        switching_frequency_hz=spec.resonant_frequency_hz,
        bus_voltage_v=spec.vbus_nom_v,
        load_fraction=1.0,
        initial_output_v=spec.vout_v,
    )
    return sampler, modulator, spice


def _exact_pi(fs_ctrl: float, *, kp: float = 0.03, ti_s: float = 0.7e-3, source: str = "test_exact_pi_hz") -> DigitalTransferFunction:
    ts = 1.0 / fs_ctrl
    ki2 = ts / (2.0 * ti_s)
    return DigitalTransferFunction(
        (kp * (1.0 + ki2), kp * (-1.0 + ki2)),
        (1.0, -1.0),
        fs_ctrl,
        "exact PI reference-step smoke",
        source=source,
    )


def test_shared_ngspice_llc_executes_exact_hz_control_clock_if_library_installed():
    library = find_ngspice_shared_library()
    if library is None:
        pytest.skip("shared libngspice is not installed")

    spec = LLCDesignSpec()
    tank = design_tank(spec)
    fs_ctrl = 40_000.0
    controller = DigitalTransferFunction(
        (0.0,),
        (1.0,),
        fs_ctrl,
        "ngspice smoke controller",
        source="test_exact_hz",
    )
    sampler, modulator, spice = _default_runtime(spec, fs_ctrl)
    scenario = ClosedLoopScenario(
        reference_v=StepProfile(spec.vout_v),
        bus_voltage_v=StepProfile(spec.vbus_nom_v),
        load_fraction=StepProfile(1.0),
    )
    sim = NgSpiceClosedLoopConfig(
        duration_s=80e-6,
        output_step_s=0.1e-6,
        max_step_s=0.05e-6,
    )

    result = run_llc_shared_closed_loop(
        spec,
        tank,
        spice,
        controller=controller,
        controller_limits=ControllerLimitConfig(-1.0, 1.0),
        sampler=sampler,
        modulator=modulator,
        scenario=scenario,
        simulation=sim,
        library=library,
    )

    samples = result.control.samples
    assert len(samples) >= 4
    sample_times = np.asarray([item.time_s for item in samples])
    assert np.all(np.diff(sample_times) > 0.0)
    assert abs(samples[1].time_s - 1.0 / fs_ctrl) < 1e-9
    assert all(abs(item.controller_output) < 1e-15 for item in samples)
    assert all(abs(item.frequency_actual_hz - spec.resonant_frequency_hz) < 1e-6 for item in samples)

    assert "time" in result.vectors
    assert "v(out)" in result.vectors
    assert result.vectors["time"].size > 20
    assert result.vectors["v(out)"].size == result.vectors["time"].size
    assert np.all(np.isfinite(result.vectors["v(out)"]))
    assert result.control.metadata["engine"] == "shared-ngspice"
    assert result.control.metadata["controller_source"] == "test_exact_hz"


def test_shared_ngspice_llc_reference_step_drives_exact_pi_and_fm_in_correct_direction_if_library_installed():
    """Nonzero exact-H(z) controller smoke on the real switching circuit."""
    library = find_ngspice_shared_library()
    if library is None:
        pytest.skip("shared libngspice is not installed")

    spec = LLCDesignSpec()
    tank = design_tank(spec)
    fs_ctrl = 40_000.0
    ts = 1.0 / fs_ctrl
    controller = _exact_pi(fs_ctrl)
    sampler, modulator, spice = _default_runtime(spec, fs_ctrl)
    step_time = 0.75e-3
    scenario = ClosedLoopScenario(
        reference_v=StepProfile(spec.vout_v, step_time_s=step_time, final=spec.vout_v + 1.0),
        bus_voltage_v=StepProfile(spec.vbus_nom_v),
        load_fraction=StepProfile(1.0),
    )
    sim = NgSpiceClosedLoopConfig(
        duration_s=1.5e-3,
        output_step_s=0.25e-6,
        max_step_s=0.08e-6,
        wall_timeout_s=120.0,
    )

    result = run_llc_shared_closed_loop(
        spec,
        tank,
        spice,
        controller=controller,
        controller_limits=ControllerLimitConfig(-0.35, 0.35),
        sampler=sampler,
        modulator=modulator,
        scenario=scenario,
        simulation=sim,
        library=library,
    )
    samples = result.control.samples
    assert len(samples) >= 55

    before = [item for item in samples if step_time - 5.0 * ts <= item.time_s < step_time]
    after = [item for item in samples if step_time <= item.time_s <= step_time + 3.0 * ts]
    assert before and after
    pre = before[-1]
    post = after[0]

    assert post.error_v - pre.error_v > 0.7
    assert post.controller_output > pre.controller_output + 0.01
    assert post.frequency_actual_hz < pre.frequency_actual_hz - 300.0

    assert samples[-1].time_s >= 1.45e-3
    vout = result.vectors["v(out)"]
    assert vout.size > 1000
    assert np.all(np.isfinite(vout))
    assert result.control.metadata["controller_source"] == "test_exact_pi_hz"
    assert result.control.metadata["shared_senddata_points"] > 1000

    bundle = ngspice_closed_loop_waveform_bundle(
        result,
        bus_voltage_v=spec.vbus_nom_v,
        samples_per_switching_cycle=80,
    )
    assert bundle.model_name == "shared-ngspice digital closed loop"
    assert np.all(np.diff(bundle.time_s) > 0.0)
    assert "v_bridge" in bundle.signals
    assert "i_resonant" in bundle.signals
    assert "v_output" in bundle.signals
    assert "control_reference" in bundle.signals
    assert "control_feedback" in bundle.signals
    assert "control_output" in bundle.signals
    assert "switching_frequency" in bundle.signals
    lengths = {len(signal.values) for signal in bundle.signals.values()}
    assert lengths == {len(bundle.time_s)}


def test_shared_ngspice_exact_pi_drives_nonlinear_pcmd_tbprd_lut_if_library_installed():
    """End-to-end smoke for Exact H(z) -> biased PCMD LUT -> TBPRD -> gates.

    This is the nonlinear modulator path used by the LLC GUI.  It verifies that
    a controller perturbation is added to the steady PCMD bias, interpolated in
    the table, timer-quantized, and then applied to the continuously running
    switching circuit without replacing H(z) by an analog/local-slope model.
    """
    library = find_ngspice_shared_library()
    if library is None:
        pytest.skip("shared libngspice is not installed")

    spec = LLCDesignSpec()
    tank = design_tank(spec)
    fs_ctrl = 40_000.0
    ts = 1.0 / fs_ctrl
    controller = _exact_pi(fs_ctrl, kp=0.02, ti_s=1.0e-3, source="test_exact_pi_lut")
    sampler = SamplerConfig(sample_rate_hz=fs_ctrl)

    # 120 MHz up/down timer: TBPRD=600 -> 100 kHz. Increasing PCMD increases
    # TBPRD and therefore reduces Fsw, matching the default LLC control polarity.
    lut = LLCFMLUTConfig(
        pcmd=(0.0, 0.25, 0.50, 0.75, 1.0),
        values=(333.0, 450.0, 600.0, 750.0, 1000.0),
        command_bias_pu=0.50,
        values_are_tbprd=True,
        tbclk_hz=120e6,
        count_mode=PWMCountMode.UP_DOWN,
        quantize_tbprd=True,
    )
    spice = LLCSpiceConfig(
        switching_frequency_hz=lut.nominal_frequency_hz,
        bus_voltage_v=spec.vbus_nom_v,
        load_fraction=1.0,
        initial_output_v=spec.vout_v,
    )
    step_time = 0.45e-3
    scenario = ClosedLoopScenario(
        reference_v=StepProfile(spec.vout_v, step_time_s=step_time, final=spec.vout_v + 0.8),
        bus_voltage_v=StepProfile(spec.vbus_nom_v),
        load_fraction=StepProfile(1.0),
    )
    sim = NgSpiceClosedLoopConfig(
        duration_s=1.0e-3,
        output_step_s=0.25e-6,
        max_step_s=0.08e-6,
        wall_timeout_s=120.0,
    )

    result = run_llc_shared_closed_loop(
        spec,
        tank,
        spice,
        controller=controller,
        controller_limits=ControllerLimitConfig(-0.20, 0.20),
        sampler=sampler,
        modulator=lut,
        scenario=scenario,
        simulation=sim,
        library=library,
    )
    samples = result.control.samples
    assert len(samples) >= 38
    pre = [s for s in samples if step_time - 4.0 * ts <= s.time_s < step_time][-1]
    post = [s for s in samples if step_time <= s.time_s <= step_time + 3.0 * ts][0]

    assert post.error_v - pre.error_v > 0.55
    assert post.controller_output > pre.controller_output + 0.005
    assert post.tbprd is not None
    assert post.tbprd > pre.tbprd
    assert post.frequency_actual_hz < pre.frequency_actual_hz
    assert math.isclose(
        post.frequency_actual_hz,
        120e6 / (2.0 * float(post.tbprd)),
        rel_tol=0.0,
        abs_tol=1e-9,
    )
    assert samples[-1].time_s >= 0.95e-3
    assert np.all(np.isfinite(result.vectors["v(out)"]))
    assert result.control.metadata["controller_source"] == "test_exact_pi_lut"
    assert result.control.metadata["shared_senddata_points"] > 800
