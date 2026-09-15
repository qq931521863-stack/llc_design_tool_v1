from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import numpy as np
from numpy.typing import NDArray


class FRASourceFormat(str, Enum):
    BODE100 = "bode100"
    SIMPLIS = "simplis"
    GENERIC = "generic"


class FRAMeasurementKind(str, Enum):
    PLANT = "plant"
    COMPLETE_LOOP = "complete_loop"


@dataclass(frozen=True)
class FRAMeasurement:
    """Measured or simulated frequency-response data.

    ``raw_phase_deg`` is preserved exactly as imported.  ``default_phase_offset_deg``
    describes the importer's recommended convention correction.  Bode100 loop
    injection data commonly uses a phase convention shifted by +180 degrees;
    the Bode100 importer therefore defaults to -180 degrees while still
    exposing the raw phase to the GUI.
    """

    frequency_hz: NDArray[np.float64]
    magnitude_db: NDArray[np.float64]
    raw_phase_deg: NDArray[np.float64]
    source_format: FRASourceFormat
    source_path: str = ""
    default_phase_offset_deg: float = 0.0
    metadata: dict[str, str | float | int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        f = np.asarray(self.frequency_hz, dtype=float).reshape(-1)
        mag = np.asarray(self.magnitude_db, dtype=float).reshape(-1)
        phase = np.asarray(self.raw_phase_deg, dtype=float).reshape(-1)
        if f.size < 2:
            raise ValueError("FRA data requires at least two frequency points")
        if f.size != mag.size or f.size != phase.size:
            raise ValueError("FRA frequency, magnitude and phase lengths must match")
        if not np.all(np.isfinite(f)) or not np.all(np.isfinite(mag)) or not np.all(np.isfinite(phase)):
            raise ValueError("FRA data contains NaN or infinite values")
        if np.any(f <= 0.0):
            raise ValueError("FRA frequencies must be positive")
        if np.any(np.diff(f) <= 0.0):
            raise ValueError("FRA frequencies must be strictly increasing")
        object.__setattr__(self, "frequency_hz", f)
        object.__setattr__(self, "magnitude_db", mag)
        object.__setattr__(self, "raw_phase_deg", phase)

    @property
    def file_name(self) -> str:
        return Path(self.source_path).name if self.source_path else ""

    def normalized_phase_deg(self, phase_offset_deg: float | None = None) -> NDArray[np.float64]:
        offset = self.default_phase_offset_deg if phase_offset_deg is None else float(phase_offset_deg)
        phase_rad = np.deg2rad(self.raw_phase_deg + offset)
        return np.unwrap(phase_rad) * 180.0 / np.pi

    def complex_response(self, phase_offset_deg: float | None = None) -> NDArray[np.complex128]:
        phase = np.deg2rad(self.normalized_phase_deg(phase_offset_deg))
        linear = np.power(10.0, self.magnitude_db / 20.0)
        return linear.astype(complex) * np.exp(1j * phase)

    def with_default_phase_offset(self, offset_deg: float) -> "FRAMeasurement":
        return FRAMeasurement(
            self.frequency_hz.copy(),
            self.magnitude_db.copy(),
            self.raw_phase_deg.copy(),
            self.source_format,
            self.source_path,
            float(offset_deg),
            dict(self.metadata),
        )
