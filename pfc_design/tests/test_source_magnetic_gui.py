"""Actual offscreen Qt selection, calculation, persistence, invalidation and disposal."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
qt = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from pfc_design.gui.inductor_design import PFCInductorDesignEditor, PFCInductorDesignResultView


@pytest.fixture(scope="module")
def app():
    return qt.QApplication.instance() or qt.QApplication([])


@pytest.fixture
def editor(app):
    view = PFCInductorDesignEditor("ttpl", lambda: dict(input_rms_v=230, bus_voltage_v=400,
        output_power_w=1000, switching_frequency_hz=120000, target_inductance_uh=200), lambda result: None)
    view.show(); app.processEvents()
    view.material_path.setCurrentIndex(view.material_path.findData("source_catalog"))
    view.source_selector.search.setText("CS400060")
    yield view
    view.close(); view.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def declaration():
    return {"B_convention": "peak_ac", "waveform": "sinusoidal_reference", "dimension_basis": "after_coating_limits",
            "source": "Test-only declared assumptions, no manufacturer qualification", "H_Oe": [0, 1000],
            "B_T": [0, 1], "frequency_Hz": [5000, 300000], "temperature_C": [20, 100], "core_temperature_C": 25}


def test_real_selector_reference_save_reopen_report(editor, tmp_path):
    assert editor.source_selector.selection.label == "CS400060"
    assert not editor.run_button.isEnabled()
    editor.source_selector.turns.setValue(37)
    editor.source_selector.h.setText("100")
    editor.source_selector.reference_button.click()
    reference = editor.source_selector.reference_result
    assert reference["turns"] == 37 and reference["reference_l_at_h_uh"] > 0
    path = tmp_path / "core.json"
    editor.save_magnetic_to(path)
    identity = editor.source_selector.selection.identity
    editor.source_selector.search.setText("CM035060")
    assert editor.source_selector.reference_result is None
    editor.open_magnetic_from(path)
    assert editor.source_selector.selection.identity == identity
    assert editor.source_selector.reference_result == reference
    report = tmp_path / "magnetic.html"
    editor.report_magnetic_to(report)
    assert "CS400060" in report.read_text(encoding="utf-8") and identity in report.read_text(encoding="utf-8")


def test_actual_full_calculation_keeps_selected_identity_and_invalidates(editor, tmp_path, app):
    editor.source_selector.declaration = declaration()
    editor.source_selector.selection_changed.emit()
    assert editor.run_button.isEnabled()
    result_view = PFCInductorDesignResultView("Test result")
    editor.design_completed.connect(result_view.set_result)
    editor.design_invalidated.connect(result_view.clear_result)
    editor.run_button.click()
    result = editor.last_result
    assert result is not None and result.inductance_target_met
    assert "CS400060" in result_view.summary.toPlainText()
    assert "Magnetics High Flux 254/custom" not in result_view.summary.toPlainText()
    assert editor.apply_button.isEnabled()
    path = tmp_path / "full.json"; editor.save_magnetic_to(path)
    editor.target_l.setValue(210)
    assert editor.last_result is None and not editor.apply_button.isEnabled()
    assert not result_view.figure.axes
    editor.open_magnetic_from(path)
    assert editor.last_result.turns == result.turns
    assert editor.last_result.request.source_snapshot == result.request.source_snapshot
    assert "CS400060" in result_view.summary.toPlainText()
    report = tmp_path / "full.html"; editor.report_magnetic_to(report)
    assert "user" in report.read_text(encoding="utf-8") and result.request.core.core_data in report.read_text(encoding="utf-8")
    editor.source_selector.declaration["frequency_Hz"] = [5000, 100000]
    editor.source_selector.selection_changed.emit()
    assert editor.last_result is None
    result_view.close(); result_view.deleteLater(); app.processEvents()


def test_invalid_open_preserves_current_selection_and_inputs(editor, tmp_path):
    editor.source_selector.reference_button.click()
    path = tmp_path / "invalid.json"; editor.save_magnetic_to(path)
    document = json.loads(path.read_text(encoding="utf-8")); document["selection"]["record"]["al_nH_per_t2"] = 999
    path.write_text(json.dumps(document), encoding="utf-8")
    previous = (editor.source_selector.selection.identity, editor.target_l.value(), editor.source_selector.reference_result)
    with pytest.raises(ValueError): editor.open_magnetic_from(path)
    assert previous == (editor.source_selector.selection.identity, editor.target_l.value(), editor.source_selector.reference_result)


def test_cancel_declaration_disposes_dialog_without_filling_unknowns(editor, app):
    for _ in range(3):
        QTimer.singleShot(0, lambda: qt.QApplication.activeModalWidget().reject())
        editor.source_selector.edit_declaration()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); app.processEvents()
        assert editor.source_selector.declaration is None
        assert editor.source_selector.findChildren(qt.QDialog) == []


def test_llc_pdf_tab_does_not_apply_as_bobbin_preset(app):
    from llc_design.gui.widgets.magnetic_catalog_view import MagneticCatalogDialog
    dialog = MagneticCatalogDialog()
    dialog.search.setText("CS400060")
    dialog.tabs.setCurrentIndex(2)
    assert dialog.pdf_reference.rowCount() == 1
    dialog.pdf_reference.selectRow(0)
    assert not dialog.apply.isEnabled()
    assert "CS400060" in dialog.note.text()
    assert "不是完整 LLC" in dialog.note.text()
    dialog.close(); dialog.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); app.processEvents()


def test_modal_save_aborts_if_selection_changes(editor, tmp_path, monkeypatch):
    import pfc_design.gui.inductor_design as module
    path = tmp_path / "stale.json"
    issues = []
    monkeypatch.setattr(module, "show_operation_issue", lambda *args, **kw: issues.append(args))
    def choose(*args, **kwargs):
        editor.source_selector.search.setText("CM035060")
        return str(path), ""
    monkeypatch.setattr(qt.QFileDialog, "getSaveFileName", choose)
    editor.save_magnetic_button.click()
    assert not path.exists()
    assert any("已变化" in str(item) for item in issues)


def test_supplemental_browser_replay_and_repeated_close(editor, app):
    from pfc_design.gui.source_reference_view import SourceReferenceView
    def interact():
        dialog = qt.QApplication.activeModalWidget()
        browser = dialog.findChild(SourceReferenceView)
        browser.search.setText("APP-023")
        assert browser.records.count() == 1
        browser.replay.click()
        assert "197.955" in browser.text.toPlainText()
        assert "120kHz" in browser.text.toPlainText()
        dialog.reject()
    for _ in range(3):
        QTimer.singleShot(0, interact)
        editor.source_selector.open_supplemental()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); app.processEvents()
        assert editor.source_selector.findChildren(qt.QDialog) == []


@pytest.mark.parametrize("field,value", [("turns", 10001), ("reference_n_cores", 101),
    ("n_cores", 17), ("efficiency", .970000001), ("target_inductance_uh", 200.000001),
    ("copper_temperature_c", 100.01)])
def test_restore_unrepresentable_values_is_fail_atomic(editor, tmp_path, field, value):
    editor.source_selector.declaration = declaration()
    editor.source_selector.selection_changed.emit()
    editor.run_button.click()
    editor.source_selector.reference_button.click()
    document = editor.magnetic_document()
    if field == "turns": document["reference_inputs"]["turns"] = value
    elif field == "reference_n_cores": document["reference_inputs"]["n_cores"] = value
    else: document["pfc_inputs"][field] = value
    path = tmp_path / "unrepresentable.json"; path.write_text(json.dumps(document), encoding="utf-8")
    before = (editor.source_selector.selection.identity, editor.last_result, editor.target_l.value(), editor.eff.value(), editor.n_cores.value())
    with pytest.raises(ValueError): editor.open_magnetic_from(path)
    after = (editor.source_selector.selection.identity, editor.last_result, editor.target_l.value(), editor.eff.value(), editor.n_cores.value())
    assert before == after


def test_restored_hidden_request_inputs_survive_recalculation(editor, tmp_path):
    editor.source_selector.declaration = declaration(); editor.source_selector.selection_changed.emit()
    editor.run_button.click()
    document = editor.magnetic_document()
    document["pfc_inputs"]["winding_length_factor"] = 1.25
    document["pfc_inputs"]["curve_points"] = 101
    path = tmp_path / "extras.json"; path.write_text(json.dumps(document), encoding="utf-8")
    editor.open_magnetic_from(path)
    before = editor.last_result
    editor.run_button.click()
    assert editor.last_result.request.winding_length_factor == 1.25
    assert editor.last_result.request.curve_points == 101
    assert editor.last_result.copper_loss_w == before.copper_loss_w


def test_live_stage_change_after_restore_blocks_apply_until_sync(editor, tmp_path):
    editor.source_selector.declaration = declaration(); editor.source_selector.selection_changed.emit()
    editor.run_button.click()
    path = tmp_path / "context.json"; editor.save_magnetic_to(path); editor.open_magnetic_from(path)
    previous_provider = editor.context_provider
    editor.context_provider = lambda: {**previous_provider(), "output_power_w": 1500}
    editor.stage_context_changed()
    assert editor.last_result is None and not editor.apply_button.isEnabled()
    editor.run_button.click()  # Explicitly recalculating saved context is still not current-stage permission.
    assert editor.last_result.request.output_power_w == 1000
    assert not editor.apply_button.isEnabled()
    editor.sync_from_stage(); editor.run_button.click()
    assert editor.last_result.request.output_power_w == 1500
    assert editor.apply_button.isEnabled()


def test_real_pfc_host_receives_actual_core_identity_and_clears_stale_loss(app):
    from pfc_design.gui.main_window import PFCMainWindow
    host = PFCMainWindow()
    editor = host.control_lab_view.control_lab.inductor_editor
    devices = host.control_lab_view.device_loss_view
    control = host.control_lab_view.control_lab
    control.vin_rms.setValue(230); control.vbus.setValue(400); control.pout.setValue(1000); control.fsw.setValue(120)
    editor.sync_from_stage(); editor.target_l.setValue(200)
    editor.material_path.setCurrentIndex(editor.material_path.findData("source_catalog"))
    editor.source_selector.search.setText("CS400060")
    editor.source_selector.declaration = declaration(); editor.source_selector.selection_changed.emit()
    editor.run_button.click()
    assert editor.last_result is not None
    assert devices._inductor_self_loss_w == editor.last_result.total_loss_w
    assert "CS400060" in devices._inductor_model_identity
    assert "user-declared" in devices._inductor_model_identity
    # Changing control inputs intentionally invalidates the prior device
    # comparison; revisit the real device stage to refresh its sizing view.
    host.control_lab_view.tabs.setCurrentWidget(devices)
    for value in ("Vin=230 V RMS", "Po=1000 W", "fs=120 kHz", "η=0.97"):
        assert value in devices._inductor_operating_point
        assert value in devices.rollup_text.toPlainText()
    editor.source_selector.search.setText("CM035060")
    assert devices._inductor_self_loss_w is None
    assert devices._inductor_model_identity is None
    assert devices._inductor_operating_point is None
    assert editor.last_result is None
    assert not control.inductor_result_view.figure.axes
    assert host.result is None
    host.close(); host.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); app.processEvents()


@pytest.mark.parametrize("size", [(1366, 768), (1280, 720)])
def test_real_pfc_small_window_preserves_scrollable_magnetic_actions(app, size):
    from pfc_design.gui.main_window import PFCMainWindow
    host = PFCMainWindow()
    control = host.control_lab_view.control_lab
    editor = control.inductor_editor
    editor.material_path.setCurrentIndex(editor.material_path.findData("source_catalog"))
    host.subtabs.setCurrentWidget(host.control_lab_view)
    host.control_lab_view.tabs.setCurrentWidget(control)
    control.input_tabs.setCurrentWidget(editor)
    host.resize(*size); host.show(); app.processEvents()
    assert (host.width(), host.height()) == size
    inner = editor.parent()
    while not isinstance(inner, qt.QScrollArea):
        inner = inner.parent()
    assert host.workspace_scroll.verticalScrollBar().maximum() > 0
    assert host.workspace_scroll.horizontalScrollBar().maximum() > 0
    for button in (editor.material_path, editor.source_selector.search, editor.source_selector.reference_button,
                   editor.run_button, editor.save_magnetic_button, editor.open_magnetic_button,
                   editor.report_magnetic_button):
        inner.ensureWidgetVisible(button, 5, 5)
        host.workspace_scroll.ensureWidgetVisible(button, 5, 5); app.processEvents()
        for viewport in (inner.viewport(), host.workspace_scroll.viewport()):
            assert viewport.rect().contains(button.mapTo(viewport, button.rect().center()))
    # Existing workspace identity/navigation remains unchanged inside the scroll area.
    for workspace in (host.control_lab_view, host.vienna_view, host.three_phase_view):
        host.subtabs.setCurrentWidget(workspace)
        assert host.subtabs.currentWidget() is workspace
    assert any(bar.isVisible() for bar in host.findChildren(qt.QToolBar) if bar.parent() is host)
    host.close(); host.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); app.processEvents()


def test_actual_stage_apply_rejects_out_of_range_result_before_mutation(app):
    from pfc_design.gui.control_lab_view import PFCControlLabView
    from pfc_design.magnetics.manufacturer_catalog import ManufacturerCoreCatalog, source_pfc_components
    from pfc_design.magnetics.pfc_inductor_designer import PFCInductorDesignRequest, design_pfc_inductor
    view = PFCControlLabView()
    selected = ManufacturerCoreCatalog().select("csc2025:part:CS400060:054")
    declared = declaration(); core, material = source_pfc_components(selected, declared)
    result = design_pfc_inductor(PFCInductorDesignRequest(topology="ttpl", input_rms_v=230,
        bus_voltage_v=400, output_power_w=100, switching_frequency_hz=120000, target_inductance_uh=20000,
        efficiency=.97, core=core, material=material, n_cores=16,
        source_snapshot=selected.snapshot, model_declaration=declared))
    assert result.l_full_load_peak_uh > view.inductance.maximum()
    before = (view.inductance.value(), view.dcr.value())
    with pytest.raises(ValueError, match="stage unchanged"): view._apply_inductor_design(result)
    assert before == (view.inductance.value(), view.dcr.value())
    view.close(); view.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); app.processEvents()
