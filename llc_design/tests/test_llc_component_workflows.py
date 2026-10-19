"""Acceptance of physical LLC component and current-input waveform workflows."""
import json
import math
import os

import numpy as np
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from llc_design.core.spec import LLCDesignSpec, PrimaryTopology
from llc_design.dynamics.waveforms import signal_statistics


def test_ac_rms_independent_dc_and_shifted_sine():
    dc = signal_statistics(np.full(2048, 7.0), samples_per_period=1024,
                           switching_frequency_hz=100_000)
    assert dc.rms == 7.0
    assert dc.ac_rms == 0.0
    phase = np.arange(2048) * 2 * math.pi / 1024
    signal = signal_statistics(7 + 3 * np.sin(phase), samples_per_period=1024,
                               switching_frequency_hz=100_000)
    assert signal.average == pytest.approx(7)
    assert signal.rms == pytest.approx(math.sqrt(49 + 4.5))
    assert signal.ac_rms == pytest.approx(3 / math.sqrt(2))


def test_waveform_session_strict_roundtrip(tmp_path):
    from llc_design.core.config import (
        create_waveform_session,
        load_project,
        save_project,
    )
    spec = LLCDesignSpec()
    state = create_waveform_session(spec, {'vbus_v': 380.0, 'load_fraction': .7}, 'fast', 'q1')
    path = save_project(spec, tmp_path / 'wave.json', waveform_session=state)
    loaded, control, restored = load_project(path, include_waveform=True)
    assert loaded == spec
    assert control is None
    assert restored == state
    assert len(load_project(path)) == 2


@pytest.fixture(scope='module')
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('topology,expected', [
    (PrimaryTopology.FULL_BRIDGE, {'q1', 'q2', 'q3', 'q4'}),
    (PrimaryTopology.HALF_BRIDGE, {'q1', 'q2'}),
])
def test_physical_schematic_targets(app, topology, expected):
    from shiboken6 import delete

    from llc_design.gui.widgets.topology import TopologyView
    view = TopologyView()
    view.set_primary_topology(topology)
    assert {key for key in view.blocks if key.startswith('q')} == expected
    assert {'sr1', 'sr2', 'sr3', 'sr4', 'lr', 'cr', 'lm', 'primary',
            'secondary', 'co', 'load'} <= set(view.blocks)
    delete(view)


@pytest.mark.parametrize('topology', list(PrimaryTopology))
def test_electrical_connectivity_and_isolation(app, topology):
    """Topology truth is a terminal graph, independent of pixel placement."""
    from shiboken6 import delete

    from llc_design.gui.widgets.topology import TopologyView
    view = TopologyView(); view.set_primary_topology(topology)
    graph = view.connections
    assert graph['q1'] == ('BUS+', 'A')
    assert graph['q2'] == ('A', 'PGND')
    assert graph['lr'] == ('A', 'XR')
    assert graph['rseries'] == ('XR', 'X')
    assert graph['cr'] == ('X', 'P+')
    assert graph['lm'] == graph['primary'] == ('P+', 'B')
    assert graph['secondary'] == ('S+', 'S−')
    assert graph['sr1'] == ('OUT+', 'S+')
    assert graph['sr2'] == ('S+', 'SGND')
    assert graph['sr3'] == ('OUT+', 'S−')
    assert graph['sr4'] == ('S−', 'SGND')
    assert graph['coesr'] == ('OUT+', 'CO+')
    assert graph['co'] == ('CO+', 'SGND')
    assert graph['load'] == ('OUT+', 'SGND')
    upper = 'q3' if topology == PrimaryTopology.FULL_BRIDGE else 'midpoint_upper'
    lower = 'q4' if topology == PrimaryTopology.FULL_BRIDGE else 'midpoint_lower'
    assert graph[upper] == ('BUS+', 'B')
    assert graph[lower] == ('B', 'PGND')
    reachable = {'PGND'}
    for _ in range(len(graph)):
        for a, b in graph.values():
            if a in reachable or b in reachable:
                reachable.update((a, b))
    assert {'BUS+', 'A', 'B', 'P+', 'X', 'XR'} <= reachable
    assert not reachable & {'S+', 'S−', 'SGND', 'OUT+', 'CO+'}
    delete(view)


