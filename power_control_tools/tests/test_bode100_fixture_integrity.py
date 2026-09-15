from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np


DATA = Path(__file__).with_name("data") / "bode100_real_264vac_190v_90pct.csv"


def test_real_bode100_rectangular_columns_match_exported_magnitude_and_phase():
    with DATA.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle, delimiter=";"))
    assert len(rows) == 102  # header + 101 measurement points

    mag_errors: list[float] = []
    phase_errors: list[float] = []
    complex_relative_errors: list[float] = []
    for row in rows[1:]:
        r1 = float(row[1]); i1 = float(row[2]); mag_db = float(row[3])
        r2 = float(row[4]); i2 = float(row[5]); phase_deg = float(row[6])
        reconstructed_mag_db = 20.0 * math.log10(math.hypot(r1, i1))
        reconstructed_phase_deg = math.degrees(math.atan2(i2, r2))
        wrapped_phase_error = (reconstructed_phase_deg - phase_deg + 180.0) % 360.0 - 180.0
        mag_errors.append(reconstructed_mag_db - mag_db)
        phase_errors.append(wrapped_phase_error)
        rectangular = complex(r2, i2)
        polar = 10.0 ** (mag_db / 20.0) * complex(
            math.cos(math.radians(phase_deg)), math.sin(math.radians(phase_deg))
        )
        magnitude = max(abs(rectangular), 1e-300)
        complex_relative_errors.append(abs(polar - rectangular) / magnitude)

    # The Bode100 export rounds its rectangular and polar column groups
    # independently, so the two descriptions of the same complex point agree
    # only to the export precision -- not to machine epsilon.
    #
    # Measured on this fixture: magnitude agrees to 1.8e-6 dB, but the phase
    # column differs from atan2(Im, Re) by up to 1.05e-4 deg at the -13.9 dB
    # point, which is 1.67e-5 of |z|.  A plain micro-degree bound is therefore
    # unreachable for this data no matter how the importer is written.
    #
    # Columns are compared as complex numbers because a phase error is
    # naturally amplified where |z| is small; the relative tolerance stays
    # tight enough that wrong column mapping (O(1) relative), a missing
    # 180 deg correction or a dB/linear unit error are still caught.
    assert float(np.max(np.abs(mag_errors))) < 5e-6
    assert float(np.max(np.abs(phase_errors))) < 1e-3
    assert float(np.max(complex_relative_errors)) < 1e-4
