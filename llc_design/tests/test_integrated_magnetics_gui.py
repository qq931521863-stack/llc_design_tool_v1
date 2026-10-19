"""Real Qt workers and project consumers for integrated leakage magnetics."""
import os
import time

import pytest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
qt=pytest.importorskip('PySide6.QtWidgets')

@pytest.fixture(scope='module')
def app():
    return qt.QApplication.instance() or qt.QApplication([])


def drain(app,window):
    end=time.monotonic()+45
    while window._active_workers and time.monotonic()<end:
        app.processEvents();time.sleep(.005)
    app.processEvents()
    assert not window._active_workers


def test_actual_gui_run_save_load_invalidate_and_transformer_apply(app,monkeypatch,tmp_path):
    import llc_design.gui.main_window as module
    from llc_design.core.config import load_project
    from llc_design.core.spec import IntegratedInductanceSource, MagneticsMode
    from llc_design.gui.main_window import LLCMainWindow
    monkeypatch.setattr(module,'check_for_updates',lambda *args,**kwargs:None)
    issues=[]
    monkeypatch.setattr(module,'show_operation_issue',lambda *a,**kw:issues.append(str(a)))
    window=LLCMainWindow();errors=[]
    monkeypatch.setattr(window,'_worker_error',errors.append)
    try:
        original_core = window.transformer_design_view._core_input()
        window._load_reachable_integrated_example()
        spec=window._spec_from_widgets()
        assert spec.magnetics_mode is MagneticsMode.INTEGRATED
        assert window.topology_view.integrated_magnetics
        assert 'APR' in window.topology_view.blocks['lr'].toolTip()
        assert not window.user_lr.isEnabled() and not window.user_lm.isEnabled()
        assert not window.transformer_design_view.preset.isEnabled()
        window.run_design();drain(app,window)
        assert not errors and window.system_analysis is not None
        assert window.system_analysis.resonant_inductor is None
        assert '同一变压器' in window.summary_text.toPlainText()
        assert 'MODEL_REACHABLE' in window.summary_text.toPlainText()
        assert 'required' not in window.integrated_summary.text()
        window.run_transformer_design();drain(app,window)
        assert not errors
        result=window.transformer_synthesis
        assert result is not None and result.primary_turns==30 and result.secondary_turns==4
        assert 'Integrated APR' in window.transformer_design_view.summary.toPlainText()
        window.transformer_design_view.apply_btn.click()
        assert window._spec_from_widgets()==spec
        path=tmp_path/'集成工程.json'
        monkeypatch.setattr(module.QFileDialog,'getSaveFileName',lambda *a,**kw:(str(path),'JSON'))
        window.save_json()
        assert not issues
        restored=load_project(path)[0]
        assert restored==spec
        before=window._power_stage_revision
        window.fields['integrated_mutual_h'].setValue(window.fields['integrated_mutual_h'].value()*.999)
        assert window._power_stage_revision>before
        assert window.system_analysis is None and window.transformer_synthesis is None
        assert not window.transformer_design_view.apply_btn.isEnabled()
        assert "失效" in window.transformer_design_view.status.text()
        assert not window.transformer_design_view.summary.toPlainText()
        assert window.transformer_design_view.work_table.rowCount() == 0
        monkeypatch.setattr(module.QFileDialog,'getOpenFileName',lambda *a,**kw:(str(path),'JSON'))
        window.load_json();drain(app,window)
        assert not errors and not issues
        assert window._spec_from_widgets()==spec
        assert window.system_analysis is None
        assert not window.transformer_design_view.apply_btn.isEnabled()
        # Switching input modes re-derives all three APR quantities coherently.
        from llc_design.core.integrated_magnetics import from_spec
        matrix=from_spec(spec)
        window.fields['integrated_lshort_h'].setValue(matrix.lr_h*1e6)
        window.integrated_source_combo.setCurrentIndex(window.integrated_source_combo.findData(IntegratedInductanceSource.OPEN_SHORT))
        assert window._spec_from_widgets().electrical_turns_ratio==pytest.approx(matrix.effective_turns_ratio,rel=2e-7)
        window.primary_turns.setValue(31)
        with pytest.raises(ValueError,match='re-characterize'):
            window._spec_from_widgets()
        window.magnetics_mode_combo.setCurrentIndex(window.magnetics_mode_combo.findData(MagneticsMode.SEPARATE))
        assert not window._spec_from_widgets().integrated_magnetics
        assert not window.topology_view.integrated_magnetics
        designer = window.transformer_design_view
        assert designer.preset.isEnabled()
        assert designer._integrated_core_input is None
        assert designer._core_input() == original_core
        assert "分立模式" in designer.magnetics_hint.text()
        assert not designer.apply_btn.isEnabled()
        assert window.waveform_view.bundle is None
        # An invalid mid-edit matrix must not trap the window in integrated locks.
        for _ in range(2):
            window.magnetics_mode_combo.setCurrentIndex(window.magnetics_mode_combo.findData(MagneticsMode.INTEGRATED))
            assert not designer.preset.isEnabled()
            assert designer._integrated_core_input.part_number == "PQ50_REF"
            window.fields["integrated_ls_h"].setValue(0)
            window.magnetics_mode_combo.setCurrentIndex(window.magnetics_mode_combo.findData(MagneticsMode.SEPARATE))
            assert designer.preset.isEnabled()
            assert designer._integrated_core_input is None
            assert designer._core_input() == original_core
            assert not designer.apply_btn.isEnabled()
    finally:
        from llc_design.gui.lifetime import dispose_widgets
        dispose_widgets([window])


