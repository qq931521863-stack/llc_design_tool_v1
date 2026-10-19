"""Validate the electrical circuit actually painted, not a parallel netlist.

The recorder delegates every call to the real QPainter. Component terminals are
recovered from IEC resistor rectangles/capacitor plates, then copper strokes are
collapsed into nets for an independent complex nodal analysis.
"""
import os
from copy import deepcopy

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    qt = pytest.importorskip("PySide6.QtWidgets")
    return qt.QApplication.instance() or qt.QApplication([])


@pytest.fixture
def paint_records(monkeypatch):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainter
    from power_control_tools.gui import type_compensator_schematic as module

    records = []

    class RecordedPainter:
        RenderHint = QPainter.RenderHint

        def __init__(self, target):
            self.painter = QPainter(target)
            self.record = {"lines": [], "rects": [], "texts": [], "polygons": [], "ellipses": []}
            records.append(self.record)

        def __getattr__(self, name):
            return getattr(self.painter, name)

        def drawLine(self, start, end):
            self.record["lines"].append(((start.x(), start.y()), (end.x(), end.y())))
            self.painter.drawLine(start, end)

        def drawRect(self, rect):
            self.record["rects"].append(QRectF(rect))
            self.painter.drawRect(rect)

        def drawText(self, rect, flags, text):
            self.record["texts"].append((QRectF(rect), text))
            self.painter.drawText(rect, flags, text)

        def drawPolygon(self, points):
            self.record["polygons"].append([(p.x(), p.y()) for p in points])
            self.painter.drawPolygon(points)

        def drawEllipse(self, center, rx, ry):
            self.record["ellipses"].append(((center.x(), center.y()), rx, ry))
            self.painter.drawEllipse(center, rx, ry)

    monkeypatch.setattr(module, "QPainter", RecordedPainter)
    return records


def _capture(app, records, kind="TYPE_III", mode="rc", size=(520, 240), values=None):
    from power_control_tools.gui.type_compensator_schematic import TypeCompensatorSchematic
    widget = TypeCompensatorSchematic()
    widget.resize(*size)
    widget.set_state(kind=kind, mode=mode, values=values or {
        "R1": "10 kΩ", "R2": "47 kΩ", "R3": "12 kΩ", "C1": "10 nF", "C2": "470 pF", "C3": "1 nF",
    })
    widget.show()
    app.processEvents()
    image = widget.grab().toImage()
    record = records[-1]
    widget.close()
    return widget, image, record


def _painted_network(record):
    """Recover physical terminals from the actual primitive calls."""
    parts = []
    for rect in record["rects"]:
        assert (rect.width(), rect.height()) == (36, 12)
        parts.append(("R", (rect.left(), rect.center().y()), (rect.right(), rect.center().y())))
    plates = sorted((a[0], (a[1] + b[1]) / 2) for a, b in record["lines"]
                    if a[0] == b[0] and abs(a[1] - b[1]) == 20)
    for x, y in plates:
        if (x + 10, y) in plates:
            parts.append(("C", (x, y), (x + 10, y)))
    components = {}
    for kind, a, b in parts:
        cx, cy = (a[0] + b[0]) / 2, a[1]
        labels = [text.split("=")[0] for rect, text in record["texts"]
                  if abs(rect.center().x() - cx) < .01 and 0 < cy - rect.bottom() < 30
                  and text.startswith(kind)]
        assert len(labels) == 1, (kind, a, b, labels)
        components[labels[0]] = (a, b)

    # Include all T joins and points inside a stroke, not just line endpoints.
    ports = {"vin": (16, 128), "minus": (322, 128), "plus": (322, 164),
             "out": (500, 146), "ref": (330, 212)}
    points = set(ports.values())
    points.update(p for line in record["lines"] for p in line)
    points.update(p for terminals in components.values() for p in terminals)
    parent = {p: p for p in points}

    def root(p):
        while parent[p] != p:
            p = parent[p]
        return p

    for a, b in record["lines"]:
        on_line = [p for p in points if
                   abs((p[0]-a[0])*(b[1]-a[1]) - (p[1]-a[1])*(b[0]-a[0])) < 1e-8
                   and min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
                   and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])]
        for p in on_line:
            parent[root(p)] = root(a)
    return {name: tuple(root(p) for p in terminals) for name, terminals in components.items()}, {
        name: root(p) for name, p in ports.items()
    }


