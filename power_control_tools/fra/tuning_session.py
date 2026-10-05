"""Measured-data PI experiments. Predictions are local FRD hypotheses, never hardware approval."""
from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .analysis import analyze_loop_response, digital_frequency_response
from .models import FRAMeasurement, FRASourceFormat
from power_control_tools.models import DigitalTransferFunction


@dataclass(frozen=True)
class PIParameters:
    kp: float
    ti_s: float
    sample_period_s: float
    method: str = "tustin"
    input_unit: str = "V"
    output_unit: str = "duty"

    def __post_init__(self):
        if any(type(v) not in (float, int) or not math.isfinite(v) or v <= 0 for v in (self.kp, self.ti_s, self.sample_period_s)):
            raise ValueError("Kp, Ti and Ts must be finite and positive")
        if self.method not in ("tustin", "backward_euler"):
            raise ValueError("V1 supports Tustin or backward-Euler PI only")
        if not isinstance(self.input_unit, str) or not isinstance(self.output_unit, str) or not self.input_unit.strip() or not self.output_unit.strip():
            raise ValueError("Controller input/output units are required")
        if not math.isfinite(self.ki) or not math.isfinite(1 / self.sample_period_s) or not all(math.isfinite(v) for v in self.digital().b):
            raise ValueError("PI coefficients/sample rate overflow")

    @property
    def ki(self):
        return self.kp / self.ti_s

    def digital(self):
        k = self.ki * self.sample_period_s
        b = (self.kp + k / 2, -self.kp + k / 2) if self.method == "tustin" else (self.kp + k, -self.kp)
        return DigitalTransferFunction(b, (1.0, -1.0), 1 / self.sample_period_s, "PI")


@dataclass(frozen=True)
class OperatingPoint:
    vin_v: float
    load_w: float
    mode: str
    label: str

    def __post_init__(self):
        if any(type(v) not in (int, float) for v in (self.vin_v, self.load_w)):
            raise ValueError("Vin and load must be numeric")
        if not math.isfinite(self.vin_v) or self.vin_v <= 0 or not math.isfinite(self.load_w) or self.load_w < 0:
            raise ValueError("Operating point requires positive Vin and nonnegative load")
        if not isinstance(self.mode, str) or not isinstance(self.label, str) or not self.mode.strip() or not self.label.strip():
            raise ValueError("Mode and operating-point group label are required")


@dataclass
class Scan:
    name: str
    measurement: FRAMeasurement
    controller: PIParameters
    operating_point: OperatingPoint
    measurement_type: str = "unknown"
    injection_convention: str = "unknown"
    phase_offset_deg: float = 0.0
    excluded_indices: list[int] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip() or type(self.phase_offset_deg) not in (int, float) or not math.isfinite(self.phase_offset_deg):
            raise ValueError("Scan name and finite phase offset are required")
        if not isinstance(self.excluded_indices, list) or not isinstance(self.quality_flags, list) or any(not isinstance(v, str) for v in self.quality_flags):
            raise ValueError("Exclusions and quality flags must be lists")
        if not isinstance(self.measurement_type, str) or not isinstance(self.injection_convention, str):
            raise ValueError("Measurement semantics must be strings")
        if any(type(i) is not int or i < 0 or i >= len(self.measurement.frequency_hz) for i in self.excluded_indices):
            raise ValueError("Excluded point index is outside the scan")
        if not np.all(np.isfinite(self.response())) or np.any(np.abs(self.response()) == 0):
            raise ValueError("Magnitude range overflows or underflows complex response")

    @property
    def group_key(self):
        p, c = self.operating_point, self.controller
        return (p.vin_v, p.load_w, p.mode, p.label, c.sample_period_s, c.method,
                c.input_unit, c.output_unit, self.measurement_type, self.injection_convention)

    def response(self):
        with np.errstate(over="ignore", invalid="ignore"):
            return self.measurement.complex_response(self.phase_offset_deg)

    def usable_mask(self):
        f = self.measurement.frequency_hz
        mask = f < 0.5 / self.controller.sample_period_s
        mask[self.excluded_indices] = False
        return mask

    def prediction_blocker(self):
        if self.measurement_type != "signed_loop_gain" or self.injection_convention != "negative_feedback_L":
            return "Prediction needs confirmed signed loop gain L with characteristic equation 1+L=0"
        if self.quality_flags:
            return "Resolve scan quality flags before prediction: " + "; ".join(self.quality_flags)
        if np.count_nonzero(self.usable_mask()) < 3:
            return "At least three usable below-Nyquist points are required"
        return None


