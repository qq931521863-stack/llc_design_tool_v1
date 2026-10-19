"""Packaging preparation checks; source execution is never labeled frozen."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_build_explicitly_collects_power_stage_workspace():
    workflow = (ROOT / '.github/workflows/build-release.yml').read_text(encoding='utf-8')
    assert '--collect-submodules power_stage_design' in workflow


def test_wheel_installs_shared_engineering_data_at_existing_loader_path(tmp_path):
    """Build actual package metadata and probe an installed wheel outside source."""
    source = tmp_path / 'source'
    source.mkdir()
    shutil.copyfile(ROOT / 'pyproject.toml', source / 'pyproject.toml')
    # Only this resource and its consumer are needed for this packaging contract.
    # The real consumer module is loaded directly to avoid unrelated GUI imports.
    for name in ('engineering_data', 'power_control_tools'):
        shutil.copytree(ROOT / name, source / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    wheels = tmp_path / 'wheels'
    installed = tmp_path / 'installed'
    run = tmp_path / 'outside-source'
    run.mkdir()
    env = dict(os.environ, PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1',
               PIP_NO_CACHE_DIR='1')
    env.pop('PYTHONPATH', None)
    completed = subprocess.run(
        [sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation',
         '--wheel-dir', str(wheels), str(source)],
        cwd=run, env=env, text=True, capture_output=True, timeout=120, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    wheel, = wheels.glob('*.whl')
    completed = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '--no-deps', '--target', str(installed), str(wheel)],
        cwd=run, env=env, text=True, capture_output=True, timeout=120, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    probe = '''
import importlib.util, json, sys
from pathlib import Path
root = Path(sys.argv[1])
module_path = root / 'power_control_tools/part_validation.py'
spec = importlib.util.spec_from_file_location('installed_part_validation', module_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
print(json.dumps(module.load_brand_taxonomy(), sort_keys=True))
'''
    completed = subprocess.run([sys.executable, '-c', probe, str(installed)],
                               cwd=run, env=env, text=True, capture_output=True,
                               timeout=30, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == json.loads(
        (ROOT / 'engineering_data/brand_taxonomy.json').read_text(encoding='utf-8'))
    for name in ('brand_taxonomy.json', 'catalog.json'):
        assert (installed / 'engineering_data' / name).read_bytes() == (
            ROOT / 'engineering_data' / name).read_bytes()


def test_real_source_packaging_entry_calculates_and_restores_psd(tmp_path):
    pytest.importorskip('PySide6.QtWidgets')
    report = tmp_path / 'source-proof.json'
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=str(ROOT),
               PDT_SELF_TEST_REPORT=str(report))
    completed = subprocess.run([sys.executable, str(ROOT / 'packaging/launcher.py'), '--self-test'],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=900, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    data = json.loads(report.read_text(encoding='utf-8'))
    assert data['success'] is True and data['frozen'] is False
    assert data['power_stage_designer'] is True
    assert data['power_stage_model_revision'] == 'psd-piecewise-2'
    assert len(data['power_stage_design_id']) == 20
    # test_release_verification separately executes the frozen-only guard using
    # real child processes, including a false frozen flag and missing PSD proof.
