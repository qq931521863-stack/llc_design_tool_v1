"""Interactive vector control block diagram used by LLC and PFC workspaces.

V7.1.3 adds the interaction model needed by the design workbench:

* semantic block selection for parameter/Bode linkage;
* vector zoom, pan, fit-to-window and 100 % view;
* selected-path highlighting so the active signal path is obvious;
* reusable diagrams for overview, focused detail and full-screen inspection.

The diagram deliberately stays lightweight: all blocks, labels and connections are
QGraphicsItems, so the view remains sharp at any zoom level and no bitmap scaling is
involved.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class BlockSpec:
    key: str
    title: str
    subtitle: str = ""
    x: float = 0.0
    y: float = 0.0
    width: float = 150.0
    height: float = 72.0
    role: str = "normal"  # normal/controller/plant/sense/modulator/aux


@dataclass(frozen=True)
class ConnectionSpec:
    source: str
    target: str
    label: str = ""
    feedback: bool = False
    # Optional named boundary ports and explicit scene-space lanes.  Complex
    # control drawings own their routing; simple consumers keep the defaults.
    source_port: tuple[str, float] | None = None
    target_port: tuple[str, float] | None = None
    via: tuple[tuple[float, float], ...] = ()


@dataclass
class _ConnectionGraphics:
    spec: ConnectionSpec
    lines: list
    arrow: object
    label: QGraphicsSimpleTextItem | None
    base_color: QColor


class _SignalProxy(QObject):
    selected = Signal(str)


_ROLE_COLORS = {
    "normal": (QColor("#f8fafc"), QColor("#64748b")),
    "controller": (QColor("#eef4ff"), QColor("#175cd3")),
    "plant": (QColor("#ecfdf3"), QColor("#0e9384")),
    "sense": (QColor("#fff7ed"), QColor("#ea580c")),
    "modulator": (QColor("#f5f3ff"), QColor("#7c3aed")),
    "aux": (QColor("#fdf2fa"), QColor("#c11574")),
}


class _BlockItem(QGraphicsRectItem):
    def __init__(self, spec: BlockSpec, proxy: _SignalProxy):
        super().__init__(0.0, 0.0, spec.width, spec.height)
        self.spec = spec
        self.proxy = proxy
        self.setPos(spec.x, spec.y)
        fill, edge = _ROLE_COLORS.get(spec.role, _ROLE_COLORS["normal"])
        self.normal_brush = QBrush(fill)
        self.normal_pen = QPen(edge, 2.0)
        self.selected_pen = QPen(QColor("#111827"), 3.2)
        self.setBrush(self.normal_brush)
        self.setPen(self.normal_pen)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{spec.title}\n{spec.subtitle}" if spec.subtitle else spec.title)

        title = QGraphicsSimpleTextItem(spec.title, self)
        font = QFont()
        font.setPointSize(10 if spec.height < 58.0 else 11)
        font.setBold(True)
        title.setFont(font)
        if title.boundingRect().width() > spec.width - 12.0:
            font.setPointSizeF(font.pointSizeF() * (spec.width - 12.0) / title.boundingRect().width())
            title.setFont(font)
        title_rect = title.boundingRect()
        title_y = 8.0 if spec.height < 58.0 else 13.0
        title.setPos((spec.width - title_rect.width()) / 2.0, title_y)

        if spec.subtitle:
            subtitle = QGraphicsSimpleTextItem(spec.subtitle, self)
            font2 = QFont()
            font2.setPointSize(8 if spec.height < 58.0 else 9)
            subtitle.setFont(font2)
            subtitle.setBrush(QBrush(QColor("#475467")))
            if subtitle.boundingRect().width() > spec.width - 12.0:
                font2.setPointSizeF(font2.pointSizeF() * (spec.width - 12.0) / subtitle.boundingRect().width())
                subtitle.setFont(font2)
            rect = subtitle.boundingRect()
            subtitle_y = max(title_y + title_rect.height() + 3.0, spec.height - rect.height() - 7.0)
            subtitle.setPos((spec.width - rect.width()) / 2.0, subtitle_y)

    def mousePressEvent(self, event):
        self.proxy.selected.emit(self.spec.key)
        super().mousePressEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setPen(self.selected_pen if bool(value) else self.normal_pen)
        return super().itemChange(change, value)


class _DiagramGraphicsView(QGraphicsView):
    """QGraphicsView with CAD-like wheel zoom and double-click fit."""

    def __init__(self, scene: QGraphicsScene, owner: "ControlBlockDiagram"):
        super().__init__(scene)
        self._owner = owner
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        self._owner.zoom_by(1.16 if delta > 0 else 1.0 / 1.16)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        # Double-clicking blank space is a quick "fit" command.  Double-clicking
        # a block is left to the block item so normal selection still works.
        if self.itemAt(event.position().toPoint()) is None:
            self._owner.fit_to_view()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ControlBlockDiagram(QWidget):
    """Reusable clickable vector control diagram.

    ``block_selected`` emits a stable semantic block key. ``select_block`` is
    used by parameter tabs or Bode selectors to drive the diagram in reverse.
    The view supports wheel zoom, hand panning, fit-to-window and selected-path
    highlighting.
    """

    block_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.view = _DiagramGraphicsView(self.scene, self)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.view.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setMinimumHeight(150)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.view.setStyleSheet(
            "QGraphicsView {background:#ffffff;border:1px solid #d0d5dd;border-radius:6px;}"
        )
        self._proxy = _SignalProxy(self)
        self._proxy.selected.connect(self._on_selected)
        self._items: dict[str, _BlockItem] = {}
        self._connections: list[ConnectionSpec] = []
        self._connection_graphics: list[_ConnectionGraphics] = []
        self._blocks: list[BlockSpec] = []
        self._auto_fit = True
        self._selected_key: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

    @property
    def block_specs(self) -> tuple[BlockSpec, ...]:
        return tuple(self._blocks)

    @property
    def connection_specs(self) -> tuple[ConnectionSpec, ...]:
        return tuple(self._connections)

    @property
    def selected_key(self) -> str | None:
        return self._selected_key

    def set_diagram(self, blocks: list[BlockSpec], connections: list[ConnectionSpec]) -> None:
        self.scene.clear()
        self._items.clear()
        self._connection_graphics.clear()
        self._blocks = list(blocks)
        self._connections = list(connections)
        self._selected_key = None
        for spec in blocks:
            item = _BlockItem(spec, self._proxy)
            self.scene.addItem(item)
            self._items[spec.key] = item
        for connection in connections:
            self._draw_connection(connection)
        self._draw_crossings()
        self._place_connection_labels()
        rect = self.scene.itemsBoundingRect().adjusted(-18, -16, 18, 16)
        self.scene.setSceneRect(rect)
        self.fit_to_view()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._auto_fit and not self.scene.sceneRect().isEmpty():
            self._fit_now()

    def _fit_now(self) -> None:
        rect = self.scene.sceneRect()
        if not rect.isEmpty() and self.view.viewport().width() > 4 and self.view.viewport().height() > 4:
            self.view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def fit_to_view(self) -> None:
        self._auto_fit = True
        self._fit_now()

    def actual_size(self) -> None:
        self._auto_fit = False
        self.view.resetTransform()
        if not self.scene.sceneRect().isEmpty():
            self.view.centerOn(self.scene.sceneRect().center())

    def zoom_by(self, factor: float) -> None:
        if factor <= 0.0:
            return
        current = abs(self.view.transform().m11())
        target = current * factor
        if target < 0.35 or target > 5.5:
            return
        self._auto_fit = False
        self.view.scale(factor, factor)

    def zoom_in(self) -> None:
        self.zoom_by(1.18)

    def zoom_out(self) -> None:
        self.zoom_by(1.0 / 1.18)

    def focus_block(self, key: str) -> None:
        item = self._items.get(key)
        if item is None:
            return
        rect = item.sceneBoundingRect().adjusted(-85.0, -65.0, 85.0, 65.0)
        self._auto_fit = False
        self.view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def _on_selected(self, key: str) -> None:
        self.select_block(key, emit=False)
        self.block_selected.emit(key)

    def select_block(self, key: str, *, emit: bool = False) -> None:
        if key not in self._items:
            return
        self._selected_key = key
        for item_key, item in self._items.items():
            item.setSelected(item_key == key)
        self._apply_signal_path_highlight(key)
        if emit:
            self.block_selected.emit(key)

    def clear_selection(self) -> None:
        self._selected_key = None
        for item in self._items.values():
            item.setSelected(False)
            item.setOpacity(1.0)
        for graphics in self._connection_graphics:
            self._style_connection(graphics, highlighted=False, dimmed=False)

    def has_block(self, key: str) -> bool:
        return key in self._items

    def _apply_signal_path_highlight(self, key: str) -> None:
        neighbours = {key}
        for connection in self._connections:
            if connection.source == key:
                neighbours.add(connection.target)
            elif connection.target == key:
                neighbours.add(connection.source)

        for item_key, item in self._items.items():
            if item_key == key:
                item.setOpacity(1.0)
            elif item_key in neighbours:
                item.setOpacity(0.92)
            else:
                item.setOpacity(0.42)

        for graphics in self._connection_graphics:
            related = graphics.spec.source == key or graphics.spec.target == key
            self._style_connection(graphics, highlighted=related, dimmed=not related)

    @staticmethod
    def _style_connection(
        graphics: _ConnectionGraphics,
        *,
        highlighted: bool,
        dimmed: bool,
    ) -> None:
        width = 3.0 if highlighted else 1.8
        pen = QPen(graphics.base_color, width)
        pen.setCosmetic(True)
        opacity = 1.0 if highlighted else (0.22 if dimmed else 1.0)
        for line in graphics.lines:
            line.setPen(pen)
            line.setOpacity(opacity)
        graphics.arrow.setPen(pen)
        graphics.arrow.setBrush(QBrush(graphics.base_color))
        graphics.arrow.setOpacity(opacity)
        if graphics.label is not None:
            graphics.label.setOpacity(1.0 if highlighted else (0.28 if dimmed else 1.0))

    @staticmethod
    def _port(rect: QRectF, port: tuple[str, float]) -> QPointF:
        side, offset = port
        if side in ("top", "bottom"):
            return QPointF(rect.left() + offset * rect.width(),
                           rect.top() if side == "top" else rect.bottom())
        if side in ("left", "right"):
            return QPointF(rect.left() if side == "left" else rect.right(),
                           rect.top() + offset * rect.height())
        raise ValueError(f"Unknown diagram port: {side}")

    @staticmethod
    def _segment_rect(p: QPointF, q: QPointF, margin: float = 0.1) -> QRectF:
        return QRectF(p, q).normalized().adjusted(-margin, -margin, margin, margin)

    def _draw_connection(self, spec: ConnectionSpec) -> None:
        if spec.source not in self._items or spec.target not in self._items:
            return
        # Use the actual rectangle, not its pen-expanded bounding box.
        a = self._items[spec.source].mapRectToScene(self._items[spec.source].rect())
        b = self._items[spec.target].mapRectToScene(self._items[spec.target].rect())
        same_row = abs(a.center().y() - b.center().y()) < min(a.height(), b.height()) * 0.55
        if spec.feedback and a.top() >= b.bottom():
            source, target = ("top", 0.25), ("bottom", 0.5)
        elif same_row:
            source, target = (("right", 0.5), ("left", 0.5)) if b.center().x() >= a.center().x() else (("left", 0.5), ("right", 0.5))
        elif b.center().y() >= a.center().y():
            source, target = ("bottom", 0.5), ("top", 0.5)
        else:
            source, target = ("top", 0.5), ("bottom", 0.5)
        source, target = spec.source_port or source, spec.target_port or target
        p1, p4 = self._port(a, source), self._port(b, target)
        if spec.via:
            points = [p1, *(QPointF(*point) for point in spec.via), p4]
        elif source[0] in ("top", "bottom") and target[0] in ("top", "bottom"):
            # A vertical port must leave vertically, even when the horizontal
            # distance is larger.  Stay in the gap, including narrow feedback gaps.
            y = (p1.y() + p4.y()) / 2.0
            points = [p1, QPointF(p1.x(), y), QPointF(p4.x(), y), p4]
            # Simple stacked consumers (e.g. PLL -> current controller) may
            # have an intervening block.  Bypass that column on its outside.
            obstacles = [item.sceneBoundingRect().adjusted(1, 1, -1, -1)
                         for key, item in self._items.items() if key not in (spec.source, spec.target)]
            if not spec.source_port and not spec.target_port and any(
                self._segment_rect(p, q).intersects(rect)
                for p, q in zip(points, points[1:]) for rect in obstacles
            ):
                x = max(item.sceneBoundingRect().right() for item in self._items.values()) + 24.0
                p1, p4 = self._port(a, ("right", 0.5)), self._port(b, ("right", 0.5))
                points = [p1, QPointF(x, p1.y()), QPointF(x, p4.y()), p4]
        else:
            x = (p1.x() + p4.x()) / 2.0
            points = [p1, QPointF(x, p1.y()), QPointF(x, p4.y()), p4]
        points = [point for i, point in enumerate(points) if i == 0 or point != points[i-1]]
        # Coalesce straight dog-leg vertices so a crossing at the old midpoint
        # receives the same visible break as one inside a single segment.
        for i in range(len(points)-2, 0, -1):
            p, q, r = points[i-1:i+2]
            if (p.x() == q.x() == r.x()) or (p.y() == q.y() == r.y()):
                points.pop(i)

        base_color = QColor("#0e9384" if spec.feedback else "#175cd3")
        pen = QPen(base_color, 1.8)
        pen.setCosmetic(True)
        line_items = []
        for p, q in zip(points[:-1], points[1:]):
            line_items.append(self.scene.addLine(p.x(), p.y(), q.x(), q.y(), pen))

        # Arrow head at target.
        p_from, p_to = points[-2], points[-1]
        dx, dy = p_to.x() - p_from.x(), p_to.y() - p_from.y()
        norm = max((dx * dx + dy * dy) ** 0.5, 1e-9)
        ux, uy = dx / norm, dy / norm
        px, py = -uy, ux
        size = 10.0
        tip = p_to
        base = QPointF(tip.x() - ux * size, tip.y() - uy * size)
        poly = QPolygonF([
            tip,
            QPointF(base.x() + px * size * 0.45, base.y() + py * size * 0.45),
            QPointF(base.x() - px * size * 0.45, base.y() - py * size * 0.45),
        ])
        arrow = self.scene.addPolygon(poly, pen, QBrush(base_color))
        arrow.setZValue(2)
        label_item: QGraphicsSimpleTextItem | None = None
        if spec.label:
            label_item = QGraphicsSimpleTextItem(spec.label)
            font = QFont()
            font.setPointSize(9)
            label_item.setFont(font)
            label_item.setBrush(QBrush(QColor("#344054")))
            self.scene.addItem(label_item)
            label_item.setZValue(3)

        self._connection_graphics.append(_ConnectionGraphics(
            spec=spec,
            lines=line_items,
            arrow=arrow,
            label=label_item,
            base_color=base_color,
        ))

    def _draw_crossings(self) -> None:
        # A white break in the underpassing stroke distinguishes an ordinary
        # crossing from a junction.  There are no implicit signal junctions.
        segments = [(graphics, item.line()) for graphics in self._connection_graphics
                    for item in graphics.lines]
        crossings = set()
        for graphics, horizontal in segments:
            if abs(horizontal.dy()) > 0.1:
                continue
            for other, vertical in segments:
                if graphics is other or abs(vertical.dx()) > 0.1:
                    continue
                x, y = vertical.x1(), horizontal.y1()
                if (min(horizontal.x1(), horizontal.x2()) + 0.1 < x < max(horizontal.x1(), horizontal.x2()) - 0.1
                        and min(vertical.y1(), vertical.y2()) + 0.1 < y < max(vertical.y1(), vertical.y2()) - 0.1
                        and (x, y) not in crossings):
                    crossings.add((x, y))
                    gap = self.scene.addEllipse(x-4, y-4, 8, 8, QPen(Qt.PenStyle.NoPen), QBrush(QColor("white")))
                    gap.setZValue(1)
                    pen = QPen(graphics.base_color, 1.8)
                    pen.setCosmetic(True)
                    bridge = self.scene.addLine(x-5, y, x+5, y, pen)
                    bridge.setZValue(1)
                    graphics.lines.append(bridge)

    def _place_connection_labels(self) -> None:
        """Place text beside a real segment, outside blocks and signal strokes."""
        if not self._items:
            return
        occupied = [item.sceneBoundingRect().adjusted(-3, -3, 3, 3) for item in self._items.values()]
        strokes = [self._segment_rect(item.line().p1(), item.line().p2(), 3.0)
                   for graphics in self._connection_graphics for item in graphics.lines]
        strokes += [graphics.arrow.sceneBoundingRect().adjusted(-2, -2, 2, 2)
                    for graphics in self._connection_graphics]
        top = min(rect.top() for rect in occupied)
        bottom = max(rect.bottom() for rect in occupied)
        for graphics in self._connection_graphics:
            label = graphics.label
            if label is None:
                continue
            w, h = label.boundingRect().width(), label.boundingRect().height()
            segments = sorted((item.line() for item in graphics.lines), key=lambda line: line.length(), reverse=True)
            candidates = []
            for line in segments:
                x, y = line.center().x(), line.center().y()
                if abs(line.dy()) < 0.1:
                    for left in (x-w/2, min(line.x1(), line.x2())+5,
                                 max(line.x1(), line.x2())-w-5):
                        candidates += [(left, y-h-5), (left, y+5)]
                else:
                    for above in (y-h/2, min(line.y1(), line.y2())+5,
                                  max(line.y1(), line.y2())-h-5):
                        candidates += [(x+10, above), (x-w-10, above)]
            # Short between-block gaps can be narrower than the signal name.
            # Place those labels immediately above/below the row, never on titles.
            a = self._items[graphics.spec.source].sceneBoundingRect()
            b = self._items[graphics.spec.target].sceneBoundingRect()
            for line in segments:
                x = line.center().x() - w/2
                candidates += [(x, min(a.top(), b.top())-h-7),
                               (x, max(a.bottom(), b.bottom())+7)]
            for line in segments:
                x = line.center().x() - w/2
                candidates += [(x, top-h-5), (x, bottom+5)]
            for x, y in candidates:
                rect = QRectF(x, y, w, h)
                if not any(rect.intersects(other) for other in occupied + strokes):
                    label.setPos(x, y)
                    occupied.append(rect.adjusted(-3, -3, 3, 3))
                    break
            else:
                # A dense custom drawing still gets a readable external label.
                label.setPos(segments[0].center().x()-w/2, top-h-5)
                top -= h+10
                occupied.append(label.sceneBoundingRect())


__all__ = ["BlockSpec", "ConnectionSpec", "ControlBlockDiagram"]
