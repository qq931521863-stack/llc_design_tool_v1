from __future__ import annotations

import numpy as np

from power_control_tools.fra import (
    FRASourceFormat,
    analyze_loop_response,
    deembed_controller,
    digital_frequency_response,
    firmware_feedback_a_to_denominator,
    load_fra_file,
    scale_digital_controller,
)
from power_control_tools.models import DigitalTransferFunction


def test_import_simplis_freq_gain_phase(tmp_path):
    path = tmp_path / "simplis.txt"
    path.write_text(
        "freq Gain Phase\n"
        "10 27.4 -1.5\n"
        "100 20.0 -30\n"
        "1000 5.0 -90\n",
        encoding="utf-8",
    )
    data = load_fra_file(path, FRASourceFormat.SIMPLIS)
    assert data.source_format == FRASourceFormat.SIMPLIS
    assert np.allclose(data.frequency_hz, [10.0, 100.0, 1000.0])
    assert np.allclose(data.magnitude_db, [27.4, 20.0, 5.0])
    assert np.allclose(data.normalized_phase_deg(), [-1.5, -30.0, -90.0])


def test_import_bode100_uses_loop_phase_convention(tmp_path):
    path = tmp_path / "bode100.csv"
    path.write_text(
        'Frequency (Hz);"Trace 1: Gain: Real ";"Trace 1: Gain: Imaginary ";'
        'Trace 1: Gain: Magnitude (dB);"Trace 2: Gain: Real ";'
        '"Trace 2: Gain: Imaginary ";Trace 2: Gain: Phase (°)\n'
        "100;1;2;12;1;2;100\n"
        "300;1;2;0;1;2;86\n"
        "1000;1;2;-10;1;2;40\n",
        encoding="utf-8",
    )
    data = load_fra_file(path, FRASourceFormat.BODE100)
    assert data.default_phase_offset_deg == -180.0
    assert np.allclose(data.magnitude_db, [12.0, 0.0, -10.0])
    assert np.allclose(data.normalized_phase_deg(), [-80.0, -94.0, -140.0])


def test_real_bode100_sample_reproduces_cursor_crossover(tmp_path):
    """Regression points copied from the provided Bode100 hardware export.

    Bode100 reports +86.325 deg at the 0-dB cursor.  With the importer loop
    convention correction (-180 deg), this is the canonical -93.675 deg loop
    phase and therefore an 86.325 deg phase margin.
    """
    path = tmp_path / "hardware_bode100.csv"
    path.write_text(
        'Frequency (Hz);"Trace 1: Gain: Real ";"Trace 1: Gain: Imaginary ";'
        'Trace 1: Gain: Magnitude (dB);"Trace 2: Gain: Real ";'
        '"Trace 2: Gain: Imaginary ";Trace 2: Gain: Phase (°)\n'
        "275.42287;1;1;0.665377440915802;1;1;85.61464981648201\n"
        "301.995172;1;1;-0.16225722187400488;1;1;86.49824532391638\n",
        encoding="utf-8",
    )
    data = load_fra_file(path, FRASourceFormat.BODE100)
    result = analyze_loop_response(data.frequency_hz, data.complex_response())
    assert len(result.gain_crossovers) == 1
    assert abs(result.main_crossover_hz - 296.59103982069337) < 1e-6
    assert abs(result.phase_margin_deg - 86.32501701589291) < 1e-6
    assert result.gain_margin_db is None
    assert result.status == "REVIEW_GM_NOT_OBSERVED"


def test_exact_digital_deembed_rebuilds_measured_loop():
    f = np.geomspace(10.0, 8_000.0, 120)
    controller = DigitalTransferFunction(
        b=(0.25, -0.20),
        a=(1.0, -0.95),
        sample_rate_hz=40_000.0,
        name="existing",
    )
    cold = digital_frequency_response(controller, f)
    plant = 2.0 / (1.0 + 1j * f / 1500.0)
    measured = plant * cold
    result = deembed_controller(measured, cold)
    assert result.passed
    assert result.magnitude_error_db_max < 1e-10
    assert result.phase_error_deg_max < 1e-10
    assert np.allclose(result.equivalent_plant, plant, rtol=1e-12, atol=1e-12)


