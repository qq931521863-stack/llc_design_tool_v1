"""Deterministic CircuitIR -> ngspice-compatible netlist rendering."""
from __future__ import annotations

from .ir import CircuitIR, ElementKind, SpiceElement, normalize_node, validate_circuit


def _params(element: SpiceElement) -> str:
    if not element.params:
        return ""
    return " " + " ".join(f"{key}={value}" for key, value in element.params)


def render_element(element: SpiceElement) -> str:
    kind = element.kind
    if kind == ElementKind.RAW:
        return element.raw_line.strip()
    if kind == ElementKind.COUPLING:
        l1, l2 = element.terminals
        return f"{element.refdes} {l1} {l2} {element.value}{_params(element)}"

    nodes = " ".join(normalize_node(v) for v in element.terminals)
    if kind in {
        ElementKind.RESISTOR,
        ElementKind.CAPACITOR,
        ElementKind.INDUCTOR,
        ElementKind.VOLTAGE_SOURCE,
        ElementKind.CURRENT_SOURCE,
    }:
        return f"{element.refdes} {nodes} {element.value}{_params(element)}"
    if kind in {ElementKind.SWITCH, ElementKind.DIODE}:
        return f"{element.refdes} {nodes} {element.model}{_params(element)}"
    raise ValueError(f"unsupported SPICE element kind {kind}")


def render_netlist(circuit: CircuitIR, *, include_end: bool = True) -> str:
    validation = validate_circuit(circuit)
    validation.require_valid()

    lines: list[str] = [f"* {circuit.name}"]
    lines.extend(render_element(element) for element in circuit.elements)
    if circuit.models:
        lines.append("")
        lines.append("* Models")
        for model in circuit.models:
            text = str(model).strip()
            if text:
                lines.append(text if text.startswith(".") else f".model {text}")
    if circuit.save_vectors:
        vectors = " ".join(str(v).strip() for v in circuit.save_vectors if str(v).strip())
        if vectors:
            lines.append(f".save {vectors}")
    for directive in circuit.directives:
        text = str(directive).strip()
        if text:
            lines.append(text)
    if include_end:
        lines.append(".end")
    return "\n".join(lines) + "\n"


__all__ = ["render_element", "render_netlist"]