@pytest.mark.parametrize('change', [
    {'solver': 'arbitrary'}, {'model_revision': 'future'}, {'spec_identity': 'wrong'},
    {'work_point': {'vbus_v': True, 'load_fraction': .7}},
    {'work_point': {'vbus_v': 380, 'load_fraction': 0}},
    {'work_point': {'vbus_v': 380, 'load_fraction': float('inf')}},
    {'solver_options': {'cycles': True, 'samples_per_cycle': 1024}},
    {'solver_options': {'cycles': 2, 'samples_per_cycle': 10**9}},
    {'component': 'q99'}, {'group': True}, {'group': 7},
    {'accepted': True}, {'arrays': [1, 2, 3]},
])
def test_session_rejects_invalid_or_authoritative_results_atomically(tmp_path, change):
    from llc_design.core.config import create_waveform_session, save_project
    spec = LLCDesignSpec()
    state = create_waveform_session(spec, {'vbus_v': 380., 'load_fraction': .7}, 'fast')
    path = save_project(spec, tmp_path / 'project.json', waveform_session=state)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        save_project(spec, path, waveform_session={**state, **change})
    assert path.read_bytes() == original


def test_legacy_projects_and_strict_json(tmp_path):
    from llc_design.core.config import load_project, save_project, save_spec
    spec = LLCDesignSpec()
    for path in (save_spec(spec, tmp_path/'old.json'), save_project(spec, tmp_path/'current.json')):
        assert load_project(path, include_waveform=True) == (spec, None, None)
    path = tmp_path/'duplicate.json'
    path.write_text('{"spec": {}, "waveform_session": null, "waveform_session": null}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_project(path)


def _wait_workers(window, app):
    import time
    deadline = time.monotonic() + 60
    while window._active_workers and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.005)
    app.processEvents()
    assert not window._active_workers, 'real Qt worker did not finish'


