"""Independent SI-unit calculators for the Power Stage Designer toolbox.

Equations follow TI SLVUBB4B (February 2023), with explicitly named extensions.
Results carry reproducible inputs, units, assumptions and source references. A
calculation is engineering model evidence, never hardware or TI-executable proof.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import brentq

from llc_design.core.bus_capacitor import required_capacitance_f

from .models import ModelDomainError, StageResult

SOURCE = "https://www.ti.com/lit/ug/slvubb4b/slvubb4b.pdf"
MODEL_VERSION = "psd-toolbox-1"


def _number(name: str, value: float, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int, np.floating, np.integer)) or not math.isfinite(value):
        raise ModelDomainError(f"{name} must be a finite real number")
    if positive and value <= 0 or nonnegative and value < 0:
        raise ModelDomainError(f"{name} must be {'positive' if positive else 'nonnegative'}")
    return float(value)


def _fraction(name: str, value: float, *, allow_zero: bool = False) -> float:
    value = _number(name, value)
    if not ((0 <= value < 1) if allow_zero else (0 < value < 1)):
        raise ModelDomainError(f"{name} must be {'0 <= value' if allow_zero else '0 < value'} < 1 (fraction, not percent)")
    return value


def _finite_tree(value: Any) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _finite_tree(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite_tree(item)
    elif isinstance(value, (float, np.floating)) and not math.isfinite(value):
        raise ModelDomainError("Calculation produced a non-finite value; inputs exceed the model's numerical domain")


@dataclass(frozen=True)
class CalculatorResult:
    tool_id: str
    inputs: dict
    outputs: dict
    units: dict[str, str]
    assumptions: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    source: str = SOURCE
    equations: str = ""
    status: str = "MODELED"
    model_version: str = MODEL_VERSION

    def __post_init__(self):
        _finite_tree(self.inputs)
        _finite_tree(self.outputs)

    @property
    def result_id(self) -> str:
        payload = {"tool": self.tool_id, "model": self.model_version, "inputs": self.inputs}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()[:20]

    def to_dict(self) -> dict:
        return {**asdict(self), "result_id": self.result_id,
                "evidence": "Analytical calculation; no TI executable or hardware validation implied"}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, indent=2) + "\n"

    def to_text(self) -> str:
        return self.to_json()

    def to_csv(self) -> str:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["section", "parameter", "value", "unit"])
        def emit(section, value, path=""):
            if isinstance(value, dict):
                for key, item in value.items():
                    emit(section, item, f"{path}.{key}" if path else key)
            elif isinstance(value, (list, tuple)):
                for index, item in enumerate(value):
                    emit(section, item, f"{path}[{index}]")
            else:
                leaf = path.split(".")[-1].split("[")[0]
                writer.writerow([section, path, "UNKNOWN" if value is None else value, self.units.get(leaf, "")])
        for key, value in self.to_dict().items():
            emit(key, value)
        return stream.getvalue()

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        formats = {".json": self.to_json, ".csv": self.to_csv, ".txt": self.to_text}
        if path.suffix.lower() not in formats:
            raise ValueError("Toolbox export requires .json, .csv or .txt")
        text = formats[path.suffix.lower()]()
        path.write_text(text, encoding="utf-8")
        return path


@dataclass(frozen=True)
class FETOperatingPoint:
    rms_current_a: float
    turn_on_current_a: float
    turn_off_current_a: float
    drain_voltage_v: float
    frequency_hz: float
    role: str = "main"
    dead_time_on_s: float = 0.0
    dead_time_off_s: float = 0.0


@dataclass(frozen=True)
class FETParameters:
    name: str = "Manual values"
    rds_on_ohm: float | None = None
    gate_voltage_v: float | None = None
    gate_charge_c: float | None = None
    coss_f: float | None = None
    qgs_c: float | None = None
    qg_threshold_c: float | None = None
    qgd_c: float | None = None
    gate_resistance_ohm: float | None = None
    miller_voltage_v: float | None = None
    threshold_voltage_v: float | None = None
    body_diode_drop_v: float | None = None
    eoss_j: float | None = None
    temperature_c: float | None = None
    source: str = "User-entered values; verify measurement/datasheet operating conditions"


def fet_parameters_from_device(device, temperature_c: float = 100.0, *, source: str = "Existing device library") -> FETParameters:
    """Reuse MosfetSpec without inventing absent Qgs/Qgd/threshold/Miller data.

    The existing library's qrr=0 default does not establish zero recovery.
    eoff_ref is deliberately not mixed with gate-charge overlap or Coss energy.
    """
    _number("temperature_c", temperature_c)
    if temperature_c < -273.15:
        raise ModelDomainError("temperature_c is below absolute zero")
    return FETParameters(name=device.part_number, rds_on_ohm=device.rds_at(temperature_c),
                         gate_voltage_v=device.gate_voltage_v, gate_charge_c=device.qg_c,
                         coss_f=device.coss_er_f, body_diode_drop_v=device.body_diode_vf_v,
                         temperature_c=temperature_c,
                         source=source + "; Rds linear temperature interpolation from existing MosfetSpec; Coss energy-equivalent reference; reference records are synthetic, not hardware-qualified")


def fet_losses(operating: FETOperatingPoint, device: FETParameters) -> CalculatorResult:
    """X01: gate-charge hard-switch main-FET or zero-overlap SR approximation."""
    if operating.role not in ("main", "sr"):
        raise ModelDomainError("FET role must be main or sr")
    for key, value in asdict(operating).items():
        if key != "role":
            _number(key, value, positive=key in ("frequency_hz", "drain_voltage_v"), nonnegative=True)
    if (operating.dead_time_on_s + operating.dead_time_off_s) * operating.frequency_hz >= 1:
        raise ModelDomainError("Sum of dead times must be shorter than one switching period")
    for key, value in asdict(device).items():
        if key not in ("name", "source", "temperature_c") and value is not None:
            _number(key, value, nonnegative=True)
    if device.temperature_c is not None:
        _number("temperature_c", device.temperature_c)
        if device.temperature_c < -273.15:
            raise ModelDomainError("temperature_c is below absolute zero")
    if device.coss_f is not None and device.eoss_j is not None:
        raise ModelDomainError("Choose Coss or Eoss, never both: they describe the same energy loss")
    op, d = operating, device
    pcond = None if d.rds_on_ohm is None else op.rms_current_a**2 * d.rds_on_ohm
    pcoss = (d.eoss_j * op.frequency_hz if d.eoss_j is not None else
             None if d.coss_f is None else 0.5 * d.coss_f * op.drain_voltage_v**2 * op.frequency_hz)
    pdriver = None if d.gate_charge_c is None or d.gate_voltage_v is None else d.gate_charge_c * d.gate_voltage_v * op.frequency_hz
    tr = tf = psw = pbody = None
    gate_fields = (d.qgs_c, d.qg_threshold_c, d.qgd_c, d.gate_resistance_ohm,
                   d.gate_voltage_v, d.miller_voltage_v, d.threshold_voltage_v)
    if all(value is not None for value in gate_fields):
        if not (d.gate_voltage_v > d.miller_voltage_v > d.threshold_voltage_v >= 0):
            raise ModelDomainError("Gate-charge model requires Vgs > Vmiller > Vthreshold >= 0")
        if d.qgs_c < d.qg_threshold_c or d.gate_resistance_ohm <= 0:
            raise ModelDomainError("Require Qgs >= Qg(threshold) and positive total gate resistance")
        qrise = d.qgs_c - d.qg_threshold_c
        vmid = (d.miller_voltage_v + d.threshold_voltage_v) / 2
        tr = d.gate_resistance_ohm * (qrise / (d.gate_voltage_v - vmid) + d.qgd_c / (d.gate_voltage_v - d.miller_voltage_v))
        tf = d.gate_resistance_ohm * (d.qgd_c / d.miller_voltage_v + qrise / vmid)
        psw = op.drain_voltage_v * op.frequency_hz * (tr * op.turn_on_current_a + tf * op.turn_off_current_a) / 2
    if d.body_diode_drop_v is not None:
        pbody = d.body_diode_drop_v * op.frequency_hz * (op.dead_time_on_s * op.turn_on_current_a + op.dead_time_off_s * op.turn_off_current_a)
    terms = {"conduction_w": pcond, "output_capacitance_w": pcoss,
             "switching_overlap_w": psw if op.role == "main" else 0.0,
             "body_diode_w": pbody if op.role == "sr" else 0.0}
    missing = [key for key, value in terms.items() if value is None]
    total = None if missing else sum(terms.values())
    warnings = ["Reverse recovery, parasitic overshoot, magnetics and thermal feedback are excluded; baseline sum is not full converter loss/efficiency."]
    if missing:
        warnings.append("Missing baseline terms: " + ", ".join(missing) + "; unknown terms remain null, never zero.")
    if op.role == "sr":
        warnings.append("SR assumes zero switching overlap; verify commutation and reverse conduction before using this approximation.")
    return CalculatorResult("X01", {"operating": asdict(op), "device": asdict(d)},
        {**terms, "baseline_total_w": total, "known_subtotal_w": sum(v for v in terms.values() if v is not None),
         "gate_driver_w": pdriver, "rise_time_s": tr, "fall_time_s": tf,
         "missing_terms": missing, "full_device_loss_w": None},
        {**{key: "W" for key in (*terms, "baseline_total_w", "known_subtotal_w", "gate_driver_w", "full_device_loss_w")},
         "rise_time_s": "s", "fall_time_s": "s"},
        ("RMS is over the full switching period. Rds is at the entered temperature and gate voltage.",
         "Main: conduction + gate-charge overlap + Coss/Eoss. SR: conduction + dead-time diode + Coss/Eoss.",
         "Gate-driver loss is separate, never added to device total. Eoss replaces constant-Coss energy; Eon/Eoff are not mixed in."),
        tuple(warnings), equations="§2, equations 1–7", status="PARTIAL" if missing else "MODELED")


def compare_fets(operating: FETOperatingPoint, devices: Iterable[FETParameters]) -> CalculatorResult:
    results = [fet_losses(operating, device) for device in devices]
    if len(results) < 2:
        raise ModelDomainError("Device comparison requires at least two parameter sets")
    return CalculatorResult("X01", {"operating": asdict(operating), "devices": [r.inputs["device"] for r in results]},
        {"devices": [{"name": r.inputs["device"]["name"], **r.outputs, "status": r.status} for r in results]},
        results[0].units, results[0].assumptions,
        tuple(dict.fromkeys(w for r in results for w in r.warnings)), equations="§2, equations 1–7",
        status="PARTIAL" if any(r.status == "PARTIAL" for r in results) else "MODELED")


def load_step_capacitance(control: str, delta_current_a: float, delta_voltage_v: float,
                          crossover_hz: float, phase_margin_deg: float, esr_ohm: float,
                          inductance_h: float | None = None) -> CalculatorResult:
    """X02: TI's distinct CMC/VMC closed-loop load-step capacitance estimates."""
    control = control.upper()
    if control not in ("CMC", "VMC"):
        raise ModelDomainError("control must be CMC or VMC")
    for key, value in (("delta_current_a", delta_current_a), ("delta_voltage_v", delta_voltage_v), ("crossover_hz", crossover_hz)):
        _number(key, value, positive=True)
    _number("esr_ohm", esr_ohm, nonnegative=True)
    _number("phase_margin_deg", phase_margin_deg)
    if not 0 < phase_margin_deg < 180:
        raise ModelDomainError("phase margin must satisfy 0 < PM < 180 degrees")
    w = 2 * math.pi * crossover_hz
    k = 2 * math.sin(math.radians(phase_margin_deg) / 2)  # stable sqrt(2-2*cos(PM))
    available_impedance = delta_voltage_v / delta_current_a
    if control == "VMC":
        _number("inductance_h", inductance_h, positive=True)
        conductance = delta_current_a / delta_voltage_v - 1 / (w * inductance_h)
        if conductance <= 0:
            raise ModelDomainError("VMC denominator ΔI/ΔV - 1/(ωc L) must be positive")
        available_impedance = 1 / conductance
    elif inductance_h is not None:
        raise ModelDomainError("CMC does not use inductance_h; omit it rather than imply an effect")
    denominator = w * (available_impedance - esr_ohm) * k
    if not math.isfinite(denominator) or denominator <= 0:
        raise ModelDomainError("ESR exhausts available transient impedance; no positive finite capacitance solution")
    warnings = ("PM below TI's 60° recommendation (65° for electrolytics).",) if phase_margin_deg < 60 else ()
    return CalculatorResult("X02", {"control": control, "delta_current_a": delta_current_a, "delta_voltage_v": delta_voltage_v,
        "crossover_hz": crossover_hz, "phase_margin_deg": phase_margin_deg, "esr_ohm": esr_ohm, "inductance_h": inductance_h},
        {"minimum_capacitance_f": 1 / denominator, "phase_factor": k,
         "available_impedance_ohm": available_impedance, "esr_headroom_ohm": available_impedance - esr_ohm},
        {"minimum_capacitance_f": "F", "phase_factor": "1", "available_impedance_ohm": "ohm", "esr_headroom_ohm": "ohm"},
        ("Closed-loop first-order TI estimate using crossover and phase margin, not a general charge-gap transient model.",
         "ESL ignored; effective DC-biased capacitance required. Internal controller compensation may constrain C."),
        warnings, equations="§3, equations 8–9")