def import_csv(path, *, columns=(0, 1, 2), frequency_unit="Hz", magnitude_unit="dB", phase_unit="deg", delimiter=",", skip_rows=1):
    """Explicit mapping; reject malformed rows/duplicates instead of silently deleting evidence."""
    if frequency_unit not in ("Hz", "kHz", "rad/s") or magnitude_unit not in ("dB", "linear") or phase_unit not in ("deg", "rad"):
        raise ValueError("Unsupported units; choose Hz/kHz/rad/s, dB/linear and deg/rad")
    if len(columns) != 3 or len(set(columns)) != 3 or any(type(i) is not int or i < 0 for i in columns):
        raise ValueError("Choose three different zero-based column indices")
    if type(skip_rows) is not int or skip_rows < 0:
        raise ValueError("Skipped header row count must be nonnegative")
    raw_text = Path(path).read_text(encoding="utf-8-sig")
    rows = list(csv.reader(raw_text.splitlines(), delimiter=delimiter))
    raw = []
    for index, row in enumerate(rows[skip_rows:], skip_rows + 1):
        if not row or all(not v.strip() for v in row):
            continue
        try:
            values = [float(row[i]) for i in columns]
        except (IndexError, ValueError) as exc:
            raise ValueError(f"Invalid numeric data at CSV row {index}") from exc
        if not all(math.isfinite(v) for v in values):
            raise ValueError(f"NaN/Inf at CSV row {index}")
        raw.append(values)
    if len(raw) < 3:
        raise ValueError("At least three complete data rows are required")
    data = np.array(raw)
    f, mag, phase = data.T.copy()
    f *= {"Hz": 1, "kHz": 1000, "rad/s": 1 / (2 * np.pi)}[frequency_unit]
    if np.any(f <= 0) or len(np.unique(f)) != len(f):
        raise ValueError("Frequencies must be positive and unique; duplicate rows need explicit correction")
    if magnitude_unit == "linear":
        if np.any(mag <= 0):
            raise ValueError("Linear magnitude must be positive")
        mag = 20 * np.log10(mag)
    if phase_unit == "rad":
        phase = np.rad2deg(phase)
    order = np.argsort(f, kind="stable")
    return FRAMeasurement(f[order], mag[order], phase[order], FRASourceFormat.GENERIC, str(path), metadata={
        "raw_text": raw_text, "raw_rows": raw, "columns": list(columns), "frequency_unit": frequency_unit,
        "magnitude_unit": magnitude_unit, "phase_unit": phase_unit, "sorted": bool(np.any(order != np.arange(len(order)))),
        "delimiter": delimiter, "skip_rows": skip_rows,
    })


@dataclass(frozen=True)
class Targets:
    crossover_hz: float = 1000.0
    phase_margin_deg: float = 50.0
    gain_margin_db: float = 6.0

    def __post_init__(self):
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError("Targets must be finite and positive")


def metrics(scan, response=None, targets=None):
    if scan.prediction_blocker():
        return None
    mask = scan.usable_mask()
    # Never bridge gaps marked unusable: omit margin inference for the whole scan.
    if not np.all(mask):
        return None
    kwargs = {} if targets is None else {"pm_pass_deg": targets.phase_margin_deg, "gm_pass_db": targets.gain_margin_db}
    return analyze_loop_response(scan.measurement.frequency_hz, scan.response() if response is None else response, **kwargs)


def score(result, targets):
    if result is None or result.main_crossover_hz is None or result.worst_phase_margin_deg is None:
        return math.inf
    # Unobserved GM remains unknown and cannot be certified by this ranking.
    gm_penalty = 1.0 if result.worst_gain_margin_db is None else max(0, targets.gain_margin_db - result.worst_gain_margin_db) / 6
    return abs(math.log(result.main_crossover_hz / targets.crossover_hz)) + max(0, targets.phase_margin_deg - result.worst_phase_margin_deg) / 30 + gm_penalty


