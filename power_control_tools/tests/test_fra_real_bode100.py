from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from power_control_tools.fra import (
    FRASourceFormat,
    analyze_loop_response,
    auto_design_controller,
    fit_rational_frequency_response,
    load_fra_file,
)
from power_control_tools.fra.analysis import phase_margin_from_unwrapped_phase
from power_control_tools.models import ControllerKind


DATA = Path(__file__).with_name("data") / "bode100_real_264vac_190v_90pct.csv"


def _real_bode100():
    data = load_fra_file(DATA, FRASourceFormat.BODE100)
    return data, data.complex_response()


def test_real_bode100_import_and_stability_matches_hardware_cursor():
    data, loop = _real_bode100()
    result = analyze_loop_response(data.frequency_hz, loop)

    assert len(data.frequency_hz) == 101
    assert data.frequency_hz[0] == 10.0
    assert data.frequency_hz[-1] == 100_000.0
    assert data.default_phase_offset_deg == -180.0

    assert len(result.gain_crossovers) == 1
    assert abs(result.main_crossover_hz - 296.59103982069337) < 1e-6
    assert abs(result.phase_margin_deg - 86.32501701589291) < 1e-6

    assert len(result.phase_crossovers) >= 1
    main_phase_cross = result.phase_crossovers[0]
    assert abs(main_phase_cross.frequency_hz - 7616.260359844676) < 1e-6
    assert abs(main_phase_cross.gain_margin_db - 27.095093412882314) < 1e-6
    assert result.status == "PASS"
    assert np.isfinite(result.ms)
    assert np.isfinite(result.mt)


def test_phase_margin_is_branch_aware_below_minus_360_deg():
    assert abs(phase_margin_from_unwrapped_phase(-138.0) - 42.0) < 1e-12
    assert abs(phase_margin_from_unwrapped_phase(-313.0) - (-133.0)) < 1e-12
    # Nearest odd-180-degree branch is -540 deg, so this is +133 deg.
    assert abs(phase_margin_from_unwrapped_phase(-407.0) - 133.0) < 1e-12


def test_real_bode100_full_band_five_pole_fit_is_rejected_as_low_confidence():
    data, loop = _real_bode100()
    result = fit_rational_frequency_response(
        data.frequency_hz,
        loop,
        max_poles=5,
        focus_hz=296.59103982069337,
        fit_delay=True,
        max_nfev=80,
        fit_target="Real Bode100 complete loop",
    )

    # The real record contains a large local irregularity around 100 Hz and a
    # complicated high-frequency region.  A single <=5-pole model must not be
    # presented as an accurate full-band model when the residuals say otherwise.
    assert result.metrics.confidence == "LOW"
    assert result.metrics.magnitude_rms_db > 1.0
    assert result.metrics.phase_rms_deg > 5.0
    assert np.all(np.isfinite(result.fitted_response.real))
    assert np.all(np.isfinite(result.fitted_response.imag))


def test_real_bode100_control_band_can_be_approximated_but_not_called_physical_model():
    data, loop = _real_bode100()
    mask = (data.frequency_hz >= 150.0) & (data.frequency_hz <= 15_000.0)
    result = fit_rational_frequency_response(
        data.frequency_hz[mask],
        loop[mask],
        max_poles=5,
        focus_hz=296.59103982069337,
        fit_delay=True,
        max_nfev=80,
        fit_target="Real Bode100 control-band complete loop",
    )

    # This is deliberately a numerical approximation test.  The measured file
    # is a complete loop; without the actual C_old it is NOT an extracted plant
    # and the fitted poles/zeros must not be presented as physical components.
    assert result.metrics.confidence in {"GOOD", "FAIR"}
    assert result.metrics.magnitude_rms_db < 0.5
    assert result.metrics.phase_rms_deg < 3.0
    assert result.metrics.focus_magnitude_rms_db is not None
    assert result.metrics.focus_magnitude_rms_db < 0.5
    assert result.metrics.focus_phase_rms_deg is not None
    assert result.metrics.focus_phase_rms_deg < 3.0


def test_real_bode100_auto_design_stress_rejects_pm_only_bad_global_candidate():
    data, measured_loop = _real_bode100()

    # NUMERICAL STRESS TEST ONLY: the uploaded file is the complete measured
    # loop.  The user's actual C_old was not supplied, so measured_loop is used
    # here only as a difficult response shape to exercise the optimizer.  This
    # test must never be interpreted as a valid hardware controller design.
    result = auto_design_controller(
        data.frequency_hz,
        measured_loop,
        controller_kind=ControllerKind.PI,
        sample_rate_hz=40_000.0,
        target_crossover_hz=1_000.0,
        target_phase_margin_deg=60.0,
        min_gain_margin_db=6.0,
        max_ms=2.0,
        fallback_points=24,
    )

    assert result.candidates
    requested = result.candidates[0]
    assert math.isclose(requested.requested_crossover_hz, 1_000.0, rel_tol=0.0, abs_tol=1e-9)
    assert requested.achieved_phase_margin_deg is not None
    # Local Fc/PM can look good, but the real scan shape creates a negative GM
    # elsewhere.  A one-click designer must inspect the whole usable FRA band.
    assert requested.metrics.worst_gain_margin_db is not None
    assert requested.metrics.worst_gain_margin_db < 0.0
    assert not requested.accepted


def test_auto_design_requires_gain_margin_evidence_for_one_click_pass():
    f = np.geomspace(20.0, 2_000.0, 300)
    s = 1j * 2.0 * np.pi * f
    # One-pole response never reaches an odd-180-degree phase crossing inside
    # this finite measurement window, so GM is not observed.
    plant = 2.0 / (1.0 + s / (2.0 * np.pi * 200.0))
    result = auto_design_controller(
        f,
        plant,
        controller_kind=ControllerKind.PI,
        sample_rate_hz=40_000.0,
        target_crossover_hz=500.0,
        target_phase_margin_deg=60.0,
        min_gain_margin_db=6.0,
        max_ms=2.0,
    )

    assert result.selected is not None
    assert result.selected.metrics.worst_gain_margin_db is None
    assert result.status != "PASS"
    assert not result.selected.accepted