@dataclass(frozen=True)
class CapacitorBranch:
    capacitance_f: float
    esr_ohm: float = 0.0
    esl_h: float = 0.0
    name: str = "capacitor"

    def validate(self):
        _number("capacitance_f", self.capacitance_f, positive=True)
        _number("esr_ohm", self.esr_ohm, nonnegative=True)
        _number("esl_h", self.esl_h, nonnegative=True)

    def impedance(self, frequency_hz: float) -> complex:
        self.validate()
        w = 2 * math.pi * _number("frequency_hz", frequency_hz, positive=True)
        return complex(self.esr_ohm, w * self.esl_h - 1 / (w * self.capacitance_f))


def capacitor_current_sharing(branches: Iterable[CapacitorBranch], total_rms_a: float, frequency_hz: float,
                              *, harmonics: Iterable[tuple[float, float]] | None = None) -> CalculatorResult:
    """X03: complex KCL at one frequency, optionally orthogonal harmonic RMS sum.

    harmonics consists of (frequency_Hz, current_RMS_A); total_rms_a must equal
    root-sum-square of the supplied components, preventing ambiguous scaling.
    """
    branches = tuple(branches)
    if not 2 <= len(branches) <= 32:
        raise ModelDomainError("Use 2–32 capacitor branches (baseline supports three)")
    _number("total_rms_a", total_rms_a, nonnegative=True)
    _number("frequency_hz", frequency_hz, positive=True)
    spectral = [(frequency_hz, total_rms_a)] if harmonics is None else list(harmonics)
    if not spectral or len(spectral) > 4096:
        raise ModelDomainError("Spectrum requires 1–4096 components")
    if len({f for f, _ in spectral}) != len(spectral):
        raise ModelDomainError("Combine same-frequency phasors before supplying distinct harmonics")
    for f, current in spectral:
        _number("harmonic_frequency_hz", f, positive=True)
        _number("harmonic_rms_a", current, nonnegative=True)
    if not math.isclose(math.sqrt(sum(i*i for _, i in spectral)), total_rms_a, rel_tol=1e-9, abs_tol=1e-12):
        raise ModelDomainError("total_rms_a must match root-sum-square of harmonic RMS currents")
    square = [0.0] * len(branches)
    spectra = []
    for frequency, current in spectral:
        impedances = [branch.impedance(frequency) for branch in branches]
        if any(abs(z) == 0 for z in impedances):
            raise ModelDomainError("Ideal zero-impedance resonant branch: specify a positive physical ESR")
        admittance = sum(1 / z for z in impedances)
        if abs(admittance) <= 1e-14 * sum(abs(1/z) for z in impedances):
            raise ModelDomainError("Parallel antiresonance is singular; specify physical ESR")
        zpar = 1 / admittance
        currents = [current * zpar / z for z in impedances]
        for index, i in enumerate(currents):
            square[index] += abs(i)**2
        spectra.append({"frequency_hz": frequency, "total_rms_a": current, "parallel_impedance_real_ohm": zpar.real,
            "parallel_impedance_imag_ohm": zpar.imag, "kcl_residual_a": abs(sum(currents) - current),
            "branches": [{"current_real_a": i.real, "current_imag_a": i.imag, "current_rms_a": abs(i),
                          "phase_deg": math.degrees(math.atan2(i.imag, i.real))} for i in currents]})
    return CalculatorResult("X03", {"branches": [asdict(b) for b in branches], "total_rms_a": total_rms_a,
        "frequency_hz": frequency_hz, "harmonics": None if harmonics is None else spectral},
        {"branches": [{"name": b.name, "current_rms_a": math.sqrt(s), "esr_loss_w": s * b.esr_ohm}
                      for b, s in zip(branches, square)], "spectrum": spectra},
        {"frequency_hz": "Hz", "total_rms_a": "A", "current_rms_a": "A", "current_real_a": "A", "current_imag_a": "A",
         "phase_deg": "deg", "kcl_residual_a": "A", "esr_loss_w": "W", "parallel_impedance_real_ohm": "ohm", "parallel_impedance_imag_ohm": "ohm"},
        ("Linear series C/ESR/ESL branch models; current-source drive. KCL sums complex currents, not magnitudes.",
         "Single-frequency baseline. Supplied multi-harmonic spectra are an extension with orthogonal RMS-square summation."),
        equations="§4, equations 10–12")