def test_integrated_physical_terminal_selection(app):
    from llc_design.analysis.fha import solve_fha
    from llc_design.analysis.types import LLCAnalysisRequest
    from llc_design.gui.widgets.waveform_view import WaveformView
    from llc_design.tests.test_integrated_magnetics import reference_spec
    result=solve_fha(LLCAnalysisRequest(reference_spec(),vbus_v=400,load_fraction=1))
    view=WaveformView()
    try:
        view.bundle=result.waveform
        view.select_component('transformer')
        assert 'v_transformer_terminal' in view._selected_keys()
        assert 'v_transformer_primary' not in view._selected_keys()
        view.select_component('lm')
        assert 'v_transformer_primary' in view._selected_keys()  # explicitly APR label
    finally:
        from llc_design.gui.lifetime import dispose_widgets
        dispose_widgets([view])


@pytest.mark.parametrize("size", [(1280, 720), (1366, 768)])
def test_integrated_narrow_dock_and_atomic_invalid_load(app, monkeypatch, tmp_path, size):
    import json
    from dataclasses import asdict

    from PySide6.QtCore import QPoint
    from shiboken6 import isValid

    import llc_design.gui.main_window as module
    from llc_design.gui.lifetime import dispose_widgets

    monkeypatch.setattr(module, "check_for_updates", lambda *args, **kwargs: None)
    issues = []
    monkeypatch.setattr(module, "show_operation_issue", lambda *args, **kwargs: issues.append(str(args)))
    window = module.LLCMainWindow()
    try:
        window.resize(*size)
        window.show()
        app.processEvents()
        scroll = window.parameter_dock.widget()
        assert scroll.horizontalScrollBar().maximum() == 0
        assert window.magnetics_mode_combo.mapTo(scroll.widget(), QPoint()).y() < 500
        window._load_reachable_integrated_example()
        app.processEvents()
        assert scroll.horizontalScrollBar().maximum() == 0
        original = window._spec_from_widgets()
        assert window.fields["integrated_minimum_insulation_m"].value() == pytest.approx(.2)
        assert window.fields["integrated_geometry_tolerance"].value() == pytest.approx(35)
        assert window.fields["integrated_target_tolerance"].value() == pytest.approx(10)
        assert "n_eff" in window.manual_derived_label.text()
        for changes in ({"integrated_characterized_core": "UNKNOWN"},
                        {"integrated_characterized_core": None},
                        {"integrated_characterized_core": 42},
                        {"integrated_interwinding_gap_m": .1},
                        {"integrated_measurement_frequency_hz": 1e8}):
            path = tmp_path / "invalid.json"
            path.write_text(json.dumps({**asdict(original), **changes}), encoding="utf-8")
            monkeypatch.setattr(module.QFileDialog, "getOpenFileName", lambda *args, path=path, **kwargs: (str(path), "JSON"))
            revision = window._power_stage_revision
            window.load_json()
            assert window._power_stage_revision == revision
            assert window._spec_from_widgets() == original
        assert len(issues) == 5
    finally:
        dispose_widgets([window])
    assert not isValid(window)