@pytest.fixture
def window_factory(app, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid

    from llc_design.gui import main_window as module
    monkeypatch.setattr(module.LLCMainWindow, '_auto_check_update', lambda self: None)
    issues, windows = [], []
    monkeypatch.setattr(module, 'show_operation_issue', lambda *a, **kw: issues.append(str(a[1:])))
    def factory(topology=PrimaryTopology.FULL_BRIDGE):
        window = module.LLCMainWindow(LLCDesignSpec(primary_topology=topology,
                                      secondary_turns=8 if topology == PrimaryTopology.HALF_BRIDGE else 4))
        window.show(); app.processEvents(); windows.append(window)
        return window, issues
    yield factory
    for window in windows:
        if isValid(window):
            _wait_workers(window, app)
            window.close(); window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def _run_fast(window, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    window.tabs.setCurrentWidget(window.waveform_view); app.processEvents()
    QTest.mouseClick(window.waveform_view.fast_button, Qt.MouseButton.LeftButton)
    _wait_workers(window, app)
    assert window.waveform_view.bundle is not None
    return window.waveform_view.bundle


@pytest.mark.parametrize('topology', list(PrimaryTopology))
def test_actual_component_clicks_plot_exact_bundle_and_ac_rms(app, window_factory, topology, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    window, issues = window_factory(topology)
    bundle = _run_fast(window, app)
    view = window.waveform_view
    targets = {
        'lr': ('v_resonant_inductor', 'i_resonant'),
        'cr': ('v_resonant_cap', 'i_resonant'),
        'lm': ('v_transformer_primary', 'i_magnetizing'),
        'primary': ('v_transformer_primary', 'i_primary_load'),
        'secondary': ('v_transformer_secondary', 'i_transformer_secondary'),
        'co': ('v_output_cap_internal', 'i_output_cap'),
        'load': ('v_output', 'i_load_output'),
    }
    for key in ['q1', 'q2', 'sr1', 'sr2', 'sr3', 'sr4'] + (
            ['q3', 'q4'] if topology == PrimaryTopology.FULL_BRIDGE else []):
        targets[key] = (f'vds_{key}', f'ids_{key}')
    for component, keys in targets.items():
        window.tabs.setCurrentIndex(0); app.processEvents()
        item = window.topology_view.blocks[component]
        position = window.topology_view.mapFromScene(item.sceneBoundingRect().center())
        QTest.mouseClick(window.topology_view.viewport(), Qt.MouseButton.LeftButton, pos=position)
        app.processEvents()
        assert window.tabs.currentWidget() is view, component
        assert view._selected_keys() == keys, component
        assert view.bundle is bundle
        for row, key in enumerate(keys):
            actual = view._plots[row].listDataItems()[0]
            np.testing.assert_array_equal(actual.yData, bundle.signals[key].values)
            np.testing.assert_array_equal(actual.xData, bundle.time_s * 1e6)
            data = bundle.signals[key].values
            assert float(view.statistics.item(row, 4).text()) == pytest.approx(np.sqrt(np.mean(data**2)), rel=1e-7, abs=1e-7)
            assert float(view.statistics.item(row, 5).text()) == pytest.approx(np.std(data), rel=1e-7, abs=1e-7)
    # Display-only navigation retains authority, including grouping and cursors.
    view.group_combo.setCurrentIndex(4); view.cursor_a_us.setValue(.25)
    assert view.bundle is bundle and view.export_button.isEnabled()
    if topology == PrimaryTopology.HALF_BRIDGE:
        assert not any(key.endswith(('_q3', '_q4')) for key in view._selected_keys())
    assert not issues


@pytest.mark.parametrize('topology', list(PrimaryTopology))
def test_workpoint_invalidation_late_worker_and_global_run(app, monkeypatch, window_factory, topology):
    import threading

    from llc_design.gui import main_window as module
    window, issues = window_factory(topology)
    old = _run_fast(window, app); view = window.waveform_view
    view.vbus.setValue(390); view.load_percent.setValue(80)
    assert view.bundle is None and not view._plots and not view.export_button.isEnabled()
    window._run_current_page(); _wait_workers(window, app)
    assert view.bundle is not old
    assert view.bundle.metadata['vbus_v'] == 390
    assert window.small_signal_analysis.steady_state.inputs.bus_voltage_v == 390
    original = module.build_small_signal_analysis
    entered, release = threading.Event(), threading.Event()
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(30)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'build_small_signal_analysis', delayed)
    window.run_waveforms(False)
    assert entered.wait(10)
    view.vbus.setValue(380); view.load_percent.setValue(70)
    release.set(); _wait_workers(window, app)
    assert view.bundle is None and not view.export_button.isEnabled()
    assert not issues
    monkeypatch.setattr(module, 'build_small_signal_analysis', original)
    window._run_current_page(); _wait_workers(window, app)
    assert view.bundle.metadata['vbus_v'] == 380
    assert view.bundle.metadata['load_fraction'] == .7
    assert window.small_signal_analysis.parameters.load_resistance_ohm == pytest.approx(53**2 / (3000*.7))


@pytest.mark.parametrize('topology', list(PrimaryTopology))
def test_actual_save_reopen_recompute_and_invalid_load_atomic(app, monkeypatch, window_factory, topology, tmp_path):
    from PySide6.QtWidgets import QFileDialog
    window, issues = window_factory(topology); view = window.waveform_view
    view.vbus.setValue(380); view.load_percent.setValue(70); view.select_component('sr4')
    old = _run_fast(window, app)
    path = tmp_path/'session.json'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(path), 'JSON'))
    window.save_json()
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document['waveform_session']['work_point'] == {'vbus_v': 380., 'load_fraction': .7}
    assert 'signals' not in document['waveform_session']
    view.vbus.setValue(370); view.load_percent.setValue(60)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(path), 'JSON'))
    window.load_json(); _wait_workers(window, app)
    assert view.work_point() == {'vbus_v': 380., 'load_fraction': .7}
    assert view.selected_component == 'sr4'
    assert view.bundle is not old
    assert view.bundle.metadata['request_identity'] == old.metadata['request_identity']
    assert view.bundle.metadata['request_generation'] != old.metadata['request_generation']
    assert window.small_signal_analysis.steady_state.inputs.bus_voltage_v == 380
    assert not issues
    current = view.bundle
    document['waveform_session']['work_point']['vbus_v'] = False
    path.write_text(json.dumps(document), encoding="utf-8")
    window.load_json()
    assert issues and view.bundle is current
    assert view.work_point() == {'vbus_v': 380., 'load_fraction': .7}