def acdc_bulk_capacitor(input_power_w: float, minimum_voltage_v: float, ripple_fraction: float,
                        line_frequency_hz: float) -> CalculatorResult:
    """X04: ideal full-wave rectifier bulk sizing and printed eq13 RMS estimate."""
    for key, value in (("input_power_w", input_power_w), ("minimum_voltage_v", minimum_voltage_v), ("line_frequency_hz", line_frequency_hz)):
        _number(key, value, positive=True)
    r = _fraction("ripple_fraction", ripple_fraction)
    vmax = minimum_voltage_v / (1-r)
    td = 1/(4*line_frequency_hz) + math.asin(1-r)/(2*math.pi*line_frequency_hz)
    tc = 1/(2*line_frequency_hz) - td
    if tc <= 0:
        raise ModelDomainError("Ripple is too small to resolve a finite charging interval")
    capacitance = required_capacitance_f(vmax, minimum_voltage_v, input_power_w, td)
    irms = math.hypot(capacitance * (vmax-minimum_voltage_v)/(tc*math.sqrt(3)), input_power_w/minimum_voltage_v)
    return CalculatorResult("X04", {"input_power_w": input_power_w, "minimum_voltage_v": minimum_voltage_v,
        "ripple_fraction": r, "line_frequency_hz": line_frequency_hz},
        {"minimum_line_rms_v": vmax/math.sqrt(2), "maximum_voltage_v": vmax, "discharge_time_s": td,
         "charge_time_s": tc, "capacitance_f": capacitance, "bulk_rms_current_eq13_a": irms},
        {"minimum_line_rms_v": "V RMS", "maximum_voltage_v": "V", "discharge_time_s": "s", "charge_time_s": "s", "capacitance_f": "F", "bulk_rms_current_eq13_a": "A RMS"},
        ("Ideal full-wave rectification; constant power drawn from bulk; sinusoidal line and zero bridge/source drop.",
         "Energy-discharge capacitance reuses llc_design.core.bus_capacitor. Rectifier charge/discharge intervals are not arbitrary hold-up time."),
        ("TI Figure 5-1 shows 2.49 A for 100 W, 85 V, 20%, 47 Hz; printed eq13 gives 2.622526265 A. This is the published approximation, not a full-period integrated capacitor current. Independent charge-balanced rectifier models give different RMS values; do not use this estimate as validated thermal sizing. No executable correlation.",),
        equations="§5, equation 13", status="MODELED_WITH_SOURCE_DISCREPANCY")


