"""Compare local exact-callback caching with the recorded pre-audit commit."""

import importlib.util
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np

from dab_design import optimization as current
from dab_design.core import DABConfig

ref = "0e5df08bcdf7c36b6d1a9a7d7b613e9fc8aa916e"
source = subprocess.check_output(
    ["git", "show", ref + ":dab_design/optimization.py"], text=True, cwd=ROOT
)
with TemporaryDirectory(prefix="dab-cache-baseline-") as temporary:
    baseline_path = Path(temporary) / "baseline_optimization.py"
    baseline_path.write_text(source, encoding="utf-8")
    module_spec = importlib.util.spec_from_file_location(
        "dab_design._baseline_optimization", baseline_path
    )
    old = importlib.util.module_from_spec(module_spec)
    sys.modules[old.__name__] = old
    module_spec.loader.exec_module(old)
config = DABConfig()
rows = []
for power in (1000, 3500, 6000):
    timings = {"baseline": [], "current": []}
    results = {}
    for rep in range(3):
        pairs = [("baseline", old), ("current", current)]
        if rep % 2:
            pairs.reverse()
        for name, module in pairs:
            start = time.perf_counter()
            r = module.optimize_tps(config, 400, power, grid_points=5)
            timings[name].append(time.perf_counter() - start)
            results[name] = r
    before, after = results["baseline"], results["current"]
    same = bool(
        np.allclose(before.optimized.phases, after.optimized.phases, rtol=0, atol=1e-12)
        and abs(before.optimized.metrics.irms_a - after.optimized.metrics.irms_a)
        < 1e-12
        and before.optimized.feasible == after.optimized.feasible
    )
    assert same
    row = {
        "target_w": power,
        "baseline_seconds": timings["baseline"],
        "current_seconds": timings["current"],
        "median_speed_ratio": statistics.median(timings["baseline"])
        / statistics.median(timings["current"]),
        "same_pwm_candidate": same,
        "baseline_physics_calls": before.evaluations,
        "current_physics_calls": after.evaluations,
        "solver_cache_hits": after.solver_cache_hits,
        "phases": after.optimized.phases,
        "pout_w": after.optimized.metrics.power_w,
        "irms_a": after.optimized.metrics.irms_a,
    }
    rows.append(row)
    print(power, row["median_speed_ratio"], row["solver_cache_hits"], flush=True)
report = {
    "baseline_git_ref": ref,
    "method": "3 alternating-order repeats; 5^3 grid; exact-float local callback cache; one BLAS thread; total time includes PWM verification; evaluation counts exclude PWM verification",
    "scope": "Three 400V points only. Repeated-computation optimization, not AI. No global-optimality claim.",
    "cases": rows,
}
(ROOT / "docs/benchmarks/dab_cache_audit.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
