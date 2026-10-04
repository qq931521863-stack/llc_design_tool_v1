"""PFC Engineering V3 — PF / THD engine regressions."""

from __future__ import annotations

import math

import numpy as np
import pytest

from pfc_design.engineering.pfc_v3 import compute_pf_thd


def _grid(line_hz: float = 50.0, cycles: int = 1, fs: float = 50e3):
    n = int(round(cycles * fs / line_hz))
    t = np.arange(n, dtype=float) / fs
    return t, line_hz


def test_case_a_ideal_sine_in_phase():
    t, fl = _grid()
    vac = 230.0 * math.sqrt(2) * np.sin(2 * math.pi * fl * t)
    iac = 10.0 * math.sqrt(2) * np.sin(2 * math.pi * fl * t)
    r = compute_pf_thd(t, vac, iac, line_hz=fl, max_harmonic=11)
    assert r.sanity_ok
    assert r.pf == pytest.approx(1.0, abs=2e-3)
    assert r.dpf == pytest.approx(1.0, abs=2e-3)
    assert r.distortion_factor == pytest.approx(1.0, abs=2e-3)
    assert r.thd == pytest.approx(0.0, abs=2e-3)
    assert abs(r.pf - r.dpf * r.distortion_factor) < 5e-3


def test_case_b_phase_shifted_sine():
    t, fl = _grid()
    phi = math.radians(30.0)
    vac = 230.0 * math.sqrt(2) * np.sin(2 * math.pi * fl * t)
    iac = 10.0 * math.sqrt(2) * np.sin(2 * math.pi * fl * t - phi)
    r = compute_pf_thd(t, vac, iac, line_hz=fl, max_harmonic=11)
    assert r.dpf == pytest.approx(math.cos(phi), abs=5e-3)
    assert r.distortion_factor == pytest.approx(1.0, abs=5e-3)
    assert r.pf == pytest.approx(math.cos(phi), abs=1e-2)
    assert abs(r.pf - r.dpf * r.distortion_factor) < 2e-2


def test_case_c_injected_h3_h5_harmonics():
    t, fl = _grid()
    w = 2 * math.pi * fl
    vac = 230.0 * math.sqrt(2) * np.sin(w * t)
    # Peak amplitudes: H1=10√2, H3=1.0√2, H5=0.5√2 → RMS 10, 1, 0.5
    iac = (
        10.0 * math.sqrt(2) * np.sin(w * t)
        + 1.0 * math.sqrt(2) * np.sin(3 * w * t)
        + 0.5 * math.sqrt(2) * np.sin(5 * w * t)
    )
    r = compute_pf_thd(t, vac, iac, line_hz=fl, max_harmonic=11)
    thd_analytic = math.sqrt(1.0**2 + 0.5**2) / 10.0
    assert r.harmonics_rms_a[3] == pytest.approx(1.0, rel=0.05)
    assert r.harmonics_rms_a[5] == pytest.approx(0.5, rel=0.08)
    assert r.thd == pytest.approx(thd_analytic, rel=0.08)
    assert r.dpf == pytest.approx(1.0, abs=2e-2)
    assert abs(r.pf - r.dpf * r.distortion_factor) < 3e-2


def test_case_d_dc_offset_does_not_claim_perfect_thd_identity_blindly():
    t, fl = _grid()
    vac = 230.0 * math.sqrt(2) * np.sin(2 * math.pi * fl * t)
    iac = 10.0 * math.sqrt(2) * np.sin(2 * math.pi * fl * t) + 0.5
    r = compute_pf_thd(t, vac, iac, line_hz=fl, max_harmonic=11)
    assert r.sanity_ok
    assert r.pf <= 1.0 + 1e-6
    # DC is outside odd-harmonic THD convention; residual may grow — engine must stay sane.
    assert r.thd >= 0.0


def test_pf_cannot_exceed_one_silently():
    t, fl = _grid()
    vac = np.sin(2 * math.pi * fl * t)
    iac = 2.0 * np.sin(2 * math.pi * fl * t)
    r = compute_pf_thd(t, vac, iac, line_hz=fl)
    assert r.pf <= 1.0 + 1e-6
    assert r.convention.fundamental_definition == "DFT_H1_RMS"


@pytest.mark.parametrize("waveform", ["time", "voltage", "current"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_rejects_nonfinite_waveforms(waveform, value):
    t, fl = _grid()
    v = np.sin(2 * math.pi * fl * t)
    i = 2.0 * v
    {"time": t, "voltage": v, "current": i}[waveform][100] = value
    with pytest.raises(ValueError, match="finite"):
        compute_pf_thd(t, v, i, line_hz=fl)


@pytest.mark.parametrize("line_hz", [math.nan, math.inf, -math.inf, 0.0, -50.0])
def test_rejects_invalid_line_frequency(line_hz):
    t, fl = _grid()
    v = np.sin(2 * math.pi * fl * t)
    with pytest.raises(ValueError, match="line frequency"):
        compute_pf_thd(t, v, v, line_hz=line_hz)


@pytest.mark.parametrize("pout_w", [math.nan, math.inf, -math.inf])
def test_rejects_nonfinite_output_power(pout_w):
    t, fl = _grid()
    v = np.sin(2 * math.pi * fl * t)
    with pytest.raises(ValueError, match="output power.*finite"):
        compute_pf_thd(t, v, v, line_hz=fl, pout_w=pout_w)


@pytest.mark.parametrize("kind", ["duplicate", "decreasing", "reversed", "nonuniform"])
def test_rejects_invalid_time_grid(kind):
    t, fl = _grid()
    if kind == "duplicate":
        t[100] = t[99]
    elif kind == "decreasing":
        t[100] = t[99] - 1e-6
    elif kind == "reversed":
        t = t[::-1]
    else:
        t[100] += 1e-6
    v = np.sin(2 * math.pi * fl * t)
    with pytest.raises(ValueError, match="time.*(increasing|uniform)"):
        compute_pf_thd(t, v, v, line_hz=fl)


@pytest.mark.parametrize(
    "max_harmonic", [0, -1, 1.5, 3.0, math.nan, math.inf, True, np.bool_(True), "3", None]
)
def test_rejects_invalid_harmonic_limit(max_harmonic):
    t, fl = _grid()
    v = np.sin(2 * math.pi * fl * t)
    with pytest.raises(ValueError, match="max_harmonic.*positive integer"):
        compute_pf_thd(t, v, v, line_hz=fl, max_harmonic=max_harmonic)


def test_accepts_uniform_grid_roundoff_numpy_harmonic_and_finite_output_power():
    t, fl = _grid(cycles=2)
    t += 0.25
    t[::2] += 1e-13
    v = 230.0 * math.sqrt(2) * np.sin(2 * math.pi * fl * t)
    i = 10.0 * math.sqrt(2) * np.sin(2 * math.pi * fl * t)
    r = compute_pf_thd(t, v, i, line_hz=fl, max_harmonic=np.int64(11), pout_w=2200.0)
    assert r.sanity_ok
    assert r.pf == pytest.approx(1.0, abs=2e-3)
    assert r.thd == pytest.approx(0.0, abs=2e-3)
    assert r.pout_w == 2200.0
    assert tuple(r.harmonics_rms_a) == tuple(range(1, 12))


def test_finite_waveforms_that_overflow_arithmetic_are_rejected():
    time = np.arange(1000) / 50_000.0
    wave = 1e160 * np.sin(2 * np.pi * 50.0 * time)
    with pytest.raises(ValueError, match="finite numeric range"):
        compute_pf_thd(time, wave, wave, line_hz=50.0)