def hold_up_capacitance(start_voltage_v: float, end_voltage_v: float, input_power_w: float, hold_time_s: float) -> CalculatorResult:
    """G04 constant-power hold-up extension; deliberately separate from X04."""
    for key, value in locals().copy().items():
        _number(key, value, positive=True)
    if start_voltage_v <= end_voltage_v:
        raise ModelDomainError("start_voltage_v must exceed end_voltage_v")
    c = required_capacitance_f(start_voltage_v, end_voltage_v, input_power_w, hold_time_s)
    return CalculatorResult("G04-hold-up", {"start_voltage_v": start_voltage_v, "end_voltage_v": end_voltage_v,
        "input_power_w": input_power_w, "hold_time_s": hold_time_s}, {"capacitance_f": c, "removed_energy_j": input_power_w*hold_time_s},
        {"capacitance_f": "F", "removed_energy_j": "J"},
        ("Ideal constant-power energy discharge; power is taken from capacitor. Does not model AC rectifier recharge or RMS ripple.",),
        equations="E = C(Vstart² − Vend²)/2 = P t")


def rcd_snubber(leakage_inductance_h: float, peak_primary_current_a: float, frequency_hz: float,
                reflected_voltage_v: float, clamp_ratio: float = 1.5, ripple_fraction: float = 0.05) -> CalculatorResult:
    """X05: dissipative flyback RCD clamp, voltage referenced to input rail."""
    for key, value in (("leakage_inductance_h", leakage_inductance_h), ("peak_primary_current_a", peak_primary_current_a),
                       ("frequency_hz", frequency_hz), ("reflected_voltage_v", reflected_voltage_v)):
        _number(key, value, positive=True)
    _number("clamp_ratio", clamp_ratio)
    if clamp_ratio <= 1:
        raise ModelDomainError("Clamp ratio Vsn/Vreflected must exceed 1")
    r = _fraction("ripple_fraction", ripple_fraction)
    vsn = clamp_ratio * reflected_voltage_v
    energy = .5*leakage_inductance_h*peak_primary_current_a**2
    power = energy*frequency_hz*vsn/(vsn-reflected_voltage_v)
    resistance = vsn**2/power
    capacitance = 1/(r*resistance*frequency_hz)
    discharge_time = leakage_inductance_h*peak_primary_current_a/(vsn-reflected_voltage_v)
    if discharge_time >= 1/frequency_hz:
        raise ModelDomainError("Leakage clamp discharge does not fit within a switching period")
    return CalculatorResult("X05", {"leakage_inductance_h": leakage_inductance_h, "peak_primary_current_a": peak_primary_current_a,
        "frequency_hz": frequency_hz, "reflected_voltage_v": reflected_voltage_v, "clamp_ratio": clamp_ratio, "ripple_fraction": r},
        {"clamp_voltage_above_input_v": vsn, "snubber_power_w": power, "resistance_ohm": resistance,
         "capacitance_f": capacitance, "leakage_energy_j": energy, "clamp_ripple_pp_v": r*vsn,
         "leakage_discharge_time_s": discharge_time},
        {"clamp_voltage_above_input_v": "V", "snubber_power_w": "W", "resistance_ohm": "ohm", "capacitance_f": "F",
         "leakage_energy_j": "J", "clamp_ripple_pp_v": "V pp", "leakage_discharge_time_s": "s"},
        ("Clamp voltage is above input rail; main FET stress also includes Vin and parasitic overshoot.",
         "Leakage energy approximation, ideal diode, small clamp ripple. R/C are starting values, not total switching loss.",
         "Verify computed leakage-discharge time fits the actual off interval; only the weaker full-period bound can be checked without duty input."),
        ("Clamp ripple is not small; the constant-clamp approximation may be inaccurate.",) if r > .1 else (), equations="§6, equations 14–16")


def rc_snubber(ringing_frequency_hz: float, loaded_frequency_hz: float, added_capacitance_f: float,
               switching_frequency_hz: float | None = None, voltage_swing_v: float | None = None) -> CalculatorResult:
    """X06: infer parasitic LC from a known test capacitance and shifted ring."""
    for key, value in (("ringing_frequency_hz", ringing_frequency_hz), ("loaded_frequency_hz", loaded_frequency_hz), ("added_capacitance_f", added_capacitance_f)):
        _number(key, value, positive=True)
    if loaded_frequency_hz >= ringing_frequency_hz:
        raise ModelDomainError("Adding capacitance must reduce ring frequency: 0 < f1 < f0")
    if (switching_frequency_hz is None) != (voltage_swing_v is None):
        raise ModelDomainError("Power estimate requires both switching_frequency_hz and voltage_swing_v")
    m2 = (ringing_frequency_hz/loaded_frequency_hz)**2
    c0 = added_capacitance_f/(m2-1)
    inductance = 1/((2*math.pi*ringing_frequency_hz)**2*c0)
    csn = 3*c0
    power = None
    if switching_frequency_hz is not None:
        _number("switching_frequency_hz", switching_frequency_hz, positive=True)
        _number("voltage_swing_v", voltage_swing_v, positive=True)
        power = csn*voltage_swing_v**2*switching_frequency_hz
    return CalculatorResult("X06", {"ringing_frequency_hz": ringing_frequency_hz, "loaded_frequency_hz": loaded_frequency_hz,
        "added_capacitance_f": added_capacitance_f, "switching_frequency_hz": switching_frequency_hz, "voltage_swing_v": voltage_swing_v},
        {"parasitic_capacitance_f": c0, "parasitic_inductance_h": inductance, "snubber_capacitance_f": csn,
         "snubber_resistance_ohm": math.sqrt(inductance/c0), "charge_discharge_power_w": power},
        {"parasitic_capacitance_f": "F", "parasitic_inductance_h": "H", "snubber_capacitance_f": "F", "snubber_resistance_ohm": "ohm", "charge_discharge_power_w": "W"},
        ("Same linear parasitic L before/after adding known C; measurement probe and operating point unchanged.",
         "R=√(L/C0), Csn=3C0 are damping starting values. Optional Csn ΔV² fs power assumes full hard-switched charge/discharge each cycle; extension, not TI eq17–21."),
        equations="§7, equations 17–21")


