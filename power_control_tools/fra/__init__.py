"""FRA-based loop-design utilities.

The FRA package keeps raw measured complex frequency-response points as the
primary stability authority.  V1.5 adds target-Fc/PM automatic controller
synthesis directly from those points; V2 adds an optional low-order rational
identification model for advanced analysis and approximate time-domain work.
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
from .tuning import firmware_feedback_a_to_denominator, scale_digital_controller
from .auto_design import AutoDesignCandidate, AutoDesignResult, auto_design_controller
from .fitting import (
    FRAFitMetrics,
    FRAFitResult,
    FitClosedLoopStepResult,
    RationalPlantModel,
    closed_loop_step_from_fitted_loop,
    fit_rational_frequency_response,
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
    "scale_digital_controller",
    "firmware_feedback_a_to_denominator",
    "AutoDesignCandidate",
    "AutoDesignResult",
    "auto_design_controller",
    "RationalPlantModel",
    "FRAFitMetrics",
    "FRAFitResult",
    "FitClosedLoopStepResult",
    "fit_rational_frequency_response",
    "closed_loop_step_from_fitted_loop",
]
