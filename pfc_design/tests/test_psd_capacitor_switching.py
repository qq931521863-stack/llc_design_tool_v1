"""Independent switching-substep evidence for A04 capacitor second moments.

The prescribed trajectories are accepted averaged inputs, not claimed hardware
measurements. The oracle integrates KCL on actual on/off segments and never
calls the production second-moment formula.
"""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from pfc_design.engineering import TTPLDesignSpec, analyze_ttpl_design
from pfc_design.engineering.cap_thermal import (
    CapacitorUnitSpec,
    design_bus_capacitor_bank,
)
from pfc_design.engineering.device_loss import compare_ttpl_devices
from pfc_design.tests.test_psd_consumer_independent import _device, _line


def _source():
    line = _line(asymmetric=True)
    # A real constant output load, with the accepted averaged capacitor KCL.
    capacitor = (1 - line.duty) * line.il_avg_a - 3.0
    return replace(line, capacitor_current_a=capacitor)


def _switching_observer(line, mask=None):
    """Midpoint quadrature of iC = i_rectifier - Iload on two PWM segments."""
    phase = (np.arange(4096) + 0.5) / 4096
    means, squares, variances = [], [], []
    if mask is None:
        mask = np.ones(len(line.time_s), dtype=bool)
    for mean, delta, duty, ic, valid in zip(line.il_avg_a, line.il_ripple_pp_a,
                                           line.duty, line.capacitor_current_a, mask):
        load = (1 - duty) * mean - ic
        on_cap = np.full_like(phase, -load)
        off_cap = mean + delta / 2 - delta * phase - load
        mean_cap = duty * np.mean(on_cap) + (1 - duty) * np.mean(off_cap)
        square = duty * np.mean(on_cap**2) + (1 - duty) * np.mean(off_cap**2)
        means.append(mean_cap)
        squares.append(square if valid else ic**2)
        variances.append(square - mean_cap**2 if valid else 0.0)
    return np.asarray(means), np.asarray(squares), np.asarray(variances)


def test_bank_heating_uses_independent_pwm_substep_current():
    line = _source()
    _, second, _ = _switching_observer(line)
    bank = design_bus_capacitor_bank(analyze_ttpl_design(TTPLDesignSpec()),
                                   CapacitorUnitSpec(), line_cycle=line)
    assert bank.bank_ripple_current_rms_a == pytest.approx(np.sqrt(np.mean(second)), rel=1e-8)
    assert bank.bank_esr_loss_w == pytest.approx(np.mean(second) * bank.bank_esr_ohm, rel=1e-8)
    assert bank.estimated_hotspot_c == pytest.approx(
        bank.config.ambient_temperature_c + bank.per_cap_esr_loss_w * bank.unit.thermal_resistance_k_per_w)


def test_decomposition_preserves_mean_and_full_cycle_weights():
    from pfc_design.engineering.capacitor_ripple import analyze_capacitor_ripple
    line = _source()
    result = analyze_capacitor_ripple(line)
    mean, square, variance = _switching_observer(line)
    assert mean == pytest.approx(line.capacitor_current_a, abs=1e-12)
    assert result.complete
    assert result.unresolved_samples == 0
    assert result.averaged_current_rms_a == pytest.approx(np.sqrt(np.mean(mean**2)))
    assert result.known_switching_variance_a2 == pytest.approx(variance, rel=1e-7, abs=1e-10)
    assert result.known_model_current_rms_a == pytest.approx(np.sqrt(np.mean(square)), rel=1e-8)
    assert result.total_current_rms_a == result.known_model_current_rms_a
    assert np.mean(result.pwm_pulsation_variance_a2 + result.inductor_ripple_variance_a2) == pytest.approx(np.mean(variance), rel=1e-8)
    assert result.model == 'accepted_cycle_local_ccm_pwm_second_moment'


def test_pwm_pulsation_exists_even_without_inductor_ripple():
    from pfc_design.engineering.capacitor_ripple import analyze_capacitor_ripple
    line = _source()
    line = replace(line, il_ripple_pp_a=np.zeros_like(line.il_avg_a))
    result = analyze_capacitor_ripple(line)
    _, square, _ = _switching_observer(line)
    assert result.known_model_current_rms_a > result.averaged_current_rms_a
    assert result.total_current_rms_a == pytest.approx(np.sqrt(np.mean(square)))
    assert np.all(result.inductor_ripple_variance_a2 == 0)


