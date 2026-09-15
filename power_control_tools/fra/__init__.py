"""FRA-based loop-design utilities.

The FRA package intentionally works on measured complex frequency-response
points directly.  It does not fit a rational plant model in V1.
"""

from .models import FRAMeasurement, FRAMeasurementKind, FRASourceFormat
from .importers import load_fra_file
from .analysis import (
    DeembedResult,
    GainCrossover,
    LoopStabilityResult,
    PhaseCrossover,
    analyze_loop_response,
    deembed_controller,
    digital_frequency_response,
    magnitude_phase,
)

__all__ = [
    "FRAMeasurement",
    "FRAMeasurementKind",
    "FRASourceFormat",
    "load_fra_file",
    "DeembedResult",
    "GainCrossover",
    "LoopStabilityResult",
    "PhaseCrossover",
    "analyze_loop_response",
    "deembed_controller",
    "digital_frequency_response",
    "magnitude_phase",
]
