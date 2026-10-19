"""CCM analog-loop calculator with physical compensation networks.

SI inputs; frequency grids are in Hz, complex evaluation uses s = j 2 pi f.
The returned loop is the *positive return ratio* L for 1 + L feedback.  The
physical inverting error-amplifier sign is removed exactly once.  No digital
ADC, ZOH, computation or PWM delay is implied by the peak-CMC sampling model.

Reference: TI SLVUBB4B, sections 12.2.1--12.2.9.  Circuit-node equations take
precedence over typographical errors in the printed finite-amplifier formula.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import numpy as np
from scipy.optimize import least_squares

from power_control_tools.controllers import design_controller
from power_control_tools.fra.analysis import LoopStabilityResult, analyze_loop_response
from power_control_tools.models import ControllerKind

TI_GUIDE = "https://www.ti.com/lit/ug/slvubb4b/slvubb4b.pdf"
CMC_REFERENCE = "https://www.ti.com/lit/an/snva542/snva542.pdf"


@dataclass(frozen=True)
class LoopPlant:
    topology: str = "buck"
    control_mode: str = "vmc"
    vin_v: float = 12.0
    vout_v: float = 5.0
    iout_a: float = 3.0
    fsw_hz: float = 100_000.0
    inductance_h: float = 47e-6
    dcr_ohm: float = 0.0
    capacitance_f: float = 100e-6
    esr_ohm: float = 0.02
    capacitance2_f: float = 0.0
    esr2_ohm: float = 0.0
    turns_ratio: float = 1.0  # Np/Ns; Forward L is secondary, Flyback L is primary.
    ramp_v: float = 1.0
    sense_ohm: float = 0.1
    slope_v: float = 0.0  # Ramp excursion over one complete switching period.
    slope_mode: str = "fixed"  # fixed (Vslope) or multiplier (SLM).
    sense_gain: float = 1.0
    slope_multiplier: float = 1.0


@dataclass(frozen=True)
class Compensator:
    network: str = "type_ii"
    r1_ohm: float = 10_000.0  # RFBT
    r2_ohm: float = 10_000.0  # RCOMP; zero selects the Type-I limit.
    r3_ohm: float = 1_000.0  # RFF (Type III only)
    c1_f: float = 100e-9  # CCOMP
    c2_f: float = 1e-9  # CHF
    c3_f: float = 1e-9  # CFF (Type III only)
    r_lower_ohm: float = 10_000.0
    cff_f: float = 0.0  # Optional CFF directly across RFBT for Type II / OTA.
    open_loop_gain_db: float = 100.0
    gbw_hz: float = 10e6
    gm_s: float = 0.001
    output_resistance_ohm: float | None = None  # OTA: None => AOL/gm.
    ctr: float = 1.0
    opto_pole_hz: float = 100_000.0
    rled_ohm: float = 1_000.0
    rpullup_ohm: float = 10_000.0


INTEGRATOR_BRANCH_NETWORKS = frozenset({"type_ii", "isolated_zener", "isolated_inner"})
INTEGRATOR_BRANCH_NOTE = (
    "积分支路设置：RCOMP=0、CHF=0、Cff=0，保留反馈分压、CCOMP 和有限误差放大器。"
    "TI SLVUBB4B 第34页的 RCOMP=RFBT、CHF=CCOMP 提示并非该网络的严格积分极限；本计算按实际节点方程。"
    "隔离网络仍含光耦增益及极点；隔离内环还保留直接 +1 通路，整个网络不等同纯积分器。"
)


def integrator_branch_settings(compensator: Compensator) -> Compensator:
    """Select the exact voltage-EA integrator branch, not a sixth network.

    This changes only the three physical branch components. All finite-EA,
    feedback-divider and isolated-path parameters remain exactly unchanged.
    """
    if compensator.network not in INTEGRATOR_BRANCH_NETWORKS:
        raise ValueError("积分支路设置仅适用于 Type II 电压误差放大器和两种隔离 Type II 网络")
    _positive("c1_f", compensator.c1_f)
    return replace(compensator, r2_ohm=0.0, c2_f=0.0, cff_f=0.0)


@dataclass(frozen=True)
class LoopResult:
    frequency_hz: np.ndarray
    plant_response: np.ndarray
    compensator_response: np.ndarray
    amplifier_response: np.ndarray
    loop_response: np.ndarray
    stability: LoopStabilityResult
    warnings: tuple[str, ...] = ()
    metrics: dict[str, float] = field(default_factory=dict)
    sources: tuple[str, ...] = (TI_GUIDE, CMC_REFERENCE)


@dataclass(frozen=True)
class SynthesisResult:
    compensator: Compensator | None
    result: LoopResult | None
    achievable: bool
    reason: str
    target_crossover_hz: float
    target_phase_margin_deg: float


def _positive(name: str, value: float, *, zero: bool = False) -> None:
    if not math.isfinite(value) or value < 0.0 or (not zero and value == 0.0):
        raise ValueError(f"{name} must be finite and {'non-negative' if zero else 'positive'}")


def _frequency_grid(plant: LoopPlant, frequency_hz=None) -> np.ndarray:
    if frequency_hz is None:
        frequency_hz = np.geomspace(max(0.01, plant.fsw_hz * 1e-6), plant.fsw_hz * 0.499, 1600)
    f = np.asarray(frequency_hz, dtype=float)
    if f.ndim != 1 or f.size < 2 or not np.all(np.isfinite(f)) or np.any(f <= 0) or np.any(np.diff(f) <= 0):
        raise ValueError("Frequency grid must contain at least two finite positive increasing Hz values")
    if f[-1] > plant.fsw_hz / 2 * (1 + 1e-12):
        raise ValueError("Analog averaged/sampled model is limited to fsw/2; frequencies above it are unsupported")
    return f


def _plant_operating_point(p: LoopPlant) -> dict[str, float]:
    for name in ("vin_v", "iout_a", "fsw_hz", "inductance_h", "capacitance_f", "turns_ratio"):
        _positive(name, getattr(p, name))
    for name in ("dcr_ohm", "esr_ohm", "capacitance2_f", "esr2_ohm"):
        _positive(name, getattr(p, name), zero=True)
    if not math.isfinite(p.vout_v) or p.vout_v == 0:
        raise ValueError("vout_v must be finite and nonzero")
    if p.topology not in ("buck", "boost", "inverting_buck_boost", "forward", "flyback"):
        raise ValueError("Unsupported analog-loop topology")
    if p.control_mode not in ("vmc", "peak_cmc"):
        raise ValueError("control_mode must be vmc or peak_cmc")
    if p.control_mode == "vmc" and p.topology != "buck":
        raise ValueError("Only Buck VMC is in the analog-loop baseline")
    if (p.topology == "inverting_buck_boost") != (p.vout_v < 0):
        raise ValueError("Only inverting_buck_boost requires a negative signed output voltage")
    v = abs(p.vout_v)
    n = p.turns_ratio
    r = v / p.iout_a
    if p.topology == "buck":
        d = v / p.vin_v
        iavg = p.iout_a
        ripple = (p.vin_v - v) * d / (p.inductance_h * p.fsw_hz)
    elif p.topology == "forward":
        d = n * v / p.vin_v
        iavg = p.iout_a
        ripple = (p.vin_v / n - v) * d / (p.inductance_h * p.fsw_hz)
    elif p.topology == "boost":
        d = 1 - p.vin_v / v
        iavg = p.iout_a / (1 - d)
        ripple = p.vin_v * d / (p.inductance_h * p.fsw_hz)
    else:
        v_referred = n * v if p.topology == "flyback" else v
        d = v_referred / (p.vin_v + v_referred)
        iavg = p.iout_a / ((n if p.topology == "flyback" else 1) * (1 - d))
        ripple = p.vin_v * d / (p.inductance_h * p.fsw_hz)
    if not 0 < d < 1:
        raise ValueError("The requested conversion ratio has no interior duty-cycle operating point")
    if iavg - ripple / 2 <= max(1e-12, iavg * 1e-10):
        raise ValueError("Analog-loop baseline requires strict CCM; boundary/DCM points are unsupported")
    return {"duty_cycle": d, "load_resistance_ohm": r, "inductor_average_a": iavg,
            "inductor_ripple_pp_a": ripple, "inductor_minimum_a": iavg - ripple / 2}


def output_impedance(p: LoopPlant, frequency_hz) -> np.ndarray:
    """Load in parallel with two effective C+ESR branches (TI equation 29)."""
    s = 2j * np.pi * np.asarray(frequency_hz, dtype=float)
    y = p.iout_a / abs(p.vout_v) + s * p.capacitance_f / (1 + s * p.capacitance_f * p.esr_ohm)
    if p.capacitance2_f:
        y = y + s * p.capacitance2_f / (1 + s * p.capacitance2_f * p.esr2_ohm)
    return 1 / y


def plant_frequency_response(p: LoopPlant, frequency_hz) -> tuple[np.ndarray, dict[str, float], tuple[str, ...]]:
    """Return COMP-to-output-magnitude response; the operating point is ideal.

    DCR is a dynamic series loss, as in TI's stated CCM approximation. DC
    conversion and CCM boundary use ideal volt-second balance, not an implicit
    efficiency model. Forward and Flyback refer impedances/current sensing to
    a consistent winding before evaluating their non-isolated equivalent.
    """
    m = _plant_operating_point(p)
    f = np.asarray(frequency_hz, dtype=float)
    s = 2j * np.pi * f
    z = output_impedance(p, f)
    zl = s * p.inductance_h + p.dcr_ohm
    warnings = ["CCM averaged model; ideal DC duty/boundary, effective output capacitance at applied bias required."]
    if p.dcr_ohm:
        warnings.append("DCR enters small-signal impedance; DC duty and CCM boundary do not include copper voltage drop.")
    if p.control_mode == "vmc":
        _positive("ramp_v", p.ramp_v)
        return p.vin_v / p.ramp_v * z / (zl + z), m, tuple(warnings)
    _positive("sense_ohm", p.sense_ohm)
    _positive("sense_gain", p.sense_gain)
    _positive("slope_v", p.slope_v, zero=True)
    _positive("slope_multiplier", p.slope_multiplier, zero=True)
    if p.slope_mode not in ("fixed", "multiplier"):
        raise ValueError("slope_mode must be fixed or multiplier")
    d = m["duty_cycle"]
    r = m["load_resistance_ohm"]
    ri = p.sense_ohm * p.sense_gain
    vin = p.vin_v
    vout = abs(p.vout_v)
    output_scale = 1.0
    buck_equivalent = p.topology in ("buck", "forward")
    if p.topology == "forward":
        vin /= p.turns_ratio
        ri /= p.turns_ratio
        warnings.append("Forward: primary current sensing referred to secondary; magnetizing-current contribution is neglected.")
    elif p.topology == "flyback":
        r *= p.turns_ratio ** 2
        z *= p.turns_ratio ** 2
        vout *= p.turns_ratio
        output_scale = p.turns_ratio
        warnings.append("Flyback: primary-referred buck-boost model; leakage, demagnetization parasitics and optocoupler bias limits are not modeled.")
    up_v = vin - vout if buck_equivalent else vin
    down_v = vout if p.topology != "boost" else vout - vin
    sn = ri * up_v / p.inductance_h
    down_slope = ri * down_v / p.inductance_h
    # SLM follows the TI definition, including Vout rather than Vout-Vin for Boost.
    se = p.slope_v * p.fsw_hz if p.slope_mode == "fixed" else p.slope_multiplier * vout * ri / p.inductance_h
    slope_v = se / p.fsw_hz
    inv_q = np.pi * ((1 + se / sn) * (1 - d) - 0.5)
    wl = np.pi * p.fsw_hz
    h = 1 + s * inv_q / wl + (s / wl) ** 2
    sample_multiplier = (se - down_slope) / (se + sn)
    # SNVA542 Appendix D (D11--D14) uses the active-to-passive switch
    # voltage Vap: Buck Vin, Boost Vout, Buck-Boost Vin+|Vout|.
    # SLVUBB4B equation 43 prints Vin for Boost; that fails the independent
    # derivative of Vc=Ri*(Vout**2/(R*Vin)+Vin*D/(2*L*fs))+Vslope*D.
    ramp_reference = vin if buck_equivalent else (vout if p.topology == "boost" else vin + vout)
    if p.topology == "boost":
        warnings.append("Boost ramp normalization uses Vout (TI SNVA542 D11-D14 and DC peak-condition derivative), correcting Vin printed in SLVUBB4B equation 43.")
    # Factoring the TI equations by 1/Km keeps D=.5, Vslope=0 finite.
    inv_km = (0.5 - d) * ri / (p.fsw_hz * p.inductance_h) + slope_v / ramp_reference
    if buck_equivalent:
        denominator = inv_km * (zl + z) + ri * h
        if np.any(np.abs(denominator) == 0):
            raise ValueError("Current-loop sampling pole is singular on this frequency grid; increase slope compensation")
        response = z / denominator
    else:
        buck_boost = p.topology != "boost"
        dz = d if buck_boost else 1.0
        rhp = (1 - d) * (1 - dz * zl / ((1 - d) ** 2 * r))
        k = 0.5 * ri / (p.fsw_hz * p.inductance_h) * d * (1 - d)
        denominator = (inv_km * ((1 - d) ** 2 + zl / z)
                       + ri * h * (dz / r + 1 / z) + k * rhp)
        response = rhp / denominator / output_scale
        wz = ((1 - d) ** 2 * r / dz - p.dcr_ohm) / p.inductance_h
        m["rhp_zero_hz"] = wz / (2 * np.pi)
        if wz <= 0:
            raise ValueError("DCR eliminates the positive RHP zero in the stated ideal operating model; use a loss-aware steady-state model")
    m.update({"effective_sense_ohm": ri, "effective_slope_v": slope_v,
              "natural_current_slope_v_per_s": sn, "compensation_slope_v_per_s": se,
              "sampling_damping_inverse_q": float(inv_q), "sampled_current_pole": sample_multiplier,
              "sampling_frequency_hz": p.fsw_hz / 2})
    warnings.append("Peak-CMC sampling term models current-loop sampled dynamics, not MCU or ADC delay.")
    if abs(sample_multiplier) >= 1 - 1e-12:
        warnings.append("UNSTABLE_OR_MARGINAL_SUBHARMONIC: slope compensation is insufficient; voltage-loop margins cannot establish stability.")
    if p.topology == "inverting_buck_boost":
        warnings.append("Inverting converter response uses output magnitude: feedback hardware must provide the required polarity/level shift.")
    return response, m, tuple(warnings)


def compensator_frequency_response(c: Compensator, frequency_hz) -> tuple[np.ndarray, np.ndarray, tuple[str, ...]]:
    """Physical R/C network, finite amplifier gain, positive return-ratio sign.

    The exact inverting-controller implementation is reused. Nodal analysis
    supplies its finite-gain correction: C/(1 + (1+Zf*(Yin+Ybottom))/A).
    The printed addition in TI equations 77/100/107 does not recover the
    ideal-amplifier limit and is deliberately not copied.
    """
    if c.network not in ("type_ii", "type_iii", "type_ii_gm", "isolated_zener", "isolated_inner"):
        raise ValueError("Unsupported compensation network")
    for name in ("r1_ohm", "r_lower_ohm", "c1_f", "gbw_hz"):
        _positive(name, getattr(c, name))
    for name in ("r2_ohm", "c2_f", "cff_f"):
        _positive(name, getattr(c, name), zero=True)
    if not math.isfinite(c.open_loop_gain_db) or not 0 < c.open_loop_gain_db <= 300:
        raise ValueError("open_loop_gain_db must be in (0, 300]")
    s = 2j * np.pi * np.asarray(frequency_hz, dtype=float)
    a0 = 10 ** (c.open_loop_gain_db / 20)
    a = 1 / (1 / a0 + s / (2 * np.pi * c.gbw_hz))
    zf = 1 / (s * c.c2_f + 1 / (c.r2_ohm + 1 / (s * c.c1_f)))
    yin = np.full_like(s, 1 / c.r1_ohm)
    if c.network == "type_iii":
        _positive("r3_ohm", c.r3_ohm)
        _positive("c3_f", c.c3_f)
        yin += 1 / (c.r3_ohm + 1 / (s * c.c3_f))
    else:
        yin += s * c.cff_f
    if c.network == "type_ii_gm":
        _positive("gm_s", c.gm_s)
        ro = a0 / c.gm_s if c.output_resistance_ohm is None else c.output_resistance_ohm
        _positive("output_resistance_ohm", ro)
        # The OTA has a high impedance input: its divider is loaded only by
        # RFBT/RFBB/CFF, unlike the virtual-earth voltage-amplifier circuits.
        actual_a0 = c.gm_s * ro
        a = 1 / (1 / actual_a0 + s / (2 * np.pi * c.gbw_hz))
        gm = a / ro
        beta = yin / (yin + 1 / c.r_lower_ohm)
        loaded_z = 1 / (1 / zf + 1 / ro)
        warning = "OTA finite output resistance and single-pole transconductance model; CCOMP/CHF load the output directly."
        if c.output_resistance_ohm is not None:
            warning += " Explicit output resistance sets DC open-loop gain to gm*Ro; open_loop_gain_db is superseded."
        return gm * beta * loaded_z, a, (warning,)
    if c.network.startswith("isolated") and c.cff_f:
        raise ValueError("Additional CFF is defined only for non-isolated Type II/OTA networks")
    if c.r2_ohm > 0 and c.c2_f > 0:
        tf = design_controller(ControllerKind.TYPE_III if c.network == "type_iii" else ControllerKind.TYPE_II,
                               type_input_mode="rc", r1_ohm=c.r1_ohm, r2_ohm=c.r2_ohm,
                               r3_ohm=c.r3_ohm, c1_f=c.c1_f, c2_f=c.c2_f, c3_f=c.c3_f)
        ideal = -np.polyval(tf.numerator, s) / np.polyval(tf.denominator, s)
        if c.network != "type_iii":
            ideal *= 1 + s * c.r1_ohm * c.cff_f
    else:
        ideal = zf * yin  # Exact Type-I or CHF=0 limit, absent in old helper.
    branch_warnings = (INTEGRATOR_BRANCH_NOTE,) if c.network in INTEGRATOR_BRANCH_NETWORKS and c.r2_ohm == 0 and c.c2_f == 0 and c.cff_f == 0 else ()
    noise_gain = 1 + zf * (yin + 1 / c.r_lower_ohm)
    closed = ideal / (1 + noise_gain / a)
    if c.network.startswith("isolated"):
        for name in ("ctr", "opto_pole_hz", "rled_ohm", "rpullup_ohm"):
            _positive(name, getattr(c, name))
        opto = c.ctr / (1 + s / (2 * np.pi * c.opto_pole_hz))
        # Fig 12-5: ideal zener grounds the LED supply in small signal.
        # Fig 12-6: LED supply follows Vout, adding the direct +1 path.
        direct = 1.0 if c.network == "isolated_inner" else 0.0
        return opto * c.rpullup_ohm / c.rled_ohm * (direct + closed), a, (
            "Isolated network uses a voltage-error-amplifier equivalent of TL431; LED/TL431 bias, saturation and nonideal zener impedance require separate checks.",
            "CTR and pullup/LED resistance gain are applied once; finite-amplifier correction is derived from circuit nodes.") + branch_warnings
    return closed, a, branch_warnings


def analyze_loop(plant: LoopPlant, compensator: Compensator, frequency_hz=None) -> LoopResult:
    f = _frequency_grid(plant, frequency_hz)
    gp, metrics, pw = plant_frequency_response(plant, f)
    gc, a, cw = compensator_frequency_response(compensator, f)
    loop = gp * gc
    if not np.all(np.isfinite(loop)):
        raise ValueError("Model produced nonfinite response; check component values and singular operating point")
    stability = analyze_loop_response(f, loop)
    if abs(metrics.get("sampled_current_pole", 0.0)) >= 1 - 1e-12:
        stability = replace(stability, status="FAIL_SUBHARMONIC")
    warnings = list(pw + cw)
    if stability.main_crossover_hz is None:
        warnings.append("No 0 dB crossover was observed within the modeled frequency interval.")
    if len(stability.gain_crossovers) > 1:
        warnings.append("Multiple gain crossovers detected; all margins must be reviewed.")
    if not stability.phase_crossovers:
        warnings.append("Gain margin is unknown: no phase crossover observed within the modeled interval.")
    return LoopResult(f, gp, gc, a, loop, stability, tuple(warnings), metrics)


def _preferred_value(value: float, series: int | None) -> float:
    if series is None:
        return value
    e24 = np.asarray([1, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2, 2.2, 2.4, 2.7, 3,
                      3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1])
    if series not in (12, 24):
        raise ValueError("preferred_series must be 12, 24 or None")
    values = e24 if series == 24 else e24[::2]
    decade = 10.0 ** math.floor(math.log10(value))
    candidates = np.concatenate((values * decade, [10 * decade]))
    return float(candidates[np.argmin(np.abs(np.log(candidates / value)))])


def synthesize_compensator(plant: LoopPlant, target_crossover_hz: float, phase_margin_deg: float = 60.0,
                          network: str = "type_iii", *, template: Compensator | None = None,
                          preferred_series: int | None = 24,
                          cancelled: Callable[[], bool] | None = None) -> SynthesisResult:
    """Suggest realizable R/C values, then measure the actual loop again.

    TI pole/zero placement supplies initial conditions. Bounded log-component
    fitting includes the finite amplifier, actual two capacitor groups, and
    isolated direct path. RFBT/RFBB and all gain/device data are held fixed.
    RCOMP/CCOMP/CHF (also RFF/CFF for Type III) are fitted and rounded to E12 or
    E24. The result never substitutes requested crossover for actual crossover.

    The conservative synthesis domain is fc <= min(fsw/10, GBW/100, RHPZ/5).
    A rejected suggestion is not a proof that no other circuit can work.
    """
    def check_cancelled():
        if cancelled is not None and cancelled():
            raise InterruptedError("Analog-loop synthesis cancelled")

    check_cancelled()
    fc = float(target_crossover_hz)
    pm = float(phase_margin_deg)

    def rejected(reason, comp=None, result=None):
        return SynthesisResult(comp, result, False, reason, fc, pm)

    if not math.isfinite(fc) or fc <= 0 or not math.isfinite(pm) or not 5 <= pm <= 85:
        return rejected("Target crossover must be positive and target phase margin must be between 5 and 85 degrees")
    if preferred_series not in (None, 12, 24):
        return rejected("Supported preferred-value series are E12, E24, or unrounded")
    c = replace(template or Compensator(), network=network)
    try:
        probe = analyze_loop(plant, c)
    except ValueError as exc:
        return rejected(str(exc))
    if probe.stability.status == "FAIL_SUBHARMONIC":
        return rejected("Correct the insufficient current-loop slope compensation before voltage-loop synthesis")
    limit = min(plant.fsw_hz / 10, c.gbw_hz / 100,
                probe.metrics.get("rhp_zero_hz", math.inf) / 5)
    if fc > limit:
        return rejected(f"Target exceeds conservative synthesis limit {limit:.6g} Hz (fsw/10, GBW/100, or RHP zero/5)")
    ctot = plant.capacitance_f + plant.capacitance2_f
    load = probe.metrics["load_resistance_ohm"]
    if plant.control_mode == "vmc":
        fz = 1 / (2 * np.pi * math.sqrt(plant.inductance_h * ctot))
    else:
        fz = 1 / (2 * np.pi * load * ctot)
    esr_zeros = [1 / (2 * np.pi * cap * esr) for cap, esr in
                 ((plant.capacitance_f, plant.esr_ohm), (plant.capacitance2_f, plant.esr2_ohm))
                 if cap > 0 and esr > 0]
    fp = min([plant.fsw_hz / 2, probe.metrics.get("rhp_zero_hz", math.inf)] + esr_zeros)
    fp = max(fp, fc * 1.5)
    fz = min(fz, fp / 2)
    keys = ["r2_ohm", "c1_f", "c2_f"]
    if network == "type_iii":
        keys += ["r3_ohm", "c3_f"]
    bounds_lo = np.log10([1 if key.startswith("r") else 1e-13 for key in keys])
    bounds_hi = np.log10([1e8 if key.startswith("r") else 1e-2 for key in keys])
    gp = plant_frequency_response(plant, np.asarray([fc]))[0][0]
    desired = np.exp(1j * np.deg2rad(-180 + pm))
    candidates = []
    for zero in (fz, fc / 10, fc / 3, fc):
        check_cancelled()
        zero = min(max(zero, fc / 1000), fp / 1.5)
        wz, wp = 2 * np.pi * zero, 2 * np.pi * fp
        # Starting integrator coefficient, then fit the exact circuit.
        total_c = 1 / (c.r1_ohm * 2 * np.pi * fc)
        c2 = total_c * wz / wp
        c1 = total_c - c2
        seed = replace(c, r2_ohm=1 / (wz * c1), c1_f=c1, c2_f=c2)
        if network == "type_iii":
            r3 = c.r1_ohm / (wp / wz - 1)
            seed = replace(seed, r3_ohm=r3, c3_f=1 / (wp * r3))
        x0 = np.clip(np.log10([getattr(seed, key) for key in keys]), bounds_lo + 1e-8, bounds_hi - 1e-8)

        def from_x(x):
            return replace(c, **{key: float(10 ** value) for key, value in zip(keys, x)})

        def residual(x, x0=x0):
            check_cancelled()
            h = gp * compensator_frequency_response(from_x(x), np.asarray([fc]))[0][0]
            ratio = h / desired
            # Tiny regularization preserves meaningful pole placement among
            # otherwise underdetermined R/C solutions, without hiding mismatch.
            return np.r_[np.log(max(abs(ratio), 1e-300)), np.angle(ratio), 1e-4 * (x - x0)]

        fit = least_squares(residual, x0, bounds=(bounds_lo, bounds_hi), max_nfev=400,
                            ftol=1e-8, xtol=1e-8, gtol=1e-5)
        check_cancelled()
        fitted = from_x(fit.x)
        realized = replace(fitted, **{key: _preferred_value(getattr(fitted, key), preferred_series) for key in keys})
        grid = np.unique(np.r_[np.geomspace(min(0.01, fc / 1000), plant.fsw_hz / 2, 1800), fc])
        result = analyze_loop(plant, realized, grid)
        actual_fc = result.stability.main_crossover_hz
        actual_pm = result.stability.worst_phase_margin_deg
        gain_margin = result.stability.worst_gain_margin_db
        single = len(result.stability.gain_crossovers) == 1
        crossover_error = abs(math.log(actual_fc / fc)) if actual_fc else math.inf
        phase_error = abs(actual_pm - pm) if actual_pm is not None else math.inf
        accepted = (fit.success and single and crossover_error <= math.log(1.2) and phase_error <= 7.5
                    and actual_pm >= max(5, pm - 7.5)
                    and (gain_margin is None or gain_margin >= 6)
                    and result.stability.status not in ("FAIL", "FAIL_SUBHARMONIC"))
        score = crossover_error + phase_error / 20 + (0 if single else 10)
        if gain_margin is not None and gain_margin < 6:
            score += 10
        candidates.append((not accepted, score, realized, result, fit.success))
    check_cancelled()
    failed, _, chosen, result, converged = min(candidates, key=lambda item: (item[0], item[1]))
    if failed:
        reason = "No acceptable realized R/C suggestion: target mismatch, multiple/no crossover, or inadequate observed margin"
        if not converged:
            reason += "; numeric fitting did not converge"
        return rejected(reason, chosen, result)
    reason = "Realized R/C values reanalyzed: crossover within 20% and phase margin within 7.5 degrees of target"
    if result.stability.gain_margin_db is None:
        reason += "; gain margin remains unknown because no phase crossover is observed below fsw/2"
    return SynthesisResult(chosen, result, True, reason, fc, pm)
