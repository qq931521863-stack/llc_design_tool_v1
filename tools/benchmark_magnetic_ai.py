"""Offline independent-temperature evaluation; no upstream training code copied.
Run: python tools/benchmark_magnetic_ai.py --data <Magnetic_Modeling directory>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from llc_design.magnetics.validated_surrogate import (
    igse_log_prediction,
    waveform_features,
)


def score(predicted, actual):
    error = abs(predicted / actual - 1) * 100
    return {
        "mape_percent": float(np.mean(error)),
        "p95_percent": float(np.percentile(error, 95)),
        "max_percent": float(np.max(error)),
        "rmse_w_m3": float(np.sqrt(np.mean((predicted - actual) ** 2))),
    }


def run(folder):
    names = [
        "B_waveform[T]_downscaled.csv",
        "Frequency[Hz]_downscaled.csv",
        "Temperature[C]_downscaled.csv",
        "Volumetric_losses[Wm-3]_downscaled.csv",
    ]
    b, f, t, y = [np.loadtxt(folder / n, delimiter=",") for n in names]
    features, rate, delta = waveform_features(b, f, t)
    # Entire 70C condition is blind; no test samples participate in fitting,
    # feature scaling, architecture selection, or early stopping.
    test = np.flatnonzero(t == 70)
    development = np.flatnonzero(t != 70)
    train, val = train_test_split(
        development, test_size=0.2, random_state=20261015, stratify=t[development]
    )
    ly = np.log(y)
    temp = (t - 60) / 50
    ose_features = np.c_[
        np.ones(len(y)), np.log(f), np.log(np.max(abs(b), axis=1)), temp, temp**2
    ]
    ose = np.linalg.lstsq(ose_features[train], ly[train], rcond=None)[0]
    start = time.perf_counter()
    result = least_squares(
        lambda c: (
            igse_log_prediction(c, rate[train], delta[train], t[train]) - ly[train]
        ),
        [ose[0], 1.5, 2.7, 0, 0],
        bounds=([-100, 1, 1.5, -10, -10], [100, 3, 4, 10, 10]),
        max_nfev=100,
        ftol=1e-8,
    )
    igse = result.x
    baseline = igse_log_prediction(igse, rate, delta, t)
    fit_s = time.perf_counter() - start
    scaler = StandardScaler().fit(features[train])
    x = scaler.transform(features)
    residual = ly - baseline
    candidates = []
    for hidden in [(32,), (48, 24)]:
        start = time.perf_counter()
        model = MLPRegressor(
            hidden_layer_sizes=hidden,
            activation="tanh",
            solver="lbfgs",
            alpha=0.05,
            max_iter=500,
            tol=1e-7,
            max_fun=30000,
            random_state=20261015,
        )
        model.fit(x[train], residual[train])
        metrics = score(np.exp(baseline[val] + model.predict(x[val])), y[val])
        candidates.append(
            (
                metrics["mape_percent"],
                model,
                {
                    "hidden": hidden,
                    "validation": metrics,
                    "training_s": time.perf_counter() - start,
                },
            )
        )
    _, model, _ = min(candidates, key=lambda row: row[0])
    test_prediction = np.exp(baseline[test] + model.predict(x[test]))
    physical = score(np.exp(baseline[test]), y[test])
    learned = score(test_prediction, y[test])
    gate = (
        learned["mape_percent"] <= 0.9 * physical["mape_percent"]
        and learned["p95_percent"] <= physical["p95_percent"]
    )
    report = {
        "schema": "magnetic-independent-test-v1",
        "material_grade": "3C90",
        "seed": 20261015,
        "data_sha256": {
            n: hashlib.sha256((folder / n).read_bytes()).hexdigest() for n in names
        },
        "samples": len(y),
        "waveform_samples": b.shape[1],
        "split": {
            "training": len(train),
            "validation": len(val),
            "test": len(test),
            "test_condition": "all 70C samples held out",
        },
        "ose_test": score(np.exp(ose_features[test] @ ose), y[test]),
        "igse_test": physical,
        "residual_fnn_test": learned,
        "validation_candidates": [row[2] for row in candidates],
        "validation_gate_passed": gate,
        "gate": ">=10% MAPE improvement and no worse P95 than fitted iGSE on blind 70C set",
        "igse_fit_seconds": fit_s,
        "igse_coefficients": igse.tolist(),
        "boundaries": [
            "MagNet-derived reference data; not our bench measurements",
            "Single 3C90 grade; no DC-bias/saturation/winding-loss claim",
            "Temperature holdout does not establish unseen-material or arbitrary-waveform generalization",
        ],
    }
    out = ROOT / "docs/benchmarks"
    out.mkdir(parents=True, exist_ok=True)
    (out / "magnetic_ai.json").write_text(json.dumps(report, indent=2) + "\n")
    modeldata = {
        "material_grade": "3C90",
        "validation_gate_passed": gate,
        "igse_coefficients": igse.tolist(),
        "weights": [a.tolist() for a in model.coefs_],
        "biases": [a.tolist() for a in model.intercepts_],
        "input_mean": scaler.mean_.tolist(),
        "input_scale": scaler.scale_.tolist(),
        "domain": {
            "frequency_hz": [float(f.min()), float(f.max())],
            "temperature_c": [25, 90],
            "b_peak_t": [
                float(np.max(abs(b), axis=1).min()),
                float(np.max(abs(b), axis=1).max()),
            ],
        },
        "validation_report": "docs/benchmarks/magnetic_ai.json",
        "source_commit": "8dd961c185fcffd777382faa2aefe67e23cef4ca",
    }
    if gate:
        (ROOT / "llc_design/data/magnetic_surrogate_3c90.json").write_text(
            json.dumps(modeldata, indent=2) + "\n"
        )
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    with threadpool_limits(limits=1):
        run(args.data)
