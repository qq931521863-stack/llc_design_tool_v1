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
    pytest.importorskip("PySide6")
    config = tmp_path / "screen.json"
    config.write_text(json.dumps({"screens": [{"name": "small-desktop",
        "width": 1280, "height": 720, "logicalDpi": 96, "logicalBaseDpi": 96}],
        "windowFrameMargins": True}), encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM=f"offscreen:configfile={config}",
        QT_SCALE_FACTOR="1", QT_FONT_DPI="96", MPLBACKEND="Agg", PYTHONPATH=str(ROOT),
        MPLCONFIGDIR=str(tmp_path / "mpl"), XDG_CACHE_HOME=str(tmp_path / "cache"),
        XDG_CONFIG_HOME=str(tmp_path / "config"))
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(font_points)],
        env=env, cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"horizontal_scroll": 0' in result.stdout
    assert '"disposed": true' in result.stdout


def _exercise_metrics(font_points):
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from shiboken6 import isValid

    from llc_design.gui import main_window as module
    from llc_design.gui import theme
    from llc_design.gui.i18n_ui import apply_language
    from llc_design.gui.lifetime import dispose_widgets

    module.check_for_updates = lambda *args, **kwargs: None
    app = QApplication([])
    theme.apply_app_theme(app)
    apply_language("zh-Hans", app)
    # Exercise different real metrics, without changing the production font or
    # substituting size hints. A fresh process keeps application fonts isolated.
    font = app.font()
    font.setPointSizeF(font_points)
    app.setFont(font)
    window = module.LLCMainWindow()
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
        fits()
        # Client dimensions alone do not include the platform frame margins.
        assert window.frameGeometry().width() > window.width()
        window.showMaximized()
        fits()
        for page in pages:
            window.tabs.setCurrentWidget(page)
            fits()
        candidate = window.two_stage_optimization_view
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
        controls = (*window.fields.values(), window.magnetics_mode_combo,
            window.parameter_mode_combo, window.integrated_source_combo,
            window.integrated_core_combo, window.integrated_note,
            window.run_button, window.fast_wave_button, window.hb_wave_button,
            window.detail_wave_button, window.small_signal_button)
        for load_integrated in (False, True):
            if load_integrated:
                window._load_reachable_integrated_example()
            for control in controls:
                scroll.ensureWidgetVisible(control)
                QTest.qWait(1)
                assert scroll.horizontalScrollBar().maximum() == 0
                bounds = QRect(control.mapTo(scroll.viewport(), QPoint()), control.size())
                assert scroll.viewport().rect().contains(bounds), (type(control).__name__, bounds)
            assert scroll.verticalScrollBar().value() > 0
            fits()
        window.close()
        window.show()
        fits()
        measurements = {"font": app.font().toString(),
            "frame": list(window.frameGeometry().getRect()),
            "minimum": list(window.minimumSizeHint().toTuple()),
            "dock_viewport": list(scroll.viewport().size().toTuple()),
            "horizontal_scroll": scroll.horizontalScrollBar().maximum()}
    finally:
        dispose_widgets([window])
    assert all(not isValid(widget) for widget in
        (window, candidate, candidate.status_scroll, candidate.status))
    print(json.dumps({**measurements, "disposed": True}), flush=True)


if __name__ == "__main__":
    _exercise_metrics(float(sys.argv[1]))