def test_quick_tune_unity_scales_reproduce_existing_controller_and_loop():
    f = np.geomspace(20.0, 8_000.0, 160)
    old = DigitalTransferFunction(
        b=(0.18, -0.31, 0.14),
        a=(1.0, -1.72, 0.74),
        sample_rate_hz=40_000.0,
        name="existing 2P2Z",
    )
    old_h = digital_frequency_response(old, f)
    plant = 0.8 / ((1.0 + 1j * f / 900.0) * (1.0 + 1j * f / 7000.0))
    measured = plant * old_h
    extracted = deembed_controller(measured, old_h).equivalent_plant

    tuned = scale_digital_controller(old)
    tuned_h = digital_frequency_response(tuned, f)
    rebuilt = extracted * tuned_h

    assert np.allclose(tuned.b, old.normalized().b, rtol=0.0, atol=1e-15)
    assert np.allclose(tuned.a, old.normalized().a, rtol=0.0, atol=1e-15)
    assert np.allclose(rebuilt, measured, rtol=1e-12, atol=1e-12)


def test_quick_tune_global_gain_moves_magnitude_without_phase_change():
    f = np.geomspace(10.0, 10_000.0, 100)
    base = DigitalTransferFunction(
        b=(0.25, -0.20),
        a=(1.0, -0.95),
        sample_rate_hz=40_000.0,
        name="existing",
    )
    h0 = digital_frequency_response(base, f)
    h1 = digital_frequency_response(scale_digital_controller(base, gain_scale=2.0), f)
    ratio = h1 / h0
    assert np.allclose(np.abs(ratio), 2.0, rtol=1e-12, atol=1e-12)
    assert np.max(np.abs(np.angle(ratio, deg=True))) < 1e-10


def test_quick_tune_individual_ba_scales_are_exact():
    base = DigitalTransferFunction(
        b=(1.0, -2.0, 3.0),
        a=(1.0, -0.8, 0.15),
        sample_rate_hz=50_000.0,
        name="2P2Z",
    )
    tuned = scale_digital_controller(
        base,
        gain_scale=1.5,
        numerator_scales=(1.0, 0.5, 2.0),
        denominator_scales=(1.25, 0.5),
    )
    assert np.allclose(tuned.b, (1.5, -1.5, 9.0))
    assert np.allclose(tuned.a, (1.0, -1.0, 0.075))


def test_firmware_plus_a_convention_converts_to_canonical_denominator():
    assert firmware_feedback_a_to_denominator((0.8, -0.15)) == (1.0, -0.8, 0.15)


def test_loop_margin_and_sensitivity_from_measured_points():
    f = np.geomspace(10.0, 100_000.0, 401)
    # 20 dB at 100 Hz, 0 dB at 1 kHz, -20 dB at 10 kHz.
    mag_db = -20.0 * np.log10(f / 1000.0)
    phase_deg = -90.0 - 20.0 * np.log10(f / 1000.0)
    loop = np.power(10.0, mag_db / 20.0) * np.exp(1j * np.deg2rad(phase_deg))
    result = analyze_loop_response(f, loop)
    assert len(result.gain_crossovers) == 1
    assert abs(result.main_crossover_hz - 1000.0) < 1e-6
    assert abs(result.phase_margin_deg - 90.0) < 1e-6
    assert result.gain_margin_db is None
    assert result.status == "REVIEW_GM_NOT_OBSERVED"
    assert np.isfinite(result.ms)
    assert np.isfinite(result.mt)


def test_multiple_gain_crossovers_are_reported():
    f = np.asarray([100.0, 300.0, 1000.0, 3000.0, 10_000.0])
    mag_db = np.asarray([8.0, -3.0, 4.0, -2.0, -8.0])
    phase_deg = np.asarray([-60.0, -90.0, -120.0, -150.0, -170.0])
    loop = np.power(10.0, mag_db / 20.0) * np.exp(1j * np.deg2rad(phase_deg))
    result = analyze_loop_response(f, loop)
    assert len(result.gain_crossovers) == 3
    assert result.status == "WARNING_MULTIPLE_CROSSOVERS"