@pytest.mark.parametrize('topology', list(PrimaryTopology))
def test_actual_export_same_source_report_csv_and_modal_change(app, monkeypatch, window_factory, topology, tmp_path):
    import pandas as pd
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog
    window, issues = window_factory(topology); view = window.waveform_view
    view.vbus.setValue(380); view.load_percent.setValue(70)
    bundle = _run_fast(window, app); view.select_component('q1')
    target = tmp_path/'report'
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **kw: str(target))
    QTest.mouseClick(view.export_button, Qt.MouseButton.LeftButton)
    metadata = json.loads((target/'waveform_metadata.json').read_text(encoding="utf-8"))
    report = (target/'LLC_waveforms.md').read_text(encoding="utf-8")
    identity = bundle.metadata['request_identity']
    assert metadata['metadata']['request_identity'] == metadata['context']['request_identity'] == identity
    assert metadata['context']['selected_component'] == 'q1'
    assert metadata['context']['connections']['q1'] == ['BUS+', 'A']
    assert identity in report and 'AC RMS' in report and '380' in report
    assert (target/'llc_schematic.png').is_file()
    data = pd.read_csv(target/'llc_waveforms.csv')
    stats = pd.read_csv(target/'llc_waveform_statistics.csv').set_index('key')
    for key, signal in bundle.signals.items():
        np.testing.assert_allclose(data[key], signal.values, atol=1e-10)
        assert stats.loc[key, 'rms'] == pytest.approx(np.sqrt(np.mean(signal.values**2)))
        assert stats.loc[key, 'ac_rms'] == pytest.approx(np.std(signal.values), abs=1e-10)
    changed = tmp_path/'stale-report'
    def change_during_dialog(*a, **kw):
        view.vbus.setValue(390)
        return str(changed)
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', change_during_dialog)
    QTest.mouseClick(view.export_button, Qt.MouseButton.LeftButton)
    assert not changed.exists() and view.bundle is None
    assert not issues


def test_failure_stale_error_global_edit_and_close_reopen(app, monkeypatch, window_factory):
    import threading

    from llc_design.gui import main_window as module
    window, issues = window_factory(); view = window.waveform_view
    _run_fast(window, app)
    original = module.build_small_signal_analysis
    def failed(*args, **kwargs):
        raise ValueError('deliberate model failure')
    monkeypatch.setattr(module, 'build_small_signal_analysis', failed)
    window.run_waveforms(False); _wait_workers(window, app)
    assert issues and view.bundle is None and not view.export_button.isEnabled()
    issues.clear()
    entered, release = threading.Event(), threading.Event()
    def late_failure(*args, **kwargs):
        entered.set(); assert release.wait(30)
        return failed()
    monkeypatch.setattr(module, 'build_small_signal_analysis', late_failure)
    window.run_waveforms(False); assert entered.wait(10)
    view.vbus.setValue(390); release.set(); _wait_workers(window, app)
    assert not issues and view.bundle is None
    monkeypatch.setattr(module, 'build_small_signal_analysis', original)
    _run_fast(window, app)
    window.fields['resonant_frequency_hz'].setValue(110)
    assert view.bundle is None and not view.export_button.isEnabled()
    _run_fast(window, app)
    window.topology.setCurrentIndex(1)
    assert view.bundle is None
    window.topology.setCurrentIndex(0)
    _run_fast(window, app)
    entered.clear(); release.clear()
    def delayed(*args, **kwargs):
        entered.set(); assert release.wait(30)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'build_small_signal_analysis', delayed)
    window.run_waveforms(False); assert entered.wait(10)
    window.close(); window.show(); release.set(); _wait_workers(window, app)
    assert view.bundle is None and not view.export_button.isEnabled()
    monkeypatch.setattr(module, 'build_small_signal_analysis', original)
    _run_fast(window, app)
    assert not issues


