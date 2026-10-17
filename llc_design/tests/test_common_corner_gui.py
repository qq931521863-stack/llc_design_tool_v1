"""Retained operating points test one exact PI, never independently retuned PIs."""
from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from llc_design.control.digital_loop import PIControllerConfig
from llc_design.control.solution_map import SolutionStatus
from llc_design.gui.widgets.solution_map_view import LoopMapSource, SolutionMapView


@pytest.fixture
def view():
    app = QApplication.instance() or QApplication([])
    widget = SolutionMapView()
    f = np.geomspace(10.0, 20_000.0, 1200)
    fixed = 1.0 / (1.0 + 1j * f / (1000 / np.tan(np.deg2rad(60))))
    fixed *= np.exp(-2j * np.pi * f * 20e-6)
    controller = PIControllerConfig(kp=0.8, ti_s=0.0003, sample_time_s=1/50000)
    widget.set_sources([LoopMapSource(
        "test", "Test loop", f, fixed, 50000, 20000,
        provenance="Unit-test fixed response (not a hardware model)",
        current_loop_response=fixed * controller.transfer_function().frequency_response(f),
        operating_point="Nominal", compatibility_key="same-test-plant-sensing-timing",
    )])
    widget.live_fc.setValue(1000)
    widget.live_pm.setValue(60)
    widget.gm_min.setValue(0)
    widget.ms_max.setValue(10)
    widget.switch_max.setValue(100)
    yield widget
    widget.close()
    app.processEvents()


def test_retention_is_explicit_frozen_and_deduplicated(view):
    source = view._source()
    assert not view._retained_sources
    view.retain_button.click()
    view.retain_button.click()
    assert len(view._retained_sources) == 1
    retained = next(iter(view._retained_sources.values()))
    assert not retained.fixed_loop_response.flags.writeable
    assert retained.fixed_loop_response is not source.fixed_loop_response
    assert "already retained" in view.corner_notice.text()
    view.invalidate("New operating point needs analysis")
    assert len(view._retained_sources) == 1
    assert not view.generate_button.isEnabled()
    assert not view.retain_button.isEnabled()
    view.set_sources([replace(source, operating_point="Second", fixed_loop_response=source.fixed_loop_response * 0.3)])
    assert len(view._active_corners()) == 2
    view.generate_candidate()
    point = view.selected_point
    assert len(point.corner_results) == 2
    assert all((item.kp, item.ti_s) == (point.kp, point.ti_s) for item in point.corner_results)
    assert "COMMON PI: 2" in view.details.toPlainText()
    assert "Worst computed PM" in view.details.toPlainText()
    assert "Unit-test fixed response" in view.details.toPlainText()
    assert len(view.preview_figure.axes[0].lines) == 3


def test_common_map_rejects_failed_corner_and_checkbox_invalidates_preview(view):
    nominal = view._source()
    view.set_sources([replace(nominal, fixed_loop_response=nominal.fixed_loop_response * 4,
                              operating_point="High gain")])
    view.retain_current_source()
    view.set_sources([nominal])
    view.generate_candidate()
    assert view.selected_point.corner_results[0].feasible
    assert not view.selected_point.corner_results[1].feasible
    assert not view.apply_button.isEnabled()
    view.fc_min.setValue(700)
    view.fc_max.setValue(1400)
    view.fc_points.setValue(5)
    view.pm_min.setValue(55)
    view.pm_max.setValue(65)
    view.pm_points.setValue(5)
    view.build_map()
    assert view.result is not None
    assert all(len(point.corner_results) == 2 for row in view.result.points for point in row)
    axis = view.figure.axes[0]
    view._map_clicked(SimpleNamespace(xdata=1000, ydata=60, inaxes=axis))
    assert not view.selected_point.feasible
    view.corner_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert view.selected_point is None
    assert view.result is None
    assert len(view._active_corners()) == 1
    view.generate_candidate()
    assert view.selected_point.feasible


