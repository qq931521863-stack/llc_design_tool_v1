"""Optional 3C90 core-loss surrogate, enabled only by an independent-test gate.

Predicts Pv in W/m3; never winding loss or saturation. Training requires the
research extra; inference uses NumPy/SciPy only. No extrapolation or cross-grade
fallback is permitted. Data source is the pinned MagNet-derived reference set.
"""

import json
from pathlib import Path

import numpy as np
from scipy.special import gammaln, logsumexp


def waveform_features(waveforms, frequency_hz, temperature_c):
    b = np.atleast_2d(np.asarray(waveforms, dtype=float))
    f = np.broadcast_to(np.asarray(frequency_hz, dtype=float), (len(b),))
    t = np.broadcast_to(np.asarray(temperature_c, dtype=float), (len(b),))
    if (
        b.shape[1] < 32
        or not np.isfinite(b).all()
        or not np.isfinite(f).all()
        or not np.isfinite(t).all()
        or np.any(f <= 0)
    ):
        raise ValueError(
            "Finite periodic B waveforms, positive Hz and finite Celsius required"
        )
    peak = np.max(abs(b), axis=1)
    delta = np.ptp(b, axis=1)
    if np.any(delta <= 0):
        raise ValueError("Nonconstant B waveforms required")
    spectrum = 2 * abs(np.fft.rfft(b, axis=1)) / b.shape[1]
    db = abs(np.roll(b, -1, axis=1) - b) * b.shape[1]
    # Retain low-order harmonic magnitudes plus phase-sensitive shape features.
    features = np.c_[
        spectrum[:, 1:14],
        np.log(f),
        t / 100,
        np.log(delta),
        np.sqrt(np.mean(db**2, axis=1)) / delta,
        peak / delta,
    ]
    lograte = np.log(np.maximum(db * f[:, None], 1e-30))
    return features, lograte, np.log(delta)


def igse_log_prediction(coefficients, lograte, logdelta, temperature_c):
    logk, alpha, beta, t1, t2 = coefficients
    temp = (np.asarray(temperature_c) - 60) / 50
    logcos = (
        np.log(2 * np.sqrt(np.pi)) + gammaln((alpha + 1) / 2) - gammaln((alpha + 2) / 2)
    )
    logden = (alpha - 1) * np.log(2 * np.pi) + (beta - alpha) * np.log(2) + logcos
    return (
        logk
        - logden
        + logsumexp(alpha * lograte, axis=1)
        - np.log(lograte.shape[1])
        + (beta - alpha) * logdelta
        + t1 * temp
        + t2 * temp**2
    )


class ValidatedMagneticSurrogate:
    def __init__(self, path=None):
        path = (
            Path(path)
            if path
            else Path(__file__).parent.parent / "data" / "magnetic_surrogate_3c90.json"
        )
        self.data = json.loads(path.read_text(encoding="utf-8"))
        if not self.data.get("validation_gate_passed"):
            raise ValueError("Surrogate has not passed the independent-test gate")

    def predict(self, waveforms, frequency_hz, temperature_c, *, material_grade):
        if material_grade.upper() != self.data["material_grade"]:
            raise ValueError("Surrogate is restricted to its validated material grade")
        b = np.atleast_2d(np.asarray(waveforms, dtype=float))
        if b.ndim != 2 or b.shape[1] != 1024 or not np.isfinite(b).all():
            raise ValueError(
                "Surrogate requires 1024 finite, equally spaced samples per period"
            )
        if np.any(abs(np.mean(b, axis=1)) > 0.01 * np.ptp(b, axis=1)):
            raise ValueError("DC-biased flux is outside the validated surrogate scope")
        f = np.broadcast_to(np.asarray(frequency_hz), (len(b),))
        t = np.broadcast_to(np.asarray(temperature_c), (len(b),))
        for key, values in [
            ("frequency_hz", f),
            ("temperature_c", t),
            ("b_peak_t", np.max(abs(b), axis=1)),
        ]:
            lo, hi = self.data["domain"][key]
            if (
                not np.isfinite(values).all()
                or np.any(values < lo)
                or np.any(values > hi)
            ):
                raise ValueError(f"{key} is outside the validated data envelope")
        features, rate, delta = waveform_features(b, f, t)
        baseline = igse_log_prediction(self.data["igse_coefficients"], rate, delta, t)
        x = (features - np.asarray(self.data["input_mean"])) / np.asarray(
            self.data["input_scale"]
        )
        for i, (weights, bias) in enumerate(
            zip(self.data["weights"], self.data["biases"])
        ):
            x = x @ np.asarray(weights) + np.asarray(bias)
            if i < len(self.data["weights"]) - 1:
                x = np.tanh(x)
        pv = np.exp(baseline + x.ravel())
        if not np.isfinite(pv).all():
            raise ValueError("Nonfinite surrogate result")
        return pv