def test_unknown_slices_are_explicit_and_never_renormalized():
    from pfc_design.engineering.capacitor_ripple import analyze_capacitor_ripple
    line = _source()
    current = line.il_avg_a.copy()
    current[0:30] = 0.0
    line = replace(line, il_avg_a=current)
    # A valid-looking positive current during an actual zero-crossing state
    # must remain unresolved too: no invented gate/body-diode policy.
    zc = np.zeros(len(current)); zc[80:100] = 1
    line.waveforms.signals = {'zero_cross_active': zc}
    result = analyze_capacitor_ripple(line)
    mask = (current >= line.il_ripple_pp_a / 2) & (zc == 0)
    _, square, variance = _switching_observer(line, mask)
    assert result.valid_sample_mask.tolist() == mask.tolist()
    assert result.unresolved_samples == np.count_nonzero(~mask)
    assert not result.complete
    assert result.total_current_rms_a is None
    assert result.known_model_current_rms_a == pytest.approx(np.sqrt(np.mean(square)), rel=1e-8)
    assert np.mean(result.known_switching_variance_a2) == pytest.approx(np.mean(variance), rel=1e-8)
    report = result.as_dict()
    assert report['status'] == 'PARTIAL'
    assert report['total_current_rms_a'] is None
    assert report['unresolved_sample_indices'] == np.flatnonzero(~mask).tolist()
    assert 'not a bound' in ' '.join(report['limitations'])
    bank = design_bus_capacitor_bank(analyze_ttpl_design(TTPLDesignSpec()),
                                   CapacitorUnitSpec(), line_cycle=line)
    assert bank.bank_ripple_current_rms_a == pytest.approx(result.known_model_current_rms_a)
    assert bank.ripple_current_ok is None
    assert bank.estimated_hotspot_c is None
    assert bank.estimated_life_h is None
    assert bank.partial_model_hotspot_c > bank.config.ambient_temperature_c
    assert bank.capacitor_ripple is not None and not bank.capacitor_ripple.complete


def test_all_consumers_preserve_averaged_metric_and_share_reconstruction():
    from pfc_design.engineering.capacitor_ripple import analyze_capacitor_ripple
    line = _source()
    design = analyze_ttpl_design(TTPLDesignSpec())
    baseline = line.capacitor_current_a.copy()
    result = analyze_capacitor_ripple(line)
    comparison = compare_ttpl_devices(design, SimpleNamespace(all=[_device()]), line_cycle=line)
    bank = design_bus_capacitor_bank(design, CapacitorUnitSpec(), line_cycle=line)
    assert np.array_equal(line.capacitor_current_a, baseline)
    assert comparison.capacitor_current_rms_a == result.averaged_current_rms_a
    assert comparison.capacitor_ripple.as_dict() == result.as_dict()
    assert comparison.as_dict()['capacitor_ripple'] == result.as_dict()
    assert line.as_dict()['capacitor_ripple'] == result.as_dict()
    assert bank.capacitor_ripple.as_dict() == result.as_dict()
    assert bank.bank_averaged_esr_loss_w + bank.bank_known_switching_esr_loss_w == pytest.approx(bank.bank_esr_loss_w)
    assert bank.bank_known_switching_esr_loss_w > 0


@pytest.mark.parametrize('bad', ['unaccepted', 'identity', 'frequency', 'nonuniform', 'incomplete', 'nan', 'negative', 'zero_cross_shape'])
def test_reconstruction_rejects_invalid_source_identity(bad):
    from pfc_design.engineering.capacitor_ripple import analyze_capacitor_ripple
    from pfc_design.engineering.pfc_v3 import ConvergenceStatus
    line = _source()
    if bad == 'unaccepted':
        line = replace(line, convergence=replace(line.convergence, status=ConvergenceStatus.NOT_CONVERGED))
    elif bad == 'identity':
        line = replace(line, config=None)
    elif bad == 'frequency':
        line = replace(line, fsw_hz=2 * line.fsw_hz)
    elif bad == 'nonuniform':
        t = line.time_s.copy(); t[1] *= 1.1
        line = replace(line, time_s=t)
    elif bad == 'incomplete':
        line = replace(line, time_s=line.time_s / 2)
    elif bad == 'nan':
        line = replace(line, il_ripple_pp_a=np.full_like(line.il_avg_a, np.nan))
    elif bad == 'negative':
        line = replace(line, il_ripple_pp_a=-line.il_ripple_pp_a)
    else:
        line.waveforms.signals = {'zero_cross_active': np.zeros(5)}
    with pytest.raises(ValueError):
        analyze_capacitor_ripple(line)


