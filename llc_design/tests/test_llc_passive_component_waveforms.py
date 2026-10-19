"""Actual terminal consumers, source conservation and replay for T21/T20."""
from __future__ import annotations

import json

import numpy as np
import pytest

from llc_design.core.spec import LLCDesignSpec, PrimaryTopology
from llc_design.tests import test_llc_component_workflows as workflows

app = workflows.app
window_factory = workflows.window_factory
_run_fast = workflows._run_fast
_wait_workers = workflows._wait_workers


def _terminal_invariants(bundle, topology):
    s = {key: value.values for key, value in bundle.signals.items()}
    np.testing.assert_array_equal(s['v_bus'], np.full_like(s['i_resonant'], bundle.metadata['vbus_v']))
    assert bundle.signals['v_bus'].statistics.ac_rms == 0
    assert bundle.signals['v_bus'].statistics.frequency_hz == 0
    np.testing.assert_allclose(s['v_series_resistance'], bundle.metadata['series_resistance_ohm'] * s['i_resonant'], atol=1e-12)
    np.testing.assert_allclose(s['v_bridge'], s['v_resonant_inductor'] + s['v_resonant_cap']
                               + s['v_transformer_primary'] + s['v_series_resistance'], atol=1e-10)
    np.testing.assert_allclose(s['v_output_cap_esr'], s['v_output'] - s['v_output_cap_internal'], atol=1e-12)
    np.testing.assert_allclose(s['v_output_cap_esr'], bundle.metadata['output_cap_esr_ohm'] * s['i_output_cap'], atol=1e-10)
    # Independent terminal power checks, not a second summed loss model.
    assert np.mean(s['v_output_cap_esr'] * s['i_output_cap']) == pytest.approx(
        bundle.metadata['output_cap_esr_ohm'] * np.mean(s['i_output_cap']**2), abs=1e-10)
    assert np.mean(s['v_series_resistance'] * s['i_resonant']) == pytest.approx(
        bundle.metadata['series_resistance_ohm'] * np.mean(s['i_resonant']**2), abs=1e-10)
    if topology == PrimaryTopology.FULL_BRIDGE:
        np.testing.assert_allclose(s['v_bus'] * s['i_bus'], s['v_bridge'] * s['i_resonant'], atol=1e-10)
        assert np.min(s['i_bus']) < 0  # resonant circulating energy returns to the DC source
    else:
        assert 'i_bus' not in s
        assert bundle.metadata['bus_current_status'].startswith('UNAVAILABLE')


