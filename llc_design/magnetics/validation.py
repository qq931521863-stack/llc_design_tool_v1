"""Measured core-loss comparisons; raw observations are never refitted silently."""

import csv
from pathlib import Path

import numpy as np

from .material import MaterialDatabase

MEASUREMENT_COLUMNS = (
    "material_key",
    "frequency_hz",
    "temperature_c",
    "b_peak_t",
    "measured_pv_w_m3",
)


def compare_measurements(path):
    """Compare sinusoidal core-only loss-density observations against OSE.

    Measured converter loss, winding loss and nonsinusoidal B must not be
    mislabelled as sinusoidal core loss. Use the waveform evaluator for B(t).
    """
    db = MaterialDatabase()
    result = []
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(MEASUREMENT_COLUMNS) <= set(reader.fieldnames or []):
            raise ValueError("Required columns: " + ",".join(MEASUREMENT_COLUMNS))
        for row_number, row in enumerate(reader, 2):
            try:
                material = db.get(row["material_key"])
                f, temp, peak, measured = [
                    float(row[key]) for key in MEASUREMENT_COLUMNS[1:]
                ]
                if (
                    not np.isfinite([f, temp, peak, measured]).all()
                    or min(f, peak, measured) <= 0
                ):
                    raise ValueError("Finite positive Hz, Bpk and measured Pv required")
                prediction = material.steinmetz_density_w_m3(f, peak, temp)
                warnings = list(material.range_warnings(f, peak))
                ts = [p.temperature_c for p in material.steinmetz_points]
                if not min(ts) <= temp <= max(ts):
                    warnings.append(
                        "Temperature outside model fit; endpoint coefficients used"
                    )
                result.append(
                    {
                        "source_row": row_number,
                        "material_key": material.key,
                        "frequency_hz": f,
                        "temperature_c": temp,
                        "b_peak_t": peak,
                        "measured_pv_w_m3": measured,
                        "predicted_pv_w_m3": prediction,
                        "signed_error_percent": 100 * (prediction / measured - 1),
                        "warnings": warnings,
                    }
                )
            except (KeyError, ValueError) as exc:
                raise ValueError(f"CSV row {row_number}: {exc}") from exc
    if not result:
        raise ValueError("No measurement rows")
    error = np.array([r["signed_error_percent"] for r in result])
    return {
        "source_file": Path(path).name,
        "samples": len(result),
        "mape_percent": float(np.mean(abs(error))),
        "mean_signed_error_percent": float(np.mean(error)),
        "p95_absolute_percent": float(np.percentile(abs(error), 95)),
        "scope": "Sinusoidal core-only Pv; no automatic coefficient fitting",
        "rows": result,
    }
