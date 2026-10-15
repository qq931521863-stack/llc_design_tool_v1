import numpy as np
import pytest
from dataclasses import replace

from power_control_tools.analysis import analyze_digital_filter
from power_control_tools.codegen import export_c99_filter, verify_c99_filter
from power_control_tools.models import DigitalTransferFunction


def test_slow_step_uses_theoretical_dc_and_reports_short_window():
    model = DigitalTransferFunction((0.0001, 0), (1, -0.9999), 1000)
    short = analyze_digital_filter(model)
    assert short.step_final_value == pytest.approx(1)
    assert short.step[-1] < 0.03
    assert short.settling_time_s is None
    assert short.step_status == "Response window insufficient"
    long = analyze_digital_filter(model, response_samples=40000)
    expected = (np.ceil(np.log(0.02) / np.log(0.9999)) - 1) / 1000
    assert long.settling_time_s == pytest.approx(expected)


def test_integrator_does_not_claim_finite_step_steady_state():
    result = analyze_digital_filter(DigitalTransferFunction((0.001, 0), (1, -1), 1000))
    assert result.step_final_value is None
    assert result.settling_time_s is None
    assert result.step_overshoot_percent is None


@pytest.mark.parametrize("filename", ["power_ctrl.h", "renamed.h"])
def test_saved_filename_does_not_change_generated_symbols(tmp_path, filename):
    model = DigitalTransferFunction((0.5, 0.5), (1,), 1000)
    exported = export_c99_filter(model, tmp_path / filename, prefix="POWER_CTRL")
    result = verify_c99_filter(model, exported)
    if not result.available:
        pytest.skip("Host C compiler unavailable")
    assert result.passed, result.message
    legacy = verify_c99_filter(model, replace(exported, prefix=""))
    assert legacy.passed, legacy.message
