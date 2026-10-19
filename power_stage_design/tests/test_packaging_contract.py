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


@pytest.mark.parametrize("fault", [None, "inductance", "dcr", "text", "icon", "details"])
def test_real_magnetic_apply_checks_values_and_message_without_window_title(tmp_path, fault):
    """Real solver/button/modal path; only platform metadata or faults are injected."""
    import importlib.util

    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    qt = pytest.importorskip('PySide6.QtWidgets')
    from PySide6.QtCore import QEvent, QObject

    from llc_design.gui.lifetime import dispose_widgets
    from pfc_design.gui.main_window import PFCMainWindow

    spec = importlib.util.spec_from_file_location('pdt_magnetic_launcher', ROOT / 'packaging/launcher.py')
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    app = qt.QApplication.instance() or qt.QApplication([])
    host = PFCMainWindow()
    control = host.control_lab_view.control_lab
    seen = []

    class ApplyDialogProbe(QObject):
        def eventFilter(self, obj, event):
            if isinstance(obj, qt.QMessageBox) and event.type() == QEvent.Type.Show:
                # Simulate Qt's documented macOS title suppression, not Apply.
                qt.QWidget.setWindowTitle(obj, '')
                seen.append(obj.text())
                if fault in ('inductance', 'dcr'):
                    widget = getattr(control, fault)
                    widget.setValue(widget.value() + 1)
                elif fault == 'text':
                    obj.setText('TTPL 电感参数已应用')  # A title alone proves no result.
                elif fault == 'icon':
                    obj.setIcon(qt.QMessageBox.Icon.Warning)
                elif fault == 'details':
                    obj.setDetailedText('Application failed')
            return False

    probe = ApplyDialogProbe()
    app.installEventFilter(probe)
    host.show()
    try:
        if fault is None:
            proof = launcher._exercise_magnetic_catalog(host, tmp_path, app)
            pfc = proof['pfc']
            assert pfc['applied'] == {'inductance_uh': 209.48, 'dcr_mohm': 48.817}
            acknowledgement, = pfc['apply_acknowledgements']
            assert acknowledgement['window_title'] == ''
            assert acknowledgement['text'] == seen[0]
            assert pfc['real_calculate_and_apply'] and pfc['recomputed_curves_identical']
            assert pfc['stale_consumers_cleared'] and pfc['report_consistency']
        else:
            mismatch = {'inductance': 'inductance', 'dcr': 'DCR'}.get(fault, 'success acknowledgement')
            with pytest.raises(RuntimeError, match=f'Real Apply {mismatch} mismatch') as error:
                launcher._exercise_magnetic_catalog(host, tmp_path, app)
            assert 'expected=' in str(error.value) and 'actual=' in str(error.value)
            assert 'acknowledgements=' in str(error.value)
        assert len(seen) == 1
    finally:
        app.removeEventFilter(probe)
        dispose_widgets([host])


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


def test_installed_wheel_runs_builtin_llc_validation_outside_source(tmp_path):
    """The public validation API must retain its bundled baseline in a wheel."""
    source = tmp_path / 'source'
    source.mkdir()
    shutil.copyfile(ROOT / 'pyproject.toml', source / 'pyproject.toml')
    packages = ('llc_design', 'pfc_design', 'engineering_data', 'power_control_tools',
                'power_codegen', 'power_stage_design', 'dab_design', 'power_sim',
                'power_agent', 'webapp', 'backend')
    for name in packages:
        shutil.copytree(ROOT / name, source / name, ignore=shutil.ignore_patterns(
            '__pycache__', '*.pyc', '.mplcache', 'output', '.pytest_cache'))
    wheels, installed, outside = (tmp_path / name for name in ('wheels', 'installed', 'outside'))
    outside.mkdir()
    env = dict(os.environ, PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_NO_CACHE_DIR='1')
    env.pop('PYTHONPATH', None)
    for command in (
        [sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation',
         '--wheel-dir', str(wheels), str(source)],
        [sys.executable, '-m', 'pip', 'install', '--no-deps', '--target', str(installed)],
    ):
        if command[3] == 'install':
            wheel, = wheels.glob('*.whl')
            command.append(str(wheel))
        done = subprocess.run(command, cwd=outside, env=env, text=True,
                              capture_output=True, timeout=120, check=False)
        assert done.returncode == 0, done.stdout + done.stderr
    resource = 'llc_design/examples/baseline_400V_53V_3kW.json'
    assert (installed / resource).read_bytes() == (ROOT / resource).read_bytes()
    probe = '''
import json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from llc_design.validation import validation_summary
import llc_design.validation.baseline as baseline
assert pathlib.Path(baseline.__file__).resolve().is_relative_to(root)
summary = validation_summary()
matrix = summary['baseline_matrix']
assert matrix['software_regression_passed'] is True
assert matrix['hardware_verified'] is False and matrix['status'] == 'UNKNOWN'
print(json.dumps(matrix, sort_keys=True))
'''
    done = subprocess.run([sys.executable, '-I', '-c', probe, str(installed)],
                          cwd=outside, env=env, text=True, capture_output=True,
                          timeout=120, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    assert json.loads(done.stdout)['baseline'] == '400V_to_53V_3kW'
