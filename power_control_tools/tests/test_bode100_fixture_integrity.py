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
    for row in rows[1:]:
        r1 = float(row[1]); i1 = float(row[2]); mag_db = float(row[3])
        r2 = float(row[4]); i2 = float(row[5]); phase_deg = float(row[6])
        reconstructed_mag_db = 20.0 * math.log10(math.hypot(r1, i1))
        reconstructed_phase_deg = math.degrees(math.atan2(i2, r2))
        wrapped_phase_error = (reconstructed_phase_deg - phase_deg + 180.0) % 360.0 - 180.0
        mag_errors.append(reconstructed_mag_db - mag_db)
        phase_errors.append(wrapped_phase_error)

    # Bode export columns can be rounded independently.  Micro-dB / micro-deg
    # agreement is much tighter than any measurement uncertainty and still
    # catches wrong trace-column mapping or unit interpretation.
    assert float(np.max(np.abs(mag_errors))) < 5e-6
    assert float(np.max(np.abs(phase_errors))) < 5e-6