# IEC preferred numbers: explicit published nominal sequences avoid rounding the
# geometric sequence into incorrect E24 entries (notably 2.7, 3.3 and 4.7).
E_SERIES = {
    "E24": (10,11,12,13,15,16,18,20,22,24,27,30,33,36,39,43,47,51,56,62,68,75,82,91),
    "E48": (100,105,110,115,121,127,133,140,147,154,162,169,178,187,196,205,215,226,237,249,261,274,287,301,316,332,348,365,383,402,422,442,464,487,511,536,562,590,619,649,681,715,750,787,825,866,909,953),
    "E96": (100,102,105,107,110,113,115,118,121,124,127,130,133,137,140,143,147,150,154,158,162,165,169,174,178,182,187,191,196,200,205,210,215,221,226,232,237,243,249,255,261,267,274,280,287,294,301,309,316,324,332,340,348,357,365,374,383,392,402,412,422,432,442,453,464,475,487,499,511,523,536,549,562,576,590,604,619,634,649,665,681,698,715,732,750,768,787,806,825,845,866,887,909,931,953,976),
}


def preferred_resistance(target_ohm: float, series: str = "E96", parallel: bool = False) -> tuple[float, ...]:
    """Nearest one or two standard resistors; two-leg search spans R..100R.

    Uses bounded E-series decades around the target, minimizing actual equivalent
    relative error. This is an explicit search range, not a universal optimum.
    """
    _number("target_ohm", target_ohm, positive=True)
    if series not in E_SERIES:
        raise ModelDomainError("Supported preferred series are E24, E48 and E96")
    exponent = math.floor(math.log10(target_ohm))
    norm = 10 if series == "E24" else 100
    candidates = [float(value/norm*10**decade) for decade in range(exponent-1, exponent+3) for value in E_SERIES[series]]
    single = min(candidates, key=lambda value: abs(value-target_ohm))
    if not parallel:
        return (single,)
    candidates = [value for value in candidates if value >= target_ohm and value <= 100*target_ohm]
    best, error = None, math.inf
    for index, first in enumerate(candidates):
        for second in candidates[index:]:
            value = first*second/(first+second)
            err = abs(value-target_ohm)
            if err < error:
                best, error = (first, second), err
    if best is None:
        raise ModelDomainError("No parallel resistor pair in the declared search range")
    return best


def divider_output(reference_v: float, high_ohm: float, low_ohm: float, *,
                   resistor_tolerance: float = .01, reference_tolerance: float = .01,
                   target_v: float | None = None) -> CalculatorResult:
    """X07 forward evaluation including independent worst-case corners."""
    for key, value in (("reference_v", reference_v), ("high_ohm", high_ohm), ("low_ohm", low_ohm)):
        _number(key, value, positive=True)
    rt = _fraction("resistor_tolerance", resistor_tolerance, allow_zero=True)
    vt = _fraction("reference_tolerance", reference_tolerance, allow_zero=True)
    if target_v is not None:
        _number("target_v", target_v, positive=True)
    nominal = reference_v*(1+high_ohm/low_ohm)
    low = reference_v*(1-vt)*(1+high_ohm*(1-rt)/(low_ohm*(1+rt)))
    high = reference_v*(1+vt)*(1+high_ohm*(1+rt)/(low_ohm*(1-rt)))
    return CalculatorResult("X07", {"reference_v": reference_v, "high_ohm": high_ohm, "low_ohm": low_ohm,
        "resistor_tolerance": rt, "reference_tolerance": vt, "target_v": target_v},
        {"output_v": nominal, "minimum_output_v": low, "maximum_output_v": high, "divider_current_a": nominal/(high_ohm+low_ohm),
         "relative_error": None if target_v is None else nominal/target_v-1,
         "minimum_relative_error": None if target_v is None else low/target_v-1,
         "maximum_relative_error": None if target_v is None else high/target_v-1,
         "high_arm_power_w": (nominal-reference_v)**2/high_ohm, "low_arm_power_w": reference_v**2/low_ohm},
        {"output_v": "V", "minimum_output_v": "V", "maximum_output_v": "V", "divider_current_a": "A",
         "relative_error": "fraction", "minimum_relative_error": "fraction", "maximum_relative_error": "fraction",
         "high_arm_power_w": "W", "low_arm_power_w": "W"},
        ("Ideal reference and zero feedback-input current. Divider current is not amplifier bias-input current.",
         "Worst-case independent resistor/reference tolerance; common fractional tolerance also bounds same-tolerance parallel pairs.",
         "Arm dissipation uses nominal values; choose package/rating with actual thermal derating rather than an unsupported package recommendation."),
        equations="§8, equations 22–25")


def design_divider(reference_v: float, target_v: float, known_resistance_ohm: float, known_arm: str = "low", *,
                   series: str | None = None, parallel: bool = False,
                   resistor_tolerance: float = .01, reference_tolerance: float = .01) -> CalculatorResult:
    for key, value in (("reference_v", reference_v), ("target_v", target_v), ("known_resistance_ohm", known_resistance_ohm)):
        _number(key, value, positive=True)
    if target_v <= reference_v:
        raise ModelDomainError("target_v must exceed reference_v for a positive two-resistor divider")
    if known_arm not in ("low", "high"):
        raise ModelDomainError("known_arm must be low or high")
    if parallel and series is None:
        raise ModelDomainError("Parallel selection requires an E-series")
    ideal = known_resistance_ohm*(target_v/reference_v-1) if known_arm == "low" else known_resistance_ohm/(target_v/reference_v-1)
    selected = (ideal,) if series is None else preferred_resistance(ideal, series, parallel)
    actual = 1/sum(1/value for value in selected)
    high, low = (actual, known_resistance_ohm) if known_arm == "low" else (known_resistance_ohm, actual)
    evaluated = divider_output(reference_v, high, low, resistor_tolerance=resistor_tolerance, reference_tolerance=reference_tolerance, target_v=target_v)
    return CalculatorResult("X07", {"reference_v": reference_v, "target_v": target_v, "known_resistance_ohm": known_resistance_ohm,
        "known_arm": known_arm, "series": series, "parallel": parallel, "resistor_tolerance": resistor_tolerance, "reference_tolerance": reference_tolerance},
        {"ideal_unknown_ohm": ideal, "selected_resistors_ohm": list(selected), "high_ohm": high, "low_ohm": low, **evaluated.outputs},
        {**evaluated.units, "ideal_unknown_ohm": "ohm", "selected_resistors_ohm": "ohm", "high_ohm": "ohm", "low_ohm": "ohm"},
        evaluated.assumptions + ("Unknown arm selected; known arm retained exactly. Two-resistor search uses each resistor from Rtarget to 100 Rtarget.",),
        equations="§8, equations 22–25; bounded E24/E48/E96 search")


