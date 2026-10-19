"""Real maximized Qt screens, launcher clicks and PSD topology calculations.

Each subprocess gets a configured offscreen QScreen, rather than resizing a
window beyond the default 800x800 screen. These are Linux/offscreen checks,
not native Windows/macOS installation or compositor qualification.
Set PSD_LAUNCHER_EVIDENCE_DIR to retain full QWidget grabs and geometry JSON.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DESKTOPS = [(1280, 720), (1366, 768), (1440, 900), (1920, 1080)]


@pytest.mark.parametrize("width,height", DESKTOPS)
def test_maximized_launcher_entries_and_real_topology_clicks(tmp_path, width, height):
    pytest.importorskip("PySide6")
    config = tmp_path / "screen.json"
    config.write_text(json.dumps({
        "screens": [{"name": f"desktop-{width}x{height}", "width": width,
                     "height": height, "logicalDpi": 96, "logicalBaseDpi": 96}],
        "windowFrameMargins": True,
    }), encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM=f"offscreen:configfile={config.name}",
               QT_SCALE_FACTOR="1", QT_FONT_DPI="96", MPLBACKEND="Agg",
               MPLCONFIGDIR=str(tmp_path / "mpl"), XDG_CACHE_HOME=str(tmp_path / "cache"),
               XDG_CONFIG_HOME=str(tmp_path / "config"), PYTHONPATH=str(ROOT))
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(width), str(height)],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert f'"screen": [{width}, {height}]' in completed.stdout


def _exercise_desktop(width, height):
    import numpy as np
    import pyqtgraph as pg
    from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QFrame, QLabel, QPushButton

    from llc_design.core.spec import LLCDesignSpec
    from llc_design.gui import launcher, theme
    from llc_design.gui import main_window as llc_module
    from llc_design.gui.i18n_ui import apply_language
    from pfc_design.gui import main_window as pfc_module
    from power_stage_design import gui as psd_module
    from power_stage_design.schematic import layout, schematic_size

    assert Path(launcher.__file__).resolve().is_relative_to(ROOT)
    assert Path(psd_module.__file__).resolve().is_relative_to(ROOT)
    # Only the unrelated network update boundary is disabled. Real controller,
    # windows, Qt events, topology inputs and numerical calculations run below.
    llc_module.check_for_updates = lambda *args, **kwargs: None
    pfc_module.check_for_updates = lambda *args, **kwargs: None
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    theme.apply_app_theme(app)
    apply_language("zh-Hans", app)
    screen = app.primaryScreen()
    assert screen.geometry().size().toTuple() == (width, height)
    assert screen.devicePixelRatio() == 1
    assert screen.logicalDotsPerInch() == 96
    evidence = os.environ.get("PSD_LAUNCHER_EVIDENCE_DIR")
    directory = Path(evidence) if evidence else None
    if directory:
        directory.mkdir(parents=True, exist_ok=True)
    measurements = {
        "platform": app.platformName(), "screen": [width, height],
        "device_pixel_ratio": screen.devicePixelRatio(),
        "logical_dpi": screen.logicalDotsPerInch(), "font": app.font().toString(),
        "launcher_source": launcher.__file__, "topology_clicks": [],
    }

    def visible(widget, window):
        assert widget.isVisible(), widget.objectName()
        rect = QRect(widget.mapTo(window, QPoint()), widget.size())
        assert window.rect().contains(rect), (widget.objectName(), rect, window.rect())
        assert widget.visibleRegion().contains(widget.rect()), widget.objectName()
        return rect

    def inspect_selector(dialog):
        assert dialog.isMaximized()
        assert screen.availableGeometry().contains(dialog.frameGeometry())
        psd_card = dialog.findChild(QFrame, "power_stage_designer_card")
        psd_button = dialog.findChild(QPushButton, "power_stage_designer_button")
        assert psd_card is not None and psd_button is not None
        text = " ".join(label.text() for label in psd_card.findChildren(QLabel))
        for expected in ("功率级设计", "Power Stage Designer", "Buck", "Boost", "Buck-Boost", "全桥"):
            assert expected in text
        psd_rect = visible(psd_card, dialog)
        assert psd_button.parentWidget() is psd_card
        for name in ("guided_system_design_card", "llc_expert_card", "pfc_expert_card",
                     "control_tools_card", "fra_designer_card", "dab_expert_card"):
            card = dialog.findChild(QFrame, name)
            assert card is not None
            assert psd_rect.bottom() < visible(card, dialog).top()
        for label in dialog.findChildren(QLabel):
            visible(label, dialog)
            assert label.height() >= label.heightForWidth(label.width()), (
                label.text(), label.size().toTuple(), label.heightForWidth(label.width()))
        for button in dialog.findChildren(QPushButton):
            visible(button, dialog)
            assert button.width() >= button.minimumSizeHint().width(), button.text()
            assert dialog.childAt(button.mapTo(dialog, button.rect().center())) is button
        # The shared contact strip is a QWidget, in the same footer row as exit.
        from PySide6.QtWidgets import QWidget
        contacts = dialog.findChild(QWidget, "author_contact_strip")
        contact_rect = visible(contacts, dialog)
        exit_rect = visible(dialog.findChild(QPushButton, "launcher_exit_button"), dialog)
        assert contact_rect.height() <= 35
        assert abs(contact_rect.center().y() - exit_rect.center().y()) <= 3
        measurements["launcher_frame"] = list(dialog.frameGeometry().getRect())
        measurements["launcher_client"] = list(dialog.size().toTuple())
        measurements["launcher_minimum"] = list(dialog.minimumSize().toTuple())
        measurements["launcher_minimum_hint"] = list(dialog.minimumSizeHint().toTuple())
        if directory:
            assert dialog.grab().save(str(directory / f"launcher-{width}x{height}-100pct.png"))

    # Every original entry remains directly clickable, including Guided Design.
    for card_name, route in (
        ("guided_system_design_card", "system_modeling"), ("llc_expert_card", "llc"),
        ("pfc_expert_card", "pfc"), ("control_tools_card", "control"),
        ("fra_designer_card", "fra"), ("dab_expert_card", "dab"),
    ):
        dialog = launcher.WorkspaceSelectionDialog()
        dialog.show()
        app.processEvents()
        inspect_selector(dialog)
        button = dialog.findChild(QFrame, card_name).findChild(QPushButton)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert dialog.selected_workspace == route
        assert dialog.result() == QDialog.DialogCode.Accepted
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    if width == 1280:
        dialog = launcher.WorkspaceSelectionDialog()
        dialog.setWindowState(Qt.WindowState.WindowNoState)
        dialog.resize(dialog.minimumSize())
        dialog.show()
        QTest.qWait(50)
        assert dialog.size() == dialog.minimumSize()
        assert screen.availableGeometry().contains(dialog.frameGeometry())
        for label in dialog.findChildren(QLabel):
            visible(label, dialog)
            assert label.height() >= label.heightForWidth(label.width()), label.text()
        for button in dialog.findChildren(QPushButton):
            visible(button, dialog)
        if directory:
            assert dialog.grab().save(str(directory / "launcher-minimum-1024x620.png"))
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    controller = launcher.WorkspaceApplicationController(LLCDesignSpec())
    errors = []

    def enter_power_stage():
        try:
            dialog = app.activeModalWidget()
            assert isinstance(dialog, launcher.WorkspaceSelectionDialog)
            QTest.qWait(50)  # Let the real maximized-window layout/paint events settle.
            inspect_selector(dialog)
            QTest.mouseClick(dialog.findChild(QPushButton, "power_stage_designer_button"),
                             Qt.MouseButton.LeftButton)
        except AssertionError as exc:
            errors.append(exc)
            if app.activeModalWidget():
                app.activeModalWidget().reject()

    try:
        # Exercise the real modal start path, not show_workspace in isolation.
        QTimer.singleShot(0, enter_power_stage)
        started = controller.start()
        assert not errors, errors
        assert started
        app.processEvents()
        window = controller.power_stage_window
        assert window.isVisible() and window.isMaximized()
        assert screen.availableGeometry().contains(window.frameGeometry())
        visible(window.topology_combo, window)
        measurements["psd_frame"] = list(window.frameGeometry().getRect())
        measurements["psd_minimum_hint"] = list(window.minimumSizeHint().toTuple())
        topologies = ("T01", "T02", "T03", "T05", "T17", "T18") if width == 1280 else ("T01", "T02", "T03", "T17")

        def click_topology(topology):
            combo = window.topology_combo
            assert not combo.view().isVisible(), (topology, "popup left open")
            QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
            QTest.qWait(QApplication.doubleClickInterval() + 10)
            assert combo.view().isVisible(), (topology, "popup did not open")
            assert max(combo.fontMetrics().horizontalAdvance(combo.itemText(row))
                       for row in range(combo.count())) + 24 <= combo.view().viewport().width()
            assert screen.availableGeometry().contains(combo.view().window().frameGeometry())
            index = combo.model().index(combo.findData(topology), 0)
            combo.view().scrollTo(index)
            QTest.qWait(50)
            option = combo.view().visualRect(index)
            visible_option = option.intersected(combo.view().viewport().rect())
            assert visible_option.height() == option.height()
            assert combo.fontMetrics().horizontalAdvance(combo.itemText(index.row())) + 24 <= visible_option.width()
            QTest.mouseMove(combo.view().viewport(), visible_option.center())
            QTest.mouseClick(combo.view().viewport(), Qt.MouseButton.LeftButton, pos=visible_option.center())
            app.processEvents()
            assert combo.currentData() == topology, (topology, combo.currentData(), visible_option.getRect())
            assert not combo.view().isVisible(), (topology, "popup did not close")
            measurements["topology_clicks"].append(topology)

        for topology in topologies:
            click_topology(topology)
            visible(window.preset_button, window)
            QTest.mouseClick(window.preset_button, Qt.MouseButton.LeftButton)
            app.processEvents()
            deadline = time.monotonic() + 45
            while window._jobs and time.monotonic() < deadline:
                QTest.qWait(10)
            assert not window._jobs, window.status.text()
            assert window.result is not None, window.status.text()
            assert window.result.topology_id == topology
            assert window.current_plot.listDataItems()
            assert window.voltage_plot.listDataItems()
            assert window.stress_table.rowCount() > 0
            assert window.report_action.isEnabled()
            assert screen.availableGeometry().contains(window.frameGeometry())
            QTest.qWait(50)
            for plot in (window.current_plot, window.voltage_plot):
                assert plot.plotItem.vb.height() > 80
            if topology == "T17":
                plots = (window.current_plot, window.voltage_plot)
                measurements["expanded_trace_heights"] = [plot.plotItem.vb.height() for plot in plots]
                before = [[values.copy() for values in plot.listDataItems()[0].getData()]
                          for plot in plots]
                scrollbar = window.stage_scroll.verticalScrollBar()
                QTest.keyClick(scrollbar, Qt.Key.Key_Home)
                QTest.qWait(20)
                visible(window.schematic_toggle, window)
                QTest.mouseClick(window.schematic_toggle, Qt.MouseButton.LeftButton)
                QTest.qWait(50)
                assert not window.schematic.isVisible()
                for plot, expected in zip(plots, before, strict=True):
                    assert plot.plotItem.vb.height() > 80
                    for actual, values in zip(plot.listDataItems()[0].getData(), expected, strict=True):
                        np.testing.assert_array_equal(actual, values)
                QTest.keyClick(scrollbar, Qt.Key.Key_End)
                QTest.qWait(50)
                visible(window.stress_table, window.stage_scroll.viewport())
                for plot in plots:
                    # QGraphicsView's opaque child viewport paints the graph;
                    # the parent's own visibleRegion excludes that child.
                    visible(plot.viewport(), window.stage_scroll.viewport())
                    assert plot.plotItem.vb.height() > 80
                measurements["collapsed_trace_heights"] = [plot.plotItem.vb.height() for plot in plots]
                measurements["stress_table_fully_visible_after_scroll"] = True
                if directory:
                    assert window.grab().save(str(directory / f"psd-T17-collapsed-{width}x{height}-100pct.png"))
                QTest.keyClick(scrollbar, Qt.Key.Key_Home)
                QTest.qWait(20)
                QTest.mouseClick(window.schematic_toggle, Qt.MouseButton.LeftButton)
                QTest.qWait(50)
                assert window.schematic.isVisible()
                for plot, expected in zip(plots, before, strict=True):
                    for actual, values in zip(plot.listDataItems()[0].getData(), expected, strict=True):
                        np.testing.assert_array_equal(actual, values)
                element = next(item for item in layout(topology)[0] if item.key == "Q1")
                schematic_width, schematic_height = schematic_size(topology)
                bounds = window.schematic.schematic_rect()
                point = QPoint(round(bounds.x() + element.x * bounds.width() / schematic_width),
                               round(bounds.y() + element.y * bounds.height() / schematic_height))
                QTest.mouseClick(window.schematic, Qt.MouseButton.LeftButton, pos=point)
                QTest.qWait(50)
                assert window.component_combo.currentData() == "Q1"
                assert window.schematic.selected == "Q1"
                wave = window.result.waveforms["Q1"]
                for plot, signal in zip(plots, (wave.current_a, wave.voltage_v), strict=True):
                    for actual, values in zip(plot.listDataItems()[0].getData(),
                                              signal.sample(count=800, periods=2), strict=True):
                        np.testing.assert_array_equal(actual, values)
                QTest.keyClick(scrollbar, Qt.Key.Key_End)
                QTest.qWait(50)
                visible(window.stress_table, window.stage_scroll.viewport())
                if directory:
                    assert window.grab().save(str(directory / f"psd-T17-scrolled-{width}x{height}-100pct.png"))
                QTest.keyClick(scrollbar, Qt.Key.Key_Home)
                QTest.qWait(20)
            if directory:
                assert window.grab().save(str(directory / f"psd-{topology}-{width}x{height}-100pct.png"))
        identity = window.result.design_id
        for route in ("llc", "pfc", "control", "fra", "dab"):
            controller.show_workspace(route)
            app.processEvents()
            assert not window.isVisible()
            controller.show_workspace("power_stage")
            app.processEvents()
            assert controller.power_stage_window is window
            assert window.result.design_id == identity
        if width == 1280:
            click_topology("T21")
            visible(window.llc_button, window)
            QTest.mouseClick(window.llc_button, Qt.MouseButton.LeftButton)
            app.processEvents()
            assert controller.llc_window.isVisible() and not window.isVisible()
            from llc_design.core.spec import PrimaryTopology
            assert controller.llc_window._spec_from_widgets().primary_topology is PrimaryTopology.FULL_BRIDGE
            controller.show_workspace("power_stage")
            app.processEvents()
            assert window.topology_combo.currentData() == "T21"
        if directory:
            (directory / f"geometry-{width}x{height}-100pct.json").write_text(
                json.dumps(measurements, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(measurements, ensure_ascii=False))
    finally:
        for window in (controller.llc_window, controller.pfc_window, controller.dab_window,
                       controller.control_window, controller.fra_window, controller.power_stage_window):
            window.close()
            for graphics in window.findChildren(pg.GraphicsLayoutWidget):
                graphics.close()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


if __name__ == "__main__":
    _exercise_desktop(int(sys.argv[1]), int(sys.argv[2]))
