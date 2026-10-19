"""Native font metrics must not turn dock content into window constraints."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("font_points", [10, 12])
def test_narrow_dock_and_window_frame_with_larger_font(tmp_path, font_points):
    _run_metrics(tmp_path, font_points)


def test_native_scrollbar_minima_fit_small_desktop(tmp_path):
    # Controlled Linux reproduction of macOS Fusion's larger scroll-area
    # minima. Native-font runs above remain the actual platform acceptance.
    _run_metrics(tmp_path, 12, native_scrollbars=True)


def _run_metrics(tmp_path, font_points, native_scrollbars=False):
    pytest.importorskip("PySide6")
    config = tmp_path / "screen.json"
    config.write_text(json.dumps({"screens": [{"name": "small-desktop",
        "width": 1280, "height": 720, "logicalDpi": 96, "logicalBaseDpi": 96}],
        "windowFrameMargins": True}), encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM=f"offscreen:configfile={config.name}",
        QT_SCALE_FACTOR="1", QT_FONT_DPI="96", MPLBACKEND="Agg", PYTHONPATH=str(ROOT),
        MPLCONFIGDIR=str(tmp_path / "mpl"), XDG_CACHE_HOME=str(tmp_path / "cache"),
        XDG_CONFIG_HOME=str(tmp_path / "config"))
    command = [sys.executable, str(Path(__file__).resolve()), str(font_points)]
    if native_scrollbars:
        command.append("native-scrollbars")
    result = subprocess.run(command,
        env=env, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"horizontal_scroll": 0' in result.stdout
    assert '"disposed": true' in result.stdout


def _bounds_in(widget, ancestor):
    from PySide6.QtCore import QPoint, QRect

    return QRect(widget.mapTo(ancestor, QPoint()), widget.size())


def _scroll_whole_control_into_view(scroll, control):
    # Qt 6.11.2 ensureWidgetVisible() may target ImCursorRectangle, then return
    # while a spin box's outer border is still clipped. Test reachability of the
    # whole control via the real scrollbar, not merely its insertion cursor.
    # https://github.com/qt/qtbase/blob/v6.11.2/src/widgets/widgets/qscrollarea.cpp#L415-L443
    bounds = _bounds_in(control, scroll.widget())
    center = bounds.center()
    # ensureVisible uses a strict > edge comparison; include the final pixel.
    scroll.ensureVisible(center.x(), center.y(),
        bounds.width() // 2 + 1, bounds.height() // 2 + 1)


def _assert_whole_control_visible(scroll, name, control):
    assert (control.isVisible() and not control.isHidden()
        and control.isVisibleTo(scroll.widget())), (name, "hidden control")
    assert scroll.horizontalScrollBar().maximum() == 0, (name, "horizontal scroll")
    bounds = _bounds_in(control, scroll.viewport())
    assert scroll.viewport().rect().contains(bounds), (name, bounds, scroll.viewport().rect())
    assert control.visibleRegion().contains(control.rect()), (name, "clipped visible region")


def _control_snapshot(scroll, name, control):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    query = Qt.InputMethodQuery.ImCursorRectangle
    def bar_state(bar):
        return {"minimum": bar.minimum(), "maximum": bar.maximum(),
            "value": bar.value(), "page_step": bar.pageStep()}

    return {"name": name, "type": type(control).__name__,
        "object_name": control.objectName(), "visible": control.isVisible(),
        "hidden": control.isHidden(), "visible_to_content": control.isVisibleTo(scroll.widget()),
        "enabled": control.isEnabled(),
        "focus": control.hasFocus(), "geometry": list(control.geometry().getRect()),
        "bounds": list(_bounds_in(control, scroll.viewport()).getRect()),
        "content_bounds": list(_bounds_in(control, scroll.widget()).getRect()),
        "cursor_rect": list(control.inputMethodQuery(query).getRect()),
        "default_cursor_rect": list(QWidget.inputMethodQuery(control, query).getRect()),
        "whole_region_visible": control.visibleRegion().contains(control.rect()),
        "viewport": list(scroll.viewport().rect().getRect()),
        "content": list(scroll.widget().geometry().getRect()),
        "horizontal": bar_state(scroll.horizontalScrollBar()),
        "vertical": bar_state(scroll.verticalScrollBar())}


def _parameter_controls(window):
    from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit, QPushButton

    controls = {f"fields.{name}": control for name, control in window.fields.items()}
    for name in ("magnetics_mode_combo", "parameter_mode_combo", "topology",
                 "integrated_source_combo", "integrated_core_combo", "integrated_note",
                 "primary_device_combo", "copy_auto_button", "run_button", "fast_wave_button",
                 "hb_wave_button", "detail_wave_button", "small_signal_button"):
        controls[name] = getattr(window, name)
    content = window.parameter_dock.widget().widget()
    for text in ("双漏感参考例（非实测）", "可达参考例（非实测）"):
        matches = [button for button in content.findChildren(QPushButton) if button.text() == text]
        assert len(matches) == 1, (text, "missing or duplicate reference-example action")
        controls[text] = matches[0]
    # A visible-state filter would silently drop hidden regressions. Include all
    # edits/actions, disabled ones too; only spin boxes' internal edits are owned
    # subcontrols rather than additional user inputs.
    actual = {control for cls in (QAbstractSpinBox, QComboBox, QLineEdit, QPushButton)
        for control in content.findChildren(cls)
        if not isinstance(control.parentWidget(), QAbstractSpinBox)}
    assert set(controls.values()) == actual, "Parameter control coverage changed"
    return controls


def _layout_snapshot(window):
    from PySide6.QtWidgets import QStyle, QToolBar

    def geometry(widget):
        return {"type": type(widget).__name__,
            "minimum": list(widget.minimumSizeHint().toTuple()),
            "geometry": list(widget.geometry().getRect())}

    candidate = window.two_stage_optimization_view
    return {"window": geometry(window), "tabs": geometry(window.tabs),
        "toolbar": geometry(window.findChild(QToolBar, "llc_main_toolbar")),
        "statusbar": geometry(window.statusBar()),
        "pages": [{"title": window.tabs.tabText(index), **geometry(window.tabs.widget(index))}
            for index in range(window.tabs.count())],
        "candidate_scroll_controls": {name: geometry(getattr(candidate, name))
            for name in ("config_text", "weights_text", "table", "details")},
        "scrollbar_extent": window.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent),
        "scrollbar_slider_min": window.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarSliderMin)}


def _exercise_candidate_editors(window, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel

    candidate = window.two_stage_optimization_view
    controls = (candidate.config_text, candidate.weights_text, candidate.maximum_candidates,
        candidate.top_n, candidate.verification_scope, candidate.run_button,
        candidate.cancel_button, candidate.apply_button, candidate.report_button,
        candidate.table, candidate.details)
    for control in controls:
        assert control.isVisible() and not control.isHidden()
        assert window.rect().contains(_bounds_in(control, window))
        assert control.visibleRegion().contains(control.rect()), type(control).__name__
    config, weights = candidate.config_text, candidate.weights_text
    assert config.geometry().right() < weights.geometry().left()
    assert config.geometry().top() == weights.geometry().top()
    for editor in (config, weights):
        assert len([label for label in candidate.findChildren(QLabel) if label.buddy() is editor]) == 1
    original = candidate.options()
    evidence = []
    for editor, key in ((config, "config"), (weights, "ranking_weights")):
        # Real keyboard entry of a long JSON line followed by a vertically
        # scrollable document, preserving the exact original option values.
        text = json.dumps(original[key])
        editor.setFocus()
        editor.selectAll()
        QTest.keyClicks(editor, text)
        for _ in range(40):
            QTest.keyClick(editor, Qt.Key.Key_Return)
        app.processEvents()
        assert editor.toPlainText() == text + "\n" * 40
        assert candidate.options() == original
        bar = editor.verticalScrollBar()
        assert bar.maximum() > 0
        bar.setValue(bar.minimum())
        assert bar.value() == bar.minimum()
        bar.setValue(bar.maximum())
        assert bar.value() == bar.maximum()
        assert editor.visibleRegion().contains(editor.rect())
        evidence.append({"editor": key, "json_characters": len(text),
            "vertical_scroll": bar.maximum(), "geometry": list(editor.geometry().getRect())})
    candidate.restore_options(original)
    assert candidate.options() == original
    assert not candidate.apply_button.isEnabled() and not candidate.report_button.isEnabled()
    return evidence


@pytest.mark.parametrize("focus", [False, True])
@pytest.mark.parametrize("height", [32, 34, 35])
def test_whole_control_scroll_and_visibility_gate(monkeypatch, focus, height):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QScrollArea, QWidget

    from llc_design.gui.lifetime import dispose_widgets

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    scroll = QScrollArea()
    content = QWidget()
    control = QDoubleSpinBox(content)
    try:
        scroll.resize(265, 620)
        content.resize(240, 1800)
        control.setGeometry(93, 800, 134, height)
        scroll.setWidget(content)
        scroll.show()
        app.processEvents()
        if focus:
            control.setFocus()
        else:
            control.clearFocus()
        app.processEvents()
        assert control.hasFocus() is focus
        viewport = scroll.viewport()
        cursor = control.inputMethodQuery(Qt.InputMethodQuery.ImCursorRectangle)
        # Derive an edge case from actual native cursor metrics. No substituted
        # size hints or mocked visibility: cursor inside, outer border outside.
        assert cursor.bottom() < control.height() - 1
        edge = control.y() + control.height() - viewport.height()
        scroll.verticalScrollBar().setValue(edge - 1)
        app.processEvents()
        assert viewport.rect().contains(QRect(control.mapTo(viewport, cursor.topLeft()), cursor.size()))
        with pytest.raises(AssertionError):
            _assert_whole_control_visible(scroll, "probe", control)
        before = scroll.verticalScrollBar().value()
        scroll.ensureWidgetVisible(control)
        app.processEvents()
        assert scroll.verticalScrollBar().value() == before
        with pytest.raises(AssertionError):
            _assert_whole_control_visible(scroll, "probe", control)
        _scroll_whole_control_into_view(scroll, control)
        app.processEvents()
        _assert_whole_control_visible(scroll, "probe", control)
        scroll.verticalScrollBar().setValue(control.y() + 1)
        assert _bounds_in(control, viewport).top() == -1
        with pytest.raises(AssertionError):
            _assert_whole_control_visible(scroll, "top-clipped probe", control)
        _scroll_whole_control_into_view(scroll, control)
        app.processEvents()
        _assert_whole_control_visible(scroll, "top-clipped probe", control)
        control.setEnabled(False)
        _assert_whole_control_visible(scroll, "disabled probe", control)
        control.hide()
        assert viewport.rect().contains(_bounds_in(control, viewport))
        with pytest.raises(AssertionError, match="hidden control"):
            _assert_whole_control_visible(scroll, "hidden probe", control)
        control.show()
        app.processEvents()
        # A real oversized control remains unreachable; the helper cannot turn
        # clipping into a pass by moving or resizing the tested widget.
        control.resize(control.width(), viewport.height() + 1)
        _scroll_whole_control_into_view(scroll, control)
        app.processEvents()
        with pytest.raises(AssertionError):
            _assert_whole_control_visible(scroll, "oversized probe", control)
    finally:
        dispose_widgets([scroll])


def _exercise_metrics(font_points, native_scrollbars=False):
    from PySide6.QtCore import Qt, qVersion
    from PySide6.QtGui import QFontInfo, QFontMetrics
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle
    from shiboken6 import isValid

    from llc_design.gui import main_window as module
    from llc_design.gui import theme
    from llc_design.gui.i18n_ui import apply_language
    from llc_design.gui.lifetime import dispose_widgets

    module.check_for_updates = lambda *args, **kwargs: None
    app = QApplication([])
    theme.apply_app_theme(app)
    apply_language("zh-Hans", app)
    if native_scrollbars:
        class NativeScrollMetrics(QProxyStyle):
            def pixelMetric(self, metric, option=None, widget=None):
                # Qt 6.11.2: QAbstractScrollArea's minimum is three scrollbar
                # extents + minimum slider length (+ frame). macOS CI records
                # 88px without a frame: 3 * 18 + 34, versus Linux's 68px.
                # This changes style metrics, never QWidget size hints/fonts.
                if metric == QStyle.PixelMetric.PM_ScrollBarExtent:
                    return 18
                if metric == QStyle.PixelMetric.PM_ScrollBarSliderMin:
                    return 34
                return super().pixelMetric(metric, option, widget)
        app.setStyle(NativeScrollMetrics("Fusion"))
    # Exercise different real metrics, without changing the production font or
    # substituting size hints. A fresh process keeps application fonts isolated.
    font = app.font()
    font.setPointSizeF(font_points)
    app.setFont(font)
    evidence = Path(os.environ.get("LLC_LAYOUT_EVIDENCE_DIR", Path.cwd()))
    evidence.mkdir(parents=True, exist_ok=True)
    prefix = f"llc-1280x720-{font_points:g}pt-dock"
    if native_scrollbars:
        prefix += "-controlled-scrollbars"
    font_info = QFontInfo(app.font())
    diagnostics = {"platform": sys.platform, "qt_platform": app.platformName(),
        "qt_version": qVersion(), "source": str(ROOT), "requested_font_points": font_points,
        "font": {"requested": app.font().toString(), "resolved_family": font_info.family(),
            "resolved_point_size": font_info.pointSizeF(), "height": QFontMetrics(app.font()).height()},
        "controlled_scrollbar_metrics": native_scrollbars,
        "controls": [], "completed": False}
    window = module.LLCMainWindow()
    scroll = window.parameter_dock.widget()
    try:
        screen = window.screen()
        assert screen.availableGeometry().size().toTuple() == (1280, 720)
        pages = tuple(window.tabs.widget(i) for i in range(window.tabs.count()))

        def fits():
            QTest.qWait(20)
            assert screen.availableGeometry().contains(window.frameGeometry()), (
                screen.availableGeometry(), window.frameGeometry(), window.minimumSizeHint())
            assert window.minimumSizeHint().width() <= window.width()
            assert window.minimumSizeHint().height() <= window.height()
            assert tuple(window.tabs.widget(i) for i in range(window.tabs.count())) == pages

        window.show()
        diagnostics["startup_layout"] = _layout_snapshot(window)
        fits()
        # Client dimensions alone do not include the platform frame margins.
        assert window.frameGeometry().width() > window.width()
        window.showMaximized()
        fits()
        for page in pages:
            window.tabs.setCurrentWidget(page)
            fits()
        candidate = window.two_stage_optimization_view
        diagnostics["candidate_editors"] = _exercise_candidate_editors(window, app)
        fits()
        for message in (
            "Diagnostic boundary remains visible.\n" * 20 + "Final engineering warning.",
            ("TD verification scope: nominal point only; other operating-point losses remain FHA\n"
             "Stage B examines 1 of 1 FHA-feasible candidates; retains top 1 ranked survivors\n"
             "*理想 TD 损耗验证；不等同硬件签核。"),
            "已取消/丢弃优化。当前候选结束后释放线程；迟到结果不会应用。",
        ):
            candidate.status.setText(message)
            fits()
            # Wrapping result messages remains readable through vertical scroll,
            # rather than raising this page's minimum height on a small screen.
            status_scroll = candidate.status_scroll
            assert status_scroll.horizontalScrollBar().maximum() == 0
            if message.endswith("Final engineering warning."):
                assert status_scroll.verticalScrollBar().maximum() > 0
            status_scroll.verticalScrollBar().setValue(status_scroll.verticalScrollBar().maximum())
            bottom = candidate.status.mapTo(status_scroll.viewport(), candidate.status.rect().bottomLeft())
            assert 0 <= bottom.y() < status_scroll.viewport().height()

        scroll = window.parameter_dock.widget()
        window.resizeDocks([window.parameter_dock], [scroll.minimumWidth()], Qt.Orientation.Horizontal)
        fits()
        assert scroll.width() == scroll.minimumWidth()
        controls = _parameter_controls(window)
        for load_integrated in (False, True):
            mode = "integrated" if load_integrated else "normal"
            if load_integrated:
                window._load_reachable_integrated_example()
            # Apply the mode's pending layout before choosing the scroll target.
            app.processEvents()
            for name, control in controls.items():
                record = {"mode": mode, "name": name,
                    "before": _control_snapshot(scroll, name, control)}
                diagnostics["controls"].append(record)
                _scroll_whole_control_into_view(scroll, control)
                record["scrolled"] = _control_snapshot(scroll, name, control)
                app.processEvents()
                record["settled"] = _control_snapshot(scroll, name, control)
                _assert_whole_control_visible(scroll, name, control)
            assert scroll.verticalScrollBar().value() > 0
            fits()
            assert window.grab().save(str(evidence / f"{prefix}-{mode}.png"))
        window.close()
        window.show()
        fits()
        measurements = {"font": app.font().toString(),
            "frame": list(window.frameGeometry().getRect()),
            "minimum": list(window.minimumSizeHint().toTuple()),
            "dock_viewport": list(scroll.viewport().size().toTuple()),
            "horizontal_scroll": scroll.horizontalScrollBar().maximum()}
        diagnostics["completed"] = True
    except BaseException as exc:
        diagnostics["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        diagnostics["failure_screenshot_saved"] = window.grab().save(str(evidence / f"{prefix}-failure.png"))
        raise
    finally:
        try:
            diagnostics.update(frame=list(window.frameGeometry().getRect()),
                layout=_layout_snapshot(window),
                minimum=list(window.minimumSizeHint().toTuple()),
                viewport=list(scroll.viewport().rect().getRect()),
                controls_at_exit=[_control_snapshot(scroll, name, control)
                    for name, control in controls.items()] if "controls" in locals() else [])
            (evidence / f"{prefix}.json").write_text(
                json.dumps(diagnostics, indent=2, ensure_ascii=False), encoding="utf-8")
        finally:
            dispose_widgets([window])
    assert all(not isValid(widget) for widget in
        (window, candidate, candidate.status_scroll, candidate.status))
    print(json.dumps({**measurements, "disposed": True}), flush=True)


if __name__ == "__main__":
    _exercise_metrics(float(sys.argv[1]), "native-scrollbars" in sys.argv[2:])
