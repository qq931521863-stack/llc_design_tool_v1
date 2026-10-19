"""Exact polynomial-plus-harmonic waveforms shared by PSD topology models.

Each segment uses its own local coordinate u=(t/T-start)/(end-start).
Optional harmonic=(cosine amplitude, sine amplitude, angular span) represents
resonant intervals without triangle approximations. Integrals, extrema and
displayed samples consume these same coefficients.
"""
from __future__ import annotations

import cmath
import itertools
import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class WaveformSegment:
    start_fraction: float
    end_fraction: float
    coefficients: tuple[float, ...]
    harmonic: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        if not (0 <= self.start_fraction < self.end_fraction <= 1):
            raise ValueError("waveform segment must have 0 <= start < end <= 1")
        if not self.coefficients or not all(math.isfinite(v) for v in self.coefficients):
            raise ValueError("finite polynomial coefficients are required")
        if len(self.harmonic) != 3 or not all(math.isfinite(v) for v in self.harmonic):
            raise ValueError("harmonic must contain finite cosine/sine amplitudes and angular span")
        if self.harmonic[2] < 0 or (self.harmonic[2] == 0 and any(self.harmonic[:2])):
            raise ValueError("a nonzero harmonic needs positive angular span")

    @property
    def duration_fraction(self) -> float:
        return self.end_fraction - self.start_fraction

    def evaluate(self, u):
        a,b,w = self.harmonic
        return np.polynomial.polynomial.polyval(u,self.coefficients) + (a*np.cos(w*np.asarray(u))+b*np.sin(w*np.asarray(u)) if w else 0)

    def integral(self, square: bool = False) -> float:
        coefficients = self.coefficients
        a,b,w = self.harmonic
        if not square:
            value = sum(c/(k+1) for k,c in enumerate(coefficients))
            if w:
                value += (a*math.sin(w)+b*(1-math.cos(w)))/w
        else:
            squared = np.polynomial.polynomial.polymul(coefficients,coefficients)
            value = sum(c/(k+1) for k,c in enumerate(squared))
            if w:
                exponential = cmath.exp(1j*w)
                moment = (exponential-1)/(1j*w)
                for k,c in enumerate(coefficients):
                    if k:
                        moment = exponential/(1j*w)-k*moment/(1j*w)
                    value += 2*c*(a*moment.real+b*moment.imag)
                value += a*a*(.5+math.sin(2*w)/(4*w))+b*b*(.5-math.sin(2*w)/(4*w))+a*b*math.sin(w)**2/w
        return self.duration_fraction*value

    def extrema(self) -> tuple[float, float]:
        points = [0.0,1.0]
        derivative = np.polynomial.polynomial.polyder(self.coefficients)
        a,b,w=self.harmonic
        if w:
            from scipy.optimize import brentq
            def slope(u):
                return np.polynomial.polynomial.polyval(u,derivative)-a*w*math.sin(w*u)+b*w*math.cos(w*u)
            grid=np.linspace(0,1,max(65,int(w*16)+1))
            previous=slope(0)
            for start,end in itertools.pairwise(grid):
                value=slope(end)
                if value == 0:
                    points.append(float(end))
                elif previous*value < 0:
                    points.append(brentq(slope,start,end,xtol=1e-14))
                previous=value
        elif len(derivative)>1:
            for root in np.polynomial.polynomial.polyroots(derivative):
                if abs(root.imag)<1e-12 and 0<root.real<1:
                    points.append(float(root.real))
        values=self.evaluate(points)
        return float(np.min(values)),float(np.max(values))


