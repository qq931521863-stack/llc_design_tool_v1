"""Regression for the v9.4.0 packaged-startup missing-taxonomy failure.

The workflow contract catches omission before expensive packaging. The real
Windows/macOS executable self-tests remain the final packaging authority.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tomllib

import pytest

import power_control_tools.part_validation as part_validation


ROOT = Path(__file__).resolve().parents[2]


def test_gui_release_uses_qualified_qt_baseline():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "PySide6==6.11.2" in project["project"]["optional-dependencies"]["gui"]


def test_default_pdf_regression_dependency_is_in_dev_extra():
    """CI runs PDF source validation without installing optional research tools."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "pdfplumber>=0.11" in project["project"]["optional-dependencies"]["dev"]
    workflow = (ROOT / ".github/workflows/build-release.yml").read_text(encoding="utf-8")
    assert 'pip install -e ".[dev,web,gui,agent]"' in workflow
    assert 'pip install -e ".[gui,dev,web,agent]"' in workflow


def test_desktop_packaging_collects_shared_engineering_data():
    workflow = (ROOT / ".github/workflows/build-release.yml").read_text(encoding="utf-8")
    assert '--add-data "engineering_data:engineering_data"' in workflow
    data = json.loads((ROOT / "engineering_data/brand_taxonomy.json").read_text(encoding="utf-8"))
    assert isinstance(data.get("brands"), list) and data["brands"]
    assert all(item.get("display_name") for item in data["brands"])


def test_taxonomy_loads_relative_to_bundle_not_working_directory(tmp_path, monkeypatch):
    expected = part_validation.load_brand_taxonomy()
    bundle = tmp_path / "bundle"
    module = bundle / "power_control_tools/part_validation.py"
    module.parent.mkdir(parents=True)
    module.write_text("# Simulated bundled module location.\n", encoding="utf-8")
    data = bundle / "engineering_data/brand_taxonomy.json"
    data.parent.mkdir()
    shutil.copyfile(ROOT / "engineering_data/brand_taxonomy.json", data)
    cwd = tmp_path / "unrelated-working-directory"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(part_validation, "__file__", str(module))
    assert part_validation.load_brand_taxonomy() == expected
    assert part_validation.brand_display_names() == tuple(
        str(item["display_name"]) for item in expected["brands"]
    )


def test_missing_bundled_taxonomy_is_not_silently_replaced(tmp_path, monkeypatch):
    module = tmp_path / "bundle/power_control_tools/part_validation.py"
    module.parent.mkdir(parents=True)
    monkeypatch.setattr(part_validation, "__file__", str(module))
    with pytest.raises(FileNotFoundError):
        part_validation.load_brand_taxonomy()