def test_full_precision_input_session_recomputes_without_rounding(app, monkeypatch, window_factory, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    from llc_design.core.config import (
        create_waveform_session,
        save_project,
        waveform_request_identity,
    )
    window, issues = window_factory()
    point = {'vbus_v': 380.1234567890123, 'load_fraction': .7345678901234567}
    spec = window._spec_from_widgets().clone(
        vbus_min_normal_v=360.1234567890123, vout_v=53.123456789,
        resonant_frequency_hz=100000.123456789,
        output_cap_esr_ohm=.002123456789)
    state = create_waveform_session(spec, point, 'fast', 'cr')
    path = save_project(spec, tmp_path/'precise.json', waveform_session=state)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(path), 'JSON'))
    window.load_json(); _wait_workers(window, app)
    view = window.waveform_view
    assert view.work_point() == point
    assert window._spec_from_widgets() == spec
    assert view.bundle.metadata['request_identity'] == waveform_request_identity(
        {**state, 'plant_inputs': json.loads(view.bundle.metadata['plant_inputs'])})
    assert window.small_signal_analysis.steady_state.inputs.bus_voltage_v == point['vbus_v']
    view.vbus.setValue(390.1234567890123)
    assert view.work_point()['vbus_v'] == 390.1234567890123
    assert view.work_point()['load_fraction'] == point['load_fraction']
    assert window.small_signal_analysis is None
    shown = window.fields['vout_v'].value()
    window.fields['vout_v'].setValue(54)
    window.fields['vout_v'].setValue(shown)
    assert window._spec_from_widgets().vout_v == shown
    assert window._spec_from_widgets().vout_v != spec.vout_v
    assert not issues


def test_modal_save_and_reopen_do_not_overwrite_newer_inputs(app, monkeypatch, window_factory, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    from llc_design.core.config import save_project
    window, issues = window_factory(); view = window.waveform_view
    target = tmp_path/'must-not-save.json'
    def edit_save(*a, **kw):
        view.vbus.setValue(390)
        return str(target), 'JSON'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', edit_save)
    window.save_json()
    assert not target.exists()
    source = save_project(window.spec, tmp_path/'old.json')
    def edit_open(*a, **kw):
        view.vbus.setValue(380)
        return str(source), 'JSON'
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', edit_open)
    window.load_json()
    assert view.work_point()['vbus_v'] == 380
    assert not window._active_workers
    assert not issues


def test_repeated_actual_window_destruction_releases_qt_children():
    import subprocess
    import sys
    program = '''
import gc,time
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid
from llc_design.gui.main_window import LLCMainWindow
app=QApplication([])
LLCMainWindow._auto_check_update=lambda self:None
for index in range(4):
    win=LLCMainWindow(); win.show(); app.processEvents()
    win.run_waveforms(False)
    deadline=time.monotonic()+50
    while win._active_workers and time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)
    assert not win._active_workers
    assert win.waveform_view.bundle is not None
    graphics=win.waveform_view.graphics
    win.close();win.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    app.processEvents();gc.collect()
    assert not isValid(win) and not isValid(graphics)
    assert not [widget for widget in app.topLevelWidgets() if widget.isVisible()]
app.shutdown()
'''
    result = subprocess.run([sys.executable, '-c', program], text=True,
                            capture_output=True, timeout=120, check=False,
                            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Traceback' not in result.stderr, result.stderr


def test_real_td_damping_changes_identity_and_frozen_inputs_replay_exactly(app, monkeypatch, window_factory, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    from llc_design.analysis import LLCAnalysisRequest, TimeDomainConfig
    from llc_design.analysis.time_domain import solve_time_domain
    from llc_design.models.system import LLCSystemAnalyzer
    window, issues = window_factory()
    spec = window._spec_from_widgets()
    analyzer = LLCSystemAnalyzer()
    baseline = analyzer.analyze(spec, work_points=[(400., 1.)])
    window._design_ready(baseline)
    first = _run_fast(window, app)
    first_r = window.small_signal_analysis.parameters.series_resistance_ohm
    td = solve_time_domain(LLCAnalysisRequest(spec, vbus_v=400., load_fraction=1.),
                           TimeDomainConfig(samples_per_cycle=512, output_cycles=2))
    updated = analyzer.with_time_domain_losses(baseline, [td])
    window._design_ready(updated)
    assert window.waveform_view.bundle is None
    second = _run_fast(window, app)
    second_r = window.small_signal_analysis.parameters.series_resistance_ohm
    assert second_r != pytest.approx(first_r, rel=1e-5)
    assert second.metadata['request_identity'] != first.metadata['request_identity']
    assert not np.array_equal(first.signals['i_resonant'].values, second.signals['i_resonant'].values)
    path = tmp_path/'td-derived-waveform.json'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(path), 'JSON'))
    window.save_json()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved['waveform_session']['plant_inputs']['series_resistance_ohm'] == second_r
    assert 'TIME_DOMAIN' in saved['waveform_session']['plant_inputs']['source']
    window._design_ready(baseline)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(path), 'JSON'))
    window.load_json(); _wait_workers(window, app)
    replay = window.waveform_view.bundle
    assert replay.metadata['request_identity'] == second.metadata['request_identity']
    assert window.small_signal_analysis.parameters.series_resistance_ohm == second_r
    np.testing.assert_array_equal(replay.signals['i_resonant'].values, second.signals['i_resonant'].values)
    assert window.system_analysis is None
    assert not window.model_comparison_view.loss_report_button.isEnabled()
    assert not issues



