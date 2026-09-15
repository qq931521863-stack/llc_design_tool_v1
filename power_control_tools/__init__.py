"""Reusable digital-control design utilities for power-electronics firmware."""
from .models import (
    AnalogTransferFunction, DigitalTransferFunction, DiscretizationMethod,
    ControllerKind, FilterResponse, IIRFamily, StabilityClass, FilterDesignResult,
)
from .controllers import design_controller
from .discretize import discretize_transfer_function
from .filters import design_iir_filter, design_fir_filter, design_moving_average, design_dc_blocker
from .analysis import analyze_digital_filter, ControlResponseAnalysis
from .codegen import export_c99_filter, verify_c99_filter, render_c99_single_file, C99Verification
from .fra import (
    FRAMeasurement, FRAMeasurementKind, FRASourceFormat,
    load_fra_file, analyze_loop_response, deembed_controller,
    digital_frequency_response, magnitude_phase,
    scale_digital_controller, firmware_feedback_a_to_denominator,
    AutoDesignCandidate, AutoDesignResult, auto_design_controller,
    RationalPlantModel, FRAFitMetrics, FRAFitResult, FitClosedLoopStepResult,
    fit_rational_frequency_response, closed_loop_step_from_fitted_loop,
    PlantControllerLinkResult, link_plant_model_with_controller,
    discretize_plant_model, closed_loop_step_from_plant_and_controller,
    plant_model_polynomials_rad_s,
)

__all__ = [
    "AnalogTransferFunction", "DigitalTransferFunction", "DiscretizationMethod",
    "ControllerKind", "FilterResponse", "IIRFamily", "StabilityClass", "FilterDesignResult",
    "design_controller", "discretize_transfer_function", "design_iir_filter",
    "design_fir_filter", "design_moving_average", "design_dc_blocker",
    "analyze_digital_filter", "ControlResponseAnalysis", "export_c99_filter", "verify_c99_filter", "render_c99_single_file", "C99Verification",
    "FRAMeasurement", "FRAMeasurementKind", "FRASourceFormat", "load_fra_file",
    "analyze_loop_response", "deembed_controller", "digital_frequency_response", "magnitude_phase",
    "scale_digital_controller", "firmware_feedback_a_to_denominator",
    "AutoDesignCandidate", "AutoDesignResult", "auto_design_controller",
    "RationalPlantModel", "FRAFitMetrics", "FRAFitResult", "FitClosedLoopStepResult",
    "fit_rational_frequency_response", "closed_loop_step_from_fitted_loop",
    "PlantControllerLinkResult", "link_plant_model_with_controller",
    "discretize_plant_model", "closed_loop_step_from_plant_and_controller",
    "plant_model_polynomials_rad_s",
]
