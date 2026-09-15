"""SPICE power-stage backends for Power Design Toolkit.

Batch ngspice is the correlation/smoke backend.  Shared-ngspice is the intended
continuous-state backend for digital closed-loop co-simulation.
"""
from .ir import (
    CircuitIR,
    CircuitValidation,
    ElementKind,
    SpiceElement,
    normalize_node,
    validate_circuit,
)
from .netlist import render_element, render_netlist
from .ngspice_batch import (
    NgSpiceBatchEngine,
    NgSpiceBatchResult,
    NgSpiceRawData,
    find_ngspice_executable,
    parse_ascii_raw,
)
from .shared import NgSpiceSharedLibrary, find_ngspice_shared_library
from .llc import LLCSpiceConfig, build_ideal_llc_circuit, default_llc_transient_window

__all__ = [
    "CircuitIR",
    "CircuitValidation",
    "ElementKind",
    "SpiceElement",
    "normalize_node",
    "validate_circuit",
    "render_element",
    "render_netlist",
    "NgSpiceBatchEngine",
    "NgSpiceBatchResult",
    "NgSpiceRawData",
    "find_ngspice_executable",
    "parse_ascii_raw",
    "NgSpiceSharedLibrary",
    "find_ngspice_shared_library",
    "LLCSpiceConfig",
    "build_ideal_llc_circuit",
    "default_llc_transient_window",
]
