"""Connected LLC electrical schematic and individual physical hit targets.

The connection graph is also included in waveform exports.  Dots denote actual
junctions; wire bridges explicitly mark the two non-connected crossings.
"""
from __future__ import annotations

from itertools import pairwise

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsScene, QGraphicsView


class _Component(QGraphicsRectItem):
    def __init__(self, key, label, kind, x, y, vertical=True, current=""):
        super().__init__(-30, -38, 60, 76)
        self.key, self.label, self.kind = key, label, kind
        self.vertical, self.current = vertical, current
        self.setPos(x, y)
        self.setAcceptHoverEvents(True)
        self.setToolTip(f"{label}：点击查看同一工况的电压、电流与统计")
        self.setPen(QPen(Qt.PenStyle.NoPen))

    def paint(self, painter, option, widget=None):
        painter.setPen(QPen(QColor("#153754"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.save()
        if not self.vertical:
            painter.rotate(-90)
        if self.kind == "switch":
            painter.drawLine(0, -38, 0, -20)
            painter.drawLine(0, 20, 0, 38)
            painter.drawLine(0, -20, -10, -20)
            painter.drawLine(-10, -20, -10, 20)
            painter.drawLine(-10, 20, 0, 20)
            painter.drawLine(-17, -20, -17, 20)
            painter.drawLine(-27, 0, -17, 0)
            # Intrinsic diode, anode below/cathode above (source -> drain).
            painter.drawLine(0, -20, 17, -20)
            painter.drawLine(17, -20, 17, 20)
            painter.drawLine(17, 20, 0, 20)
            painter.drawLine(10, -7, 24, -7)
            painter.drawLine(10, 7, 24, 7)
            painter.drawLine(10, 7, 17, -7)
            painter.drawLine(24, 7, 17, -7)
        elif self.kind in {"coil", "winding"}:
            painter.drawLine(0, -38, 0, -24)
            path = QPainterPath()
            path.moveTo(0, -24)
            for start in (-24, -12, 0, 12):
                path.cubicTo(17, start, 17, start + 12, 0, start + 12)
            painter.drawPath(path)
            painter.drawLine(0, 24, 0, 38)
        elif self.kind == "capacitor":
            painter.drawLine(0, -38, 0, -5)
            painter.drawLine(-18, -5, 18, -5)
            painter.drawLine(-18, 5, 18, 5)
            painter.drawLine(0, 5, 0, 38)
        elif self.kind == "resistor":
            painter.drawLine(0, -38, 0, -23)
            painter.drawRect(-10, -23, 20, 46)
            painter.drawLine(0, 23, 0, 38)
        else:
            painter.drawLine(0, -38, 0, -24)
            painter.drawEllipse(-24, -24, 48, 48)
            painter.drawLine(-8, -9, 8, -9)
            painter.drawLine(0, -17, 0, -1)
            painter.drawLine(-8, 11, 8, 11)
            painter.drawLine(0, 24, 0, 38)
        painter.restore()
        if self.vertical:
            painter.drawText(32, -5, self.label)
            painter.drawText(32, 14, self.current)
        else:
            painter.drawText(-15, -24, self.label)
            painter.drawText(-15, 30, self.current)
            painter.drawText(-35, -7, "+")
            painter.drawText(29, -7, "−")


class TopologyView(QGraphicsView):
    component_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMinimumHeight(300)
        self.blocks: dict[str, _Component] = {}
        self.connections: dict[str, tuple[str, str]] = {}
        self.primary_topology = ""
        self.set_primary_topology("FULL_BRIDGE")

    def _wire(self, *points):
        for a, b in pairwise(points):
            self.scene().addLine(*a, *b, QPen(QColor("#35516b"), 1.6))

    def _label(self, text, x, y):
        self.scene().addSimpleText(text).setPos(x, y)

    def _dot(self, x, y):
        self.scene().addEllipse(x-3, y-3, 6, 6, QPen(Qt.PenStyle.NoPen), QColor("#35516b"))

    def _part(self, key, label, kind, a, b, nodes, current=""):
        vertical = a[0] == b[0]
        x, y = (a[0]+b[0])/2, (a[1]+b[1])/2
        item = _Component(key, label, kind, x, y, vertical, current)
        self.scene().addItem(item)
        self.blocks[key] = item
        self.connections[key] = nodes
        if vertical:
            self._wire(a, (x, y-38)); self._wire((x, y+38), b)
        else:
            self._wire(a, (x-38, y)); self._wire((x+38, y), b)
        return item

    def _crossing(self, x, y):
        # Horizontal wire hops over a vertical conductor; no electrical junction.
        path = QPainterPath()
        path.moveTo(x-9, y)
        path.cubicTo(x-9, y-14, x+9, y-14, x+9, y)
        self.scene().addPath(path, QPen(QColor("#35516b"), 1.6))

    def _ground(self, x, y, label):
        self._wire((x, y), (x, y+10))
        for offset, width in ((10, 13), (16, 8), (22, 3)):
            self._wire((x-width, y+offset), (x+width, y+offset))
        self._label(label, x-20, y+25)

    def set_primary_topology(self, topology) -> None:
        value = getattr(topology, "value", topology)
        if value == self.primary_topology:
            return
        self.primary_topology = value
        half = value == "HALF_BRIDGE"
        self.scene().clear(); self.blocks.clear(); self.connections.clear()
        self.setSceneRect(0, 0, 1380, 570)
        self._label("LLC 半桥" if half else "LLC 全桥", 20, 10)
        self._label("点击每个器件查看 V / I；+ / − 为电压参考，箭头为电流参考", 230, 10)
        self._part("bus", "Vbus", "source", (60, 60), (60, 360), ("BUS+", "PGND"))
        self._wire((60, 60), (300, 60)); self._wire((60, 360), (300, 360))
        self._part("q1", "Q1", "switch", (180, 60), (180, 210), ("BUS+", "A"), "I ↓")
        self._part("q2", "Q2", "switch", (180, 210), (180, 360), ("A", "PGND"), "I ↑")
        if half:
            self._part("midpoint_upper", "Vbus/2", "source", (300, 60), (300, 250), ("BUS+", "B"))
            self._part("midpoint_lower", "Vbus/2", "source", (300, 250), (300, 360), ("B", "PGND"))
            # Ideal source equivalents are explicit assumptions, not physical Q3/4.
            for key in ("midpoint_upper", "midpoint_lower"):
                self.blocks[key].setToolTip("理想母线中点参考；不模拟分压电容不平衡")
            self._label("B = Vbus/2 理想中点", 245, 390)
            self._label("Cr 电压参考：中点回路，直流偏置为 0；非单端母线负端回路", 20, 515)
        else:
            self._part("q3", "Q3", "switch", (300, 60), (300, 250), ("BUS+", "B"), "I ↑")
            self._part("q4", "Q4", "switch", (300, 250), (300, 360), ("B", "PGND"), "I ↓")
        self._wire((180, 210), (291, 210)); self._crossing(300, 210)
        self._wire((309, 210), (410, 210))
        self._label("A", 184, 184); self._label("+  Vab  −", 365, 172)
        self._part("lr", "Lr", "coil", (410, 210), (490, 210), ("A", "XR"), "Ir →")
        # Effective primary series damping is included by all waveform solvers.
        self.connections["rseries"] = ("XR", "X")
        self._wire((490, 210), (507, 210)); self._wire((533, 210), (550, 210))
        self.scene().addRect(507, 203, 26, 14, QPen(QColor("#35516b"), 1.6))
        self._label("RΣ", 509, 180)
        self._part("cr", "Cr", "capacitor", (550, 210), (630, 210), ("X", "P+"), "Ir →")
        self._wire((630, 210), (740, 210))
        self._wire((300, 250), (380, 250), (380, 400), (740, 400))
        self._part("lm", "Lm", "coil", (650, 210), (650, 400), ("P+", "B"), "Im ↓")
        self._part("primary", "Np", "winding", (740, 210), (740, 400), ("P+", "B"), "Ir−Im ↓")
        self._part("secondary", "Ns", "winding", (830, 210), (830, 400), ("S+", "S−"), "Is ↑")
        for x in (762, 770):
            self._wire((x, 267), (x, 343))
        isolation = QPen(QColor("#a45230"), 1.2, Qt.PenStyle.DashLine)
        self.scene().addLine(795, 100, 795, 460, isolation)
        self._label("隔离", 775, 466)
        self._label("+", 720, 217); self._label("−", 720, 375)
        self._label("+", 811, 217); self._label("−", 811, 375)
        self._dot(730, 279); self._dot(820, 279)
        self._wire((830, 210), (870, 210), (870, 285), (930, 285))
        self._wire((830, 400), (880, 400), (880, 315), (921, 315))
        self._crossing(930, 315); self._wire((939, 315), (1050, 315))
        self._part("sr1", "SR1", "switch", (930, 130), (930, 285), ("OUT+", "S+"), "I ↑")
        self._part("sr2", "SR2", "switch", (930, 285), (930, 440), ("S+", "SGND"), "I ↑")
        self._part("sr3", "SR3", "switch", (1050, 130), (1050, 315), ("OUT+", "S−"), "I ↑")
        self._part("sr4", "SR4", "switch", (1050, 315), (1050, 440), ("S−", "SGND"), "I ↑")
        self._wire((930, 130), (1240, 130)); self._wire((930, 440), (1240, 440))
        self._part("coesr", "ESR", "resistor", (1150, 130), (1150, 230), ("OUT+", "CO+"))
        self._part("co", "Co", "capacitor", (1150, 230), (1150, 440), ("CO+", "SGND"), "ICo ↓")
        self._part("load", "Rload", "resistor", (1240, 130), (1240, 440), ("OUT+", "SGND"), "Io ↓")
        self._label("+ Vo", 1170, 100); self._label("−", 1205, 445)
        for point in ((180, 60), (180, 210), (180, 360), (300, 250), (650, 210),
                      (650, 400), (930, 285), (1050, 315), (1050, 130),
                      (1050, 440), (1150, 130), (1150, 440)):
            self._dot(*point)
        self._ground(60, 360, "PGND"); self._ground(1240, 440, "SGND")
        self._label("一次侧 Q 电流按有符号 Ir 导通窗口；SR 电流按整流方向，VDS 上正下负。", 20, 485)
        self._label("理想开关/整流等效：不含真实栅极驱动、Coss 换流和布线寄生。损耗须使用同工况来源。", 20, 540)
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def context(self) -> dict:
        return {"primary_topology": self.primary_topology,
                "connections": {key: list(nodes) for key, nodes in self.connections.items()},
                "isolation": ["primary: PGND", "secondary: SGND"],
                "half_bridge_reference": "ideal Vbus/2 midpoint; zero DC Cr bias" if
                self.primary_topology == "HALF_BRIDGE" else "full bridge differential A-B"}

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if isinstance(item, _Component) and not item.key.startswith("midpoint_"):
            self.component_selected.emit(item.key)
        super().mousePressEvent(event)