@pytest.mark.parametrize('topology', list(PrimaryTopology))
def test_actual_passive_click_save_restore_and_report(app, monkeypatch, window_factory, topology, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog, QScrollArea

    from llc_design.core.config import waveform_request_identity
    from llc_design.dynamics import export as exporter

    window, issues = window_factory(topology)
    view = window.waveform_view
    accepted = _run_fast(window, app)
    _terminal_invariants(accepted, topology)
    expected = {
        'coesr': ('v_output_cap_esr', 'i_output_cap'),
        'rseries': ('v_series_resistance', 'i_resonant'),
        'bus': ('v_bus', 'i_bus') if topology == PrimaryTopology.FULL_BRIDGE else ('v_bus',),
    }
    # Keep actual selected-component plotting, CSV and report generation; skip
    # repeated unrelated plots already covered by the established export tests.
    monkeypatch.setattr(exporter, 'DEFAULT_GROUPS', ())
    for component, keys in expected.items():
        window.tabs.setCurrentIndex(0); app.processEvents()
        item = window.topology_view.blocks[component]
        point = window.topology_view.mapFromScene(item.sceneBoundingRect().center())
        QTest.mouseClick(window.topology_view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        for _ in range(4):
            app.processEvents()
        assert window.tabs.currentWidget() is view
        assert view.selected_component == component
        assert view._selected_keys() == keys
        assert view.bundle is accepted
        assert view.statistics.rowCount() == len(keys)
        assert view._plots[-1].sceneBoundingRect().bottom() <= view.graphics.height() + 1
        assert view.graphics.height() <= max(view.findChild(QScrollArea).viewport().height(),
                                             view.graphics.minimumHeight()) + 1
        for row, key in enumerate(keys):
            signal = accepted.signals[key]
            np.testing.assert_array_equal(view._plots[row].listDataItems()[0].yData, signal.values)
            for column, value in ((3, np.mean(signal.values)), (4, np.sqrt(np.mean(signal.values**2))),
                                  (6, np.min(signal.values)), (7, np.max(signal.values))):
                assert float(view.statistics.item(row, column).text()) == pytest.approx(value, rel=1e-7, abs=1e-7)
        context = view.component_context[component]
        assert context['source_identity'] == accepted.metadata['request_identity']
        if component == 'bus' and topology == PrimaryTopology.HALF_BRIDGE:
            assert '电流未提供' in view.component_label.text()
            assert context['current_available'] is False
        assert window.grab().save(str(tmp_path / f'{component}-actual-click.png'))
        # Display-only selection is absent from the electrical request identity.
        assert waveform_request_identity(window._waveform_session()) == accepted.metadata['request_identity']
        path = tmp_path / f'{component}.json'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, path=path, **kw: (str(path), 'JSON'))
        window.save_json()
        saved = json.loads(path.read_text(encoding='utf-8'))
        assert saved['waveform_session']['component'] == component
        view.select_component('q1')
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, path=path, **kw: (str(path), 'JSON'))
        window.load_json(); _wait_workers(window, app)
        replay = view.bundle
        assert replay is not accepted
        assert view.selected_component == component
        assert view._selected_keys() == keys
        assert replay.metadata['request_identity'] == accepted.metadata['request_identity']
        for key in accepted.signals:
            np.testing.assert_array_equal(replay.signals[key].values, accepted.signals[key].values)
        accepted = replay
        directory = tmp_path / component
        monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, directory=directory, **kw: str(directory))
        QTest.mouseClick(view.export_button, Qt.MouseButton.LeftButton)
        exported = json.loads((directory / 'waveform_metadata.json').read_text(encoding='utf-8'))
        assert exported['context']['selected_component'] == component
        assert exported['context']['selected_signals'] == list(keys)
        assert exported['metadata']['request_identity'] == accepted.metadata['request_identity']
        assert exported['context']['component_evidence'][component]['source_identity'] == accepted.metadata['request_identity']
        assert (directory / 'selected_component.png').stat().st_size > 1000
        report = (directory / 'LLC_waveforms.md').read_text(encoding='utf-8')
        assert 'selected_component.png' in report and accepted.metadata['request_identity'] in report
        assert all(key in (directory / 'llc_waveforms.csv').read_text(encoding='utf-8').splitlines()[0] for key in keys)
    assert not issues


@pytest.mark.parametrize('topology', list(PrimaryTopology))
@pytest.mark.parametrize('solver', ['fha', 'harmonic', 'detailed'])
def test_original_solvers_terminal_kcl_and_energy(topology, solver):
    from llc_design.analysis import LLCAnalysisRequest
    from llc_design.analysis.fha import solve_fha
    from llc_design.analysis.harmonic_balance import (
        HarmonicBalanceConfig,
        solve_harmonic_balance,
    )
    from llc_design.analysis.time_domain import TimeDomainConfig, solve_time_domain

    spec = LLCDesignSpec(primary_topology=topology, secondary_turns=8 if topology == PrimaryTopology.HALF_BRIDGE else 4)
    request = LLCAnalysisRequest(spec, frequency_hz=100000, samples_per_cycle=256, waveform_cycles=2)
    if solver == 'fha':
        result = solve_fha(request)
    elif solver == 'harmonic':
        result = solve_harmonic_balance(request, HarmonicBalanceConfig(max_harmonic=5, minimum_harmonic=5,
            adaptive_harmonics=False, samples_per_cycle=256, zero_crossing_samples=256,
            series_resistance_ohm=.123))
    else:
        result = solve_time_domain(request, TimeDomainConfig(samples_per_cycle=1024, output_cycles=2,
            minimum_settling_cycles=5, series_resistance_ohm=.123, trim_frequency_to_output=False,
            strict_convergence=True))
    assert result.convergence.converged
    bundle = result.waveform
    _terminal_invariants(bundle, topology)
    s = {key: value.values for key, value in bundle.signals.items()}
    np.testing.assert_allclose(s['i_rectified'], s['i_load_output'] + s['i_output_cap'], atol=1e-10)
    # Rectified-node power partitions into load, ideal C and its existing ESR.
    np.testing.assert_allclose(s['v_output'] * s['i_rectified'],
        s['v_output'] * s['i_load_output'] + s['v_output_cap_internal'] * s['i_output_cap']
        + s['v_output_cap_esr'] * s['i_output_cap'], atol=1e-9)
    assert bundle.metadata['output_node_kcl_residual_peak_a'] < 1e-10
    assert bundle.metadata['series_resistance_ohm'] == (0.0 if solver == 'fha' else .123)


