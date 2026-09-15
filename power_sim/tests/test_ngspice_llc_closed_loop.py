from __future__ import annotations

import numpy as np
import pytest

from llc_design.core.spec import LLCDesignSpec
from llc_design.core.tank import design_tank
from power_control_tools.models import DigitalTransferFunction
from power_sim.closed_loop import ClosedLoopScenario, StepProfile
from power_sim.digital_control import (
    ControllerLimitConfig,
    LLCFMConfig,
    LLCFMMode,
    PWMCountMode,
    SamplerConfig,
)
from power_sim.spice import (
    LLCSpiceConfig,
    NgSpiceClosedLoopConfig,
    find_ngspice_shared_library,
    run_llc_shared_closed_loop,
)


def test_shared_ngspice_llc_executes_exact_hz_control_clock_if_library_installed():
    library = find_ngspice_shared_library()
    if library is None:
        pytest.skip("shared libngspice is not installed")

    spec = LLCDesignSpec()
    tank = design_tank(spec)
    fs_ctrl = 40_000.0
    # Deliberate zero controller: this first integration test verifies the real
    # shared circuit, callback clock, gate scheduler and exact H(z) runtime path
    # without conflating the result with loop-tuning quality.
    controller = DigitalTransferFunction(
        (0.0,),
        (1.0,),
        fs_ctrl,
        "ngspice smoke controller",
        source="test_exact_hz",
    )
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