def test_component_traces_fit_and_large_groups_are_scrollable(app, window_factory):
    from PySide6.QtWidgets import QScrollArea
    window, issues = window_factory()
    window.resize(1360, 900)
    _run_fast(window, app)
    view = window.waveform_view
    scroll = view.findChild(QScrollArea)
    view.select_component('q1')
    for _ in range(4):
        app.processEvents()
    assert len(view._plots) == 2
    assert view._plots[-1].sceneBoundingRect().bottom() <= view.graphics.height() + 1
    assert view.graphics.height() <= scroll.viewport().height() + 1
    view.group_combo.setCurrentIndex(5)
    for _ in range(4):
        app.processEvents()
    assert len(view._plots) == 12
    assert scroll.verticalScrollBar().isVisible()
    assert scroll.verticalScrollBar().maximum() > 1000
    assert view._plots[-1].sceneBoundingRect().bottom() <= view.graphics.height() + 1
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    assert scroll.verticalScrollBar().value() == scroll.verticalScrollBar().maximum()
    view.select_component('sr4')
    for _ in range(4):
        app.processEvents()
    assert len(view._plots) == 2
    assert view.graphics.height() <= scroll.viewport().height() + 1
    assert view._plots[-1].sceneBoundingRect().bottom() <= view.graphics.height() + 1
    assert not issues


