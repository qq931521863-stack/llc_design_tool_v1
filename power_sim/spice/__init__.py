"""SPICE power-stage backends for Power Design Toolkit.

Batch ngspice is the correlation/smoke backend. Shared-ngspice is the
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
from .gate import FrequencySegment, FrequencyTimeline, LLCFullBridgeGateScheduler
from .sync import SyncEvent, LLCCoSimulationSynchronizer
from .closed_loop import NgSpiceClosedLoopConfig, NgSpiceClosedLoopResult, run_llc_shared_closed_loop
from .pfc import DutySegment, DutyTimeline, TTPLGateScheduler, TTPLSpiceConfig, build_ttpl_pfc_circuit
from .pfc_closed_loop import (
    TTPLClosedLoopScenario,
    TTPLControlSample,
    TTPLSharedNgSpiceConfig,
    TTPLSharedNgSpiceMetrics,
    TTPLSharedNgSpiceResult,
    run_ttpl_shared_closed_loop,
)
from .waveforms import ngspice_closed_loop_waveform_bundle

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
    "FrequencySegment",
    "FrequencyTimeline",
    "LLCFullBridgeGateScheduler",
    "SyncEvent",
    "LLCCoSimulationSynchronizer",
    "NgSpiceClosedLoopConfig",
    "NgSpiceClosedLoopResult",
    "run_llc_shared_closed_loop",
    "DutySegment",
    "DutyTimeline",
    "TTPLGateScheduler",
    "TTPLSpiceConfig",
    "build_ttpl_pfc_circuit",
    "TTPLClosedLoopScenario",
    "TTPLControlSample",
    "TTPLSharedNgSpiceConfig",
    "TTPLSharedNgSpiceMetrics",
    "TTPLSharedNgSpiceResult",
    "run_ttpl_shared_closed_loop",
    "ngspice_closed_loop_waveform_bundle",
]