def analog_scaling(minimum_output_v: float, maximum_output_v: float, reference_v: float,
                   maximum_adjust_v: float, top_resistance_ohm: float) -> CalculatorResult:
    """X08: feedback-node KCL, analog adjustment 0..Vadj,max (inverting slope)."""
    for key, value in locals().copy().items():
        _number(key, value, positive=True)
    if not maximum_output_v > minimum_output_v > reference_v:
        raise ModelDomainError("Require Vout,max > Vout,min > Vref > 0")
    r3 = top_resistance_ohm*maximum_adjust_v/(maximum_output_v-minimum_output_v)
    denominator = r3*(maximum_output_v-reference_v)-top_resistance_ohm*reference_v
    if denominator <= 0:
        raise ModelDomainError("No positive bottom resistor for requested endpoints and adjustment range")
    r2 = top_resistance_ohm*r3*reference_v/denominator
    def output(adjust):
        return reference_v*(1+top_resistance_ohm/r2+top_resistance_ohm/r3)-adjust*top_resistance_ohm/r3
    return CalculatorResult("X08", {"minimum_output_v": minimum_output_v, "maximum_output_v": maximum_output_v,
        "reference_v": reference_v, "maximum_adjust_v": maximum_adjust_v, "top_resistance_ohm": top_resistance_ohm},
        {"bottom_resistance_ohm": r2, "adjust_resistance_ohm": r3,
         "minimum_top_current_a": (minimum_output_v-reference_v)/top_resistance_ohm,
         "output_at_zero_adjust_v": output(0), "output_at_maximum_adjust_v": output(maximum_adjust_v),
         "adjust_to_output_gain": -top_resistance_ohm/r3,
         "mapping": [{"adjust_v": a, "output_v": output(a)} for a in np.linspace(0, maximum_adjust_v, 11).tolist()]},
        {"bottom_resistance_ohm": "ohm", "adjust_resistance_ohm": "ohm", "minimum_top_current_a": "A",
         "output_at_zero_adjust_v": "V", "output_at_maximum_adjust_v": "V", "adjust_to_output_gain": "V/V", "adjust_v": "V", "output_v": "V"},
        ("Ideal voltage-source adjustment 0..Vadj,max. DAC/PWM source must source and sink the feedback-branch current.",
         "Zero feedback input current; output decreases as adjustment voltage increases."), equations="§9, equation 26; independent feedback-node KCL")


def digital_scaling(minimum_output_v: float, maximum_output_v: float, reference_v: float,
                    bits: int, top_resistance_ohm: float) -> CalculatorResult:
    """X09: ideal active-high code closes open-drain bit resistors to ground."""
    for key, value in (("minimum_output_v", minimum_output_v), ("maximum_output_v", maximum_output_v),
                       ("reference_v", reference_v), ("top_resistance_ohm", top_resistance_ohm)):
        _number(key, value, positive=True)
    if isinstance(bits, bool) or not isinstance(bits, int) or not 1 <= bits <= 16:
        raise ModelDomainError("bits must be an integer 1–16 (explicit mapping resource limit)")
    if not maximum_output_v > minimum_output_v > reference_v:
        raise ModelDomainError("Require Vout,max > Vout,min > Vref > 0")
    bottom = top_resistance_ohm*reference_v/(minimum_output_v-reference_v)
    step = (maximum_output_v-minimum_output_v)/(2**bits-1)
    resistors = [top_resistance_ohm*reference_v/(2**bit*step) for bit in range(bits)]
    # Re-evaluate actual parallel conductance rather than merely echo targets.
    codes = np.arange(2**bits, dtype=np.uint32)
    conductance = np.full(len(codes), 1/bottom)
    for bit, resistor in enumerate(resistors):
        conductance += ((codes >> bit) & 1) / resistor
    voltages = reference_v*(1+top_resistance_ohm*conductance)
    mapping = [{"code": int(code), "output_v": float(voltage)} for code, voltage in zip(codes, voltages)]
    return CalculatorResult("X09", {"minimum_output_v": minimum_output_v, "maximum_output_v": maximum_output_v,
        "reference_v": reference_v, "bits": bits, "top_resistance_ohm": top_resistance_ohm},
        {"bottom_resistance_ohm": bottom, "bit_resistances_ohm_lsb_first": resistors, "step_v": step,
         "minimum_top_current_a": (minimum_output_v-reference_v)/top_resistance_ohm, "mapping": mapping},
        {"bottom_resistance_ohm": "ohm", "bit_resistances_ohm_lsb_first": "ohm", "step_v": "V", "minimum_top_current_a": "A", "output_v": "V", "code": "integer"},
        ("Code bit=1 closes its resistor to ground; bit=0 disconnects. Ideal switches, no pull-high GPIO voltage.",
         "Nominal resistors; switch Rds/leakage and resistor tolerance excluded. This generates a setpoint, not a digital feedback controller."),
        equations="§10, equation 27")


# Factors to a category-specific base SI unit. PCB ounce is oz/ft², with the
# baseline's nominal 35 µm/oz convention; metric PS is distinct from mechanical hp.
UNIT_CATEGORIES = {
    "flux_density": {"T": 1.0, "mT": 1e-3, "G": 1e-4},
    "voltage_gain": {"factor": 1.0, "dB": None},
    "power_gain": {"factor": 1.0, "dB": None},
    "length": {"m": 1.0, "mm": .001, "mil": .0000254, "in": .0254},
    "mass": {"kg": 1.0, "g": .001, "oz": .028349523125},
    "airflow": {"m/s": 1.0, "lfm": .00508},
    "pcb_copper": {"um": 1.0, "oz/ft2 (nominal)": 35.0},
    "temperature": {"C": None, "F": None, "K": None},
    "power": {"W": 1.0, "kW": 1000.0, "PS (metric hp)": 735.49875, "hp (mechanical)": 745.6998715822702},
    "torque": {"N m": 1.0, "lb ft": 1.3558179483314004},
    "speed": {"m/s": 1.0, "km/h": 1/3.6, "mph": .44704},
}


def convert_units(value: float, category: str, from_unit: str, to_unit: str) -> CalculatorResult:
    """X10: ten baseline families plus explicitly distinct power-gain dB."""
    _number("value", value)
    if category not in UNIT_CATEGORIES or from_unit not in UNIT_CATEGORIES[category] or to_unit not in UNIT_CATEGORIES[category]:
        raise ModelDomainError("Units must belong to the same supported category")
    units = UNIT_CATEGORIES[category]
    if category in ("voltage_gain", "power_gain"):
        factor = 20 if category == "voltage_gain" else 10
        linear = value if from_unit == "factor" else 10**(value/factor)
        if linear <= 0:
            raise ModelDomainError("dB gain requires a strictly positive linear ratio")
        converted = linear if to_unit == "factor" else factor*math.log10(linear)
    elif category == "temperature":
        kelvin = value+273.15 if from_unit == "C" else (value-32)*5/9+273.15 if from_unit == "F" else value
        if kelvin < -1e-10:
            raise ModelDomainError("Temperature cannot be below absolute zero")
        kelvin = max(0.0, kelvin)
        converted = kelvin-273.15 if to_unit == "C" else (kelvin-273.15)*9/5+32 if to_unit == "F" else kelvin
    else:
        converted = value*units[from_unit]/units[to_unit]
    return CalculatorResult("X10", {"value": value, "category": category, "from_unit": from_unit, "to_unit": to_unit},
        {"converted_value": converted}, {"value": from_unit, "converted_value": to_unit},
        ("Amplitude/voltage gain uses 20 log10; power gain uses 10 log10. No impedance equivalence implied.",
         "PCB ounce means nominal copper areal weight/thickness: 1 oz/ft² = 35 µm. Metric PS and mechanical hp are explicitly distinct."),
        equations="§11, Figure 11-1; exact unit definitions except nominal PCB copper thickness")