@pytest.mark.parametrize("topology", list(PrimaryTopology))
def test_component_known_losses_constraints_and_parallel_current_basis(app, window_factory, topology):
    window, issues = window_factory(topology)
    bundle = _run_fast(window, app)
    evidence = window.waveform_view.component_context
    spec = window._spec_from_widgets()
    for component, current_key, resistance in (
            ('cr', 'i_resonant', spec.resonant_cap_esr_ohm),
            ('co', 'i_output_cap', spec.output_cap_esr_ohm)):
        values = bundle.signals[current_key].values
        independent_mean_square = math.fsum(float(value)**2 for value in values) / len(values)
        assert evidence[component]['esr_loss_w'] == pytest.approx(independent_mean_square * resistance)
        assert evidence[component]['source_identity'] == bundle.metadata['request_identity']
        window.waveform_view.select_component(component)
        assert '损耗=' in window.waveform_view.component_label.text()
    assert evidence['cr']['derated_voltage_limit_v'] == spec.resonant_cap_voltage_rating_v * spec.capacitor_voltage_derating
    assert evidence['cr']['current_rms_limit_a'] == spec.resonant_cap_rms_current_rating_a
    bias = 0.5 * window.waveform_view.work_point()['vbus_v'] if topology == PrimaryTopology.HALF_BRIDGE else 0.0
    absolute_peak = max(abs(float(value) + bias) for value in bundle.signals['v_resonant_cap'].values)
    assert evidence['cr']['rating_evaluation_dc_bias_v'] == bias
    assert evidence['cr']['rating_evaluation_peak_v'] == pytest.approx(absolute_peak)
    raw_peak = max(abs(float(value)) for value in bundle.signals['v_resonant_cap'].values)
    assert evidence['cr']['voltage_within_model_limit'] == (raw_peak <= evidence['cr']['derated_voltage_limit_v'])
    assert evidence['cr']['single_ended_variant_voltage_within_limit'] == (absolute_peak <= evidence['cr']['derated_voltage_limit_v'])
    if topology == PrimaryTopology.HALF_BRIDGE:
        from dataclasses import replace

        from llc_design.models.waveform_evidence import component_waveform_evidence
        assert absolute_peak > raw_peak
        between_limit = (raw_peak + absolute_peak) / 2
        changed_spec = replace(spec, resonant_cap_voltage_rating_v=between_limit / spec.capacitor_voltage_derating)
        changed = component_waveform_evidence(bundle, changed_spec)['cr']
        assert changed['voltage_within_model_limit'] is True
        assert changed['single_ended_variant_voltage_within_limit'] is False
        assert changed['modeled_return_connection'] == 'B = Vbus/2 ideal midpoint'
        assert 'PGND' in changed['single_ended_variant_return_connection']
    assert evidence['co']['ripple_voltage_signal'] == 'v_output'
    assert evidence['co']['terminal_ripple_vpp'] == pytest.approx(float(np.ptp(bundle.signals['v_output'].values)))
    assert evidence['co']['ripple_limit_vpp'] == spec.output_ripple_limit_vpp
    assert evidence['primary']['flux_density_limit_t'] == spec.transformer_max_b_t
    assert evidence['lr']['flux_density_limit_t'] == spec.resonant_inductor_max_b_t
    assert evidence['primary']['peak_flux_density_t'] == float(np.max(np.abs(bundle.signals['b_transformer'].values)))
    assert evidence['sr1']['parallel_devices_per_position'] == spec.sr_parallel_devices_per_position
    assert evidence['sr1']['per_device_current_rms_a'] == pytest.approx(
        bundle.signals['ids_sr1'].statistics.rms / spec.sr_parallel_devices_per_position)
    assert 'whole switch position' in evidence['sr1']['current_basis']
    assert not issues


def test_closed_graphics_invalidate_and_repeated_owner_close(app, window_factory):
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid
    window, issues = window_factory()
    _run_fast(window, app)
    view = window.waveform_view
    graphics = view.graphics
    graphics.close()
    assert isValid(graphics) and graphics.closed
    assert not isValid(graphics.ci)
    view.invalidate()
    view.invalidate()
    assert view.bundle is None and not view._plots
    assert view.statistics.rowCount() == 0 and not view.export_button.isEnabled()
    window.close()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    assert not isValid(window) and not isValid(graphics)
    assert not issues


def test_waveform_export_reports_real_file_error(app, window_factory, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog
    window, issues = window_factory()
    accepted = _run_fast(window, app)
    blocked = tmp_path / 'not-a-directory'
    blocked.write_text('existing user file', encoding="utf-8")
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **kw: str(blocked))
    window.export_current_waveforms()
    assert len(issues) == 1
    assert blocked.read_text(encoding="utf-8") == 'existing user file'
    assert window.waveform_view.bundle is accepted
