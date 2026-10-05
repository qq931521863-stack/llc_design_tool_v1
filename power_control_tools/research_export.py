"""Self-contained research scripts from completed, frozen loop analyses.

No analysis is rerun here. Mixed continuous/discrete loops are exported as their
original complex frequency samples, never silently fitted to rational models.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from llc_design.control.digital_loop import DigitalLoopAnalysis
    from pfc_design.control.analysis import PFCControlLabAnalysis


@dataclass(frozen=True)
class ResearchModel:
    """Immutable model; digital coefficients are ascending powers of z^-1."""

    name: str
    description: str
    domain: str
    input_unit: str
    output_unit: str
    sample_time_s: float | None
    numerator: tuple[float, ...]
    denominator: tuple[float, ...]
    response: tuple[complex, ...]
    source: str

    @property
    def rational(self) -> bool:
        return bool(self.denominator)


@dataclass(frozen=True)
class ResearchLoop:
    name: str
    plant: str
    controller: str
    open_loop: str
    closed_loop: str
    sensitivity: str
    forward: str
    sense: str


@dataclass(frozen=True)
class ResearchExport:
    """Detached snapshot: mutable source arrays/configuration cannot affect it."""

    title: str
    frequencies_hz: tuple[float, ...]
    models: tuple[ResearchModel, ...]
    loops: tuple[ResearchLoop, ...]
    metadata_json: str
    notes: tuple[str, ...]


_COMMON_NOTES = (
    "Snapshot of a completed analysis; metadata is provenance only. Editing metadata does not rebuild models.",
    "Exact means faithful to this linear analysis, not a validated hardware model.",
    "Continuous coefficients use descending powers of s; s=j*2*pi*f with f in Hz.",
    "Discrete coefficients use b0+b1*z^-1+... over a0+a1*z^-1+...; z=exp(j*2*pi*f*Ts). Leading zeros are delays.",
    "Mixed-domain models are exact complex samples ONLY on the exported frequency grid. No rational fitting, interpolation, or single-rate discretization is implied.",
    "Mixed-domain samples have no exact finite rational pole/zero set. Pole/zero plots are limited to rational blocks.",
    "Feedback is negative: L=forward*sense, T=L/(1+L), S=1/(1+L). T is the measured-output closed loop.",
    "Nonlinear saturation, soft-start, protection, commutation and control-ownership transitions are outside this small-signal model.",
)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, np.generic):
        return _jsonable(value.item())
    if isinstance(value, complex):
        return {"real": value.real, "imag": value.imag}
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _model(name: str, description: str, response: Any, *, domain: str = "mixed",
           numerator: Any = (), denominator: Any = (), sample_time_s: float | None = None,
           input_unit: str = "1", output_unit: str = "1", source: str = "") -> ResearchModel:
    num = tuple(float(v) for v in numerator)
    den = tuple(float(v) for v in denominator)
    values = tuple(complex(v) for v in response)
    if not np.all(np.isfinite(values)) or not np.all(np.isfinite(num + den)):
        raise ValueError(f"{name}: export requires finite coefficients and frequency samples")
    if den and (not num or not any(den)):
        raise ValueError(f"{name}: invalid rational model")
    if den and domain not in ("continuous", "discrete"):
        raise ValueError(f"{name}: a rational model needs a single time domain")
    if domain == "discrete" and (sample_time_s is None or sample_time_s <= 0):
        raise ValueError(f"{name}: discrete model needs a positive sample time")
    return ResearchModel(name, description, domain, input_unit, output_unit,
                         sample_time_s, num, den, values, source)


def _controller(name: str, controller: Any, response: Any, input_unit: str,
                output_unit: str, source: str) -> ResearchModel:
    return _model(name, controller.name + ": exact completed controller", response,
                  domain="discrete", numerator=controller.numerator,
                  denominator=controller.denominator, sample_time_s=controller.sample_time_s,
                  input_unit=input_unit, output_unit=output_unit, source=source)


def _bundle(title: str, frequencies: Any, models: list[ResearchModel],
            loops: list[ResearchLoop], metadata: dict[str, Any], notes: tuple[str, ...]) -> ResearchExport:
    f = tuple(float(v) for v in frequencies)
    if not f or not np.all(np.isfinite(f)) or np.any(np.asarray(f) <= 0) or np.any(np.diff(f) <= 0):
        raise ValueError("export frequency grid must be finite, positive and strictly increasing")
    if any(len(m.response) != len(f) for m in models):
        raise ValueError("export responses must match the completed analysis frequency grid")
    return ResearchExport(title, f, tuple(models), tuple(loops),
                          json.dumps(_jsonable(metadata), ensure_ascii=False, allow_nan=False),
                          _COMMON_NOTES + notes)


def build_llc_research_export(analysis: DigitalLoopAnalysis) -> ResearchExport:
    """Freeze an LLC result without reading GUI inputs or rebuilding its control loop."""
    a = analysis
    f, r, small = a.frequencies_hz, a.responses, a.small_signal
    plant, digital = small.continuous_transfer, small.discrete_plant
    measured_unit = "V" if a.analog_sense.normalize_to_engineering_units else "ADC input V"
    models = [
        _model("G_s", "LLC power stage (continuous)", r["power_stage"], domain="continuous",
               numerator=plant.numerator, denominator=plant.denominator,
               input_unit=plant.input_unit, output_unit=plant.output_unit,
               source="small_signal.continuous_transfer / responses.power_stage"),
        _model("G_z", "LLC power stage: stored ZOH discretization, separate from the mixed loop",
               digital.frequency_response(f), domain="discrete",
               numerator=np.concatenate((np.zeros(digital.input_delay_samples), digital.numerator)),
               denominator=digital.denominator, sample_time_s=digital.sample_time_s,
               input_unit=digital.input_unit, output_unit=digital.output_unit,
               source="small_signal.discrete_plant; explicit input_delay_samples included in numerator"),
        _controller("C_z", a.controller, r["controller"], measured_unit, "PCMD pu", a.controller_source),
        _model("Gfm_s", "Local FM gain times continuous LLC power stage", r["fm_power_stage"],
               domain="continuous", numerator=plant.numerator * a.fm_operating_point.gain_hz_per_pu,
               denominator=plant.denominator, input_unit="PCMD pu", output_unit="V",
               source="responses.fm_power_stage; local FM slope preserved"),
    ]
    analog_num, analog_den = a.analog_sense.normalized_continuous_tf()
    models.append(_model("H_s", "Calibrated analog sensing only", r["sense_analog_calibrated"],
                         domain="continuous", numerator=analog_num, denominator=analog_den,
                         input_unit="V", output_unit=measured_unit,
                         source="analog_sense.normalized_continuous_tf"))
    for name, key, description, in_unit, out_unit in (
        ("H", "sense_total", "Complete sensing with ADC aperture and intra-frame timing", "V", measured_unit),
        ("D", "delay_nominal", "Nominal application delay and optional ZOH", "PCMD pu", "PCMD pu"),
        ("L", "open_loop_nominal", "Nominal exact mixed-domain open loop", "1", "1"),
        ("T", "closed_loop_nominal", "Nominal measured-output closed loop", measured_unit, measured_unit),
        ("S", "sensitivity_nominal", "Nominal sensitivity", "1", "1"),
    ):
        models.append(_model(name, description, r[key], input_unit=in_unit, output_unit=out_unit,
                             source="responses." + key))
    forward = r["controller"] * r["fm_power_stage"] * r["delay_nominal"]
    models.append(_model("F", "Forward path C * Gfm * D (without sensing)", forward,
                         input_unit=measured_unit, output_unit="V", source="product of stored response blocks"))
    # Preserve the remaining source curves, including all delay envelopes.
    used = {"power_stage", "controller", "fm_power_stage", "sense_analog_calibrated",
            "sense_total", "delay_nominal", "open_loop_nominal", "closed_loop_nominal", "sensitivity_nominal"}
    for key, values in r.items():
        if key not in used:
            if key == "sense_analog_raw":
                in_unit, out_unit = "V", "ADC input V"
            elif key == "adc_sampling" or key.startswith("closed_loop_") and key != "closed_loop_output_impedance":
                in_unit = out_unit = measured_unit
            elif key.startswith("delay_"):
                in_unit = out_unit = "PCMD pu"
            elif key == "closed_loop_output_impedance":
                in_unit, out_unit = "A", "V"
            else:
                in_unit = out_unit = "1"
            models.append(_model(key, key.replace("_", " "), values,
                                 input_unit=in_unit, output_unit=out_unit,
                                 source="responses." + key))
    input_kind_note = ()
    if small.control_input_kind.value != "frequency_hz":
        input_kind_note = ("SOURCE MODEL CAVEAT: the LLC mixed-loop builder multiplies its power stage by an FM slope in Hz/pu even for a non-Hz control input. These samples preserve that source behavior; the Gfm_s and full-loop physical unit conversion is not validated. G_s retains its original input units.",)
    metadata = {
        "analysis_kind": "LLC DigitalLoopAnalysis", "controller_source": a.controller_source,
        "controller_config": a.controller_config, "design_spec": small.spec,
        "operating_point": small.operating_point, "plant_parameters": small.parameters,
        "control_input_kind": small.control_input_kind, "sample_time_s": a.controller.sample_time_s,
        "fm_lut": a.fm_lut, "fm_operating_point": a.fm_operating_point,
        "analog_sense": a.analog_sense, "adc_sampling": a.adc_sampling,
        "adc_sample_offsets_s": a.adc_sampling.sample_offsets_s,
        "command_timing": a.command_timing, "plant_input_delay_samples": digital.input_delay_samples,
        "warnings": a.warnings,
        "nominal_margins": a.margins_nominal_delay,
    }
    return _bundle("LLC voltage-loop research export", f, models,
                   [ResearchLoop("LLC voltage", "G_s", "C_z", "L", "T", "S", "F", "H")], metadata,
                   ("LLC L=C_z*Gfm_s*D*H. G_s is the power-stage input/output model; FM gain is a separate block.",
                    "LLC ADC samples use positive intra-frame phase offsets; command delay owns EOC, compute, PWM update and zero-event wait.",
                    "G_z is the stored ZOH power-stage model, not the full mixed-domain loop. The source all-discrete/Thiran loop approximation is not exported as exact L/T/S.") + input_kind_note)


def _pfc_analog_coefficients(sense: Any) -> tuple[list[float], np.ndarray]:
    gain = 1.0 if sense.normalize_to_engineering_units else sense.raw_dc_gain
    den = np.asarray([1.0])
    taus = [1.0 / (2.0 * math.pi * sense.amplifier_bandwidth_hz),
            sense.source_resistance_ohm * sense.shunt_capacitance_f,
            sense.output_resistance_ohm * sense.adc_capacitance_f,
            sense.second_resistance_ohm * sense.second_capacitance_f]
    for tau in taus:
        if tau > 0.0:
            den = np.convolve(den, [tau, 1.0])
    return [gain], den


def build_pfc_research_export(analysis: PFCControlLabAnalysis, loop: str = "both") -> ResearchExport:
    """Freeze current, voltage or both PFC loops, retaining their distinct rates."""
    if loop not in ("current", "voltage", "both"):
        raise ValueError("loop must be 'current', 'voltage' or 'both'")
    a, models, loops = analysis, [], []
    cfg, stage, f = a.config, a.config.power_stage, a.frequencies_hz
    if loop in ("current", "both"):
        r = a.current_loop.responses
        models.extend([
            _model("Gi_s", "CCM duty-to-current plant", r["plant_gid"], domain="continuous",
                   numerator=[stage.bus_voltage_v], denominator=[stage.boost_inductance_h, stage.equivalent_series_resistance_ohm],
                   input_unit="duty pu", output_unit="A", source="responses.plant_gid"),
            _controller("Ci_z", a.current_loop.controller, r["controller_ci"],
                        "A" if cfg.current_sense.normalize_to_engineering_units else "ADC input V", "duty pu",
                        "completed current_loop.controller"),
        ])
        loops.append(ResearchLoop("PFC current", "Gi_s", "Ci_z", "Li", "Ti", "Si", "Fi", "Hi"))
    if loop in ("voltage", "both"):
        r = a.voltage_loop.responses
        models.extend([
            _model("Gv", "Bus plant including the exact inner-current closed-loop samples", r["bus_plant_gvg"],
                   input_unit="conductance A/V", output_unit="V", source="responses.bus_plant_gvg"),
            _controller("Cv_z", a.voltage_loop.controller, r["controller_cv"],
                        "V" if cfg.vbus_sense.normalize_to_engineering_units else "ADC input V", "voltage-loop output",
                        "completed voltage_loop.controller"),
        ])
        # This is only the continuous bus factor; multiplying by the current
        # closed-loop response is required to recover the actual outer plant.
        load_slope = (2.0 * stage.output_power_w / stage.bus_voltage_v
                      if stage.load_model.value == "resistive" else 0.0)
        num = [stage.vin_rms_v**2 * stage.bus_capacitance_f * stage.bus_cap_esr_ohm,
               stage.vin_rms_v**2]
        den = [stage.bus_capacitance_f * stage.bus_voltage_v, load_slope]
        s = 2j * math.pi * f
        base_response = np.polyval(num, s) / np.polyval(den, s)
        models.append(_model("Gv_base_s", "Continuous bus factor ONLY, excludes inner-current closed loop",
                             base_response, domain="continuous", numerator=num, denominator=den,
                             input_unit="conductance A/V", output_unit="V", source="exact continuous factor in _bus_power_plant_response"))
        loops.append(ResearchLoop("PFC voltage", "Gv", "Cv_z", "Lv", "Tv", "Sv", "Fv", "Hv"))
    for short, which, result, sense in (
        ("i", "current", a.current_loop, cfg.current_sense),
        ("v", "voltage", a.voltage_loop, cfg.vbus_sense),
    ):
        if loop not in (which, "both"):
            continue
        r = result.responses
        controller_key = "controller_ci" if short == "i" else "controller_cv"
        plant_key = "plant_gid" if short == "i" else "bus_plant_gvg"
        closed_key = "closed_current_measured" if short == "i" else "closed_voltage"
        physical_unit = "A" if short == "i" else "V"
        measured_unit = physical_unit if sense.normalize_to_engineering_units else "ADC input V"
        signal_units = {
            f"open_{which}": ("1", "1"), closed_key: (measured_unit, measured_unit),
            f"sensitivity_{which}": ("1", "1"), f"forward_{which}": (measured_unit, physical_unit),
            "sense_h" + short: (physical_unit, measured_unit),
            "sense_h" + short + "_adc": (measured_unit, measured_unit),
            "indu_comp_gain": ("duty pu", "duty pu"), "pwm_zoh": ("duty pu", "duty pu"),
            "closed_current_actual": (measured_unit, "A"),
            "current_closed_for_outer": ("A" if cfg.current_sense.normalize_to_engineering_units else "ADC input V", "A"),
            "amc_vff": ("voltage-loop output", "conductance A/V"),
        }
        mapping = {f"open_{which}": "L" + short, closed_key: "T" + short,
                   f"sensitivity_{which}": "S" + short, f"forward_{which}": "F" + short,
                   "sense_h" + short: "H" + short}
        for key, values in r.items():
            if key in (controller_key, plant_key, "sense_h" + short + "_analog"):
                continue
            in_unit, out_unit = signal_units[key]
            models.append(_model(mapping.get(key, key), key.replace("_", " "), values,
                                 input_unit=in_unit, output_unit=out_unit,
                                 source=which + "_loop.responses." + key))
        num, den = _pfc_analog_coefficients(sense)
        models.append(_model("H" + short + "_s", "Calibrated analog sensing only", r["sense_h" + short + "_analog"],
                             domain="continuous", numerator=num, denominator=den,
                             input_unit="A" if short == "i" else "V",
                             output_unit=("A" if short == "i" else "V") if sense.normalize_to_engineering_units else "ADC input V",
                             source=which + "_sense analog RC/amplifier configuration"))
    return _bundle("PFC " + loop + "-loop research export", f, models, loops,
                   {"analysis_kind": "PFCControlLabAnalysis", "selected_loop": loop,
                    "config": cfg, "operating_point": a.operating_point,
                    "current_sample_time_s": a.current_loop.controller.sample_time_s,
                    "voltage_sample_time_s": a.voltage_loop.controller.sample_time_s,
                    "amc_sample_time_s": 1.0 / cfg.firmware.amc_rate_hz,
                    "samples_above_controller_nyquist": {
                        "current": int(np.count_nonzero(f > 0.5 / a.current_loop.controller.sample_time_s)),
                        "voltage": int(np.count_nonzero(f > 0.5 / a.voltage_loop.controller.sample_time_s)),
                        "amc": int(np.count_nonzero(f > 0.5 * cfg.firmware.amc_rate_hz)),
                    }, "warnings": a.warnings},
                   ("Li=Ci_z*indu_comp_gain*pwm_zoh*Gi_s*Hi; Fi excludes Hi.",
                    "Gv=Gv_base_s*current_closed_for_outer; Lv=Cv_z*amc_vff*Gv*Hv.",
                    "PFC current, voltage, sensing and AMC layers can have different sample times; no exact single-rate rational full loop is claimed.",
                    "PFC ADC aperture, SOC offsets, sensor timing ZOH and firmware PWM/AMC ZOH are retained exactly as the source analysis defines them.",
                    "The original frequency grid is not clipped at individual Nyquist frequencies. Samples above a layer's Nyquist rate are source multirate evaluations, not an independently valid unique discrete system; counts are recorded in metadata."))


def _payload(bundle: ResearchExport) -> dict[str, Any]:
    indices = sorted(set(np.linspace(0, len(bundle.frequencies_hz) - 1, min(5, len(bundle.frequencies_hz)), dtype=int).tolist()))
    return {
        "title": bundle.title, "metadata": json.loads(bundle.metadata_json), "notes": bundle.notes,
        "frequencies_hz": bundle.frequencies_hz, "reference_indices": indices,
        "loops": [asdict(loop) for loop in bundle.loops],
        "models": [{**{key: value for key, value in asdict(m).items() if key != "response"},
                    "response_real": [z.real for z in m.response],
                    "response_imag": [z.imag for z in m.response],
                    "reference_real": [m.response[i].real for i in indices],
                    "reference_imag": [m.response[i].imag for i in indices]}
                   for m in bundle.models],
    }


def render_python(bundle: ResearchExport) -> str:
    """Render a standalone Python 3 script (NumPy, SciPy, Matplotlib only)."""
    payload = json.dumps(_payload(bundle), ensure_ascii=True, allow_nan=False, indent=2)
    # Quoted individual lines avoid executable interpolation or triple-quote
    # injection through user-defined controller labels/provenance strings.
    literal = "\n".join("    " + repr(line + "\n") for line in payload.splitlines())
    return _PYTHON_HEADER + "\nDATA = json.loads(\n" + literal + "\n)\n" + _PYTHON_BODY


_PYTHON_HEADER = '''#!/usr/bin/env python3
"""Standalone completed-analysis research export.
Run: python exported_loop.py
Headless check: MPLBACKEND=Agg python exported_loop.py
Requires numpy, scipy, matplotlib; no toolkit imports or external data files.
Metadata is provenance only: editing it does not rebuild coefficients/samples.
"""
import json
import numpy as np
from scipy import signal
import matplotlib.pyplot as plt
'''

_PYTHON_BODY = '''
metadata = DATA["metadata"]
frequencies_hz = np.asarray(DATA["frequencies_hz"], dtype=float)
omega_rad_s = 2.0 * np.pi * frequencies_hz
reference_indices = np.asarray(DATA["reference_indices"], dtype=int)
model_info = {item["name"]: item for item in DATA["models"]}
responses = {name: np.asarray(item["response_real"]) + 1j * np.asarray(item["response_imag"])
             for name, item in model_info.items()}
models = {}  # Rational SciPy TF objects only. Mixed models remain complex samples.
poles, zeros = {}, {}


def evaluate_rational(name, frequency_hz):
    """Evaluate original coefficient orientation without changing model domain."""
    item = model_info[name]
    if not item["denominator"]:
        raise ValueError(name + " contains frequency samples only, not a rational TF")
    f = np.asarray(frequency_hz, dtype=float)
    if item["domain"] == "continuous":
        point = 2j * np.pi * f
        return np.polyval(item["numerator"], point) / np.polyval(item["denominator"], point)
    z = np.exp(2j * np.pi * f * item["sample_time_s"])
    # Sum z^-k explicitly, preserving b0 and every leading delay zero.
    num = sum(b * z**(-k) for k, b in enumerate(item["numerator"]))
    den = sum(a * z**(-k) for k, a in enumerate(item["denominator"]))
    return num / den


def assert_response(actual, expected, name):
    # Normalize by the reference response scale: a tiny real gain must not
    # disappear under a fixed absolute tolerance.
    scale = max(float(np.max(np.abs(expected))), np.finfo(float).tiny)
    np.testing.assert_allclose(np.asarray(actual) / scale, np.asarray(expected) / scale,
                               rtol=2e-6, atol=5e-13, err_msg=name)


def exact_roots(coefficients):
    nonzero = np.trim_zeros(np.asarray(coefficients, dtype=float), "f")
    if len(nonzero) <= 1:
        return np.asarray([], dtype=complex)
    return np.roots(nonzero / np.max(np.abs(nonzero))).astype(complex)


for name, item in model_info.items():
    reference = np.asarray(item["reference_real"]) + 1j * np.asarray(item["reference_imag"])
    assert_response(responses[name][reference_indices], reference, name + " sample reference")
    if not item["denominator"]:
        continue
    b, a = np.asarray(item["numerator"]), np.asarray(item["denominator"])
    if item["domain"] == "discrete":
        # SciPy TF uses descending z powers, not z^-1. Multiply BOTH original
        # polynomials by the same z^order; append zeros, never reverse arrays.
        order = max(len(b), len(a))
        bz = np.pad(b, (0, order - len(b)))
        az = np.pad(a, (0, order - len(a)))
        models[name] = signal.TransferFunction([1.0], [1.0], dt=item["sample_time_s"])
        # The constructor's normalization trims small nonzero coefficients.
        # Public setters preserve them. Strip exact leading zeros only AFTER
        # common-order padding, so integer delays remain in the z denominator.
        models[name].num = np.trim_zeros(bz, "f") if np.any(bz) else np.asarray([0.0])
        models[name].den = np.trim_zeros(az, "f")
        _, check = signal.dfreqresp(models[name], w=omega_rad_s[reference_indices] * item["sample_time_s"])
    else:
        models[name] = signal.TransferFunction([1.0], [1.0])
        models[name].num = np.trim_zeros(b, "f") if np.any(b) else np.asarray([0.0])
        models[name].den = np.trim_zeros(a, "f")
        _, check = signal.freqresp(models[name], w=omega_rad_s[reference_indices])
    assert_response(evaluate_rational(name, frequencies_hz[reference_indices]), reference,
                    name + " coefficient/reference mismatch")
    assert_response(check, reference, name + " SciPy TF orientation/reference mismatch")
    # Use these dictionaries for P/Z: SciPy tf2zpk/.zeros can discard very
    # small numerator coefficients while normalizing a transfer function.
    poles[name], zeros[name] = exact_roots(models[name].den), exact_roots(models[name].num)

for loop in DATA["loops"]:
    L = responses[loop["open_loop"]]
    assert_response(responses[loop["closed_loop"]], L / (1.0 + L), "T feedback identity")
    assert_response(responses[loop["sensitivity"]], 1.0 / (1.0 + L), "S feedback identity")
    assert_response(L, responses[loop["forward"]] * responses[loop["sense"]], "L feedback identity")

# Canonical G/C/L/T/S per loop. A value is either a rational SciPy model or
# exact complex samples; domains/units and sample times live in model_info.
loop_models = {loop["name"]: {symbol: models.get(loop[key], responses[loop[key]])
               for symbol, key in (("G", "plant"), ("C", "controller"), ("L", "open_loop"),
                                   ("T", "closed_loop"), ("S", "sensitivity"))}
               for loop in DATA["loops"]}


def plot_results():
    for loop in DATA["loops"]:
        fig, axes = plt.subplots(2, 1, sharex=True, num=loop["name"] + " Bode")
        for key in ("plant", "controller", "open_loop", "closed_loop", "sensitivity"):
            name = loop[key]
            response = responses[name]
            axes[0].semilogx(frequencies_hz, 20 * np.log10(np.maximum(np.abs(response), np.finfo(float).tiny)), label=name)
            axes[1].semilogx(frequencies_hz, np.unwrap(np.angle(response)) * 180 / np.pi, label=name)
        axes[0].set_ylabel("Magnitude (dB; units in model_info)")
        axes[1].set_ylabel("Phase (deg)")
        axes[1].set_xlabel("Frequency (Hz)")
        axes[0].set_title(loop["name"] + " | exact completed-analysis samples")
        for ax in axes:
            ax.grid(True, which="both")
            ax.legend()
        fig.tight_layout()
    fig, axes = plt.subplots(1, 2, num="Rational block poles and zeros")
    for name, model in models.items():
        ax = axes[0 if model_info[name]["domain"] == "continuous" else 1]
        ax.scatter(poles[name].real, poles[name].imag, marker="x", label=name + " poles")
        ax.scatter(zeros[name].real, zeros[name].imag, marker="o", facecolors="none", edgecolors="C0", label=name + " zeros")
    theta = np.linspace(0, 2 * np.pi, 361)
    axes[1].plot(np.cos(theta), np.sin(theta), "k--", linewidth=0.8)
    for ax, title in zip(axes, ("Continuous s plane (rad/s)", "Discrete z plane")):
        ax.axhline(0, color="0.6", linewidth=0.5)
        ax.axvline(0, color="0.6", linewidth=0.5)
        ax.set_title(title)
        ax.set_xlabel("Real")
        ax.set_ylabel("Imaginary")
        ax.grid(True)
        ax.legend(fontsize="small")
    axes[1].set_aspect("equal", adjustable="datalim")
    fig.tight_layout()


if __name__ == "__main__":
    print(DATA["title"])
    for note in DATA["notes"]:
        print("- " + note)
    print("Reference and feedback identity checks passed.")
    plot_results()
    plt.show()
'''


def _matlab_string(value: str) -> str:
    return "'" + value.replace("'", "''").replace("\r", " ").replace("\n", " ") + "'"


def _matlab_vector(values: Any) -> str:
    # Line continuations keep the file editable even for dense analysis grids.
    tokens = [format(float(v), ".17g") for v in values]
    if not tokens:
        return "[]"
    return "[" + " ...\n    ".join(" ".join(tokens[i:i + 6]) for i in range(0, len(tokens), 6)) + "]"


def _matlab_metadata(path: str, value: Any) -> list[str]:
    if isinstance(value, dict):
        lines = [path + " = struct();"]
        for key, item in value.items():
            lines.extend(_matlab_metadata(path + ".(" + _matlab_string(key) + ")", item))
        return lines
    if isinstance(value, list):
        if all(isinstance(item, (float, int)) for item in value):
            return [path + " = " + _matlab_vector(value) + ";"]
        lines = [path + " = cell(1," + str(len(value)) + ");"]
        for index, item in enumerate(value, 1):
            lines.extend(_matlab_metadata(path + "{" + str(index) + "}", item))
        return lines
    if value is None:
        text = "[]"
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (int, float)):
        text = format(value, ".17g")
    else:
        text = _matlab_string(str(value))
    return [path + " = " + text + ";"]


def render_matlab(bundle: ResearchExport) -> str:
    """Render an R2014b-compatible script requiring Control System Toolbox."""
    data = _payload(bundle)
    lines = ["% " + bundle.title, "% MATLAB R2014b+; requires Control System Toolbox.",
             "% Standalone script. No JSON decoder, local functions, or external data files."]
    lines.extend("% " + note for note in bundle.notes)
    lines.extend(["", "clear; close all; clc;", "models = struct(); responses = struct(); model_info = struct();",
                  "poles = struct(); zeros = struct(); loop_models = struct();"])
    lines.extend(_matlab_metadata("metadata", data["metadata"]))
    lines.extend(["frequencies_hz = " + _matlab_vector(bundle.frequencies_hz) + ";",
                  "omega_rad_s = 2*pi*frequencies_hz;",
                  "reference_indices = " + _matlab_vector([i + 1 for i in data["reference_indices"]]) + ";"])
    for model, item in zip(bundle.models, data["models"]):
        n = model.name
        lines.extend(["", "% " + n + ": " + model.description.replace("\n", " ").replace("\r", " "),
                      "responses." + n + " = " + _matlab_vector(item["response_real"]) + " + 1i*" + _matlab_vector(item["response_imag"]) + ";"])
        info = {"domain": model.domain, "sample_time_s": model.sample_time_s,
                "input_unit": model.input_unit, "output_unit": model.output_unit,
                "source": model.source, "description": model.description,
                "numerator": list(model.numerator), "denominator": list(model.denominator)}
        lines.extend(_matlab_metadata("model_info." + n, info))
        lines.extend(["reference = " + _matlab_vector(item["reference_real"]) + " + 1i*" + _matlab_vector(item["reference_imag"]) + ";",
                      "assert(all(abs(responses." + n + "(reference_indices)-reference) <= 5e-13*max(max(abs(reference)),realmin)+2e-6*abs(reference)), 'Sample reference mismatch: " + n + "');"])
        if model.rational:
            b, a = _matlab_vector(model.numerator), _matlab_vector(model.denominator)
            if model.domain == "discrete":
                args = ", " + format(model.sample_time_s, ".17g") + ", 'Variable', 'z^-1'"
            else:
                args = ""
            lines.extend([n + " = tf(" + b + ", " + a + args + ");",
                          "models." + n + " = " + n + ";",
                          "check = reshape(freqresp(" + n + ", omega_rad_s(reference_indices)), 1, []);",
                          "assert(all(abs(check-reference) <= 5e-13*max(max(abs(reference)),realmin)+2e-6*abs(reference)), 'TF coefficient/reference mismatch: " + n + "');",
                          "poles." + n + " = pole(" + n + "); zeros." + n + " = zero(" + n + ");"])
        else:
            lines.extend(["% Frequency data only. FRD uses Ts=0 as a container convention, not a continuous/full-loop dynamics claim.",
                          n + " = frd(reshape(responses." + n + ",1,1,[]), omega_rad_s, 0);",
                          "models." + n + " = " + n + ";"])
    for index, loop in enumerate(bundle.loops, 1):
        fields = {"G": loop.plant, "C": loop.controller, "L": loop.open_loop,
                  "T": loop.closed_loop, "S": loop.sensitivity}
        for symbol, name in fields.items():
            lines.append("loop_models.loop" + str(index) + "." + symbol + " = " + name + ";")
        lines.extend(["L_samples = responses." + loop.open_loop + ";",
                      "assert(all(abs(responses." + loop.closed_loop + " - L_samples./(1+L_samples)) <= 1e-11+2e-7*abs(responses." + loop.closed_loop + ")), 'T feedback identity mismatch');",
                      "assert(all(abs(responses." + loop.sensitivity + " - 1./(1+L_samples)) <= 1e-11+2e-7*abs(responses." + loop.sensitivity + ")), 'S feedback identity mismatch');",
                      "assert(all(abs(L_samples-responses." + loop.forward + ".*responses." + loop.sense + ") <= 1e-11+2e-7*abs(L_samples)), 'L feedback identity mismatch');",
                      "figure('Name', " + _matlab_string(loop.name + " Bode") + ");"])
        names = [loop.plant, loop.controller, loop.open_loop, loop.closed_loop, loop.sensitivity]
        for pane in (1, 2):
            lines.append("subplot(2,1," + str(pane) + "); hold on;")
            for name in names:
                expression = ("20*log10(max(abs(responses." + name + "),realmin))" if pane == 1
                              else "unwrap(angle(responses." + name + "))*180/pi")
                lines.append("semilogx(frequencies_hz, " + expression + ");")
            lines.extend(["set(gca,'XScale','log'); grid on;",
                          "legend(" + ", ".join(_matlab_string(n) for n in names) + ", 'Interpreter', 'none', 'Location', 'best');",
                          "ylabel(" + _matlab_string("Magnitude (dB; units in model_info)" if pane == 1 else "Phase (deg)") + ");"])
        lines.append("xlabel('Frequency (Hz)');")
    lines.append("figure('Name','Rational block poles and zeros');")
    for pane, domain in ((1, "continuous"), (2, "discrete")):
        lines.append("subplot(1,2," + str(pane) + "); hold on;")
        legends = []
        for model in bundle.models:
            if not model.rational or model.domain != domain:
                continue
            n = model.name
            lines.extend(["plot(real(poles." + n + "), imag(poles." + n + "), 'x');",
                          "plot(real(zeros." + n + "), imag(zeros." + n + "), 'o');"])
            legends.extend([n + " poles", n + " zeros"])
        if legends:
            lines.append("legend(" + ", ".join(_matlab_string(v) for v in legends) + ", 'Interpreter', 'none', 'Location', 'best');")
        if domain == "discrete":
            lines.append("theta = linspace(0,2*pi,361); plot(cos(theta),sin(theta),'k--'); axis equal;")
        lines.extend(["grid on; xlabel('Real'); ylabel('Imaginary');",
                      "title(" + _matlab_string("Continuous s plane (rad/s)" if pane == 1 else "Discrete z plane") + ");"])
    lines.append("disp('Reference and feedback identity checks passed. Mixed loops are frequency data only.');")
    return "\n".join(lines) + "\n"


def export_research_script(bundle: ResearchExport, path: str | Path, language: str = "python") -> Path:
    """Write one self-contained UTF-8 script; callers choose the destination."""
    renderers = {"python": render_python, "matlab": render_matlab}
    if language not in renderers:
        raise ValueError("language must be 'python' or 'matlab'")
    output = Path(path)
    output.write_text(renderers[language](bundle), encoding="utf-8")
    return output


__all__ = [
    "ResearchExport",
    "ResearchLoop",
    "ResearchModel",
    "build_llc_research_export",
    "build_pfc_research_export",
    "export_research_script",
    "render_matlab",
    "render_python",
]
