"""Engineering-oriented PFC power-stage design kernels.

This package is intentionally separate from the historical two-phase PFC loss
model and from the TTPL control laboratory.  It owns specification-to-hardware
sizing calculations that should remain usable from GUI, CLI, web and Agent
front ends.
"""

from .ttpl_design import (
    TTPLDesignResult,
    TTPLDesignSpec,
    TTPLInputWorkPoint,
    TTPLLineTrace,
    analyze_ttpl_design,
)

__all__ = [
    "TTPLDesignResult",
    "TTPLDesignSpec",
    "TTPLInputWorkPoint",
    "TTPLLineTrace",
    "analyze_ttpl_design",
]
