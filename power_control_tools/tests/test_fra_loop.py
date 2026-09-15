from __future__ import annotations

import numpy as np

from power_control_tools.fra import (
    FRASourceFormat,
    analyze_loop_response,
    deembed_controller,
    digital_frequency_response,
    load_fra_file,
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
