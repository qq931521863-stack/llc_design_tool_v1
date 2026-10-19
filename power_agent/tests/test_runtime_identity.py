"""Runtime provenance excludes artifacts but still rejects changed inputs."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from llc_design.core.config import save_spec
from llc_design.core.spec import LLCDesignSpec
from power_agent import storage
from power_control_tools.research_export import ResearchExport, export_research_script

ROOT = Path(__file__).resolve().parents[2]
INPUTS = (
    "power_agent/storage.py",
    "llc_design/core/tank.py",
    "llc_design/data/devices.json",
    "pfc_design/data/mosfets.json",
    "engineering_data/catalog.json",
    "llc_design/examples/baseline_400V_53V_3kW.json",
)


def load_storage(path):
    # A separate import takes its own real startup snapshot; never refresh the
    # production module's baseline or replace runtime_identity with a fake hash.
    spec = importlib.util.spec_from_file_location("runtime_storage_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runtime_tree(tmp_path):
    root = tmp_path / "runtime"
    for relative in INPUTS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, path)
    return root, load_storage(root / "power_agent/storage.py")


def test_real_matplotlib_cache_creation_change_and_removal_preserve_snapshots(runtime_tree, tmp_path):
    root, module = runtime_tree
    store = module.DesignStore(tmp_path / "workspace")
    design = store.create({})
    baseline = module._LOADED_RUNTIME.copy()
    cache = root / "webapp/.mplcache"
    # This is the real file producer in the failing whole-suite order: the web
    # app configures this directory, then a later plotting child builds it.
    result = subprocess.run([sys.executable, "-c", "import matplotlib.font_manager"],
        env={**os.environ, "MPLCONFIGDIR": str(cache), "MPLBACKEND": "Agg"},
        capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    fontlist, = cache.glob("fontlist-*.json")
    for action in ("created", "changed", "removed"):
        if action == "changed":
            payload = json.loads(fontlist.read_text(encoding="utf-8"))
            payload["test_cache_rebuild"] = True
            fontlist.write_text(json.dumps(payload), encoding="utf-8")
        elif action == "removed":
            fontlist.unlink()
        assert module.checked_runtime() == baseline, action
        assert module._LOADED_RUNTIME == baseline, action
        assert store.create({})["design_id"] == design["design_id"], action
        assert store.load(design["design_id"])["runtime"] == baseline, action


def test_real_project_script_and_agent_outputs_do_not_change_runtime(runtime_tree):
    root, module = runtime_tree
    baseline = module._LOADED_RUNTIME.copy()
    # Include a real content-addressed workspace within a package's output tree.
    store = module.DesignStore(root / "power_agent/output/session")
    design = store.create({})
    project = root / "llc_design/output/project.json"
    script = project.with_suffix(".py")
    for power in (3000.0, 2000.0):
        save_spec(LLCDesignSpec(pout_w=power), project)
        bundle = ResearchExport(f"Export at {power} W", (10.0, 100.0), (), (), "{}", ())
        export_research_script(bundle, script)
        assert module.checked_runtime() == baseline
        assert store.create({})["design_id"] == design["design_id"]
        assert store.load(design["design_id"])["runtime"] == baseline
    project.unlink()
    script.unlink()
    assert module.checked_runtime() == baseline


@pytest.mark.parametrize("relative", INPUTS[1:])
@pytest.mark.parametrize("change", ["modify", "add", "delete"])
def test_real_source_and_catalog_file_drift_rejects_old_and_new_snapshots(
        runtime_tree, tmp_path, relative, change):
    root, module = runtime_tree
    path = root / relative
    if change == "add" and relative.startswith("llc_design/examples/"):
        # The optional baseline may be absent in an installed wheel. Its later
        # appearance must count just like addition of a new catalog file.
        baseline_bytes = path.read_bytes()
        path.unlink()
        module = load_storage(root / "power_agent/storage.py")
    store = module.DesignStore(tmp_path / "workspace")
    design = store.create({})
    if change == "delete":
        path.unlink()
    elif change == "add":
        if relative.startswith("llc_design/examples/"):
            path.write_bytes(baseline_bytes)
        else:
            path.with_name("additional" + path.suffix).write_bytes(path.read_bytes())
    elif path.suffix == ".py":
        original = path.read_text(encoding="utf-8")
        changed = original.replace("8.0 / math.pi**2", "7.0 / math.pi**2")
        assert changed != original
        path.write_text(changed, encoding="utf-8")
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if relative == "llc_design/data/devices.json":
            payload["primary_mosfets"][0]["rds_on_25_ohm"] *= 2
        elif relative == "pfc_design/data/mosfets.json":
            payload["mosfets"][0]["rds_on_25c"] *= 2
        else:
            payload["runtime_identity_regression"] = "changed maintained data"
        path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="server startup"):
        store.create({})
    with pytest.raises(ValueError, match="runtime/data changed"):
        store.load(design["design_id"])
    # Restart permits a new identity, never recomputation of the old snapshot.
    restarted = load_storage(root / "power_agent/storage.py").DesignStore(store.root)
    with pytest.raises(ValueError, match="runtime/data changed"):
        restarted.load(design["design_id"])
    assert restarted.create({})["design_id"] != design["design_id"]


@pytest.mark.parametrize("package", storage._PACKAGES)
def test_new_calculation_modules_in_every_runtime_package_invalidate(runtime_tree, package):
    root, module = runtime_tree
    path = root / package / "new_calculation.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("GAIN = 2.0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="server startup"):
        module.checked_runtime()


@pytest.mark.parametrize("dependency", ["numpy", "scipy", "pandas", "pydantic", "mcp"])
def test_dependency_drift_still_rejects_snapshots(runtime_tree, tmp_path, monkeypatch, dependency):
    _, module = runtime_tree
    store = module.DesignStore(tmp_path / "workspace")
    design = store.create({})
    version = module.importlib.metadata.version
    monkeypatch.setattr(module.importlib.metadata, "version",
        lambda name: "changed-version" if name == dependency else version(name))
    with pytest.raises(ValueError, match="server startup"):
        store.create({})
    with pytest.raises(ValueError, match="runtime/data changed"):
        store.load(design["design_id"])


@pytest.mark.parametrize("directory", [".mplcache", "__pycache__", "output", "cache", "artifacts", "build", "dist", "tests"])
def test_non_runtime_trees_are_excluded_for_source_and_json(runtime_tree, directory):
    root, module = runtime_tree
    for suffix in (".py", ".json"):
        path = root / "llc_design" / directory / ("generated" + suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    assert module.checked_runtime() == module._LOADED_RUNTIME
