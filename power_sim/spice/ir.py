"""Backend-neutral circuit IR for SPICE power-stage simulation.

The design is intentionally smaller and stricter than a schematic editor.  It
represents only electrical connectivity and simulation intent so LLC/PFC design
objects can be rendered to ngspice without coupling the engineering model to a
particular netlist dialect.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Iterable


class ElementKind(str, Enum):
    RESISTOR = "resistor"
    CAPACITOR = "capacitor"
    INDUCTOR = "inductor"
    VOLTAGE_SOURCE = "voltage_source"
    CURRENT_SOURCE = "current_source"
    SWITCH = "switch"
    DIODE = "diode"
    COUPLING = "coupling"
    RAW = "raw"


_PIN_COUNTS: dict[ElementKind, int | None] = {
    ElementKind.RESISTOR: 2,
    ElementKind.CAPACITOR: 2,
    ElementKind.INDUCTOR: 2,
    ElementKind.VOLTAGE_SOURCE: 2,
    ElementKind.CURRENT_SOURCE: 2,
    ElementKind.SWITCH: 4,
    ElementKind.DIODE: 2,
    ElementKind.COUPLING: 2,  # inductor reference names, not electrical nodes
    ElementKind.RAW: None,
}

_GROUND_ALIASES = {"0", "gnd", "ground", "agnd", "dgnd"}
_NODE_RE = re.compile(r"[^A-Za-z0-9_.:+-]+")


def normalize_node(value: str) -> str:
    node = str(value).strip()
    if node.lower() in _GROUND_ALIASES:
        return "0"
    node = _NODE_RE.sub("_", node)
    if not node:
        raise ValueError("SPICE node name cannot be empty")
    return node


@dataclass(frozen=True)
class SpiceElement:
    refdes: str
    kind: ElementKind | str
    terminals: tuple[str, ...] = ()
    value: str = ""
    model: str = ""
    params: tuple[tuple[str, str], ...] = ()
    raw_line: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ElementKind(self.kind))
        object.__setattr__(self, "refdes", str(self.refdes).strip())
        object.__setattr__(self, "terminals", tuple(str(v).strip() for v in self.terminals))
        object.__setattr__(self, "params", tuple((str(k), str(v)) for k, v in self.params))
        if not self.refdes:
            raise ValueError("SPICE element refdes cannot be empty")


@dataclass
class CircuitIR:
    name: str
    elements: list[SpiceElement] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    directives: list[str] = field(default_factory=list)
    save_vectors: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    def add(self, element: SpiceElement) -> "CircuitIR":
        self.elements.append(element)
        return self

    def extend(self, elements: Iterable[SpiceElement]) -> "CircuitIR":
        self.elements.extend(elements)
        return self


@dataclass(frozen=True)
class CircuitValidation:
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    def require_valid(self) -> None:
        if not self.valid:
            raise ValueError("invalid CircuitIR: " + "; ".join(self.errors))


def validate_circuit(circuit: CircuitIR) -> CircuitValidation:
    errors: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    connected_nodes: set[str] = set()

    for element in circuit.elements:
        ref = element.refdes.upper()
        if ref in seen:
            errors.append(f"duplicate element reference {element.refdes}")
        seen.add(ref)

        expected = _PIN_COUNTS[element.kind]
        if expected is not None and len(element.terminals) != expected:
            errors.append(
                f"{element.refdes} ({element.kind.value}) requires {expected} terminals, got {len(element.terminals)}"
            )

        if element.kind == ElementKind.RAW:
            if not element.raw_line.strip():
                errors.append(f"{element.refdes}: RAW element requires raw_line")
            continue

        if element.kind == ElementKind.COUPLING:
            if not element.value.strip():
                errors.append(f"{element.refdes}: coupling coefficient is required")
            continue

        for node in element.terminals:
            try:
                connected_nodes.add(normalize_node(node))
            except ValueError as exc:
                errors.append(f"{element.refdes}: {exc}")

        if element.kind in {
            ElementKind.RESISTOR,
            ElementKind.CAPACITOR,
            ElementKind.INDUCTOR,
            ElementKind.VOLTAGE_SOURCE,
            ElementKind.CURRENT_SOURCE,
        } and not element.value.strip():
            errors.append(f"{element.refdes}: value/source expression is required")
        if element.kind in {ElementKind.SWITCH, ElementKind.DIODE} and not element.model.strip():
            errors.append(f"{element.refdes}: model name is required")

    if "0" not in connected_nodes:
        errors.append("circuit has no SPICE ground node 0")
    if not circuit.elements:
        errors.append("circuit contains no elements")

    for vector in circuit.save_vectors:
        if not str(vector).strip():
            warnings.append("empty save vector ignored")

    return CircuitValidation(not errors, tuple(errors), tuple(warnings))


__all__ = [
    "ElementKind",
    "SpiceElement",
    "CircuitIR",
    "CircuitValidation",
    "normalize_node",
    "validate_circuit",
]
