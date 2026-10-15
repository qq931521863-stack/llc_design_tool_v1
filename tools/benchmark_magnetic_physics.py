"""Reproducible numerical/TDK table checks; not claims of bench validation."""

import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from llc_design.magnetics.catalog import catalog_summary
from llc_design.magnetics.material import MaterialDatabase
from llc_design.magnetics.validation import compare_measurements


def run():
    db = MaterialDatabase()
    samples = []
    for key in ("TDK_PC40_2002", "TDK_PC44_2002", "TDK_N97_REF"):
        material = db.get(key)
        for count in (256, 1024, 4096):
            f, bpk = 100000.0, 0.1
            time = np.arange(count) / (count * f)
            flux = bpk * np.sin(2 * np.pi * f * time)
            ose = material.steinmetz_density_w_m3(f, bpk, 100)
            igse = material.igse_density_w_m3(time, flux, 100)
            samples.append(
                {
                    "material": key,
                    "samples": count,
                    "ose_w_m3": ose,
                    "igse_w_m3": igse,
                    "relative_error": igse / ose - 1,
                }
            )
    folder = ROOT / "docs/benchmarks"
    folder.mkdir(exist_ok=True, parents=True)
    measured = folder / "tdk_reference_points.csv"
    # Independent TABLE p.6; excluded 100kHz/0.2T point from the graph fitting.
    with measured.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            (
                "material_key",
                "frequency_hz",
                "temperature_c",
                "b_peak_t",
                "measured_pv_w_m3",
            )
        )
        for grade, pv in [("PC40", 410000), ("PC44", 300000)]:
            writer.writerow((f"TDK_{grade}_2002", 100000, 100, 0.2, pv))
    report = {
        "scope": "Numerical identity + 2002 TDK manufacturer table; NOT our bench measurements",
        "catalog": catalog_summary(),
        "sinusoidal_igse_convergence": samples,
        "tdk_independent_table": compare_measurements(measured),
        "diagnosis": [
            "Table/graph fit discrepancy is retained, not hidden by a correction multiplier",
            "Legacy Ae*le vs Ve anomalies are flagged; raw values retained",
            "Core-window area is not usable bobbin winding area",
            "Bench waveform, copper/core separation and temperature records still required",
        ],
    }
    (folder / "magnetic_physics.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    run()
