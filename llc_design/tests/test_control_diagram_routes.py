"""Real Qt routing, label and interaction checks for every shared consumer."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton
from shiboken6 import isValid

from llc_design.gui.lifetime import dispose_widgets
from llc_design.gui.widgets.control_block_diagram import BlockSpec, ConnectionSpec, ControlBlockDiagram


VARIANTS = ["ttpl", "vienna", "dab", "llc", "three_phase", "pll_SRF", "pll_DDSRF", "pll_DDSOGI"] + [
    "llc_" + key for key in ("sum", "controller", "clamp", "fm", "pwm", "plant", "sense", "adc", "unknown")]


def make_view(kind):
    from llc_design.gui.widgets.digital_loop_view import DigitalLoopView
    if kind == "ttpl":
        from pfc_design.gui.control_lab_view import PFCControlLabView
        view = PFCControlLabView()
    elif kind == "vienna":
        from pfc_design.gui.vienna_control_view import ViennaControlLabView
        view = ViennaControlLabView()
    elif kind == "dab":
        from dab_design.core import DABConfig
        from dab_design.gui.control_view import DABControlView
        view = DABControlView(lambda: DABConfig())
    elif kind == "llc":
        view = DigitalLoopView()
    elif kind == "three_phase":
        from pfc_design.gui.three_phase_loop_view import ThreePhaseLoopView
        view = ThreePhaseLoopView()
    elif kind.startswith("pll_"):
        from pfc_design.gui.pll_comparison_view import PLLComparisonView
        view = PLLComparisonView()
        view.kind.setCurrentText(kind[4:])
    else:
        view = ControlBlockDiagram()
        _, blocks, edges = DigitalLoopView._detail_definition(kind[4:])
        view.set_diagram(blocks, edges)
        return view, view
    return view, view.diagram


def segment_rect(line, margin=0.01):
    return QRectF(line.p1(), line.p2()).normalized().adjusted(-margin, -margin, margin, margin)


def assert_geometry(diagram):
    blocks = {key: item.mapRectToScene(item.rect()) for key, item in diagram._items.items()}
    labels = []
    for item in diagram._items.values():
        for text in item.childItems():
            assert item.rect().contains(text.mapRectToParent(text.boundingRect()))
    for graphics in diagram._connection_graphics:
        for line in graphics.lines:
            ray = line.line()
            assert abs(ray.dx()) < 1e-7 or abs(ray.dy()) < 1e-7
            for key, rect in blocks.items():
                assert not segment_rect(ray).intersects(rect.adjusted(1, 1, -1, -1)), (graphics.spec, key)
        tip = graphics.arrow.polygon()[0]
        target = blocks[graphics.spec.target]
        assert target.adjusted(-.01, -.01, .01, .01).contains(tip)
        assert not target.adjusted(.01, .01, -.01, -.01).contains(tip)
        base = (graphics.arrow.polygon()[1] + graphics.arrow.polygon()[2])/2
        assert not target.contains(base), graphics.spec
        if graphics.label:
            rect = graphics.label.sceneBoundingRect()
            assert not any(rect.intersects(block) for block in blocks.values()), graphics.spec
            assert not any(rect.intersects(other) for other in labels), graphics.spec
            for other in diagram._connection_graphics:
                assert not rect.intersects(other.arrow.sceneBoundingRect()), graphics.spec
                assert not any(rect.intersects(segment_rect(line.line(), 2)) for line in other.lines), graphics.spec
            labels.append(rect)
    # Separate signals may cross, but must never silently share copper.
    for index, first in enumerate(diagram._connection_graphics):
        for second in diagram._connection_graphics[index+1:]:
            for first_line in first.lines:
                for second_line in second.lines:
                    a, b = first_line.line(), second_line.line()
                    same_horizontal = abs(a.dy()) < 1e-7 and abs(b.dy()) < 1e-7 and abs(a.y1()-b.y1()) < 1e-7
                    same_vertical = abs(a.dx()) < 1e-7 and abs(b.dx()) < 1e-7 and abs(a.x1()-b.x1()) < 1e-7
                    if same_horizontal:
                        assert min(max(a.x1(), a.x2()), max(b.x1(), b.x2())) <= max(min(a.x1(), a.x2()), min(b.x1(), b.x2())) + 1e-7, (first.spec, second.spec)
                    if same_vertical:
                        assert min(max(a.y1(), a.y2()), max(b.y1(), b.y2())) <= max(min(a.y1(), a.y2()), min(b.y1(), b.y2())) + 1e-7, (first.spec, second.spec)


def save_scene(diagram, path):
    bounds = diagram.scene.itemsBoundingRect().adjusted(-10, -10, 10, 10)
    image = QImage(round(2*bounds.width()), round(2*bounds.height()), QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    diagram.scene.render(painter, QRectF(0, 0, image.width(), image.height()), bounds)
    painter.end()
    assert image.save(str(path))


@pytest.mark.parametrize("kind", VARIANTS)
def test_real_consumer_routes_labels_clicks_and_zoom(kind):
    app = QApplication.instance() or QApplication([])
    view, diagram = make_view(kind)
    view.resize(1280, 720)
    view.show()
    app.processEvents()
    assert_geometry(diagram)
    picked = []
    diagram.block_selected.connect(picked.append)
    for key, item in diagram._items.items():
        diagram.fit_to_view()
        app.processEvents()
        QTest.mouseClick(diagram.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=diagram.view.mapFromScene(item.sceneBoundingRect().center()))
        assert picked[-1] == key and diagram.selected_key == key
        assert item.isSelected()
        assert_geometry(diagram)
        related = [g for g in diagram._connection_graphics if key in (g.spec.source, g.spec.target)]
        assert all(g.lines[0].pen().widthF() == 3 for g in related)
        diagram.focus_block(key)
        app.processEvents()
        assert diagram.view.viewport().rect().contains(diagram.view.mapFromScene(item.sceneBoundingRect().center()))
    diagram.clear_selection()
    diagram.actual_size()
    assert diagram.view.transform().m11() == 1
    diagram.zoom_in()
    assert diagram.view.transform().m11() > 1
    diagram.zoom_out()
    diagram.fit_to_view()
    app.processEvents()
    directory = os.environ.get("CONTROL_DIAGRAM_EVIDENCE_DIR")
    if directory:
        from pathlib import Path
        save_scene(diagram, Path(directory)/f"{kind}-scene.png")
        assert view.grab().save(str(Path(directory)/f"{kind}-{view.width()}x{view.height()}.png"))
    proxy, scene = diagram._proxy, diagram.scene
    dispose_widgets([view])
    assert not isValid(view) and not isValid(proxy) and not isValid(scene)


@pytest.mark.parametrize("kind", ["ttpl", "vienna"])
def test_pfc_real_fullscreen_repeated_close_and_720p_controls(kind):
    app = QApplication.instance() or QApplication([])
    view, diagram = make_view(kind)
    view.resize(1280, 720)
    view.show()
    app.processEvents()
    for button in (view.run_button, view.fit_diagram_button, view.fullscreen_diagram_button, view.diagram_toggle):
        assert view.rect().contains(button.mapTo(view, button.rect().center()))
    assert view.splitter.height() >= 250
    for index in range(view.input_tabs.count()):
        view.input_tabs.setCurrentIndex(index)
        app.processEvents()
        assert view.input_tabs.currentWidget().isVisible()
    for _ in range(2):
        errors = []
        def close_modal():
            try:
                dialog = app.activeModalWidget()
                assert isinstance(dialog, QDialog)
                big = dialog.findChild(ControlBlockDiagram)
                assert_geometry(big)
                key = "plant"
                item = big._items[key]
                QTest.mouseClick(big.view.viewport(), Qt.MouseButton.LeftButton,
                                 pos=big.view.mapFromScene(item.sceneBoundingRect().center()))
                assert diagram.selected_key == key
                close = next(button for button in dialog.findChildren(QPushButton) if button.text() in ("关闭", "Close"))
                QTest.mouseClick(close, Qt.MouseButton.LeftButton)
            except Exception as exc:
                errors.append(exc)
                if app.activeModalWidget():
                    app.activeModalWidget().reject()
        QTimer.singleShot(50, close_modal)
        QTest.mouseClick(view.fullscreen_diagram_button, Qt.MouseButton.LeftButton)
        assert not errors, errors
        assert app.activeModalWidget() is None
    QTest.mouseClick(view.diagram_toggle, Qt.MouseButton.LeftButton)
    assert not diagram.isVisible()
    QTest.mouseClick(view.diagram_toggle, Qt.MouseButton.LeftButton)
    assert diagram.isVisible()


def test_narrow_feedback_gap_and_vertical_ports_do_not_cross_sensor():
    app = QApplication.instance() or QApplication([])
    diagram = ControlBlockDiagram()
    diagram.set_diagram([BlockSpec("target", "Target", x=20, y=0, width=100, height=52),
                         BlockSpec("sense", "Sensor", x=30, y=64, width=142, height=52)],
                        [ConnectionSpec("sense", "target", "measured", True)])
    assert_geometry(diagram)
    assert diagram._connection_graphics[0].lines[0].line().p1().y() == 64
    app.processEvents()


def test_crossing_is_an_actual_visible_gap_not_a_false_junction():
    app = QApplication.instance() or QApplication([])
    diagram = ControlBlockDiagram()
    diagram.set_diagram([
        BlockSpec("left", "L", x=0, y=80, width=50, height=40),
        BlockSpec("right", "R", x=300, y=80, width=50, height=40),
        BlockSpec("top", "T", x=150, y=0, width=50, height=40),
        BlockSpec("bottom", "B", x=150, y=160, width=50, height=40),
    ], [ConnectionSpec("left", "right"), ConnectionSpec("top", "bottom")])
    image = QImage(1400, 800, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    diagram.scene.render(painter, QRectF(0, 0, 1400, 800), QRectF(0, 0, 350, 200))
    painter.end()
    assert image.pixelColor(175*4, 100*4).blue() > image.pixelColor(175*4, 100*4).red()
    assert image.pixelColor(175*4, 97*4).lightness() > 240
    assert image.pixelColor(175*4, 94*4).blue() > image.pixelColor(175*4, 94*4).red()
    app.processEvents()


def test_pfc_measurement_origins_and_parallel_modulator_inputs():
    app = QApplication.instance() or QApplication([])
    ttpl, diagram = make_view("ttpl")
    edges = {(edge.source, edge.target): edge for edge in diagram.connection_specs}
    assert {("plant", "current_sense"), ("plant", "vbus_sense"),
            ("vac_sense", "duty_ff"), ("iref", "indu_comp")} <= edges.keys()
    assert ("vbus_sense", "duty_ff") not in edges  # Dff uses the setpoint, not sampled Vbus.
    assert next(block for block in diagram.block_specs if block.key == "duty_ff").subtitle == "1 − |Vac| / Vbus_set"
    vienna, diagram = make_view("vienna")
    edges = {(edge.source, edge.target): edge for edge in diagram.connection_specs}
    assert {("plant", "current_sense"), ("plant", "bus_sense")} <= edges.keys()
    assert ("plant", "phase_voltage_sense") not in edges  # External grid voltage.
    assert edges["current_controller", "modulator"].label == "−Δmabc"
    assert edges["balance", "modulator"].label == "−balance"
    assert ("current_controller", "feedforward") not in edges
    app.processEvents()


def test_empty_diagram_can_be_replaced_and_cleared():
    app = QApplication.instance() or QApplication([])
    diagram = ControlBlockDiagram()
    diagram.set_diagram([], [])
    diagram.set_diagram([BlockSpec("x", "X")], [])
    diagram.set_diagram([], [])
    assert not diagram.block_specs and not diagram.connection_specs
    app.processEvents()