def test_transformer_only_worker_invalidates_visible_pass(app, monkeypatch):
    import llc_design.gui.main_window as module
    from llc_design.gui.lifetime import dispose_widgets

    monkeypatch.setattr(module, "check_for_updates", lambda *args, **kwargs: None)
    window = module.LLCMainWindow()
    errors = []
    monkeypatch.setattr(window, "_worker_error", errors.append)
    try:
        window._load_reachable_integrated_example()
        window.run_transformer_design()
        drain(app, window)
        view = window.transformer_design_view
        assert not errors and window.system_analysis is None
        assert view.result is not None and view.apply_btn.isEnabled()
        assert view.summary.toPlainText() and view.work_table.rowCount() > 0
        # The actual object remains authoritative even if a consumer is called
        # outside the common worker callback that updates this advisory flag.
        window._results_current = False
        window.fields["integrated_mutual_h"].setValue(39.99)
        assert window.transformer_synthesis is None and view.result is None
        assert not view.apply_btn.isEnabled() and not view.export_btn.isEnabled()
        assert "失效" in view.status.text() and "PASS" not in view.status.text()
        assert not view.summary.toPlainText() and view.work_table.rowCount() == 0
        assert not view.loss_figure.axes
    finally:
        dispose_widgets([window])


@pytest.mark.parametrize("maximum", [1, 3])
def test_actual_comparison_run_accepts_ui_low_harmonic_orders(app, monkeypatch, maximum):
    import llc_design.gui.main_window as module
    from llc_design.analysis.types import FidelityLevel
    from llc_design.gui.lifetime import dispose_widgets

    monkeypatch.setattr(module, "check_for_updates", lambda *args, **kwargs: None)
    captured = []
    actual_solver = module.LLCGoldenSolver

    class CapturingSolver(actual_solver):
        def __init__(self, config):
            captured.append(config)
            super().__init__(config)

    monkeypatch.setattr(module, "LLCGoldenSolver", CapturingSolver)
    window = module.LLCMainWindow()
    errors = []
    monkeypatch.setattr(window, "_worker_error", errors.append)
    try:
        window._load_reachable_integrated_example()
        view = window.model_comparison_view
        view.max_harmonic.setValue(maximum)
        view.hb_samples.setValue(256)
        view.include_td.setChecked(False)
        view.run_button.click()
        drain(app, window)
        assert not errors and len(captured) == 1
        assert captured[0].harmonic_balance.minimum_harmonic == maximum
        assert captured[0].harmonic_balance.max_harmonic == maximum
        result = window.multi_fidelity_analysis
        assert result is not None and FidelityLevel.FHA in result.results
        if FidelityLevel.HARMONIC_BALANCE in result.results:
            hb = result.results[FidelityLevel.HARMONIC_BALANCE]
            assert isinstance(hb.convergence.converged, bool)
        else:
            assert any("HB solve failed" in warning for warning in result.warnings)
        assert not any("minimum_harmonic cannot exceed" in warning for warning in result.warnings)
    finally:
        dispose_widgets([window])


def test_actual_fast_edf_waveform_discloses_voltage_projection(app, monkeypatch):
    import llc_design.gui.main_window as module
    from llc_design.gui.lifetime import dispose_widgets

    monkeypatch.setattr(module, "check_for_updates", lambda *args, **kwargs: None)
    window = module.LLCMainWindow()
    errors = []
    monkeypatch.setattr(window, "_worker_error", errors.append)
    try:
        window._load_reachable_integrated_example()
        window.run_waveforms(False)
        drain(app, window)
        assert not errors
        waveform = window.waveform_view.bundle
        assert waveform.model_name == "dynamic_phasor_edf_fast"
        assert waveform.metadata["faraday_validated"] == "false"
        assert waveform.metadata["voltage_projection_method"] == "EDF"
        assert "EDF 投影" in waveform.signal("v_transformer_core").label
        assert "not Faraday validated" in waveform.signal("v_transformer_core").description
        assert any("Integrated EDF" in warning for warning in waveform.warnings)
        assert any("not this EDF display waveform" in warning for warning in waveform.warnings)
    finally:
        dispose_widgets([window])
