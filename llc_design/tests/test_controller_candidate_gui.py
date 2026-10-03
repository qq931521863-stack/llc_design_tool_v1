"""Candidate preview is a transaction, never an implicit controller change."""
from __future__ import annotations

import os
from dataclasses import replace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication

from llc_design.control.digital_loop import PIControllerConfig
from llc_design.gui.widgets.solution_map_view import LoopMapSource, SolutionMapView
from power_control_tools.system_definition import ControllerIntent


@pytest.fixture
def view():
    app = QApplication.instance() or QApplication([])
    widget = SolutionMapView()
    f = np.geomspace(10.0, 20_000.0, 1200)
    fixed = 1.0 / (1.0 + 1j * f / (1000 / np.tan(np.deg2rad(60))))
    current = fixed * PIControllerConfig(kp=0.8, ti_s=0.0003, sample_time_s=1/50000).transfer_function().frequency_response(f)
    widget.set_sources([LoopMapSource("test", "Test full loop", f, fixed, 50000, 20000,
                                     current_loop_response=current)])
    widget.live_fc.setValue(1000)
    widget.live_pm.setValue(60)
    widget.gm_min.setValue(0)
    widget.ms_max.setValue(3)
    widget.switch_max.setValue(0)
    yield widget
    widget.close()
    app.processEvents()


def test_generate_compares_current_candidate_and_does_not_apply(view):
    received = []
    view.controller_selected.connect(lambda *args: received.append(args))
    view.generate_button.click()
    point = view.selected_point
    assert point.feasible
    assert received == []
    assert view.apply_button.isEnabled()
    assert len(view.preview_figure.axes) == 4
    assert all([line.get_label() for line in ax.lines] == ["Current", "Candidate"]
               for ax in view.preview_figure.axes)
    assert "Candidate H(z)" in view.details.toPlainText()
    assert "Frequency-domain comparison only" in view.details.toPlainText()
    assert view.live_kp.isReadOnly() and view.live_ti.isReadOnly()
    view.apply_button.click()
    view.apply_button.click()
    view._apply_selected()  # programmatic repeated action also cannot reuse it
    assert received == [("test", point)]
    assert not view.apply_button.isEnabled()


@pytest.mark.parametrize("field", ["live_fc", "live_pm", "gm_min", "ms_max", "switch_max", "fc_tol", "pm_tol"])
def test_edit_invalidates_previous_candidate(view, field):
    received = []
    view.controller_selected.connect(lambda *args: received.append(args))
    view.generate_candidate()
    control = getattr(view, field)
    control.setValue(control.value() + 0.1)
    assert view.selected_point is None
    assert view._preview_loop is None
    assert not view.apply_button.isEnabled()
    view._apply_selected()
    assert received == []


def test_cancel_regenerate_and_reopen_does_not_change_controller(view):
    received = []
    view.controller_selected.connect(lambda *args: received.append(args))
    view.generate_candidate()
    first = view.selected_point
    view.cancel_button.click()
    assert view.selected_point is None
    assert not view.apply_button.isEnabled()
    view.hide()
    view.show()
    assert view.selected_point is None
    view.generate_candidate()
    assert view.selected_point == first
    assert received == []


def test_changed_source_clears_map_preview_selection(view):
    view.generate_candidate()
    source = view._source()
    view.set_sources([replace(source, fixed_loop_response=source.fixed_loop_response*2)])
    assert view.selected_point is None
    assert not view.apply_button.isEnabled()
    assert not view.preview_figure.axes
    view.generate_candidate()
    assert view.selected_point.feasible
    view.invalidate()
    assert not view.sources
    assert view.selected_point is None
    assert not view.generate_button.isEnabled()


def test_unreachable_candidate_cannot_reuse_prior_feasible_candidate(view):
    view.generate_candidate()
    assert view.selected_point.feasible
    view.live_pm.setValue(179)
    view.generate_candidate()
    assert not view.selected_point.feasible
    assert not view.apply_button.isEnabled()
    assert not view.preview_figure.axes


def test_guided_auto_candidate_uses_targets_but_never_applies(view):
    received = []
    view.controller_selected.connect(lambda *args: received.append(args))
    intent = ControllerIntent(design_mode="auto", target_crossover_hz=1000,
                              target_phase_margin_deg=60, minimum_gain_margin_db=0,
                              maximum_sensitivity=3, maximum_switching_loop_gain_db=0)
    view.configure_intent(intent)
    assert view.selected_point.feasible
    assert received == []
    view.configure_intent(replace(intent, structure="PIF"))
    assert view.selected_point is None
    assert "not implemented" in view.details.toPlainText()
    view.generate_candidate()
    assert view.selected_point is None
    assert received == []


def test_guided_unspecified_target_never_silently_uses_previous_value(view):
    view.generate_candidate()
    view.configure_intent(ControllerIntent(design_mode="auto"))
    assert view.selected_point is None
    assert not view.apply_button.isEnabled()
    assert "explicit Fc and PM" in view.details.toPlainText()


def test_map_selection_clears_previous_plot_for_unreachable_point(view):
    from types import SimpleNamespace
    view.fc_min.setValue(700)
    view.fc_max.setValue(1400)
    view.pm_min.setValue(60)
    view.pm_max.setValue(179)
    view.fc_points.setValue(5)
    view.pm_points.setValue(5)
    view.build_map()
    axis = view.figure.axes[0]
    view._map_clicked(SimpleNamespace(xdata=1000, ydata=60, inaxes=axis))
    assert view.selected_point.feasible
    assert view.preview_figure.axes
    view._map_clicked(SimpleNamespace(xdata=1000, ydata=179, inaxes=axis))
    assert not view.selected_point.feasible
    assert not view.preview_figure.axes
    assert view._preview_loop is None
    assert not view.apply_button.isEnabled()


def test_unsupported_intent_cannot_reuse_previous_map(view):
    from types import SimpleNamespace
    view.fc_min.setValue(700)
    view.fc_max.setValue(1400)
    view.fc_points.setValue(5)
    view.pm_points.setValue(5)
    view.build_map()
    axis = view.figure.axes[0]
    view.configure_intent(ControllerIntent(structure="PIF", design_mode="auto",
        target_crossover_hz=view.live_fc.value(), target_phase_margin_deg=view.live_pm.value()))
    assert view.result is None
    view._map_clicked(SimpleNamespace(xdata=1000, ydata=60, inaxes=axis))
    assert view.selected_point is None
    assert not view.apply_button.isEnabled()


def test_missing_switching_frequency_coverage_is_explicit(view):
    source = view._source()
    view.set_sources([replace(source, switching_frequency_hz=100000)])
    view.generate_candidate()
    assert view.selected_point.feasible
    assert view.selected_point.switching_loop_gain_db is None
    assert "outside the analyzed range" in view.comparison_summary.text()
    assert "all configured constraints satisfied" not in view.details.toPlainText()
    assert "Closed-loop OK" not in view.details.toPlainText()
