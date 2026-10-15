"""RMS phasors of one uniformly sampled, endpoint-exclusive period."""
import numbers

import numpy as np


def rms_phasors(waveform, max_harmonic: int) -> tuple[complex, ...]:
    if not isinstance(max_harmonic, numbers.Integral) or max_harmonic < 0:
        raise ValueError('max_harmonic must be a nonnegative integer')
    values = np.asarray(waveform, dtype=float)
    if values.ndim != 1 or len(values) < 32 or not np.isfinite(values).all():
        raise ValueError('waveform must contain at least 32 finite samples')
    coeff = np.fft.rfft(values) / len(values)
    coeff[1:] *= np.sqrt(2.0)
    # A real even-length Nyquist bin is unpaired: its RMS equals its amplitude.
    if len(values) % 2 == 0:
        coeff[-1] /= np.sqrt(2.0)
    out = np.zeros(max_harmonic + 1, dtype=complex)
    n = min(len(out), len(coeff))
    out[:n] = coeff[:n]
    return tuple(out)
