"""Exact-input persistence for the existing FRA Loop Designer.

Only inputs are saved. Reopening always re-evaluates the measured/model plant
and the frozen controller; no saved margins, steps or export-valid flag exist.
The PI experiment map keeps its separate, already supported scan schema.
"""
from __future__ import annotations

import json
import math
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from llc_design import __version__
from llc_design.control.design_session import MAX_FILE_BYTES, strict_json_loads
from power_control_tools.models import DigitalTransferFunction

from .fitting import RationalPlantModel
from .models import FRAMeasurement, FRAMeasurementKind, FRASourceFormat

SCHEMA = "power-design-toolkit/fra-exact-loop-v1"
MODEL_REVISION = "exact-hz-causal-zoh-v2"


def _fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("FRA session object has missing or unsupported fields")


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _controller(data):
    if data is None:
        return None
    _fields(data, ("b", "a", "sample_rate_hz", "name", "source"))
    for key in ("b", "a"):
        if not isinstance(data[key], list) or not 1 <= len(data[key]) <= 33 or not all(_number(v) for v in data[key]):
            raise ValueError("Exact H(z) requires 1..33 finite coefficients per polynomial")
    fs = data["sample_rate_hz"]
    # Current desktop sample-rate controls use 0.1 Hz resolution. Reject a
    # file they would silently round rather than changing its H(z) identity.
    if not _number(fs) or not 1000 <= fs <= 1_000_000 or round(fs, 1) != fs:
        raise ValueError("FRA desktop session Fs must be 1000..1000000 Hz at 0.1 Hz resolution")
    if data["a"][0] != 1.0:
        raise ValueError("Stored exact H(z) must be normalized to a0=1")
    if not all(isinstance(data[k], str) for k in ("name", "source")):
        raise ValueError("Controller name/source must be text")
    return DigitalTransferFunction(tuple(data["b"]), tuple(data["a"]), fs, data["name"], data["source"])


