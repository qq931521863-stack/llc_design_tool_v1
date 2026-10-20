"""Real Qt Review/Bode uses the same TTPL frequency-domain contract."""
import os
from dataclasses import replace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def app():
    widgets = pytest.importorskip("PySide6.QtWidgets")
    application = widgets.QApplication.instance() or widgets.QApplication([])
    yield application
    application.processEvents()


def test_review_accepts_40k_and_discloses_actual_domains(app):
    from llc_design.gui.system_modeling import (
        SystemModelingDesignDialog,
        ttpl_config_from_system_definition,
    )
    from pfc_design.control import build_pfc_control_lab_analysis
    from power_control_tools.system_definition import SystemTopology
    dialog = SystemModelingDesignDialog()
    try:
        dialog.select_topology(SystemTopology.TTPL_PFC)
        dialog.current_sense["sample"].setValue(40)
        dialog._refresh_review()
        assert dialog.finish_button.isEnabled()
        rows = [dialog.review_checks.item(i).text() for i in range(dialog.review_checks.count())]
        row = next(text for text in rows if "Actual Bode frequency domains" in text)
        assert "Requested 0.5–20000 Hz" in row
        assert "strictly below 20000 Hz" in row and "strictly below 5000 Hz" in row
        result = build_pfc_control_lab_analysis(ttpl_config_from_system_definition(dialog.definition))
        assert result.current_loop.frequencies_hz[-1] < 20000
        assert result.voltage_loop.frequencies_hz[-1] < 5000
    finally:
        dialog.close()


def test_review_rejects_hidden_saved_range_without_losing_request(app):
    from llc_design.gui.system_modeling import SystemModelingDesignDialog
    from pfc_design.control import PFCControlLabConfig
    from power_control_tools.system_definition import SystemTopology
    dialog = SystemModelingDesignDialog()
    try:
        dialog.ttpl_base_config = replace(PFCControlLabConfig(), frequency_start_hz=5000)
        dialog.select_topology(SystemTopology.TTPL_PFC)
        dialog._refresh_review()
        assert not dialog.finish_button.isEnabled() and dialog.definition is None
        rows = [dialog.review_checks.item(i).text() for i in range(dialog.review_checks.count())]
        assert any("voltage loop needs at least two" in row for row in rows)
        assert dialog.ttpl_base_config.frequency_start_hz == 5000
    finally:
        dialog.close()


def test_bode_cursor_phase_budget_and_candidate_source_use_loop_band(app):
    from llc_design.gui.widgets.bode_cursor import BodeCursorMeasurement
    from pfc_design.control import PFCControlLabConfig, build_pfc_control_lab_analysis
    from pfc_design.gui.control_lab_view import PFCControlLabView
    from pfc_design.gui.solution_map_install import ttpl_sources
    analysis = build_pfc_control_lab_analysis(PFCControlLabConfig())
    view = PFCControlLabView()
    try:
        view.result = (analysis, None, None)
        view._set_bode_results(analysis)
        np.testing.assert_array_equal(view.current_bode._frequencies_hz, analysis.current_loop.frequencies_hz)
        np.testing.assert_array_equal(view.voltage_bode._frequencies_hz, analysis.voltage_loop.frequencies_hz)
        assert view.sense_bode._frequencies_hz[-1] < 5000
        view.voltage_bode.set_cursor_frequency(12000)
        assert view.voltage_bode._cursor.frequency_hz < 5000
        view.voltage_bode.cursor_changed.emit(BodeCursorMeasurement(6000, ()))
        assert "unavailable outside" in view.phase_budget_label.text()
        for source in ttpl_sources(analysis):
            loop = getattr(analysis, source.key + "_loop")
            np.testing.assert_array_equal(source.frequencies_hz, loop.frequencies_hz)
            np.testing.assert_allclose(source.fixed_loop_response * loop.responses["controller_ci" if source.key == "current" else "controller_cv"],
                                       loop.responses["open_" + source.key], rtol=2e-14)
    finally:
        view.close()


def test_guided_target_uses_inner_band_and_is_never_clipped(app):
    from llc_design.gui.system_modeling import SystemModelingDesignDialog
    from pfc_design.control import PFCControlLabConfig
    from power_control_tools.system_definition import SystemTopology

    dialog = SystemModelingDesignDialog()
    try:
        dialog.select_topology(SystemTopology.TTPL_PFC)
        dialog.vbus_sense["sample"].setValue(2)
        dialog.target_fc.setValue(3000)
        dialog._refresh_review()
        assert dialog.finish_button.isEnabled()
        assert dialog.definition.controller.target_crossover_hz == 3000
        dialog.ttpl_base_config = replace(PFCControlLabConfig(), frequency_stop_hz=2000)
        dialog._refresh_review()
        assert not dialog.finish_button.isEnabled()
        assert dialog.target_fc.value() == 3000
        rows = [dialog.review_checks.item(i).text() for i in range(dialog.review_checks.count())]
        assert any("Current-inner-loop target Fc covered" in row and "✕" in row for row in rows)
    finally:
        dialog.close()
