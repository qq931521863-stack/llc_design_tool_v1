from dataclasses import replace
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest


def test_guided_review_rejects_old_gain_and_recovers_with_explicit_valid_gain():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from llc_design.gui.system_modeling import SystemModelingDesignDialog, ttpl_config_from_system_definition
    from power_control_tools.system_definition import SystemTopology
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = SystemModelingDesignDialog()
    try:
        dialog.select_topology(SystemTopology.TTPL_PFC)
        initial = dialog.build_definition()
        assert initial.sensors[2].front_end_gain_v_per_unit == pytest.approx(1600/250600, rel=1e-7)
        dialog.pages.setCurrentIndex(dialog.PAGE_REVIEW)
        app.processEvents()
        assert dialog.finish_button.isEnabled()
        # Loading an older design must preserve its exact wrong gain, show the
        # overrange and refuse Analyze, rather than silently repairing hardware.
        old = replace(initial, sensors=(*initial.sensors[:2], replace(initial.sensors[2],
                      front_end_gain_v_per_unit=1600/118600)))
        dialog._load_definition(old)
        dialog.pages.setCurrentIndex(dialog.PAGE_REVIEW)
        dialog._refresh_review()
        assert not dialog.finish_button.isEnabled()
        assert dialog.definition is None
        assert any("5.39629" in dialog.review_checks.item(i).text()
                   for i in range(dialog.review_checks.count()))
        # Restoring the actual accepted value reaches the actual model unchanged.
        dialog._load_definition(initial)
        dialog._refresh_review()
        assert dialog.finish_button.isEnabled()
        actual = ttpl_config_from_system_definition(dialog.build_definition())
        assert actual.vbus_sense.raw_dc_gain == initial.sensors[2].front_end_gain_v_per_unit
        assert 400*actual.vbus_sense.raw_dc_gain < actual.vbus_sense.adc_vref_v
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_original_guided_context_never_claims_to_contain_current_expert_edits():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from llc_design.gui.system_modeling import install_guided_context_actions
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = QtWidgets.QMainWindow()
    try:
        window.addToolBar("tools")
        action = install_guided_context_actions(window, lambda: None)
        assert action.text() == "Original Guided Setup"
        assert "not synchronized" in action.toolTip()
        assert "replaces" in action.toolTip()
        assert "not this wizard context" in action.toolTip()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