@pytest.mark.parametrize("change", [{"sample_rate_hz": 60000}, {"compatibility_key": "changed-plant"}])
def test_incompatible_source_clears_snapshots_and_candidate(view, change):
    source = view._source()
    view.retain_current_source()
    view.generate_candidate()
    view.set_sources([replace(source, **change)])
    assert not view._retained_sources
    assert view.selected_point is None
    assert not view.apply_button.isEnabled()
    assert "incompatible" in view.corner_notice.text()


def test_loop_kind_scopes_snapshots_and_remove_clear_are_explicit(view):
    source = view._source()
    view.retain_current_source()
    other = replace(source, key="other", label="Other loop")
    view.set_sources([source, other])
    view.source_combo.setCurrentIndex(1)
    assert view.corner_list.count() == 0
    assert len(view._active_corners()) == 1
    view.source_combo.setCurrentIndex(0)
    assert view.corner_list.count() == 1
    view.corner_list.setCurrentRow(0)
    view.remove_corner_button.click()
    assert not view._retained_sources
    view.retain_current_source()
    view.clear_corners_button.click()
    assert not view._retained_sources


def test_conditional_map_never_counts_as_feasible_and_guided_resets_permission(view):
    from power_control_tools.system_definition import ControllerIntent
    source = view._source()
    view.set_sources([replace(source, switching_frequency_hz=100000)])
    view.allow_conditional.setChecked(True)
    view.fc_min.setValue(700)
    view.fc_max.setValue(1400)
    view.fc_points.setValue(5)
    view.pm_min.setValue(55)
    view.pm_max.setValue(65)
    view.pm_points.setValue(5)
    view.build_map()
    assert not view.result.feasible_mask.any()
    assert np.any(view.result.status_codes == int(SolutionStatus.CONDITIONAL))
    view.configure_intent(ControllerIntent())
    assert not view.allow_conditional.isChecked()
    assert view.result is None


def test_preview_displays_worst_signed_gain_margin(view, monkeypatch):
    from llc_design.gui.widgets import solution_map_view
    view.generate_candidate()
    point = view.selected_point
    # The maintained margin record can contain an earlier negative crossing
    # and a later positive one. The display must not conceal the negative GM.
    mixed = SimpleNamespace(critical_gain_crossover_hz=1000, phase_margin_deg=60,
                            gain_margin_db=20, gain_margins_db=(-5, 20))
    monkeypatch.setattr(solution_map_view, "calculate_stability_margins", lambda *_args: mixed)
    view._update_preview(point)
    assert "GM=-5 dB" in view.comparison_summary.text()
    assert "GM=20 dB" not in view.comparison_summary.text()


def test_changed_source_resets_conditional_adoption_permission(view):
    source = view._source()
    view.allow_conditional.setChecked(True)
    view.set_sources([replace(source, operating_point="New point")])
    assert not view.allow_conditional.isChecked()
    view.allow_conditional.setChecked(True)
    view.invalidate()
    assert not view.allow_conditional.isChecked()


@pytest.mark.parametrize("count", [0, 1, 2])
def test_short_source_cannot_generate_retain_or_reuse_candidate(view, count):
    from power_control_tools.system_definition import ControllerIntent
    source = view._source()
    view.retain_current_source()
    view.generate_candidate()
    view.set_sources([replace(source, frequencies_hz=source.frequencies_hz[:count],
                              fixed_loop_response=source.fixed_loop_response[:count],
                              current_loop_response=source.current_loop_response[:count])])
    assert view.selected_point is None
    assert len(view._retained_sources) == 1
    assert not view._active_corners()
    assert not view.generate_button.isEnabled()
    assert not view.run_button.isEnabled()
    assert not view.retain_button.isEnabled()
    assert not view.apply_button.isEnabled()
    assert "Insufficient response coverage" in view.source_info.text()
    view.generate_candidate()
    view.build_map()
    assert view.selected_point is None
    assert view.result is None
    view.configure_intent(ControllerIntent(design_mode="auto", target_crossover_hz=1000,
                                           target_phase_margin_deg=60))
    assert not view.generate_button.isEnabled()
    view.set_sources([source])
    assert len(view._active_corners()) == 2
    assert view.generate_button.isEnabled()