@dataclass(frozen=True)
class FilterInputs:
    inductance_h: float
    converter_capacitance_f: float
    filter_capacitance_f: float
    damping_capacitance_f: float
    damping_resistance_ohm: float
    input_voltage_v: float
    input_current_a: float
    inductor_dcr_ohm: float = 0.0
    inductor_srf_hz: float = 1e6
    converter_esr_ohm: float = .01
    converter_esl_h: float = 0.0
    filter_esr_ohm: float = .01
    filter_esl_h: float = 0.0
    damping_esr_ohm: float = .01
    damping_esl_h: float = 0.0
    minimum_frequency_hz: float = 10.0
    maximum_frequency_hz: float = 1e6
    points: int = 401
    configuration: str = "pi"
    damping_frequency_hz: float | None = None
    noise_frequency_hz: float = 500e3
    ripple_amplitude_v: float = .015
    limit_dbuv: float = 65.0


def differential_filter(inputs: FilterInputs) -> CalculatorResult:
    """X12: differential LC/π impedance and both directional transfer functions.

    CIN/OUT is the converter-side capacitor, CF is the other-side capacitor.
    LC omits CF: noise→other-side current attenuation is then unity for this
    ideal current-source model; source→converter voltage still has LC filtering.
    """
    positive = {"inductance_h", "converter_capacitance_f", "filter_capacitance_f", "damping_capacitance_f",
                "input_voltage_v", "inductor_srf_hz", "minimum_frequency_hz", "maximum_frequency_hz", "noise_frequency_hz", "ripple_amplitude_v"}
    for key, value in asdict(inputs).items():
        if key in ("points", "configuration", "damping_frequency_hz", "limit_dbuv"):
            continue
        _number(key, value, positive=key in positive, nonnegative=True)
    if inputs.configuration not in ("lc", "pi"):
        raise ModelDomainError("configuration must be lc or pi")
    if isinstance(inputs.points, bool) or not isinstance(inputs.points, int) or not 16 <= inputs.points <= 10000:
        raise ModelDomainError("Frequency points must be an integer 16–10000")
    if inputs.maximum_frequency_hz <= inputs.minimum_frequency_hz:
        raise ModelDomainError("maximum_frequency_hz must exceed minimum_frequency_hz")
    _number("limit_dbuv", inputs.limit_dbuv)
    i = inputs
    cin = CapacitorBranch(i.converter_capacitance_f, i.converter_esr_ohm, i.converter_esl_h)
    cf = CapacitorBranch(i.filter_capacitance_f, i.filter_esr_ohm, i.filter_esl_h)
    cd = CapacitorBranch(i.damping_capacitance_f, i.damping_resistance_ohm+i.damping_esr_ohm, i.damping_esl_h)
    cl = 1/(i.inductance_h*(2*math.pi*i.inductor_srf_hz)**2)
    def parallel(*zs):
        if any(abs(z) == 0 for z in zs):
            return 0j
        admittance = sum(1/z for z in zs)
        if abs(admittance) <= 1e-14*sum(abs(1/z) for z in zs):
            raise ModelDomainError("Ideal filter resonance is singular; enter physical resistance")
        return 1/admittance
    def calc(f):
        s = 2j*math.pi*f
        zl = (i.inductor_dcr_ohm+s*i.inductance_h)/(1+s*cl*(i.inductor_dcr_ohm+s*i.inductance_h))
        zi, zf, zd = cin.impedance(f), cf.impedance(f), cd.impedance(f)
        shunt = parallel(zi, zd)
        return {"noise_to_other": 1+0j if i.configuration == "lc" else zf/(zf+zl),
                "other_to_noise_undamped": zi/(zi+zl), "other_to_noise_damped": shunt/(shunt+zl),
                "output_impedance_undamped": parallel(zl, zi), "output_impedance_damped": parallel(zl, zi, zd)}
    frequencies = np.geomspace(i.minimum_frequency_hz, i.maximum_frequency_hz, i.points)
    try:
        response = [calc(float(f)) for f in frequencies]
    except ZeroDivisionError as exc:
        raise ModelDomainError("Filter has an ideal singularity; enter physical ESR/DCR or change the frequency span") from exc
    curves = {"frequency_hz": frequencies.tolist()}
    for key in response[0]:
        values = np.array([row[key] for row in response])
        if not np.all(np.isfinite(values)) or np.any(np.abs(values) == 0):
            raise ModelDomainError("Filter response contains an ideal pole/zero at a sampled point; add physical resistance")
        curves[key+"_magnitude"] = np.abs(values).tolist()
        curves[key+"_phase_deg"] = np.degrees(np.unwrap(np.angle(values))).tolist()
        if "impedance" not in key:
            curves[key+"_db"] = (20*np.log10(np.abs(values))).tolist()
    # TI eq123–129 explicitly ignore ESL/SRF in the damping estimate. Resolve
    # fdamp by Im(jωL + Zi||Zd)=0 using exactly that RC network, or honor user f.
    def rc_parallel(f):
        w = 2*math.pi*f
        return parallel(complex(i.converter_esr_ohm, -1/(w*i.converter_capacitance_f)),
                        complex(i.damping_resistance_ohm+i.damping_esr_ohm, -1/(w*i.damping_capacitance_f)))
    fd = i.damping_frequency_hz
    if fd is None:
        low = 1/(2*math.pi*math.sqrt(i.inductance_h*(i.converter_capacitance_f+i.damping_capacitance_f)))
        high = 1/(2*math.pi*math.sqrt(i.inductance_h*i.converter_capacitance_f))
        fd = brentq(lambda f: 2*math.pi*f*i.inductance_h+rc_parallel(f).imag, low*.1, high*10, xtol=1e-10)
    else:
        _number("damping_frequency_hz", fd, positive=True)
    zeff = rc_parallel(fd)
    if zeff.imag >= 0:
        raise ModelDomainError("Damping equivalent must remain capacitive")
    ceff = -1/(2*math.pi*fd*zeff.imag)
    esreff = zeff.real
    z0 = math.sqrt(i.inductance_h/ceff)
    negative_resistance = None if i.input_current_a == 0 else -i.input_voltage_v/i.input_current_a
    damping = .5*((i.inductor_dcr_ohm+esreff)/z0-z0*i.input_current_a/i.input_voltage_v)
    warnings = ["Negative constant-power input resistance is retained in δ. Positive δ alone is not a full converter/filter stability or EMI compliance proof.",
                "Damping estimate ignores ESL and inductor self-capacitance; frequency curves include them. Automatic fdamp is an explicit RC-resonance extension; no TI executable match is claimed."]
    if damping <= 0:
        warnings.append("Non-positive approximate damping: constant-power load overwhelms passive damping in this reduced model.")
    try:
        noise_response = calc(i.noise_frequency_hz)
    except ZeroDivisionError as exc:
        raise ModelDomainError("Noise frequency is at an ideal filter singularity; enter physical ESR/DCR") from exc
    if any(not np.isfinite(value) or abs(value)==0 for value in noise_response.values()):
        raise ModelDomainError("Noise frequency has an ideal pole/zero; enter physical ESR/DCR")
    ripple_dbuv = 20*math.log10(i.ripple_amplitude_v/1e-6)
    noise_checks = {}
    for key in ("noise_to_other", "other_to_noise_undamped", "other_to_noise_damped"):
        attenuation = -20*math.log10(abs(noise_response[key]))
        noise_checks[key] = {"attenuation_db": attenuation, "filtered_level_dbuv": ripple_dbuv-attenuation,
                             "margin_to_limit_db": i.limit_dbuv-(ripple_dbuv-attenuation)}
    suggestion_c = i.filter_capacitance_f if i.configuration == "pi" else i.converter_capacitance_f
    return CalculatorResult("X12", asdict(i), {"inductor_parallel_capacitance_f": cl,
        "unfiltered_level_dbuv": ripple_dbuv, "required_attenuation_db": ripple_dbuv-i.limit_dbuv,
        "noise_frequency_checks": noise_checks,
        "suggested_damping_capacitance_f": 4*suggestion_c,
        "suggested_damping_total_resistance_ohm": math.sqrt(i.inductance_h/suggestion_c), "damping_frequency_hz": fd,
        "effective_capacitance_f": ceff, "effective_esr_ohm": esreff, "characteristic_impedance_ohm": z0,
        "constant_power_input_resistance_ohm": negative_resistance, "damping_factor": damping,
        "inductor_dc_loss_w": i.input_current_a**2*i.inductor_dcr_ohm, "curves": curves},
        {"inductor_parallel_capacitance_f": "F", "damping_frequency_hz": "Hz", "effective_capacitance_f": "F",
         "effective_esr_ohm": "ohm", "characteristic_impedance_ohm": "ohm", "constant_power_input_resistance_ohm": "ohm",
         "damping_factor": "1", "inductor_dc_loss_w": "W", "frequency_hz": "Hz",
         "unfiltered_level_dbuv": "dBµV", "required_attenuation_db": "dB", "attenuation_db": "dB",
         "filtered_level_dbuv": "dBµV", "margin_to_limit_db": "dB", "suggested_damping_capacitance_f": "F",
         "suggested_damping_total_resistance_ohm": "ohm",
         **{key: "deg" if key.endswith("_deg") else "dB" if key.endswith("_db") else "ohm" if "impedance" in key else "1" for key in curves if key != "frequency_hz"}},
        ("Source-side CF, series LF, converter-side CIN/OUT and series RD+CD shunt damping; all capacitor ESR/ESL and inductor DCR/SRF included in frequency response.",
         "Directional current/voltage transfers use TI's ideal source/load terminations. Output impedance assumes the source is AC-shortened; CF then drops out.",
         "Noise amplitude and dBµV limit must use the same detector/amplitude convention; no peak-to-RMS conversion is assumed.",
         "Suggested CD=4C, RD+ESRd=√(L/C) reproduces Figure 13-1 starting values, not a documented TI internal optimizer. Re-evaluate chosen damping components."),
        tuple(warnings), equations="§13, equations 114–129")


