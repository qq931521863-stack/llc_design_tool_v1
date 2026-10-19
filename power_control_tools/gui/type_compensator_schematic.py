"""Type-II / Type-III inverting op-amp compensator schematic.

The network matches ``power_control_tools.controllers`` RC definitions:
Type II: input R1; feedback C2 || (R2 + C1 series).
Type III: input R1 || (R3 + C3 series); the same feedback network.
Pole/zero mode shows topology only, since RC values are not unique.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QFontMetricsF
from PySide6.QtWidgets import QWidget, QSizePolicy


class TypeCompensatorSchematic(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.kind = "TYPE_II"
        self.mode = "rc"  # rc | pz
        self.values: dict[str, str] = {}
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_state(self, *, kind: str, mode: str, values: dict[str, str] | None = None) -> None:
        self.kind = str(kind)
        self.mode = str(mode).lower()
        self.values = dict(values or {})
        names = ("R1", "R2", "R3", "C1", "C2", "C3") if "III" in self.kind.upper() else ("R1", "R2", "C1", "C2")
        description = "Inverting amplifier: Vin to − through Zin; Vref to +; Vout to − through Zf."
        if self.mode == "rc":
            description += "\n" + "\n".join(f"{name} = {self.values[name]}" for name in names if name in self.values)
        else:
            description += "\nPole/zero mode: topology only; R/C values are not unique."
        self.setToolTip(description)
        self.setAccessibleDescription(description)
        self.update()

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(520, 240)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        # One logical canvas keeps terminal spacing and glyph proportions intact
        # in the narrow parameter pane as well as standalone/wide views.
        scale = min(self.width() / 520.0, self.height() / 240.0)
        painter.translate((self.width() - 520 * scale) / 2, (self.height() - 240 * scale) / 2)
        painter.scale(scale, scale)
        painter.setPen(QPen(QColor("#101828"), 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        font = QFont("Sans Serif")
        font.setPixelSize(12)
        painter.setFont(font)
        is_iii = "III" in self.kind.upper()

        def wire(x1, y1, x2, y2):
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        def label(name, x, y, width):
            # Dedicated text lanes never overlap conductors or symbol bodies.
            text = name
            if self.mode == "rc" and name in self.values:
                text += f"={self.values[name]}"
            # Preserve units at the end; exact long values remain available in
            # the tooltip/accessibility description instead of covering wires.
            text = QFontMetricsF(painter.font()).elidedText(text, Qt.TextElideMode.ElideMiddle, width)
            painter.drawText(QRectF(x - width / 2, y, width, 14), Qt.AlignmentFlag.AlignCenter, text)

        def resistor(x, y):
            # IEC resistor: copper stops at the two edges, never through it.
            painter.drawRect(QRectF(x - 18, y - 6, 36, 12))

        def capacitor(x, y):
            wire(x - 18, y, x - 5, y)
            wire(x - 5, y - 10, x - 5, y + 10)
            wire(x + 5, y - 10, x + 5, y + 10)
            wire(x + 5, y, x + 18, y)

        def node(x, y):
            painter.setBrush(QColor("#101828"))
            painter.drawEllipse(QPointF(x, y), 2.5, 2.5)
            painter.setBrush(Qt.BrushStyle.NoBrush)

        title = "Type III" if is_iii else "Type II"
        title += " · R/C components" if self.mode == "rc" else " · Pole/zero: topology only (R/C not unique)"
        painter.drawText(QRectF(12, 4, 496, 18), Qt.AlignmentFlag.AlignLeft, title)

        # Vin -> R1 -> summing node -> inverting input. No bypass branch.
        painter.drawText(QRectF(8, 104, 36, 18), Qt.AlignmentFlag.AlignLeft, "Vin")
        wire(16, 128, 148, 128)
        resistor(166, 128)
        wire(184, 128, 322, 128)
        label("R1", 166, 104, 190)

        if is_iii:
            # A second, series R3-C3 path shares exactly the R1 end nodes.
            wire(48, 128, 48, 196)
            wire(48, 196, 92, 196)
            resistor(110, 196)
            wire(128, 196, 202, 196)
            capacitor(220, 196)
            wire(238, 196, 284, 196)
            wire(284, 196, 284, 128)
            label("R3", 110, 168, 104)
            label("C3", 220, 168, 104)
            node(48, 128)

        # Ideal op-amp: upper input is −, lower input is +. The Vref
        # terminal is a reference voltage, not a ground or an extra feedback.
        painter.drawPolygon([QPointF(322, 110), QPointF(322, 182), QPointF(390, 146)])
        painter.drawText(QRectF(325, 119, 18, 18), Qt.AlignmentFlag.AlignCenter, "−")
        painter.drawText(QRectF(325, 155, 18, 18), Qt.AlignmentFlag.AlignCenter, "+")
        wire(322, 164, 306, 164)
        wire(306, 164, 306, 212)
        wire(306, 212, 330, 212)
        painter.drawEllipse(QPointF(333, 212), 3, 3)
        painter.drawText(QRectF(341, 203, 60, 18), Qt.AlignmentFlag.AlignLeft, "Vref")
        wire(390, 146, 500, 146)
        painter.drawText(QRectF(466, 154, 48, 18), Qt.AlignmentFlag.AlignLeft, "Vout")

        # Feedback bus: C2 in parallel with series R2-C1. Junction dots
        # identify both branch joins; there are no ambiguous wire crossings.
        wire(284, 128, 284, 54)
        wire(284, 54, 356, 54)
        capacitor(374, 54)
        wire(392, 54, 464, 54)
        wire(464, 54, 464, 146)
        label("C2", 374, 26, 172)

        wire(284, 96, 312, 96)
        resistor(330, 96)
        wire(348, 96, 400, 96)
        capacitor(418, 96)
        wire(436, 96, 464, 96)
        label("R2", 330, 68, 82)
        label("C1", 418, 68, 82)
        for x, y in ((284, 128), (284, 96), (464, 96), (464, 146)):
            node(x, y)
        painter.end()


__all__ = ["TypeCompensatorSchematic"]