@dataclass(frozen=True)
class FRALoopSession:
    controller: DigitalTransferFunction
    old_controller: DigitalTransferFunction | None
    measurement: FRAMeasurement | None
    measurement_kind: FRAMeasurementKind
    phase_offset_deg: float
    plant_source: str
    identified_plant: RationalPlantModel | None = None
    identified_band_hz: tuple[float, float] | None = None
    identified_confidence: str | None = None
    step_enabled: bool = False
    step_max_order: int = 3
    step_samples: int = 1500
    export_prefix: str = "FRA_CTRL"

    def to_document(self):
        m = self.measurement
        measured = None if m is None else {
            "frequency_hz": m.frequency_hz.tolist(), "magnitude_db": m.magnitude_db.tolist(),
            "raw_phase_deg": m.raw_phase_deg.tolist(), "source_format": m.source_format.value,
            "source_path": m.source_path, "default_phase_offset_deg": m.default_phase_offset_deg,
            "metadata": m.metadata,
        }
        model = None if self.identified_plant is None else {
            "model": asdict(self.identified_plant), "band_hz": self.identified_band_hz,
            "confidence": self.identified_confidence,
        }
        document = {
            "schema": SCHEMA, "model_revision": MODEL_REVISION, "toolkit_version": __version__,
            "controller": asdict(self.controller.normalized()),
            "old_controller": None if self.old_controller is None else asdict(self.old_controller.normalized()),
            "measurement": measured, "measurement_kind": FRAMeasurementKind(self.measurement_kind).value,
            "phase_offset_deg": self.phase_offset_deg, "plant_source": self.plant_source,
            "identified": model,
            "settings": {"step_enabled": self.step_enabled, "step_max_order": self.step_max_order,
                         "step_samples": self.step_samples, "export_prefix": self.export_prefix},
        }
        # The same bounded, duplicate-key/nonfinite rejecting parser as the
        # existing LLC/TTPL sessions is used at both boundaries.
        document = strict_json_loads(json.dumps(document, ensure_ascii=False, allow_nan=False))
        self.from_document(document)
        return document

    @classmethod
    def from_document(cls, data):
        _fields(data, ("schema", "model_revision", "toolkit_version", "controller", "old_controller",
                       "measurement", "measurement_kind", "phase_offset_deg", "plant_source", "identified", "settings"))
        if data["schema"] != SCHEMA or data["model_revision"] != MODEL_REVISION:
            raise ValueError("Unsupported FRA loop session schema/model revision; explicit migration is required")
        if not isinstance(data["toolkit_version"], str):
            raise ValueError("Toolkit version must be text")  # noqa: TRY004 -- malformed persisted data is a ValueError.
        controller, old = _controller(data["controller"]), _controller(data["old_controller"])
        if controller is None:
            raise ValueError("FRA session requires an exact controller")
        source = data["plant_source"]
        if source not in ("measured", "identified"):
            raise ValueError("Unsupported FRA plant source")
        kind = FRAMeasurementKind(data["measurement_kind"])
        offset = data["phase_offset_deg"]
        if not _number(offset) or not -720 <= offset <= 720 or round(offset, 3) != offset:
            raise ValueError("Phase offset exceeds desktop range/resolution")
        measurement = None
        if data["measurement"] is not None:
            m = data["measurement"]
            _fields(m, ("frequency_hz", "magnitude_db", "raw_phase_deg", "source_format", "source_path",
                        "default_phase_offset_deg", "metadata"))
            for key in ("frequency_hz", "magnitude_db", "raw_phase_deg"):
                if not isinstance(m[key], list) or not 2 <= len(m[key]) <= 20_000 or not all(_number(v) for v in m[key]):
                    raise ValueError("FRA measurement requires 2..20000 finite data points")
            if not isinstance(m["source_path"], str) or not isinstance(m["metadata"], dict) or not _number(m["default_phase_offset_deg"]):
                raise ValueError("Invalid FRA measurement provenance")
            m = {**m, "source_format": FRASourceFormat(m["source_format"])}
            measurement = FRAMeasurement(**m)
        model, band, confidence = None, None, None
        if data["identified"] is not None:
            item = data["identified"]
            _fields(item, ("model", "band_hz", "confidence"))
            model_data = item["model"]
            _fields(model_data, ("numerator_coefficients", "real_pole_hz", "complex_pole_pairs", "reference_frequency_hz", "delay_s"))
            for key in ("numerator_coefficients", "real_pole_hz"):
                if not isinstance(model_data[key], list) or len(model_data[key]) > 11 or not all(_number(v) for v in model_data[key]):
                    raise ValueError("Invalid/bounded rational model polynomial")
            if not model_data["numerator_coefficients"] or any(v == 0 for v in model_data["real_pole_hz"]):
                raise ValueError("Empty numerator or zero model pole frequency")
            pairs = model_data["complex_pole_pairs"]
            if not isinstance(pairs, list) or len(pairs) > 5 or any(
                not isinstance(p, list) or len(p) != 2 or not all(_number(v) for v in p) or p[0] <= 0 for p in pairs
            ):
                raise ValueError("Invalid rational complex pole pairs")
            if not _number(model_data["reference_frequency_hz"]) or model_data["reference_frequency_hz"] <= 0 or not _number(model_data["delay_s"]) or model_data["delay_s"] < 0:
                raise ValueError("Invalid model frequency/delay")
            band = item["band_hz"]
            if not isinstance(band, list) or len(band) != 2 or not all(_number(v) for v in band) or not 0 < band[0] < band[1]:
                raise ValueError("Invalid identified-model frequency band")
            confidence = item["confidence"]
            if confidence not in (None, "GOOD", "FAIR", "LOW"):
                raise ValueError("Invalid fit confidence")
            model = RationalPlantModel(**{**model_data, "numerator_coefficients": tuple(model_data["numerator_coefficients"]),
                                          "real_pole_hz": tuple(model_data["real_pole_hz"]),
                                          "complex_pole_pairs": tuple(tuple(p) for p in pairs)})
            band = tuple(band)
        if (source == "measured" and measurement is None) or (source == "identified" and model is None):
            raise ValueError("Active FRA source is missing")
        if source == "measured" and kind == FRAMeasurementKind.COMPLETE_LOOP and old is None:
            raise ValueError("Complete-loop de-embedding requires the exact old controller")
        settings = data["settings"]
        _fields(settings, ("step_enabled", "step_max_order", "step_samples", "export_prefix"))
        if type(settings["step_enabled"]) is not bool or type(settings["step_max_order"]) is not int or not 1 <= settings["step_max_order"] <= 5 or type(settings["step_samples"]) is not int or not 200 <= settings["step_samples"] <= 20_000:
            raise ValueError("Invalid/bounded step settings")
        if not isinstance(settings["export_prefix"], str) or len(settings["export_prefix"]) > 256:
            raise ValueError("Invalid export prefix")
        return cls(controller, old, measurement, kind, offset, source, model, band, confidence, **settings)


def read_loop_session(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    return FRALoopSession.from_document(strict_json_loads(raw))


def write_loop_session(session, path):
    document = session.to_document()
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=f".{output.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output