def test_changed_current_changes_moments_without_cached_result():
    from pfc_design.engineering.capacitor_ripple import analyze_capacitor_ripple
    line = _source()
    first = analyze_capacitor_ripple(line)
    changed = replace(line, il_ripple_pp_a=line.il_ripple_pp_a / 2)
    second = analyze_capacitor_ripple(changed)
    assert second.averaged_current_rms_a == first.averaged_current_rms_a
    assert second.pwm_pulsation_variance_a2 == pytest.approx(first.pwm_pulsation_variance_a2)
    assert second.inductor_ripple_variance_a2 == pytest.approx(first.inductor_ripple_variance_a2 / 4)
    assert second.known_model_current_rms_a < first.known_model_current_rms_a


def test_gui_capacitor_report_tracks_source_unknowns_and_input_invalidation(monkeypatch, tmp_path):
    import json
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QFileDialog

    from pfc_design.engineering import PFCDeviceDatabase
    from pfc_design.gui.cap_thermal_view import TTPLCapacitorThermalView
    app = QApplication.instance() or QApplication([])
    line = _source()
    view = TTPLCapacitorThermalView(analyze_ttpl_design(TTPLDesignSpec()),
                                  PFCDeviceDatabase(include_user=False))
    try:
        view.set_line_cycle(line)
        view.source.setCurrentIndex(view.source.findData('closed_loop'))
        assert view.cap_result.line_cycle is line
        assert 'local CCM PWM reconstruction' in view.cap_summary.toPlainText()
        path = tmp_path / 'capacitor.json'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(path), 'JSON'))
        view._export_capacitor()
        report = json.loads(path.read_text(encoding="utf-8"))
        assert report == view.cap_result.as_dict()
        assert report['status'] == 'COMPLETE_IN_STATED_MODEL'
        assert report['bank_known_switching_esr_loss_w'] > 0
        preserved = path.read_bytes()
        view.cap_esr.setValue(view.cap_esr.value() * 1.2)
        assert view.cap_result is None and not view.cap_export_button.isEnabled()
        view._export_capacitor()
        assert path.read_bytes() == preserved
        view.run_capacitor()
        # Changed source becomes partial and does not silently keep old life.
        partial = replace(line, il_avg_a=np.zeros_like(line.il_avg_a))
        view.set_line_cycle(partial)
        assert 'PARTIAL; complete RMS unknown' in view.cap_summary.toPlainText()
        assert 'Ripple derating     : UNKNOWN' in view.cap_summary.toPlainText()
        assert view.cap_result.estimated_life_h is None
        view._export_capacitor()
        assert json.loads(path.read_text(encoding="utf-8"))['estimated_hotspot_c'] is None
        # A modal input edit cancels the old click, including a fresh calculation.
        stale = tmp_path / 'stale.json'
        def choose_after_edit(*args):
            view.cap_esr.setValue(view.cap_esr.value() + 1)
            view.run_capacitor()
            return str(stale), 'JSON'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose_after_edit)
        view._export_capacitor()
        assert not stale.exists()
        view.set_line_cycle(None)
        assert view.cap_result is None and not view.cap_export_button.isEnabled()
    finally:
        view.close(); view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def test_device_report_modal_source_change_does_not_export_replacement(monkeypatch, tmp_path):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QFileDialog

    from pfc_design.engineering import PFCDeviceDatabase
    from pfc_design.gui.pfc_device_view import TTPLDeviceLossView
    app = QApplication.instance() or QApplication([])
    view = TTPLDeviceLossView(analyze_ttpl_design(TTPLDesignSpec()),
                             PFCDeviceDatabase(include_user=False))
    try:
        line = _source()
        view.set_line_cycle(line)
        view.source.setCurrentIndex(view.source.findData('closed_loop'))
        assert 'Known PWM ripple RMS' in view.rollup_text.toPlainText()
        path = tmp_path / 'must-not-exist.json'
        def change_source(*args):
            view.set_line_cycle(replace(line, il_ripple_pp_a=line.il_ripple_pp_a / 2))
            return str(path), 'JSON'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', change_source)
        view._export_comparison()
        assert not path.exists()
        assert view._last_comparison.capacitor_ripple.complete
    finally:
        view.close(); view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def test_unknown_convergence_report_does_not_create_accepted_ripple():
    from pfc_design.engineering.pfc_v3 import ConvergenceStatus
    line = _source()
    line = replace(line, convergence=replace(line.convergence, status=ConvergenceStatus.UNKNOWN))
    assert line.as_dict()['capacitor_ripple'] is None


def test_extreme_finite_inputs_cannot_export_infinite_second_moments():
    from pfc_design.engineering.capacitor_ripple import analyze_capacitor_ripple
    line = _source()
    line = replace(line, il_avg_a=np.full_like(line.il_avg_a, 1e200))
    with pytest.raises(ValueError, match='finite numeric range'):
        analyze_capacitor_ripple(line)