def predict(scan, candidate):
    blocker = scan.prediction_blocker()
    if blocker:
        raise ValueError(blocker)
    c = scan.controller
    if (c.sample_period_s, c.method, c.input_unit, c.output_unit) != (candidate.sample_period_s, candidate.method, candidate.input_unit, candidate.output_unit):
        raise ValueError("Prediction cannot change sample period, PI discretization or controller units")
    f = scan.measurement.frequency_hz
    mask = scan.usable_mask()
    old = digital_frequency_response(c.digital(), f[mask])
    if np.any(np.abs(old) < 1e-12) or np.max(np.abs(old)) / np.min(np.abs(old)) > 1e12:
        raise ValueError("Controller division is zero or ill-conditioned")
    response = np.full(len(f), np.nan + 1j * np.nan)
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        response[mask] = scan.response()[mask] / old * digital_frequency_response(candidate.digital(), f[mask])
    if not np.all(np.isfinite(response[mask])) or np.any(np.abs(response[mask]) == 0):
        raise ValueError("Predicted response overflows or underflows")
    return response


@dataclass
class Prediction:
    controller: PIParameters
    response: np.ndarray
    result: object
    score: float
    reason: str


def suggestions(scan, targets):
    if scan.prediction_blocker():
        return []
    out = []
    for kp_factor in (0.8, 1.0, 1.25):
        for ti_factor in (0.8, 1.0, 1.25):
            if kp_factor == ti_factor == 1:
                continue
            try:
                p = PIParameters(**{**asdict(scan.controller), "kp": scan.controller.kp * kp_factor, "ti_s": scan.controller.ti_s * ti_factor})
                h = predict(scan, p)
            except ValueError:
                continue
            m = metrics(scan, h, targets)
            s = score(m, targets)
            if not math.isfinite(s):
                continue
            out.append(Prediction(p, h, m, s,
                f"Local FRD hypothesis: Kp ×{kp_factor:g}, Ti ×{ti_factor:g}; Ki={p.ki:.6g}. "
                "Kp scales loop gain; Ti changes the PI zero and low-frequency gain. "
                "Bandwidth/noise and margin tradeoffs require a new scan. Low confidence; not hardware-safe bounds."))
    return sorted(out, key=lambda p: p.score)


