"""Power Design Toolkit digital closed-loop and circuit-simulation runtime.

Backend-independent ADC/controller/FM scheduling remains the control authority.
The optional ``power_sim.spice`` layer adds Circuit IR plus batch/shared ngspice
backends so the exact same digital controller can close around a switching
circuit model without duplicating controller mathematics.
"""

from .digital_control import (
    LLCFMMode,
    PWMCountMode,
    SamplerConfig,
    SamplerRuntime,
    ControllerLimitConfig,
    DigitalTransferRuntime,
    LLCFMConfig,
    LLCFMRuntime,
    LLCFMLUTConfig,
    LLCFMLUTRuntime,
    LLCFMStep,
    make_fm_runtime,
)
from .closed_loop import (
    StepProfile,
    ClosedLoopTiming,
    ClosedLoopScenario,
    ClosedLoopSample,
    ClosedLoopDiagnostics,
    ClosedLoopResult,
    LinearClosedLoopAnalysis,
    FirstOrderLLCPlant,
    analyze_linear_closed_loop,
    llc_small_signal_to_digital_plant,
    run_closed_loop,
)
from .spice import (
    CircuitIR,
    CircuitValidation,
    ElementKind,
    SpiceElement,
    NgSpiceBatchEngine,
    NgSpiceBatchResult,
    NgSpiceRawData,
    NgSpiceSharedLibrary,
    LLCSpiceConfig,
    build_ideal_llc_circuit,
    default_llc_transient_window,
    find_ngspice_executable,
    find_ngspice_shared_library,
    parse_ascii_raw,
    render_netlist,
    validate_circuit,
)

__all__ = [
    "LLCFMMode", "PWMCountMode", "SamplerConfig", "SamplerRuntime",
    "ControllerLimitConfig", "DigitalTransferRuntime", "LLCFMConfig",
    "LLCFMRuntime", "LLCFMLUTConfig", "LLCFMLUTRuntime", "LLCFMStep",
    "make_fm_runtime", "StepProfile", "ClosedLoopTiming",
    "ClosedLoopScenario", "ClosedLoopSample", "ClosedLoopDiagnostics",
    "ClosedLoopResult", "LinearClosedLoopAnalysis", "FirstOrderLLCPlant",
    "analyze_linear_closed_loop", "llc_small_signal_to_digital_plant",
    "run_closed_loop",
    "CircuitIR", "CircuitValidation", "ElementKind", "SpiceElement",
    "NgSpiceBatchEngine", "NgSpiceBatchResult", "NgSpiceRawData",
    "NgSpiceSharedLibrary", "LLCSpiceConfig", "build_ideal_llc_circuit",
    "default_llc_transient_window", "find_ngspice_executable",
    "find_ngspice_shared_library", "parse_ascii_raw", "render_netlist",
    "validate_circuit",
]