def _assert_circuit(record, kind):
    components, ports = _painted_network(record)
    expected = {"R1", "R2", "C1", "C2"} | ({"R3", "C3"} if kind == "TYPE_III" else set())
    assert set(components) == expected, "Missing actual resistor/capacitor glyphs"
    assert len({ports[p] for p in ("vin", "minus", "out", "plus")}) == 4, "Copper bypass/short"
    assert ports["plus"] == ports["ref"]
    vin, minus, out = (ports[k] for k in ("vin", "minus", "out"))
    assert components["R1"] == (vin, minus)
    assert components["C2"] == (minus, out)
    assert components["R2"][0] == minus
    assert components["C1"][1] == out
    assert components["R2"][1] == components["C1"][0]
    assert components["R2"][1] not in (vin, minus, out, ports["plus"])
    if kind == "TYPE_III":
        assert components["R3"][0] == vin
        assert components["C3"][1] == minus
        assert components["R3"][1] == components["C3"][0]
        assert components["R3"][1] not in (vin, minus, out, ports["plus"], components["R2"][1])
    # Both +/- positions must agree with the actual triangle and pin strokes.
    assert record["polygons"] == [[(322, 110), (322, 182), (390, 146)]]
    signs = {text: rect.center().y() for rect, text in record["texts"] if text in ("+", "−")}
    assert signs == {"−": 128, "+": 164}
    dots = {p for p, rx, ry in record["ellipses"] if rx == ry == 2.5}
    assert {(284, 128), (284, 96), (464, 96), (464, 146)} <= dots
    if kind == "TYPE_III":
        assert (48, 128) in dots
    return components, ports


@pytest.mark.parametrize("kind", ["TYPE_II", "TYPE_III"])
@pytest.mark.parametrize("mode", ["rc", "pz"])
@pytest.mark.parametrize("size", [(360, 220), (520, 240), (700, 300), (1100, 240)])
def test_actual_qpainter_circuit_and_symbol_pixels(app, paint_records, kind, mode, size):
    widget, image, record = _capture(app, paint_records, kind, mode, size)
    components, _ = _assert_circuit(record, kind)
    scale = min(widget.width()/520, widget.height()/240)
    dx, dy = (widget.width()-520*scale)/2, (widget.height()-240*scale)/2

    def lightness(x, y):
        return image.pixelColor(round(dx+x*scale), round(dy+y*scale)).lightness()

    for rect in record["rects"]:
        x, y = rect.center().x(), rect.center().y()
        assert lightness(x, y) > 240, "A resistor must not be a copper short"
        assert lightness(x, y-6) < 180, "Missing resistor body"
    for (x1, y1), (x2, y2) in record["lines"]:
        if x1 == x2 and y2-y1 == 20 and ((x1+10, y1), (x1+10, y2)) in record["lines"]:
            assert lightness(x1+5, (y1+y2)/2) > 240, "Capacitor dielectric is shorted"
            assert lightness(x1, (y1+y2)/2) < 180, "Missing capacitor plate"
    if mode == "pz":
        assert not any("=" in text for _, text in record["texts"])
        assert "not unique" in widget.toolTip()


def _nodal_gain(components, ports, values, frequency):
    """Stamp complex admittances and ideal virtual-earth KCL, without TF formulas."""
    known = {ports["vin"]: 1., ports["minus"]: 0.}
    nodes = set(p for pair in components.values() for p in pair)
    unknown = sorted(nodes - known.keys())
    equations = sorted(nodes - {ports["vin"], ports["out"]})
    matrix = np.zeros((len(equations), len(unknown)), complex)
    rhs = np.zeros(len(equations), complex)
    for name, (a, b) in components.items():
        admittance = 1/values[name] if name.startswith("R") else 2j*np.pi*frequency*values[name]
        for i, node in enumerate(equations):
            if node not in (a, b):
                continue
            other = b if node == a else a
            for terminal, coefficient in ((node, admittance), (other, -admittance)):
                if terminal in known:
                    rhs[i] -= coefficient*known[terminal]
                else:
                    matrix[i, unknown.index(terminal)] += coefficient
    solution = np.linalg.solve(matrix, rhs)
    return solution[unknown.index(ports["out"])]