def test_fast_output_network_approximation_is_explicit(app, window_factory):
    window, issues = window_factory()
    bundle = _run_fast(window, app)
    s = {key: value.values for key, value in bundle.signals.items()}
    rload = window.small_signal_analysis.parameters.load_resistance_ohm
    residual = s['i_rectified'] - s['i_load_output'] - s['i_output_cap']
    np.testing.assert_allclose(residual, (bundle.metadata['output_voltage_v'] - s['v_output']) / rload, atol=1e-12)
    assert np.max(np.abs(residual)) > 1e-6
    assert bundle.metadata['output_node_kcl_residual_peak_a'] == pytest.approx(np.max(np.abs(residual)))
    assert any('KCL residual' in warning for warning in bundle.warnings)
    assert not issues


def test_terminal_projection_preserves_source_arrays_and_known_zero_resistances():
    from dataclasses import replace

    from llc_design.analysis import LLCAnalysisRequest
    from llc_design.analysis.fha import solve_fha
    from llc_design.dynamics.waveforms import with_component_terminal_signals

    spec = LLCDesignSpec(output_cap_esr_ohm=0)
    solved = solve_fha(LLCAnalysisRequest(spec, frequency_hz=100000, samples_per_cycle=128)).waveform
    source = replace(solved, signals={key: signal for key, signal in solved.signals.items()
        if key not in {'v_bus', 'i_bus', 'v_series_resistance', 'v_output_cap_esr'}})
    projected = with_component_terminal_signals(source, bus_voltage_v=400, primary_topology='FULL_BRIDGE',
        series_resistance_ohm=0, output_cap_esr_ohm=0)
    for key, signal in source.signals.items():
        assert projected.signals[key] is signal
    assert not np.any(projected.signals['v_series_resistance'].values)
    assert not np.any(projected.signals['v_output_cap_esr'].values)
    assert projected.metadata['series_resistance_ohm'] == 0
    assert projected.metadata['output_cap_esr_ohm'] == 0


def test_half_bridge_detailed_solution_checks_continuous_orbit_and_strict_residual(monkeypatch):
    from types import SimpleNamespace

    from llc_design.analysis import LLCAnalysisRequest
    from llc_design.analysis.time_domain import TimeDomainConfig, solve_time_domain
    from llc_design.dynamics import switched
    from llc_design.tests.test_switched_integration_accuracy import _independent_cycle

    spec = LLCDesignSpec(primary_topology=PrimaryTopology.HALF_BRIDGE, secondary_turns=8)
    request = LLCAnalysisRequest(spec, frequency_hz=100000, samples_per_cycle=256)
    config = TimeDomainConfig(samples_per_cycle=256, output_cycles=2, minimum_settling_cycles=5,
        series_resistance_ohm=.123, trim_frequency_to_output=False, strict_convergence=True)
    # The old explicit RK grid failed here. Output density must no longer force
    # a physical failure: independently integrate the same unchanged circuit.
    result = solve_time_domain(request, config)
    assert result.convergence.converged
    keys = ('i_resonant', 'v_resonant_cap', 'i_magnetizing', 'v_output_cap_internal')
    states = np.stack([result.waveform.signal(key).values for key in keys], axis=-1)
    scales = np.maximum(np.max(np.abs(states), axis=0), 1.)
    end, independent, peak = _independent_cycle(result.native_result.parameters,
        result.waveform.switching_frequency_hz, states[0], 256)
    np.testing.assert_allclose(states[:256] / scales, independent / scales, atol=2e-8, rtol=0)
    np.testing.assert_allclose(end[:4] / scales, states[0] / scales, atol=2e-8, rtol=0)
    np.testing.assert_allclose(states[:256] / scales, states[256:] / scales, atol=2e-8, rtol=0)
    assert result.metrics.magnetizing_current_peak_a == pytest.approx(peak, abs=2e-7)
    _terminal_invariants(result.waveform, PrimaryTopology.HALF_BRIDGE)

    def false_success(_residual, initial, **_kwargs):
        bad = initial.copy()
        bad[2] += .2
        return SimpleNamespace(x=bad, success=True)

    monkeypatch.setattr(switched, 'root', false_success)
    # Preserve strict rejection for actual nonperiodicity, even if a numerical
    # optimizer incorrectly claims success; the residual gate is unchanged.
    with pytest.raises(RuntimeError, match='periodic solution did not converge'):
        solve_time_domain(request, config)
