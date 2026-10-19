"""Same-cycle capacitor PWM second moments, with unresolved slices explicit.

This is an observer of the accepted averaged trajectory, not a replacement
switching simulation. In a local CCM triangle the capacitor carries -Iload
while the active switch is on, and iL-Iload while its rectifier is on. Thus
E[iC] is unchanged and Var(iC | line sample) = D(1-D)IL² +(1-D)ΔIL²/12.
The local integral is exact for that triangle, while the triangle itself is
an approximation. No switching phase history, DCM policy or commutation
trajectory is invented. Missing slices are not renormalized or filled in.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    from .pfc_v3 import PFCLineCycleResult


@dataclass(frozen=True)
class PFCCapacitorRipple:
    averaged_current_rms_a: float
    pwm_pulsation_variance_a2: NDArray[np.float64]
    inductor_ripple_variance_a2: NDArray[np.float64]
    valid_sample_mask: NDArray[np.bool_]
    known_model_current_rms_a: float
    zero_cross_state_available: bool
    model: str = 'accepted_cycle_local_ccm_pwm_second_moment'

    @property
    def known_switching_variance_a2(self) -> NDArray[np.float64]:
        return self.pwm_pulsation_variance_a2 + self.inductor_ripple_variance_a2

    @property
    def unresolved_samples(self) -> int:
        return int(np.count_nonzero(~self.valid_sample_mask))

    @property
    def complete(self) -> bool:
        return self.unresolved_samples == 0

    @property
    def total_current_rms_a(self) -> float | None:
        return self.known_model_current_rms_a if self.complete else None

    @property
    def known_switching_current_rms_a(self) -> float:
        return float(np.sqrt(np.mean(self.known_switching_variance_a2)))

    @property
    def limitations(self) -> tuple[str, ...]:
        notes = (
            'Local CCM triangular reconstruction from accepted IL, ripple and duty; not switching-resolved simulation or hardware evidence.',
            'Ideal PWM branch allocation; dead-time/body-diode commutation, nonlinear parasitics and frequency-dependent vendor ESR are not resolved.',
            'Averaged capacitor current and PF/THD remain the accepted averaged trajectory; HF moments do not change its means.',
        )
        if not self.complete:
            notes += (
                'Unresolved DCM/zero-crossing slices contribute their averaged current only. Known-model RMS/heating is partial, not a bound on the physical result; complete RMS, hot-spot and lifetime are unavailable.',
            )
        if not self.zero_cross_state_available:
            notes += ('No zero-crossing state metadata: the supplied trajectory assumes ordinary local CCM PWM branch allocation.',)
        return notes

    def as_dict(self) -> dict[str, Any]:
        return {
            'model': self.model,
            'status': 'COMPLETE_IN_LOCAL_CCM_MODEL' if self.complete else 'PARTIAL',
            'source': 'closed_loop_final_line_cycle',
            'weighting': 'uniform_endpoint_exclusive_full_line_cycle; unresolved slices retain original weight',
            'averaged_current_rms_a': self.averaged_current_rms_a,
            'known_pwm_pulsation_mean_square_a2': float(np.mean(self.pwm_pulsation_variance_a2)),
            'known_inductor_ripple_mean_square_a2': float(np.mean(self.inductor_ripple_variance_a2)),
            'known_switching_current_rms_a': self.known_switching_current_rms_a,
            'known_model_current_rms_a': self.known_model_current_rms_a,
            'total_current_rms_a': self.total_current_rms_a,
            'valid_samples': int(np.count_nonzero(self.valid_sample_mask)),
            'sample_count': len(self.valid_sample_mask),
            'valid_fraction': float(np.mean(self.valid_sample_mask)),
            'unresolved_sample_indices': np.flatnonzero(~self.valid_sample_mask).tolist(),
            'zero_cross_state_available': self.zero_cross_state_available,
            'limitations': list(self.limitations),
        }


def analyze_capacitor_ripple(line: PFCLineCycleResult) -> PFCCapacitorRipple:
    """Integrate available local CCM PWM variance with the source AC weights."""
    # Reuse the accepted-cycle identity/period/finiteness gate of every other
    # downstream consumer. Local import avoids making either model own the other.
    from .device_loss import _closed_loop_trace
    _closed_loop_trace(line)
    mean = np.asarray(line.il_avg_a, dtype=float)
    ripple = np.asarray(line.il_ripple_pp_a, dtype=float)
    duty = np.asarray(line.duty, dtype=float)
    if np.any(mean < 0) or np.any(ripple < 0):
        raise ValueError('capacitor reconstruction requires nonnegative rectified current and ripple')
    valid = mean >= 0.5 * ripple
    # A nonzero triangle needs both on and off intervals. Do not repair an
    # inconsistent caller-supplied endpoint by silently clamping its waveform.
    valid &= ((duty > 0) & (duty < 1)) | (ripple == 0)
    signals = getattr(line.waveforms, 'signals', {})
    state_available = 'zero_cross_active' in signals
    if state_available:
        state = np.asarray(signals['zero_cross_active'][line.settled_slice], dtype=float)
        if (state.shape != mean.shape or not np.all(np.isfinite(state))
                or np.any((state < 0) | (state > 1))):
            raise ValueError('invalid same-cycle zero-crossing state samples')
        valid &= state <= 0.5
    # Zero at unknown slices means "no available contribution", never physical
    # zero variance. The explicit mask/null total is mandatory in each consumer.
    try:
        with np.errstate(over='raise', invalid='raise'):
            pulsation = np.where(valid, duty * (1 - duty) * mean**2, 0.0)
            triangular = np.where(valid, (1 - duty) * ripple**2 / 12, 0.0)
            averaged_square = float(np.mean(line.capacitor_current_a**2))
            known_square = averaged_square + float(np.mean(pulsation + triangular))
    except FloatingPointError as exc:
        raise ValueError('capacitor second moments exceed the finite numeric range') from exc
    if not np.isfinite(known_square):
        raise ValueError('capacitor second moments exceed the finite numeric range')
    return PFCCapacitorRipple(
        averaged_current_rms_a=float(np.sqrt(averaged_square)),
        pwm_pulsation_variance_a2=pulsation,
        inductor_ripple_variance_a2=triangular,
        valid_sample_mask=valid,
        known_model_current_rms_a=float(np.sqrt(known_square)),
        zero_cross_state_available=state_available,
    )
