"""FRA-based loop-design utilities.

Raw measured complex frequency-response points remain the primary stability
authority. V1.5 adds target-Fc/PM automatic controller synthesis directly from
those points; V2 adds optional low-order rational identification for advanced
analysis and approximate time-domain work.
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
    phase_margin_from_unwrapped_phase,
)
from .quality import FRAQualityIssue, FRAQualityReport, analyze_fra_quality
from .tuning import firmware_feedback_a_to_denominator, scale_digital_controller
from .power_compensators import design_power_pz_compensator
from .auto_design import AutoDesignCandidate, AutoDesignResult, auto_design_controller
from .fitting import (
    FRAFitLoopValidation,
    FRAFitMetrics,
    FRAFitResult,
    FitClosedLoopStepResult,
    RationalPlantModel,
    closed_loop_step_from_fitted_loop,
    fit_rational_frequency_response,
    validate_fitted_open_loop,
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
    "phase_margin_from_unwrapped_phase",
    "FRAQualityIssue",
    "FRAQualityReport",
    "analyze_fra_quality",
    "scale_digital_controller",
    "firmware_feedback_a_to_denominator",
    "design_power_pz_compensator",
    "AutoDesignCandidate",
    "AutoDesignResult",
    "auto_design_controller",
    "RationalPlantModel",
    "FRAFitMetrics",
    "FRAFitResult",
    "FRAFitLoopValidation",
    "FitClosedLoopStepResult",
    "fit_rational_frequency_response",
    "closed_loop_step_from_fitted_loop",
    "validate_fitted_open_loop",
]