@pytest.mark.parametrize("kind", ["TYPE_II", "TYPE_III"])
def test_painted_netlist_complex_nodal_gain_matches_rc_model(app, paint_records, kind):
    from power_control_tools.controllers import design_controller
    from power_control_tools.models import ControllerKind
    _, _, record = _capture(app, paint_records, kind)
    components, ports = _assert_circuit(record, kind)
    rng = np.random.default_rng(20261019)
    for _ in range(12):
        values = {name: 10**rng.uniform(2, 6) if name.startswith("R") else 10**rng.uniform(-12, -6)
                  for name in components}
        kwargs = {name.lower() + ("_ohm" if name.startswith("R") else "_f"): value
                  for name, value in values.items()}
        analog = design_controller(ControllerKind[kind], type_input_mode="rc", **kwargs)
        for frequency in np.geomspace(.1, 1e7, 49):
            actual = _nodal_gain(components, ports, values, frequency)
            s = 2j*np.pi*frequency
            expected = np.polyval(analog.numerator, s)/np.polyval(analog.denominator, s)
            assert actual == pytest.approx(expected, rel=1e-9, abs=1e-11)


@pytest.mark.parametrize("bypass", [((16, 128), (322, 128)), ((284, 54), (464, 54))])
def test_copper_bypass_is_rejected_even_with_all_labels_and_symbols(app, paint_records, bypass):
    _, _, record = _capture(app, paint_records)
    shorted = deepcopy(record)
    shorted["lines"].append(bypass)
    with pytest.raises(AssertionError, match="Copper bypass/short"):
        _assert_circuit(shorted, "TYPE_III")


def test_wire_only_legacy_style_is_not_accepted_as_a_schematic(app, paint_records):
    _, _, record = _capture(app, paint_records)
    wire_only = deepcopy(record)
    wire_only["rects"] = []
    wire_only["lines"] = [(a, b) for a, b in wire_only["lines"] if not (a[0] == b[0] and abs(a[1]-b[1]) == 20)]
    # Label text is unchanged: an assertion on names alone would pass this.
    with pytest.raises(AssertionError, match="Missing actual"):
        _assert_circuit(wire_only, "TYPE_III")


def test_long_values_do_not_cover_copper_or_symbols(app, paint_records):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QFont, QFontMetricsF
    values = {name: "12345678901234567890.123456789 " + ("Ω" if name.startswith("R") else "nF")
              for name in ("R1", "R2", "R3", "C1", "C2", "C3")}
    widget, _, record = _capture(app, paint_records, values=values, size=(360, 220))
    font = QFont("Sans Serif")
    font.setPixelSize(12)
    for rect, text in record["texts"]:
        if not text.startswith(tuple(values)):
            continue
        assert QFontMetricsF(font).horizontalAdvance(text) <= rect.width()
        assert text.endswith(("Ω", "nF"))
        for body in record["rects"]:
            assert not rect.intersects(body)
        for a, b in record["lines"]:
            wire = QRectF(min(a[0], b[0])-.8, min(a[1], b[1])-.8, abs(a[0]-b[0])+1.6, abs(a[1]-b[1])+1.6)
            assert not rect.intersects(wire), (text, a, b)
    for name, value in values.items():
        assert f"{name} = {value}" in widget.toolTip()
        assert f"{name} = {value}" in widget.accessibleDescription()


def test_main_window_switches_real_schematic_modes_and_disposes(app, paint_records):
    from PySide6.QtWidgets import QScrollArea
    from power_control_tools.gui.main_window import ControlToolsMainWindow
    from power_control_tools.models import ControllerKind
    window = ControlToolsMainWindow()
    window.resize(1280, 720)
    window.show()
    for kind, mode in ((ControllerKind.TYPE_II, "rc"), (ControllerKind.TYPE_III, "rc"),
                       (ControllerKind.TYPE_III, "pz"), (ControllerKind.TYPE_II, "pz")):
        window.ctrl.setCurrentIndex(window.ctrl.findData(kind))
        window.type_input_mode.setCurrentIndex(window.type_input_mode.findData(mode))
        window.recalculate()
        app.processEvents()
        schematic = window.type_schematic
        scroll = next(s for s in window.findChildren(QScrollArea) if s.widget() is window.param_widget)
        scroll.ensureWidgetVisible(schematic)
        app.processEvents()
        assert not schematic.isHidden()
        assert not schematic.grab().isNull()
        _assert_circuit(paint_records[-1], kind.name)
        assert schematic.mode == mode
        if mode == "rc":
            assert schematic.values["C2"] == f"{window.c2_nf.value():.4g} nF"
        else:
            assert schematic.values == {}
    window.ctrl.setCurrentIndex(window.ctrl.findData(ControllerKind.PI))
    app.processEvents()
    assert window.type_schematic.isHidden()
    window.close()
    app.processEvents()
    assert not window.timer.isActive()
