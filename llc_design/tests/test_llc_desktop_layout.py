"""Real laptop QScreens, scrolling, solver results and unchanged page ownership.

These are offscreen Qt widget checks, not native OS compositor qualification.
Set LLC_LAYOUT_EVIDENCE_DIR to retain full-window screenshots and geometry.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("width,height", [(1280, 720), (1366, 768), (1440, 900)])
def test_llc_full_bridge_workflow_fits_real_desktop(tmp_path, width, height):
    pytest.importorskip("PySide6")
    config = tmp_path / "screen.json"
    config.write_text(json.dumps({"screens": [{"name": f"desktop-{width}x{height}",
        "width": width, "height": height, "logicalDpi": 96, "logicalBaseDpi": 96}],
        "windowFrameMargins": True}), encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM=f"offscreen:configfile={config}",
        QT_SCALE_FACTOR="1", QT_FONT_DPI="96", MPLBACKEND="Agg", PYTHONPATH=str(ROOT),
        MPLCONFIGDIR=str(tmp_path / "mpl"), XDG_CACHE_HOME=str(tmp_path / "cache"),
        XDG_CONFIG_HOME=str(tmp_path / "config"))
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
        str(width), str(height), str(tmp_path)], env=env, cwd=ROOT,
        capture_output=True, text=True, timeout=240, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'"screen": [{width}, {height}]' in result.stdout


def _exercise_desktop(width, height, output):
    from dataclasses import asdict

    import numpy as np
    from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (
        QApplication,
        QFileDialog,
        QScrollArea,
        QToolBar,
        QToolButton,
    )

    from llc_design.core.spec import LLCDesignSpec, PrimaryTopology
    from llc_design.gui import main_window as module
    from llc_design.gui import theme
    from llc_design.gui.i18n_ui import apply_language
    from llc_design.optimization.sweep import OptimizationConfig

    assert Path(module.__file__).resolve().is_relative_to(ROOT)
    # The update network boundary and file pickers are the only replacements;
    # widget events, workers, analysis, plots, source checks and exports are real.
    module.check_for_updates = lambda *a, **kw: None
    issues = []
    module.show_operation_issue = lambda *a, **kw: issues.append(str(a[1:]))
    app = QApplication.instance() or QApplication([])
    theme.apply_app_theme(app)
    apply_language("zh-Hans", app)
    screen = app.primaryScreen()
    assert screen.geometry().size().toTuple() == (width, height)
    assert screen.logicalDotsPerInch() == 96
    spec = LLCDesignSpec(primary_topology=PrimaryTopology.FULL_BRIDGE,
        ln_ratio=4.5, primary_turns=32, vbus_hold_end_v=320., bus_capacitance_f=.0033)
    window = module.LLCMainWindow(spec)
    window.show()
    QTest.qWait(50)
    assert screen.availableGeometry().contains(window.frameGeometry())
    window.showMaximized()
    QTest.qWait(50)
    pages = tuple(window.tabs.widget(i) for i in range(window.tabs.count()))
    view = window.waveform_view
    digital = window.digital_loop_view
    candidate = window.two_stage_optimization_view
    evidence = Path(os.environ["LLC_LAYOUT_EVIDENCE_DIR"]) if os.environ.get("LLC_LAYOUT_EVIDENCE_DIR") else None
    if evidence:
        evidence.mkdir(parents=True, exist_ok=True)
    measurements = {"screen": [width, height], "minimum": list(window.minimumSizeHint().toTuple()),
        "frame": list(window.frameGeometry().getRect()), "pages": [], "source": module.__file__}

    def settle():
        for _ in range(3):
            app.processEvents()

    def fits():
        settle()
        assert screen.availableGeometry().contains(window.frameGeometry())
        assert window.minimumSizeHint().width() <= window.width()
        assert window.minimumSizeHint().height() <= window.height()
        assert tuple(window.tabs.widget(i) for i in range(window.tabs.count())) == pages
        assert window.tabs.count() == 12
        page = window.tabs.currentWidget()
        assert window.rect().contains(QRect(page.mapTo(window, QPoint()), page.size()))

    def visible(widget):
        settle()
        assert widget.isVisible()
        rect = QRect(widget.mapTo(window, QPoint()), widget.size())
        assert window.rect().contains(rect), (type(widget).__name__, rect, window.rect())
        assert widget.visibleRegion().contains(widget.rect()), (type(widget).__name__, widget.size())
        ancestor = widget.parentWidget()
        while ancestor is not None:
            bounds = QRect(widget.mapTo(ancestor, QPoint()), widget.size())
            assert ancestor.rect().contains(bounds), (type(widget).__name__,
                type(ancestor).__name__, bounds, ancestor.rect())
            ancestor = ancestor.parentWidget()
        return rect

    def click(widget):
        visible(widget)
        assert widget.isEnabled()
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        settle()

    def tab(page):
        index = window.tabs.indexOf(page)
        bar = window.tabs.tabBar()
        for _ in range(window.tabs.count() * 3):
            rect = bar.tabRect(index)
            right = bar.findChild(QToolButton, "ScrollRightButton")
            left = bar.findChild(QToolButton, "ScrollLeftButton")
            edge = right.geometry().left() if right.isVisible() else bar.width()
            if rect.left() >= 0 and rect.right() < edge:
                QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=rect.center())
                settle()
                assert window.tabs.currentWidget() is page
                fits()
                return
            click(left if rect.left() < 0 else right)
        raise AssertionError("Tab could not be reached through its real scroll buttons")

    def scroll_end(scroll, bottom=True):
        bar = scroll.verticalScrollBar()
        initial = bar.value()
        target = bar.maximum() if bottom else bar.minimum()
        for _ in range(30):
            if bar.value() == target:
                break
            event = QWheelEvent(QPointF(bar.rect().center()),
                QPointF(bar.mapToGlobal(bar.rect().center())), QPoint(),
                QPoint(0, -1200 if bottom else 1200), Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            app.sendEvent(bar, event)
            settle()
        assert bar.value() == target, (initial, bar.value(), target)
        return bar.value()

    def wait_workers():
        deadline = time.monotonic() + 180
        while window._active_workers and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        settle()
        assert not window._active_workers
        assert not issues, issues
        fits()

    def shot(name):
        if evidence:
            assert window.grab().save(str(evidence / f"llc-{width}x{height}-{name}.png"))

    fits()
    # Visit every original tab; hidden large pages cannot enlarge the window.
    for page in pages:
        tab(page)
        measurements["pages"].append({"type": type(page).__name__,
            "size": list(page.size().toTuple()), "minimum": list(page.minimumSizeHint().toTuple())})
    tab(pages[0])
    visible(window.topology_view)
    assert window.topology_view.viewport().rect().contains(
        window.topology_view.mapFromScene(window.topology_view.blocks["q4"].sceneBoundingRect()).boundingRect())
    shot("overview")

    tab(view)
    for control in (view.vbus, view.load_percent, view.group_combo,
                    view.fast_button, view.harmonic_button, view.detail_button, view.export_button):
        visible(control)
    click(view.fast_button)
    wait_workers()
    accepted = view.bundle
    assert accepted is not None
    original_arrays = {key: value.values for key, value in accepted.signals.items()}
    source_identity = accepted.metadata["request_identity"]
    # Actual component click after a larger trace group must leave a scrollable,
    # correctly sized two-trace canvas rather than the previous tall canvas.
    tab(pages[0])
    item = window.topology_view.blocks["q4"]
    point = window.topology_view.mapFromScene(item.sceneBoundingRect().center())
    QTest.mouseClick(window.topology_view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    settle()
    assert window.tabs.currentWidget() is view
    assert view.selected_component == "q4"
    wave_scroll = view.findChild(QScrollArea)
    assert len(view._plots) == 2
    assert view.graphics.height() <= max(wave_scroll.viewport().height(), view.graphics.minimumHeight()) + 1
    for plot, key in zip(view._plots, view._selected_keys()):
        assert plot.height() >= 180
        np.testing.assert_array_equal(plot.listDataItems()[0].yData, original_arrays[key])
    scroll_end(wave_scroll, False)
    first_rect = view.graphics.mapFromScene(view._plots[0].sceneBoundingRect()).boundingRect()
    assert wave_scroll.viewport().rect().contains(QRect(
        view.graphics.mapTo(wave_scroll.viewport(), first_rect.topLeft()), first_rect.size()))
    shot("waveform-top")
    scroll_end(wave_scroll)
    last_rect = view.graphics.mapFromScene(view._plots[-1].sceneBoundingRect()).boundingRect()
    last_bottom = view.graphics.mapTo(wave_scroll.viewport(), last_rect.bottomLeft())
    assert 0 <= last_bottom.y() <= wave_scroll.viewport().height() + 1
    shot("waveform-scrolled")

    # Scroll the genuinely tall digital-control content and operate its existing
    # run/export controls. The Bode canvas retains its readable 430px minimum.
    tab(digital)
    assert window.tabs.currentWidget() is digital
    assert digital.page_scroll.verticalScrollBar().maximum() > 0
    shot("digital-top")
    scroll_end(digital.page_scroll)
    visible(digital.run_button)
    visible(digital.codegen_button)
    click(digital.run_button)
    wait_workers()
    loop_result = digital.result
    assert loop_result is not None
    assert digital.research_export.analysis is loop_result
    scroll_end(digital.page_scroll)
    visible(digital.canvas)
    assert digital.canvas.height() >= 430
    shot("digital-bode-scrolled")
    scroll_end(digital.page_scroll, False)
    visible(digital.research_export.matlab_button)
    visible(digital.research_export.python_button)

    # Real bounded nominal TD candidate run, with original ownership/status
    # gates; the table, details and report action stay reachable at laptop size.
    tab(candidate)
    cfg = OptimizationConfig((spec.ln_ratio,), (spec.q_full_load,),
        (spec.resonant_frequency_hz,), (spec.primary_turns,), (spec.secondary_turns,),
        (spec.primary_device,), (spec.sr_parallel_devices_per_position,))
    candidate.config_text.setPlainText(json.dumps(asdict(cfg)))
    candidate.maximum_candidates.setValue(1)
    candidate.top_n.setValue(1)
    settle()
    for control in (candidate.config_text, candidate.weights_text, candidate.maximum_candidates,
                    candidate.top_n, candidate.verification_scope, candidate.run_button,
                    candidate.cancel_button, candidate.apply_button, candidate.report_button,
                    candidate.table, candidate.details):
        visible(control)
    click(candidate.run_button)
    wait_workers()
    assert candidate.result is not None
    verified = candidate.selected_candidate()
    assert verified is not None
    assert candidate.table.viewport().height() >= candidate.table.rowHeight(0)
    row_point = candidate.table.visualItemRect(candidate.table.item(0, 0)).center()
    QTest.mouseClick(candidate.table.viewport(), Qt.MouseButton.LeftButton, pos=row_point)
    settle()
    visible(candidate.report_button)
    visible(candidate.apply_button)
    assert candidate.report_button.isEnabled() and candidate.apply_button.isEnabled()
    assert candidate.details.toPlainText()
    result_identity = candidate.result
    shot("candidate-results")
    report = output / "candidate.zip"
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **kw: (str(report), "ZIP"))
    click(candidate.report_button)
    wait_workers()
    assert report.exists() and report.stat().st_size > 0
    with zipfile.ZipFile(report) as archive:
        provenance = json.loads(archive.read("optimization_provenance.json"))
        sources = provenance["selected_candidate"]["sources"]
        assert any(source["waveform_sha256"] == verified.analysis.nominal.loss_waveform_sha256
                   for source in sources)

    # Repeated navigation and scrolling do not reinterpret any electrical data.
    for page in (digital, view, candidate, digital, view, candidate):
        tab(page)
        assert view.bundle is accepted
        assert view.bundle.metadata["request_identity"] == source_identity
        assert all(view.bundle.signals[key].values is values for key, values in original_arrays.items())
        assert digital.result is loop_result
        assert digital.research_export.analysis is loop_result
        assert candidate.result is result_identity
        assert candidate.selected_candidate() is verified
    project = output / "desktop.json"
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **kw: (str(project), "JSON"))
    toolbar = window.findChild(QToolBar, "llc_main_toolbar")
    save_action = next(action for action in toolbar.actions() if action.text() == "保存工程 JSON")
    click(toolbar.widgetForAction(save_action))
    payload = json.loads(project.read_text(encoding="utf-8"))
    assert payload["waveform_session"]["component"] == "q4"
    assert payload["optimization_session"]["candidates"]
    measurements.update(waveform_scroll=wave_scroll.verticalScrollBar().maximum(),
        digital_scroll=digital.page_scroll.verticalScrollBar().maximum(),
        candidate_table_height=candidate.table.viewport().height(),
        digital_canvas_size=list(digital.canvas.size().toTuple()),
        statistics_height=view.statistics.height(),
        waveform_identity=source_identity, tab_identities_preserved=True,
        waveform_array_identities_preserved=True, report_bytes=report.stat().st_size)
    if evidence:
        (evidence / f"llc-{width}x{height}-geometry.json").write_text(
            json.dumps(measurements, indent=2), encoding="utf-8")
    print(json.dumps(measurements), flush=True)
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


if __name__ == "__main__":
    _exercise_desktop(int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3]))
