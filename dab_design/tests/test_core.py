"""Formula, physical invariants and export contracts, not MATLAB runtime parity."""

import csv
import math
from dataclasses import replace

import numpy as np
import pytest

from dab_design.core import (
    DABConfig,
    analyze_dab,
    export_csv,
    load_config,
    periodic_waveform,
    save_config,
    tps_command,
)


@pytest.fixture(scope="module")
def result():
    return analyze_dab(DABConfig())


def test_supplied_25_points_power_tracking_and_cp_energy(result):
    assert len(result.points) == 25
    assert {p.command.mode_id for p in result.points} == {1, 2, 3, 4}
    for p in result.points:
        assert p.ideal.metrics["pout_w"] == pytest.approx(p.pref_w, rel=0.004)
        m = p.with_cp.metrics
        assert m["pin_w"] - m["pout_w"] == pytest.approx(m["series_loss_w"], abs=1e-6)
        assert np.mean(p.with_cp.signals["il_a"]) == pytest.approx(0, abs=1e-10)
        assert np.mean(p.with_cp.signals["vcp_v"]) == pytest.approx(0, abs=1e-10)
        assert p.summary["zvs_status"] == "UNKNOWN"
        assert p.summary["evidence"] == "SOFTWARE_MODEL_ONLY"


def test_equal_voltage_sps_independent_analytic_solution():
    c = DABConfig()
    command = tps_command(c, 400, 7000)
    assert (command.d1, command.d2, command.d3) == pytest.approx((0, 0.3, 0.3))
    wave = periodic_waveform(c, 400, command, include_cp=False)
    peak = c.va_v * command.d2 / (2 * c.switching_hz * c.inductance_h)
    rms = peak * math.sqrt(1 - 2 * command.d2 / 3)
    assert wave.metrics["ipeak_a"] == pytest.approx(peak, rel=0.001)
    assert wave.metrics["irms_a"] == pytest.approx(rms, rel=0.001)


@pytest.mark.parametrize("ratio,vb", [(2, 400), (0.5, 400), (1.3, 500)])
def test_nonunity_turns_ratio_power_and_referral(ratio, vb):
    c = replace(DABConfig(), turns_ratio=ratio)
    base = c.va_v * vb / (8 * ratio * c.switching_hz * c.inductance_h)
    command = tps_command(c, vb, 0.6 * base)
    wave = periodic_waveform(c, vb, command, include_cp=False)
    assert wave.metrics["pout_w"] == pytest.approx(0.6 * base, rel=0.002)
    assert vb * wave.metrics["iout_average_a"] == pytest.approx(wave.metrics["pout_w"])


@pytest.mark.parametrize("m", [0.4, 0.8, 1.2, 3])
def test_mode_boundaries_are_continuous(m):
    c = DABConfig()
    vb = c.va_v / m
    seed = tps_command(c, vb, 0)
    p = seed.p_boundary
    left = tps_command(c, vb, (p - 1e-9) * seed.pbase_w)
    right = tps_command(c, vb, (p + 1e-9) * seed.pbase_w)
    assert left.mode_id != right.mode_id
    assert [left.d1, left.d2, left.d3] == pytest.approx(
        [right.d1, right.d2, right.d3], abs=1e-7
    )


def test_clamp_is_distinct_from_ideal_feasibility():
    c = DABConfig()
    base = tps_command(c, 400, 0).pbase_w
    limited = tps_command(c, 400, base * 0.997)
    assert limited.feasible and limited.limited and limited.p == 0.995
    impossible = tps_command(c, 400, base * 1.1)
    assert not impossible.feasible and impossible.limited
    with pytest.raises(ValueError, match="forward"):
        tps_command(c, 400, -100)


@pytest.mark.parametrize(
    "change",
    [
        {"va_v": float("nan")},
        {"p_limit": 1.1},
        {"wave_samples": 513},
        {"inductance_h": 0},
        {"series_resistance_ohm": -1},
        {"vb_cases_v": ()},
        {"load_ratios": (-0.1,)},
        {"rated_start_v": 350},
    ],
)
def test_invalid_config_rejected(change):
    with pytest.raises(ValueError):
        analyze_dab(replace(DABConfig(), **change))


def test_zero_power_remains_finite_and_relative_error_undefined():
    result = analyze_dab(replace(DABConfig(), load_ratios=(0,)))
    for p in result.points:
        assert p.summary["power_error_percent"] is None
        assert np.all(np.isfinite(p.with_cp.signals["il_a"]))
        assert p.with_cp.metrics["pout_w"] == pytest.approx(0, abs=0.1)


def test_undamped_exact_resonance_is_withheld():
    c = DABConfig()
    c = replace(
        c,
        series_resistance_ohm=0,
        primary_capacitance_f=1
        / ((2 * math.pi * c.switching_hz) ** 2 * c.inductance_h),
    )
    with pytest.raises(ValueError, match="resonance"):
        analyze_dab(c)


def test_project_roundtrip_and_csv_snapshot(result, tmp_path):
    project = tmp_path / "dab.json"
    save_config(result.config, project)
    assert load_config(project) == result.config
    path = tmp_path / "points.csv"
    export_csv(result, path)
    with path.open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 25
    assert float(rows[-1]["pref_w"]) == 7000
    assert rows[-1]["zvs_status"] == "UNKNOWN"