@dataclass(frozen=True)
class Waveform:
    period_s: float
    unit: str
    reference: str
    segments: tuple[WaveformSegment, ...]

    def __post_init__(self) -> None:
        if not math.isfinite(self.period_s) or self.period_s <= 0 or not self.segments:
            raise ValueError("waveform requires a finite positive period and segments")
        cursor = 0.0
        for segment in self.segments:
            if abs(segment.start_fraction - cursor) > 1e-12:
                raise ValueError("waveform segments must cover one period without gaps")
            cursor = segment.end_fraction
        if abs(cursor - 1.0) > 1e-12:
            raise ValueError("waveform segments must finish at one full period")

    def statistics(self) -> dict[str, float]:
        bounds = [segment.extrema() for segment in self.segments]
        low = min(value[0] for value in bounds)
        high = max(value[1] for value in bounds)
        mean = math.fsum(segment.integral() for segment in self.segments)
        # Integrate the centered waveform rather than subtracting two large
        # squares: RMS² - mean² loses a small ripple on a large DC bias.
        centered_square = math.fsum(
            WaveformSegment(
                segment.start_fraction, segment.end_fraction,
                (segment.coefficients[0] - mean, *segment.coefficients[1:]),
                segment.harmonic,
            ).integral(square=True)
            for segment in self.segments
        )
        return {
            "min": low,
            "max": high,
            "mean": mean,
            "rms": math.sqrt(max(0.0, sum(segment.integral(square=True) for segment in self.segments))),
            "ac_rms": math.sqrt(max(0.0, centered_square)),
            "peak_to_peak": high - low,
        }

    def sample(self, count: int = 1000, periods: int = 1) -> tuple[np.ndarray, np.ndarray]:
        """Uniform samples; duplicate edge points preserve switch discontinuities."""
        if count < 2 or periods < 1 or not isinstance(periods, int):
            raise ValueError("count >= 2 and integer periods >= 1 are required")
        times, values = [], []
        for period in range(periods):
            for segment in self.segments:
                n = max(2, math.ceil(count * segment.duration_fraction))
                u = np.linspace(0.0, 1.0, n)
                times.extend((period + segment.start_fraction + u * segment.duration_fraction) * self.period_s)
                values.extend(segment.evaluate(u))
        return np.asarray(times), np.asarray(values)

    def affine(self, scale: float = 1.0, offset: float = 0.0, *, unit: str | None = None,
               reference: str | None = None) -> Waveform:
        segments = []
        for segment in self.segments:
            coefficients = [scale * c for c in segment.coefficients]
            coefficients[0] += offset
            segments.append(WaveformSegment(segment.start_fraction, segment.end_fraction, tuple(coefficients), (scale*segment.harmonic[0], scale*segment.harmonic[1], segment.harmonic[2])))
        return Waveform(self.period_s, unit or self.unit, reference or self.reference, tuple(segments))


def piecewise(period_s: float, durations: tuple[float, ...], endpoints: tuple[tuple[float, float], ...],
              unit: str, reference: str) -> Waveform:
    """Construct linear segments, retaining discontinuities between intervals."""
    if len(durations) != len(endpoints) or abs(sum(durations) - 1) > 1e-10:
        raise ValueError("interval durations must span exactly one period")
    segments = []
    cursor = 0.0
    for duration, (start, end) in zip(durations, endpoints):
        if duration < -1e-12:
            raise ValueError("interval duration cannot be negative")
        if duration > 0:
            stop = min(1.0, cursor + duration)
            if stop > cursor:
                segments.append(WaveformSegment(cursor, stop, (float(start), float(end - start))))
                cursor = stop
    return Waveform(period_s, unit, reference, tuple(segments))


def capacitor_voltage(current: Waveform, capacitance_f: float, mean_voltage_v: float,
                      reference: str = "positive capacitor terminal to return") -> Waveform:
    """Integrate i/C exactly, then impose the specified DC voltage.

    This is a first-order ripple reconstruction; current slopes are solved at
    the specified DC voltage rather than feeding ripple back into the model.
    """
    if not math.isfinite(capacitance_f) or capacitance_f <= 0:
        raise ValueError("capacitance must be finite and positive")
    scale = max(1.0, current.statistics()["rms"])
    if abs(current.statistics()["mean"]) > 1e-9 * scale:
        raise ValueError("nonzero mean capacitor current: no periodic voltage solution")
    segments = []
    value = 0.0
    for segment in current.segments:
        factor = current.period_s * segment.duration_fraction / capacitance_f
        a,b,w=segment.harmonic
        coefficients = (value+(factor*b/w if w else 0),) + tuple(factor*c/(k+1) for k,c in enumerate(segment.coefficients))
        harmonic=(-factor*b/w,factor*a/w,w) if w else (0.0,0.0,0.0)
        integrated=WaveformSegment(segment.start_fraction,segment.end_fraction,coefficients,harmonic)
        segments.append(integrated)
        value=float(integrated.evaluate(1.0))
    waveform = Waveform(current.period_s, "V", reference, tuple(segments))
    return waveform.affine(offset=mean_voltage_v - waveform.statistics()["mean"])
