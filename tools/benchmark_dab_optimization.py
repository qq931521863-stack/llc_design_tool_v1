"""Compare TPS formula/grid/constrained optimization and surrogate warm starts.

Independent ideal-L labels; no incompatible upstream DAB labels are reused.
400V is excluded from surrogate fitting. All results checked with exact physics
and PWM quantization. Training cost is reported separately, never hidden.
"""

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dab_design.core import DABConfig
from dab_design.optimization import TPSConstraints, exact_tps_metrics, optimize_tps


def run():
    config = DABConfig()
    rng = np.random.default_rng(20261015)
    count = 6000
    phases = rng.uniform(0, 0.98, (count, 3))
    phases[:, 1:] = np.sort(phases[:, 1:], axis=1)
    voltage = rng.choice([300.0, 350.0, 450.0, 500.0], count)
    x = np.c_[voltage / config.va_v, phases]
    start = time.perf_counter()
    labels = np.array(
        [
            [m.power_w, m.irms_a, m.ipeak_a]
            for m in (exact_tps_metrics(config, v, d) for v, d in zip(voltage, phases))
        ]
    )
    label_s = time.perf_counter() - start
    start = time.perf_counter()
    model = ExtraTreesRegressor(
        n_estimators=64, min_samples_leaf=2, random_state=20261015, n_jobs=1
    ).fit(x, labels)
    train_s = time.perf_counter() - start
    limits = TPSConstraints()
    cases = []
    for power in (1000.0, 3500.0, 6000.0):
        start = time.perf_counter()
        direct = optimize_tps(config, 400, power, limits)
        direct_s = time.perf_counter() - start
        start = time.perf_counter()
        candidates = rng.uniform(0, 0.98, (12000, 3))
        candidates[:, 1:] = np.sort(candidates[:, 1:], axis=1)
        predicted = model.predict(
            np.c_[np.ones(len(candidates)) * 400 / config.va_v, candidates]
        )
        merit = (
            predicted[:, 1]
            + 1000 * abs(predicted[:, 0] - power) / power
            + 1000 * np.maximum(predicted[:, 2] - limits.peak_current_limit_a, 0)
        )
        seeds = candidates[np.argsort(merit)[:3]]
        ai = optimize_tps(
            config, 400, power, limits, grid_points=0, initial_seeds=seeds
        )
        ai_s = time.perf_counter() - start
        cases.append(
            {
                "vb_v": 400,
                "target_w": power,
                "formula": asdict(direct.baseline),
                "grid": asdict(direct.grid) if direct.grid else None,
                "constrained": asdict(direct.optimized),
                "surrogate_warm_start": asdict(ai.optimized),
                "direct_total_s": direct_s,
                "direct_timing_s": direct.timings_s,
                "surrogate_online_total_s": ai_s,
                "grid_solver_evaluations_direct": direct.evaluations,
                "grid_solver_evaluations_surrogate": ai.evaluations,
            }
        )
    # Genuine independent voltage test. Prediction errors cannot be substituted
    # for exact verification of the optimizer's final candidates.
    phases = rng.uniform(0, 0.98, (1000, 3))
    phases[:, 1:] = np.sort(phases[:, 1:], axis=1)
    truth = np.array(
        [
            [m.power_w, m.irms_a, m.ipeak_a]
            for m in (exact_tps_metrics(config, 400, d) for d in phases)
        ]
    )
    prediction = model.predict(np.c_[np.ones(len(phases)) * 400 / config.va_v, phases])
    gate = all(
        c["surrogate_warm_start"]["feasible"]
        and c["constrained"]["feasible"]
        and c["surrogate_warm_start"]["metrics"]["irms_a"]
        <= 1.01 * c["constrained"]["metrics"]["irms_a"]
        for c in cases
    )
    speed_ratio = float(
        np.median([c["direct_total_s"] / c["surrogate_online_total_s"] for c in cases])
    )
    gate = bool(gate and speed_ratio >= 1.2)
    report = {
        "seed": 20261015,
        "config": asdict(config),
        "training_samples": count,
        "label_generation_s": label_s,
        "training_s": train_s,
        "independent_test_voltage_v": 400,
        "test_mae_power_rms_peak": np.mean(abs(prediction - truth), axis=0).tolist(),
        "cases": cases,
        "median_online_speed_ratio": speed_ratio,
        "validation_gate_passed": gate,
        "gate": "All three exact-verified feasible; RMS no worse than 1%; >=1.2x median online speed",
        "production_decision": "No production surrogate: three points are insufficient for operating-envelope validation even if this screening gate passes",
        "scope": "Ideal-L preliminary comparison, not global optimality; Cp/deadtime/ZVS/losses excluded. Evaluation counters exclude PWM verification; timings include it.",
    }
    out = ROOT / "docs/benchmarks/dab_optimization.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    with threadpool_limits(limits=1):
        run()
