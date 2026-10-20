"""CLI exports preserve independent sampled domains without inventing samples."""
import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from pfc_design import vienna_cli
from pfc_design.vienna import (
    ViennaControlLabConfig,
    build_vienna_control_lab_analysis,
)


@pytest.mark.parametrize("domain_case", ["default", "slow_current", "three_bands"])
def test_cli_csv_preserves_all_loop_grids_and_missing_samples(
    tmp_path, monkeypatch, domain_case
):
    config = replace(
        ViennaControlLabConfig(),
        waveform_line_cycles=3,
        waveform_integration_rate_hz=200e3,
        switching_samples_per_cycle=100,
    )
    if domain_case == "slow_current":
        sense = config.phase_current_sense
        config = replace(
            config,
            phase_current_sense=replace(
                sense, timing=replace(sense.timing, sample_rate_hz=2000)
            ),
        )
    elif domain_case == "three_bands":
        config = replace(
            config,
            firmware=replace(
                config.firmware,
                voltage_loop_rate_hz=2000,
                balance_loop_rate_hz=6000,
            ),
            voltage_controller=replace(
                config.voltage_controller, sample_time_s=1/2000
            ),
            balance_controller=replace(
                config.balance_controller, sample_time_s=1/6000
            ),
        )
    # Exercise the real CLI, analysis, waveform solvers and file writers.
    monkeypatch.setattr(vienna_cli, "ViennaControlLabConfig", lambda: config)
    result = CliRunner().invoke(vienna_cli.cli, ["--output", str(tmp_path)])
    assert result.exit_code == 0, result.output + repr(result.exception)
    analysis = build_vienna_control_lab_analysis(config)
    loops = {
        key: getattr(analysis, key + "_loop")
        for key in ("current", "voltage", "balance")
    }
    counts = {key: len(loop.frequencies_hz) for key, loop in loops.items()}
    if domain_case == "default":
        assert counts["current"] > counts["voltage"] == counts["balance"]
    elif domain_case == "slow_current":
        assert counts["balance"] > counts["current"] == counts["voltage"]
    else:
        assert counts["current"] > counts["balance"] > counts["voltage"]

    data = pd.read_csv(
        tmp_path / "bode_open_loops.csv", float_precision="round_trip"
    )
    union = np.unique(np.concatenate([loop.frequencies_hz for loop in loops.values()]))
    np.testing.assert_array_equal(data["frequency_hz"], union)
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert "not interpolated or extrapolated" in summary["bode_csv_missing_samples"]
    for key, loop in loops.items():
        valid = np.isin(union, loop.frequencies_hz)
        response = loop.responses["open_" + key]
        gain = data[f"{key}_open_gain_db"]
        phase = data[f"{key}_open_phase_deg"]
        np.testing.assert_array_equal(gain.notna(), valid)
        np.testing.assert_array_equal(phase.notna(), valid)
        np.testing.assert_array_equal(data.loc[valid, "frequency_hz"], loop.frequencies_hz)
        np.testing.assert_allclose(
            gain[valid], 20*np.log10(np.maximum(np.abs(response), 1e-300)), rtol=1e-14
        )
        np.testing.assert_allclose(
            phase[valid], np.unwrap(np.angle(response))*180/np.pi, rtol=1e-14
        )
        domain = summary["bode_analysis"][key]
        assert domain["sample_count"] == len(loop.frequencies_hz)
        assert domain["actual_band_hz"] == [
            loop.frequencies_hz[0], loop.frequencies_hz[-1]
        ]
        assert domain["nyquist_hz_exclusive"] == loop.nyquist_hz
        assert np.all(data.loc[valid, "frequency_hz"] < loop.nyquist_hz)
        assert domain["closed_loop_stability"] == "NOT_PROVEN"
    assert (tmp_path / "line_cycle.csv").is_file()
    assert (tmp_path / "switching.csv").is_file()