def stage_import_values(tool_id: str, result: StageResult, *, control: str = "VMC", role: str = "main") -> dict:
    """Return only physically mapped inputs; never guess missing device/control data."""
    if not isinstance(result, StageResult) or result.design_id != result.inputs.design_id:
        raise ModelDomainError("Stage import requires a current, matching StageResult")
    if tool_id == "X01":
        if role != "main" or result.topology_id not in ("T01", "T02", "T03", "T05", "T06", "T07", "T08", "T09", "T10"):
            raise ModelDomainError("Hard-switch Q1 import supports Buck, Boost, IBB, 2-switch Buck-Boost, SEPIC, ZETA, Cuk and 1/2-switch Flyback; bridge/reset/SR/soft-switch commutation mappings require explicit manual inputs")
        wave = result.waveforms["Q1"].current_a
        conducting = wave.segments[0]
        if len(conducting.coefficients) > 2 or conducting.start_fraction != 0 or not math.isclose(conducting.end_fraction, result.duty, abs_tol=1e-10):
            raise ModelDomainError("Stage does not expose the expected Q1 on-interval ramp")
        minimum = conducting.coefficients[0]
        maximum = sum(conducting.coefficients)
        if minimum < 0 or maximum < 0:
            raise ModelDomainError("Hard-switch gate-charge import requires nonnegative commutation currents")
        return {"rms_current_a": wave.statistics()["rms"], "turn_on_current_a": minimum,
                "turn_off_current_a": maximum, "drain_voltage_v": result.stress["Q1"]["voltage_max_v"],
                "frequency_hz": result.inputs.fsw_hz}
    if tool_id == "X02":
        if control != "VMC" or result.topology_id != "T01":
            raise ModelDomainError("Only VMC Buck inductance has a defined load-step import; ΔI, ΔV, fc and PM must be entered")
        return {"inductance_h": result.inputs.inductance_h}
    if tool_id == "X03":
        raise ModelDomainError("Stage Cout RMS is broadband; cannot import it as a single sinusoid. Supply a measured/derived spectrum or enter single-frequency current independently")
    if tool_id == "X05":
        if result.topology_id != "T09":
            raise ModelDomainError("RCD import requires single-switch Flyback; other clamp arrangements are not equivalent")
        return {"peak_primary_current_a": result.stress["Q1"]["current_max_a"], "frequency_hz": result.inputs.fsw_hz,
                "reflected_voltage_v": result.inputs.turns_ratio_np_ns*(result.inputs.vout_v+result.inputs.diode_drop_v)}
    if tool_id == "X12":
        return {"input_voltage_v": result.inputs.vin_v, "input_current_a": result.metrics["input_current_mean_a"]}
    raise ModelDomainError(f"{tool_id} has no physically defined direct stage mapping; use explicit inputs")
