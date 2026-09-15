from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from .models import FRAMeasurement


@dataclass(frozen=True)
class FRAQualityIssue:
    index: int | None
    frequency_hz: float | None
    kind: str
    severity: str
    message: str


@dataclass(frozen=True)
class FRAQualityReport:
    status: str
    point_count: int
    decade_span: float
    median_points_per_decade: float
    suspicious_point_indices: tuple[int, ...]
    issues: tuple[FRAQualityIssue, ...]

    @property
    def warning_count(self) -> int:
        return sum(1 for item in self.issues if item.severity == "WARNING")


def analyze_fra_quality(
    measurement: FRAMeasurement,
    *,
    phase_offset_deg: float | None = None,
    local_magnitude_residual_db: float = 6.0,
    local_phase_residual_deg: float = 30.0,
    phase_step_deg: float = 75.0,
) -> FRAQualityReport:
    """Audit FRA sampling/data continuity without modifying measured points.

    The quality layer is intentionally conservative.  It only reports points
    that deserve inspection; it never removes, smooths, or replaces measured
    data.  A sharp real resonance can therefore be flagged as suspicious and
    must be judged by the engineer rather than silently filtered away.
    """
    f = np.asarray(measurement.frequency_hz, dtype=float)
    mag = np.asarray(measurement.magnitude_db, dtype=float)
    phase = np.asarray(measurement.normalized_phase_deg(phase_offset_deg), dtype=float)
    issues: list[FRAQualityIssue] = []
    suspicious: set[int] = set()

    decades = math.log10(float(f[-1]) / float(f[0]))
    points_per_decade = (len(f) - 1) / max(decades, 1e-12)
    log_steps = np.diff(np.log10(f))
    median_step = float(np.median(log_steps)) if log_steps.size else math.inf
    if log_steps.size and median_step > 0.0:
        gap_indices = np.flatnonzero(log_steps > 4.0 * median_step)
        for i in gap_indices:
            issues.append(
                FRAQualityIssue(
                    int(i),
                    float(f[i]),
                    "FREQUENCY_GAP",
                    "WARNING",
                    f"frequency gap {f[i]:.6g} -> {f[i+1]:.6g} Hz is >4x the median log-frequency step",
                )
            )

    # Local leave-one-out interpolation in log-frequency.  This is a data
    # consistency indicator, not a smoothing algorithm.
    for i in range(1, len(f) - 1):
        lx0 = math.log10(float(f[i - 1]))
        lx1 = math.log10(float(f[i + 1]))
        lxi = math.log10(float(f[i]))
        t = 0.5 if lx1 == lx0 else (lxi - lx0) / (lx1 - lx0)
        mag_pred = float(mag[i - 1] + t * (mag[i + 1] - mag[i - 1]))
        phase_pred = float(phase[i - 1] + t * (phase[i + 1] - phase[i - 1]))
        mag_resid = float(mag[i] - mag_pred)
        phase_resid = float(phase[i] - phase_pred)
        if abs(mag_resid) >= float(local_magnitude_residual_db) or abs(phase_resid) >= float(local_phase_residual_deg):
            suspicious.add(i)
            issues.append(
                FRAQualityIssue(
                    i,
                    float(f[i]),
                    "LOCAL_OUTLIER_OR_RESONANCE",
                    "WARNING",
                    f"local residual: dMag={mag_resid:+.3f} dB, dPhase={phase_resid:+.3f} deg; inspect before model fitting",
                )
            )

    if len(phase) >= 2:
        steps = np.diff(phase)
        for i in np.flatnonzero(np.abs(steps) >= float(phase_step_deg)):
            suspicious.add(int(i))
            suspicious.add(int(i + 1))
            issues.append(
                FRAQualityIssue(
                    int(i + 1),
                    float(f[i + 1]),
                    "PHASE_STEP",
                    "WARNING",
                    f"adjacent unwrapped phase step is {steps[i]:+.3f} deg",
                )
            )

    if len(f) < 20:
        issues.append(
            FRAQualityIssue(None, None, "SPARSE_DATA", "WARNING", "fewer than 20 FRA points; crossover/margin interpolation confidence is limited")
        )

    status = "PASS" if not issues else "WARNING"
    return FRAQualityReport(
        status,
        int(len(f)),
        float(decades),
        float(points_per_decade),
        tuple(sorted(suspicious)),
        tuple(issues),
    )


__all__ = ["FRAQualityIssue", "FRAQualityReport", "analyze_fra_quality"]
