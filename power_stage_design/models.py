"""SI-unit inputs and immutable result identity for power-stage design."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .psfb_dynamics import PSFBOptions
    from .qr_dynamics import QROptions
    from .two_switch_dynamics import T05Options

from .waveforms import Waveform

MODEL_VERSION = "psd-piecewise-2"


class ModelDomainError(ValueError):
    """Inputs or operating mode lie outside the implemented physical model."""


class UnsupportedTopologyError(ModelDomainError):
    """No defensible numerical model is connected for the selected topology."""


@dataclass(frozen=True)
class DesignInputs:
    topology: str
    vin_v: float
    vout_v: float
    iout_a: float
    fsw_hz: float
    inductance_h: float
    capacitance_f: float = 100e-6
    turns_ratio_np_ns: float = 1.0
    diode_drop_v: float = 0.0
    mode: str = "auto"
    secondary_inductance_h: float | None = None
    transfer_capacitance_f: float | None = None
    magnetizing_inductance_h: float | None = None
    reset_turns_ratio_np_nr: float = 1.0
    resonant_capacitance_f: float | None = None
    dead_time_s: float = 0.0
    leakage_inductance_h: float | None = None
    primary_capacitance_f: float | None = None
    primary_output_current_a: float | None = None

    two_switch_options: T05Options | None = None
    qr_options: QROptions | None = None
    psfb_options: PSFBOptions | None = None

    def __post_init__(self):
        for name, module, typename in (("two_switch_options", ".two_switch_dynamics", "T05Options"),
                                       ("qr_options", ".qr_dynamics", "QROptions"),
                                       ("psfb_options", ".psfb_dynamics", "PSFBOptions")):
            value = getattr(self, name)
            if value is None:
                continue
            from importlib import import_module
            option_type = getattr(import_module(module, __package__), typename)
            if type(value) is dict:
                try:
                    value = option_type(**value)
                except TypeError as exc:
                    raise ModelDomainError(f"Invalid {name} fields: {exc}") from exc
                object.__setattr__(self, name, value)
            if type(value) is not option_type:
                raise ModelDomainError(f"{name} requires exactly {typename} or its JSON fields")
        if sum(value is not None for value in (self.two_switch_options, self.qr_options, self.psfb_options)) > 1:
            raise ModelDomainError("Only one topology-specific advanced model can be selected")

    @property
    def design_id(self) -> str:
        values = asdict(self)
        # Preserve all legacy identities when no optional physics is selected.
        for key in ("two_switch_options", "qr_options", "psfb_options"):
            if values[key] is None:
                del values[key]
        payload = {"model_version": MODEL_VERSION, "inputs": values}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()[:20]


@dataclass(frozen=True)
class DeviceWaveforms:
    label: str
    current_a: Waveform
    voltage_v: Waveform


@dataclass(frozen=True)
class StageResult:
    inputs: DesignInputs
    design_id: str
    topology_id: str
    mode: str
    duty: float
    waveforms: dict[str, DeviceWaveforms]
    stress: dict[str, dict[str, float]]
    metrics: dict[str, float]
    assumptions: tuple[str, ...]
    warnings: tuple[str, ...]
    diagnostics: dict[str, float]
    provenance: dict[str, str]
    model_version: str = MODEL_VERSION

    def to_dict(self) -> dict:
        return asdict(self)
