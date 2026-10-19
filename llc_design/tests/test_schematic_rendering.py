"""Electrical meaning must survive the actual Qt paint paths, fit and scroll."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    qt = pytest.importorskip("PySide6.QtWidgets")
    return qt.QApplication.instance() or qt.QApplication([])


@pytest.mark.parametrize("topology", ["HALF_BRIDGE", "FULL_BRIDGE"])
@pytest.mark.parametrize("integrated", [False, True])
def test_rendered_mosfet_body_diodes_have_no_copper_short(app, topology, integrated):
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from llc_design.gui.widgets.topology import TopologyView

    view = TopologyView()
    view.set_primary_topology(topology)
    view.set_integrated_magnetics(integrated)
    image = QImage(2760, 1140, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    view.scene().render(painter, QRectF(0, 0, 2760, 1140), view.sceneRect())
    painter.end()
    for key, part in view.blocks.items():
        if part.kind != "switch":
            continue
        def lightness(x, y):
            point = part.mapToScene(QPointF(x, y))
            return image.pixelColor(round(2*point.x()), round(2*point.y())).lightness()
        # Interior of an outline diode is empty. A D-S wire down its centre
        # shorts the semiconductor even if the declared terminal graph is right.
        assert lightness(17, 0) > 240, (key, "body diode is shorted by a stroke")
        assert lightness(17, -14) < 140, (key, "missing drain/cathode lead")
        assert lightness(17, 14) < 140, (key, "missing source/anode lead")
        assert lightness(11, -7) < 140, (key, "missing upper cathode bar")
    view.close()


@pytest.mark.parametrize("topology", ["HALF_BRIDGE", "FULL_BRIDGE"])
@pytest.mark.parametrize("integrated", [False, True])
def test_actual_clicks_on_visible_leads_and_labels_survive_fit_and_scroll(app, topology, integrated):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    from llc_design.gui.widgets.topology import TopologyView

    view = TopologyView()
    view.set_primary_topology(topology)
    view.set_integrated_magnetics(integrated)
    view.resize(960, 480)
    view.show()
    app.processEvents()
    selected = []
    view.component_selected.connect(selected.append)
    for scrolled in (False, True):
        if scrolled:
            view.scale(2.5, 2.5)
        for key, part in view.blocks.items():
            if scrolled:
                view.centerOn(part)
            positions = [QPointF(0, 0)]
            if not part.vertical:
                positions.extend([QPointF(-35, 0), QPointF(35, 0)])
            else:
                positions.append(QPointF(40, -8))  # the actual painted name
            for local in positions:
                before = len(selected)
                point = view.mapFromScene(part.mapToScene(local))
                assert view.viewport().rect().contains(point), (key, point)
                QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
                app.processEvents()
                assert selected[before:] == ([] if key.startswith("midpoint_") else [key]), (key, local, scrolled)
    view.close()

