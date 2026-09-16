"""Engineering-oriented PFC power-stage design kernels.

This package is intentionally separate from the historical two-phase PFC loss
model and from the TTPL control laboratory.  It owns specification-to-hardware
sizing calculations that should remain usable from GUI, CLI, web and Agent
front ends.
"""

from .device_library import PFCDeviceDatabase, default_user_pfc_device_library_path
from .device_loss import (
    TTPLDeviceComparison,
    TTPLHFDeviceLoss,
    TTPLSlowDeviceLoss,
    compare_ttpl_devices,
    evaluate_hf_device,
    evaluate_slow_device,
)
from .ttpl_design import (
    TTPLDesignResult,
    TTPLDesignSpec,
    TTPLInputWorkPoint,
    TTPLLineTrace,
    analyze_ttpl_design,
)

__all__ = [
    "PFCDeviceDatabase",
    "TTPLDesignResult",
    "TTPLDesignSpec",
    "TTPLDeviceComparison",
    "TTPLHFDeviceLoss",
    "TTPLInputWorkPoint",
    "TTPLLineTrace",
    "TTPLSlowDeviceLoss",
    "analyze_ttpl_design",
    "compare_ttpl_devices",
    "default_user_pfc_device_library_path",
    "evaluate_hf_device",
    "evaluate_slow_device",
]