@dataclass
class Session:
    scans: list[Scan] = field(default_factory=list)
    targets: Targets = field(default_factory=Targets)
    current_index: int = -1

    def add(self, scan):
        self.scans.append(scan)
        self.current_index = len(self.scans) - 1

    @property
    def current(self):
        return self.scans[self.current_index] if self.scans else None

    def group(self):
        return [s for s in self.scans if s.group_key == self.current.group_key] if self.current else []

    def best(self):
        valid = [s for s in self.group() if math.isfinite(score(metrics(s), self.targets))]
        return min(valid, key=lambda s: score(metrics(s), self.targets)) if valid else None

    def mismatch(self):
        group = [s for s in self.scans[:self.current_index + 1] if s.group_key == self.current.group_key] if self.current else []
        if len(group) < 2:
            return None
        old, new = group[-2:]
        if old.prediction_blocker() or new.prediction_blocker() or not np.all(old.usable_mask()) or not np.all(new.usable_mask()):
            return {"status": "unavailable", "reason": "Unresolved measurement semantics/quality"}
        try:
            expected = predict(old, new.controller)
        except ValueError as exc:
            return {"status": "unavailable", "reason": str(exc)}
        f, nf = old.measurement.frequency_hz, new.measurement.frequency_hz
        mask = (nf >= f[0]) & (nf <= f[-1])
        if np.count_nonzero(mask) < 3:
            return {"status": "unavailable", "reason": "Fewer than three overlapping measured frequencies"}
        x = np.log(nf[mask])
        emag = np.interp(x, np.log(f), 20 * np.log10(np.abs(expected)))
        ephase = np.interp(x, np.log(f), np.unwrap(np.angle(expected)))
        observed = new.response()[mask]
        mag_error = float(np.max(np.abs(20 * np.log10(np.abs(observed)) - emag)))
        phase_error = float(np.max(np.abs(np.angle(observed * np.exp(-1j * ephase), deg=True))))
        return {"status": "mismatch" if mag_error > 3 or phase_error > 15 else "consistent_in_band", "magnitude_error_db_max": mag_error, "phase_error_deg_max": phase_error,
                "reason": "Review operating point, injection convention, noise and nonlinearity; thresholds are diagnostic (3 dB / 15°), not hardware limits"}

    def to_json(self):
        scans = []
        for s in self.scans:
            m = s.measurement
            scans.append({"name": s.name, "controller": asdict(s.controller), "coefficients": {"b": s.controller.digital().b, "a": s.controller.digital().a},
                          "operating_point": asdict(s.operating_point), "measurement_type": s.measurement_type, "injection_convention": s.injection_convention,
                          "phase_offset_deg": s.phase_offset_deg, "excluded_indices": s.excluded_indices, "quality_flags": s.quality_flags,
                          "measurement": {"frequency_hz": m.frequency_hz.tolist(), "magnitude_db": m.magnitude_db.tolist(), "raw_phase_deg": m.raw_phase_deg.tolist(),
                                          "source_path": m.source_path, "source_format": m.source_format.value, "default_phase_offset_deg": m.default_phase_offset_deg, "metadata": m.metadata}})
        return json.dumps({"schema": "fra-pi-session-v1", "targets": asdict(self.targets), "current_index": self.current_index, "scans": scans}, indent=2, allow_nan=False)

    @classmethod
    def from_json(cls, text):
        def reject_constant(value):
            raise ValueError(f"Non-finite JSON value: {value}")
        try:
            data = json.loads(text, parse_constant=reject_constant)
            json.dumps(data, allow_nan=False)  # Also reject overflowed exponents inside metadata.
            return cls._from_data(data)
        except (KeyError, TypeError, AttributeError, OverflowError) as exc:
            raise ValueError(f"Malformed FRA session: {exc}") from exc

    @classmethod
    def _from_data(cls, data):
        def require(value, fields):
            if not isinstance(value, dict) or set(value) != set(fields):
                raise ValueError("Session object has missing or unsupported fields")
        require(data, ("schema", "targets", "current_index", "scans"))
        if data["schema"] != "fra-pi-session-v1":
            raise ValueError("Unsupported FRA session schema")
        require(data["targets"], ("crossover_hz", "phase_margin_deg", "gain_margin_db"))
        if not isinstance(data["scans"], list):
            raise ValueError("Session scans must be a list")
        result = cls(targets=Targets(**data["targets"]))
        for original in data["scans"]:
            require(original, ("name", "controller", "coefficients", "operating_point", "measurement_type", "injection_convention", "phase_offset_deg", "excluded_indices", "quality_flags", "measurement"))
            s = dict(original)
            require(s["controller"], ("kp", "ti_s", "sample_period_s", "method", "input_unit", "output_unit"))
            require(s["operating_point"], ("vin_v", "load_w", "mode", "label"))
            require(s["measurement"], ("frequency_hz", "magnitude_db", "raw_phase_deg", "source_path", "source_format", "default_phase_offset_deg", "metadata"))
            supplied = s.pop("coefficients")
            require(supplied, ("b", "a"))
            c = PIParameters(**s.pop("controller"))
            if any(not isinstance(supplied[k], list) or len(supplied[k]) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in supplied[k]) for k in ("b", "a")):
                raise ValueError("Stored coefficients must be finite two-element lists")
            if not np.allclose(supplied["b"], c.digital().b, rtol=1e-12, atol=0) or supplied["a"] != list(c.digital().a):
                raise ValueError("Stored coefficients disagree with PI parameters")
            m = dict(s["measurement"])
            m["source_format"] = FRASourceFormat(m["source_format"])
            if not isinstance(m["metadata"], dict) or not isinstance(m["source_path"], str) or type(m["default_phase_offset_deg"]) not in (int, float) or not math.isfinite(m["default_phase_offset_deg"]):
                raise ValueError("Invalid measurement metadata or phase convention")
            for key in ("frequency_hz", "magnitude_db", "raw_phase_deg"):
                if not isinstance(m[key], list) or any(type(v) not in (int, float) for v in m[key]):
                    raise ValueError("Measurement arrays must contain numbers")
            result.add(Scan(**{**s, "controller": c, "operating_point": OperatingPoint(**s["operating_point"]),
                              "measurement": FRAMeasurement(**m)}))
        index = data["current_index"]
        if type(index) is not int or (result.scans and not 0 <= index < len(result.scans)) or (not result.scans and index != -1):
            raise ValueError("Invalid current scan index")
        result.current_index = index
        return result


def export_scan_csv(scan):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["frequency_Hz", "magnitude_dB", "raw_phase_deg", "excluded"])
    for i, values in enumerate(zip(scan.measurement.frequency_hz, scan.measurement.magnitude_db, scan.measurement.raw_phase_deg)):
        writer.writerow([*values, int(not scan.usable_mask()[i])])
    return out.getvalue()
