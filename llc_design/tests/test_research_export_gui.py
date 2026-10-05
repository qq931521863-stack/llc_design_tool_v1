"""Research exports use completed analysis snapshots, never unrun GUI edits."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QFileDialog, QMessageBox


@pytest.fixture
def export_ui(monkeypatch):
    from llc_design.gui.widgets import research_export

    # Keep success notifications non-modal; real errors must still fail tests.
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[1:]))
    yield research_export
    # Assert outside Qt slots, where Python exceptions may otherwise be logged
    # instead of propagated to pytest (especially after a repeated save).
    assert warnings == [], f"Unexpected export errors: {warnings!r}"


@pytest.fixture
def panel_factory(desktop_qapp, export_ui):
    widgets = []

    def make(kind="llc"):
        panel = export_ui.ExportResearchPanel(kind=kind)
        widgets.append(panel)
        return panel

    yield make
    for widget in widgets:
        widget.close()
    desktop_qapp.processEvents()


@pytest.fixture
def stub_export(monkeypatch, export_ui):
    calls = []

    def build_llc(analysis):
        calls.append(("llc", analysis, None))
        return "llc", analysis, None

    def build_pfc(analysis, loop="both"):
        calls.append(("pfc", analysis, loop))
        return "pfc", analysis, loop

    monkeypatch.setattr(export_ui, "build_llc_research_export", build_llc)
    monkeypatch.setattr(export_ui, "build_pfc_research_export", build_pfc)
    for language in ("matlab", "python"):
        monkeypatch.setattr(
            export_ui, f"render_{language}",
            lambda bundle, language=language: (
                f"{language}: {bundle[0]} / {bundle[2]} / {bundle[1].marker}\n"
            ),
        )
    return calls


def _save_to(monkeypatch, path):
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        lambda *args, **kwargs: (str(path), ""),
    )


@pytest.mark.parametrize("kind", ["llc", "pfc"])
def test_missing_analysis_and_busy_state_block_export(
    kind, panel_factory, stub_export, monkeypatch,
):
    panel = panel_factory(kind)
    dialogs = []
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        lambda *args, **kwargs: (dialogs.append(args) or "", ""),
    )

    def assert_blocked():
        assert not panel.matlab_button.isEnabled()
        assert not panel.python_button.isEnabled()
        for language in ("matlab", "python"):
            getattr(panel, f"{language}_button").click()
            panel._export(language)
        assert not dialogs
        assert not stub_export

    assert_blocked()
    panel.set_busy(True)
    panel.set_analysis(SimpleNamespace(marker="已完成"))
    assert_blocked()
    panel.set_busy(False)
    assert panel.matlab_button.isEnabled()
    assert panel.python_button.isEnabled()
    panel.set_busy(True)
    assert_blocked()
    panel.set_busy(False)
    panel.set_analysis(None)
    assert_blocked()


@pytest.mark.parametrize("language", ["matlab", "python"])
def test_cancel_save_does_not_write_or_discard_completed_analysis(
    language, panel_factory, stub_export, monkeypatch, tmp_path,
):
    panel = panel_factory()
    analysis = SimpleNamespace(marker="completed")
    panel.set_analysis(analysis)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: ("", ""))
    writes = []
    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_text", lambda *args, **kwargs: writes.append(args))
        getattr(panel, f"{language}_button").click()
    assert writes == []
    assert panel.matlab_button.isEnabled()
    assert panel.python_button.isEnabled()

    # Cancel is reversible: the same completed result can be saved afterward.
    suffix = ".m" if language == "matlab" else ".py"
    path = tmp_path / ("after_cancel" + suffix)
    _save_to(monkeypatch, path)
    getattr(panel, f"{language}_button").click()
    assert path.read_text(encoding="utf-8") == f"{language}: llc / None / completed\n"
    assert stub_export[-1] == ("llc", analysis, None)


@pytest.mark.parametrize("language,suffix", [("matlab", ".m"), ("python", ".py")])
def test_unicode_filename_and_repeated_saves_keep_same_snapshot(
    language, suffix, panel_factory, stub_export, monkeypatch, tmp_path,
):
    panel = panel_factory()
    analysis = SimpleNamespace(marker="完成的传递函数 μΩ")
    panel.set_analysis(analysis)
    expected = f"{language}: llc / None / {analysis.marker}\n"
    paths = [tmp_path / ("传递函数 研究" + suffix), tmp_path / ("另存一份" + suffix)]
    for path in (paths[0], paths[0], paths[1]):
        _save_to(monkeypatch, path)
        getattr(panel, f"{language}_button").click()
        assert path.read_text(encoding="utf-8") == expected
    assert len(stub_export) == 3
    assert all(call == ("llc", analysis, None) for call in stub_export)
    assert panel.matlab_button.isEnabled() and panel.python_button.isEnabled()


@pytest.mark.parametrize("kind", ["llc", "pfc"])
@pytest.mark.parametrize("language,suffix", [("matlab", ".m"), ("python", ".py")])
def test_save_dialog_nested_completion_does_not_replace_clicked_snapshot(
    kind, language, suffix, panel_factory, stub_export, monkeypatch, tmp_path,
):
    panel = panel_factory(kind)
    original = SimpleNamespace(marker="original completed analysis")
    updated = SimpleNamespace(marker="newly completed analysis")
    panel.set_analysis(original)
    path = tmp_path / ("captured_snapshot" + suffix)
    loop = "both" if kind == "pfc" else None

    def complete_analysis_during_dialog(*args, **kwargs):
        # Modal dialogs process nested Qt events, including queued worker
        # completions. This click must keep the result it began exporting.
        panel.set_analysis(updated)
        return str(path), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", complete_analysis_during_dialog)
    getattr(panel, f"{language}_button").click()
    assert stub_export[-1] == (kind, original, loop)
    assert path.read_text(encoding="utf-8") == f"{language}: {kind} / {loop} / {original.marker}\n"

    # The replacement is still installed and is used by the next export.
    _save_to(monkeypatch, path)
    getattr(panel, f"{language}_button").click()
    assert stub_export[-1] == (kind, updated, loop)
    assert path.read_text(encoding="utf-8") == f"{language}: {kind} / {loop} / {updated.marker}\n"


@pytest.mark.parametrize("language,suffix", [("matlab", ".m"), ("python", ".py")])
def test_save_without_extension_adds_language_suffix(
    language, suffix, panel_factory, stub_export, monkeypatch, tmp_path,
):
    panel = panel_factory()
    panel.set_analysis(SimpleNamespace(marker="completed"))
    path = tmp_path / "无后缀研究模型"
    _save_to(monkeypatch, path)
    getattr(panel, f"{language}_button").click()
    assert not path.exists()
    assert path.with_suffix(suffix).read_text(encoding="utf-8") == (
        f"{language}: llc / None / completed\n"
    )


@pytest.mark.parametrize("language,suffix", [("matlab", ".m"), ("python", ".py")])
def test_failed_write_warns_without_success_and_allows_retry(
    language, suffix, panel_factory, stub_export, monkeypatch, tmp_path,
):
    panel = panel_factory()
    panel.set_analysis(SimpleNamespace(marker="completed"))
    warnings = []
    successes = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: successes.append(args))
    # A directory at the requested file path reliably fails on every platform.
    blocked = tmp_path / ("directory" + suffix)
    blocked.mkdir()
    _save_to(monkeypatch, blocked)
    getattr(panel, f"{language}_button").click()
    assert len(warnings) == 1
    assert warnings[0][2]
    assert successes == []
    assert blocked.is_dir()
    assert panel.matlab_button.isEnabled() and panel.python_button.isEnabled()

    destination = tmp_path / ("retry" + suffix)
    _save_to(monkeypatch, destination)
    getattr(panel, f"{language}_button").click()
    assert destination.read_text(encoding="utf-8") == f"{language}: llc / None / completed\n"
    assert len(warnings) == 1
    assert len(successes) == 1


@pytest.mark.parametrize("language,suffix", [("matlab", ".m"), ("python", ".py")])
def test_pfc_selection_routes_both_current_and_voltage_loops(
    language, suffix, panel_factory, stub_export, monkeypatch, tmp_path,
):
    panel = panel_factory("pfc")
    analysis = SimpleNamespace(marker="PFC snapshot")
    panel.set_analysis(analysis)
    assert panel.loop_selector.currentData() == "both"
    for loop in ("both", "current", "voltage", "current"):
        index = panel.loop_selector.findData(loop)
        assert index >= 0
        panel.loop_selector.setCurrentIndex(index)
        path = tmp_path / (loop + suffix)
        _save_to(monkeypatch, path)
        getattr(panel, f"{language}_button").click()
        assert stub_export[-1] == ("pfc", analysis, loop)
        assert path.read_text(encoding="utf-8") == f"{language}: pfc / {loop} / PFC snapshot\n"


@pytest.fixture(scope="module")
def llc_analyses():
    from llc_design.control.analysis import build_small_signal_analysis
    from llc_design.control.digital_loop import (
        PIControllerConfig,
        build_digital_loop_analysis,
    )
    from llc_design.core.spec import LLCDesignSpec
    from llc_design.models.system import LLCSystemAnalyzer

    spec = LLCDesignSpec()
    system = LLCSystemAnalyzer().analyze(spec)
    small = build_small_signal_analysis(spec, system_analysis=system, sample_time_s=20e-6)
    return tuple(
        build_digital_loop_analysis(
            small, controller_config=PIControllerConfig(kp=kp, ti_s=.003),
        )
        for kp in (.002, .004)
    )


@pytest.fixture(scope="module")
def pfc_analyses():
    from pfc_design.control import PFCControlLabConfig, build_pfc_control_lab_analysis

    original = PFCControlLabConfig()
    updated = replace(
        original,
        current_controller=replace(original.current_controller, kp=original.current_controller.kp * 1.5),
        voltage_controller=replace(original.voltage_controller, kp=original.voltage_controller.kp * 1.5),
    )
    return tuple(build_pfc_control_lab_analysis(config) for config in (original, updated))


@pytest.mark.parametrize("language,suffix", [("matlab", ".m"), ("python", ".py")])
def test_llc_view_exports_last_completed_analysis_until_replaced(
    language, suffix, desktop_qapp, export_ui, llc_analyses, monkeypatch, tmp_path,
):
    from llc_design.gui.widgets.digital_loop_view import DigitalLoopView

    view = DigitalLoopView()
    # Plot rendering has separate GUI tests. Exercise the real completion and
    # busy-state callbacks here, without spending time redrawing the Bode plot.
    monkeypatch.setattr(view, "refresh", lambda: None)
    original, updated = llc_analyses
    render = getattr(export_ui, f"render_{language}")
    path = tmp_path / ("LLC 已完成分析" + suffix)
    _save_to(monkeypatch, path)
    try:
        assert not view.research_export.matlab_button.isEnabled()
        assert not view.research_export.python_button.isEnabled()
        view.set_analysis(original)
        expected = render(export_ui.build_llc_research_export(original))
        getattr(view.research_export, f"{language}_button").click()
        assert path.read_text(encoding="utf-8") == expected

        view.kp.setValue(view.kp.value() * 1.5)
        view.sample_us.setValue(view.sample_us.value() + 1)
        assert view.result is original
        getattr(view.research_export, f"{language}_button").click()
        assert path.read_text(encoding="utf-8") == expected

        view.set_busy(True)
        assert not view.research_export.matlab_button.isEnabled()
        assert not view.research_export.python_button.isEnabled()
        view.set_analysis(updated)
        assert not view.research_export.matlab_button.isEnabled()
        assert not view.research_export.python_button.isEnabled()
        view.set_busy(False)
        new_expected = render(export_ui.build_llc_research_export(updated))
        assert new_expected != expected
        getattr(view.research_export, f"{language}_button").click()
        assert path.read_text(encoding="utf-8") == new_expected
    finally:
        view.close()
        desktop_qapp.processEvents()


@pytest.mark.parametrize("language,suffix", [("matlab", ".m"), ("python", ".py")])
@pytest.mark.parametrize("loop", ["both", "current", "voltage"])
def test_pfc_view_exports_last_completed_analysis_until_replaced(
    language, suffix, loop, desktop_qapp, export_ui, pfc_analyses, monkeypatch, tmp_path,
):
    from pfc_design.gui.control_lab_view import PFCControlLabView

    view = PFCControlLabView()
    for method in (
        "_set_bode_results", "_plot_ac_overview", "_plot_ac_control",
        "_plot_switching", "_plot_zero_crossing", "_plot_harmonics", "_show_summary",
    ):
        monkeypatch.setattr(view, method, lambda *args: None)
    # Only transfer-function export is under test; avoid generating unrelated
    # switching/line-cycle waveforms solely for the completion status label.
    line_cycle = SimpleNamespace(
        metrics=SimpleNamespace(input_current_rms_a=1.0, current_error_rms_a=.1),
        signals={"i_inductor": np.ones(2)},
    )
    switching = SimpleNamespace(signals={"inductor_current": np.ones(2)})
    original, updated = pfc_analyses
    render = getattr(export_ui, f"render_{language}")
    path = tmp_path / ("PFC " + loop + suffix)
    _save_to(monkeypatch, path)
    try:
        panel = view.research_export
        assert not panel.matlab_button.isEnabled()
        assert not panel.python_button.isEnabled()
        view.set_result((original, line_cycle, switching))
        panel.loop_selector.setCurrentIndex(panel.loop_selector.findData(loop))
        expected = render(export_ui.build_pfc_research_export(original, loop=loop))
        getattr(panel, f"{language}_button").click()
        assert path.read_text(encoding="utf-8") == expected

        view.current_ctrl["kp"].setValue(view.current_ctrl["kp"].value() * 1.5)
        view.voltage_ctrl["kp"].setValue(view.voltage_ctrl["kp"].value() * 1.5)
        view.inductance.setValue(view.inductance.value() + 1)
        assert view.result[0] is original
        getattr(panel, f"{language}_button").click()
        assert path.read_text(encoding="utf-8") == expected

        view.set_busy(True)
        assert not panel.matlab_button.isEnabled()
        assert not panel.python_button.isEnabled()
        view.set_result((updated, line_cycle, switching))
        assert not panel.matlab_button.isEnabled()
        assert not panel.python_button.isEnabled()
        view.set_busy(False)
        new_expected = render(export_ui.build_pfc_research_export(updated, loop=loop))
        assert new_expected != expected
        getattr(panel, f"{language}_button").click()
        assert path.read_text(encoding="utf-8") == new_expected
    finally:
        view.close()
        desktop_qapp.processEvents()
