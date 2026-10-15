"""PFC Engineering V3 — unified closed-loop line-cycle."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from pfc_design.control import PFCControlLabConfig
from pfc_design.engineering.pfc_v3 import (
    ConvergenceStatus,
    assess_line_cycle_convergence,
    build_line_cycle_result,
)


def _fast_cfg(**kwargs):
    base = replace(
        PFCControlLabConfig(),
        waveform_line_cycles=4,
        waveform_integration_rate_hz=250e3,
    )
    return replace(base, **kwargs) if kwargs else base


def test_line_cycle_result_exposes_instant_point_and_arrays():
    line = build_line_cycle_result(_fast_cfg())
    assert len(line.time_s) > 100
    assert line.fsw_hz > 0.0
    assert np.all(np.isfinite(line.iac_a))
    pt = line.instant_point(90.0, pout_w=line.waveforms.metrics.real_input_power_w)
    assert 80.0 <= pt.theta_deg <= 100.0
    assert pt.il_peak_a >= pt.il_avg_a >= pt.il_valley_a
    assert pt.mode == "CCM_AVERAGED"


def test_line_cycle_convergence_status_is_explicit():
    line = build_line_cycle_result(_fast_cfg())
    assert line.convergence.status in ConvergenceStatus
    assert line.convergence.vbus_average_v > 0.0
    payload = line.as_dict()
    assert "convergence" in payload
    # Converged results may be VERIFIED later for PF; NOT_CONVERGED must never be silent.
    if line.convergence.status == ConvergenceStatus.NOT_CONVERGED:
        assert line.convergence.notes


def test_vin_min_and_full_load_instant_still_builds():
    cfg = _fast_cfg()
    stage = replace(cfg.power_stage, vin_rms_v=176.0, output_power_w=3300.0)
    line = build_line_cycle_result(replace(cfg, power_stage=stage))
    assert line.il_peak_a.max() > line.il_avg_a.mean()
    zc = line.instant_point(0.0, pout_w=3300.0)
    mid = line.instant_point(90.0, pout_w=3300.0)
    assert abs(mid.vac_v) > abs(zc.vac_v)


def test_convergence_needs_two_complete_cycles():
    line_hz = 50.0
    time_s = np.arange(300, dtype=float) / 10000.0  # 1.5 periods
    waveforms = SimpleNamespace(
        time_s=time_s,
        signals={
            "input_power": np.full_like(time_s, 1000.0),
            "vbus": np.full_like(time_s, 400.0),
            "i_input_signed": np.sin(2.0 * np.pi * line_hz * time_s),
        },
    )
    result = assess_line_cycle_convergence(
        waveforms, line_hz=line_hz, vbus_command_v=400.0
    )
    assert result.status == ConvergenceStatus.UNKNOWN
    assert result.pin_cycle_relative_error is None
