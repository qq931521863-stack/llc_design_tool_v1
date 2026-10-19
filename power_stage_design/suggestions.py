"""Target-driven component proposals, separate from applying a design.

These are derived analytical starting values, not TI executable output or
catalogue parts. Every returned proposal passes the production stage model.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass, replace
from numbers import Real

from .models import DesignInputs, ModelDomainError, StageResult
from .topologies import _topology_id, _validate, calculate_stage

RATIO_TOPOLOGIES = frozenset({"T09", "T10", "T12", "T13", "T14", "T15", "T16", "T17"})
TWO_INDUCTOR_TOPOLOGIES = frozenset({"T06", "T07", "T08", "T19"})
MAGNETIZING_TOPOLOGIES = frozenset({"T12", "T13", "T14", "T15", "T16", "T17"})
NUMERICAL_TOPOLOGIES = frozenset({"T04", "T11", "T18"})
SUGGESTION_VERSION = "volt-second-targets-1"


@dataclass(frozen=True)
class SuggestionTargets:
    """Absolute peak-to-peak currents avoid ambiguous percent references.

    max_duty uses StageResult.duty: bridge active fraction per half-cycle,
    not the individual switch duty (bridge per-switch duty is half of it).
    For QR it is the actual on-time fraction of the solved switching period.
    """

    ripple_pp_a: float
    max_duty: float = 0.45
    secondary_ripple_pp_a: float | None = None
    magnetizing_ripple_pp_a: float | None = None
    size_turns_ratio: bool = False
    reflected_voltage_v: float | None = None


@dataclass(frozen=True)
class ParameterProposal:
    source_inputs: DesignInputs
    targets: SuggestionTargets
    inputs: DesignInputs
    result: StageResult
    changed_fields: tuple[str, ...]
    assumptions: tuple[str, ...]

    @property
    def source_design_id(self) -> str:
        return self.source_inputs.design_id

    def record(self) -> dict:
        """Persist intent and checked outcome, never substitute cached curves."""
        return {
            "algorithm": SUGGESTION_VERSION,
            "source_inputs": asdict(self.source_inputs),
            "source_design_id": self.source_design_id,
            "result_design_id": self.result.design_id,
            "targets": asdict(self.targets),
            "changed_fields": list(self.changed_fields),
            "assumptions": list(self.assumptions),
            "achieved": achieved_values(self.result),
        }


def achieved_values(result: StageResult) -> dict:
    values = {"mode": result.mode, "duty": result.duty,
              "ripple_pp_a": result.metrics["inductor_ripple_pp_a"],
              "switching_frequency_hz": result.metrics["actual_switching_frequency_hz"]}
    for component, name in (("L2", "secondary_ripple_pp_a"), ("Lm", "magnetizing_ripple_pp_a")):
        if component in result.stress:
            values[name] = result.stress[component]["current_pp_a"]
    return values


def _validate_targets(targets: SuggestionTargets) -> None:
    if type(targets.size_turns_ratio) is not bool:
        raise ModelDomainError("size_turns_ratio must be a boolean")
    for name, value in asdict(targets).items():
        if name == "size_turns_ratio" or value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value <= 0:
            raise ModelDomainError(f"{name} must be finite and positive")
    if targets.ripple_pp_a is None or targets.max_duty is None or targets.max_duty >= 1:
        raise ModelDomainError("A positive ripple target and 0 < max_duty < 1 are required")


def _cancel(cancelled) -> None:
    if cancelled is not None and cancelled():
        raise InterruptedError("Parameter suggestion cancelled")


def _single_inductance(inputs, on_v, off_v, ripple, gain=1.0, continuous=False):
    if min(on_v, off_v) <= 0:
        raise ModelDomainError("Positive energizing and demagnetizing voltages are required")
    duty = off_v / (on_v + off_v)
    average = inputs.iout_a / gain / (1 if continuous else 1-duty)
    if ripple > 2*average:
        # Charge balance of the true triangular DCM pulse, including idle time.
        duty *= 2*average/ripple
    return on_v*duty/(inputs.fsw_hz*ripple), duty


def _numerical_inductance(inputs, target, cancelled):
    """Bounded logarithmic root search; never extrapolate across invalid modes."""
    from scipy.optimize import brentq

    cache = {}
    last_error = "No valid sampled operating point"

    def evaluate(log_l):
        nonlocal last_error
        _cancel(cancelled)
        if log_l not in cache:
            if len(cache) >= 48:
                raise ModelDomainError("Inductance search exhausted its 48-evaluation budget")
            candidate = replace(inputs, inductance_h=math.exp(log_l))
            try:
                result = calculate_stage(candidate, cancelled=cancelled)
            except ModelDomainError as exc:
                last_error = str(exc)
                cache[log_l] = None
            else:
                cache[log_l] = result
        result = cache[log_l]
        if result is None:
            raise ModelDomainError(last_error)
        return result.metrics["inductor_ripple_pp_a"]/target-1

    center = math.log(inputs.inductance_h)
    lower, upper = math.log(1e-12), math.log(1e3)
    valid = {}
    # Current value first, then symmetric factors of two (at most 4096x).
    for exponent in (0, *(value for k in range(1, 13) for value in (k, -k))):
        x = center + exponent*math.log(2)
        if not lower <= x <= upper:
            continue
        try:
            residual = evaluate(x)
        except ModelDomainError:
            continue
        if abs(residual) <= 2e-6:
            return cache[x]
        valid[x] = residual
        ordered = sorted(valid)
        for a, b in itertools.pairwise(ordered):
            if valid[a]*valid[b] < 0:
                try:
                    root = brentq(evaluate, a, b, xtol=1e-8, maxiter=20)
                except (ModelDomainError, ValueError, RuntimeError) as exc:
                    raise ModelDomainError(f"No continuous valid ripple solution found in the bracket (QR valley transitions can leave unattainable target gaps): {exc}") from exc
                return cache[root]
    raise ModelDomainError(f"No ripple solution in the bounded inductance search (L/4096 to L*4096, 1 pH–1000 H): {last_error}")


def _numerical_ratio(inputs, targets, cancelled):
    """Joint L/n solve for finite-leakage Fly-Buck and PSFB duty/ripple."""
    import numpy as np
    from scipy.optimize import least_squares

    initial = _numerical_inductance(inputs, targets.ripple_pp_a, cancelled)
    maximum_n = inputs.vin_v/(inputs.vout_v+inputs.diode_drop_v)
    maximum_n *= 0.949 if _topology_id(inputs.topology) == "T04" else 0.999
    bounds = (np.log([1e-12, 1e-6]), np.log([1e3, maximum_n]))
    evaluations = 0

    def residual(values):
        nonlocal evaluations
        _cancel(cancelled)
        evaluations += 1
        if evaluations > 80:
            raise ModelDomainError("Joint L/ratio sizing exceeded 80 stage evaluations")
        inductance, ratio = np.exp(values)
        trial = replace(inputs, inductance_h=float(inductance), turns_ratio_np_ns=float(ratio))
        result = calculate_stage(trial, cancelled=cancelled)
        return [math.log(result.metrics["inductor_ripple_pp_a"]/targets.ripple_pp_a),
                result.duty/targets.max_duty-1]

    start = np.log([initial.inputs.inductance_h, initial.inputs.turns_ratio_np_ns])
    if np.any(start <= bounds[0]) or np.any(start >= bounds[1]):
        raise ModelDomainError("Initial L/ratio lies outside the bounded joint sizing domain")
    try:
        solution = least_squares(residual, start, bounds=bounds, max_nfev=24,
                                 diff_step=2e-3 if _topology_id(inputs.topology) == "T04" else 1e-4,
                                 ftol=1e-9, xtol=1e-9, gtol=1e-9)
    except (ValueError, ArithmeticError) as exc:
        raise ModelDomainError(f"Joint L/ratio search left the valid physical domain: {exc}") from exc
    if not solution.success or max(abs(value) for value in residual(solution.x)) > 1e-5:
        raise ModelDomainError("No converged joint L/ratio solution for the requested duty and ripple; revise the targets or fixed parasitics")
    inductance, ratio = np.exp(solution.x)
    return calculate_stage(replace(inputs, inductance_h=float(inductance), turns_ratio_np_ns=float(ratio)), cancelled=cancelled)


def suggest_parameters(inputs: DesignInputs, targets: SuggestionTargets, *, cancelled=None) -> ParameterProposal:
    """Propose actual values, reject unsupported requests, reanalyse achieved targets."""
    _cancel(cancelled)
    _validate(inputs)
    _validate_targets(targets)
    topology = _topology_id(inputs.topology)
    if topology in {"T20", "T21"}:
        raise ModelDomainError("LLC resonant component design belongs to the existing LLC workspace")
    if inputs.iout_a <= 0:
        raise ModelDomainError("A positive output load is required for target-driven sizing")
    if targets.secondary_ripple_pp_a is not None and topology not in TWO_INDUCTOR_TOPOLOGIES:
        raise ModelDomainError("This topology has no separately sized second inductor")
    if targets.magnetizing_ripple_pp_a is not None and topology not in MAGNETIZING_TOPOLOGIES:
        raise ModelDomainError("Separate Lm sizing is available only for Forward/PWM bridges; for Flyback/Fly-Buck use the main ripple target, and PSFB retains its supplied Lm")
    if targets.size_turns_ratio and topology not in RATIO_TOPOLOGIES | NUMERICAL_TOPOLOGIES:
        raise ModelDomainError("This nonisolated topology has no transformer ratio to size")
    if targets.reflected_voltage_v is not None and (topology != "T11" or not targets.size_turns_ratio):
        raise ModelDomainError("A reflected-voltage target is used only when sizing a QR Flyback ratio")
    assumptions = [
        "Independent derived starting values; no catalogue, tolerance, saturation, core, loss or hardware qualification.",
        "Targets apply only at the entered Vin, Vout, load and frequency; verify input/load corners separately.",
        "Ripple targets are absolute peak-to-peak state currents in A. Existing capacitances and parasitics are retained and validated.",
        "Maximum duty uses the model's active fraction: PWM bridges use each half-cycle's active fraction (individual switch duty is half); PSFB uses phase shift / 180 degrees.",
    ]
    vin, vo, vf, fs, ripple = inputs.vin_v, inputs.vout_v, inputs.diode_drop_v, inputs.fsw_hz, targets.ripple_pp_a
    candidate = inputs
    if topology in NUMERICAL_TOPOLOGIES or inputs.two_switch_options is not None:
        if targets.size_turns_ratio and topology in {"T04", "T18"}:
            result = _numerical_ratio(inputs, targets, cancelled)
            assumptions.append("Joint bounded numerical L and Np/Ns sizing against actual ripple and actual maximum duty, including the supplied finite leakage/capacitors/Lm; no parasitic values are invented.")
        else:
            if topology == "T11" and targets.size_turns_ratio:
                reflected = targets.reflected_voltage_v
                if reflected is None:
                    raise ModelDomainError("QR fixed power and ripple do not uniquely determine Np/Ns from duty alone; specify an explicit reflected-voltage target")
                candidate = replace(inputs, turns_ratio_np_ns=reflected/(vo+vf))
                assumptions.append("QR Np/Ns = target reflected voltage / (Vout+Vf); duty alone is insufficient to determine the ratio at fixed power/ripple. The actual solved on-time and frequency ceiling are checked.")
                if inputs.qr_options is not None:
                    assumptions.append("QR inverse sizing retains the explicit valley policy, delays, capacitance data and turn-on path; every trial re-solves that selected dynamic model.")
                elif reflected >= vin:
                    assumptions.append("QR first-zero-clamp inverse sizing uses the actual signed-current, fixed-power model; only its first zero-voltage crossing is allowed, with no later-clamped-valley fallback.")
            else:
                assumptions.append("Bounded numerical main-inductance sizing at fixed user Np/Ns and other inputs.")
            result = _numerical_inductance(candidate, ripple, cancelled)
        candidate = result.inputs
        if topology == "T18" and result.mode in {"DCM", "BCM"}:
            assumptions.append("PSFB diode-secondary DCM/BCM sizing retains supplied Lm/Llk and uses the actual model. Ripple determines Lo at fixed ratio; joint sizing uses both ripple and phase-duty targets. Switching-transition/ZVS behavior remains unverified.")
    else:
        n = inputs.turns_ratio_np_ns
        if targets.size_turns_ratio:
            if topology in {"T09", "T10"}:
                n = vin*targets.max_duty/((vo+vf)*(1-targets.max_duty))
                assumptions.append("Np/Ns = Vin Dmax / [(Vout+Vf)(1-Dmax)] from CCM volt-second balance; DCM actual duty can be lower.")
            else:
                amplitude = vin/2 if topology == "T15" else vin
                n = amplitude*targets.max_duty/(vo+vf)
                assumptions.append("Np/Ns = primary pulse amplitude * Dmax / (Vout+Vf). Half bridge uses Vin/2; center-tapped bridge Ns is one secondary half.")
            candidate = replace(candidate, turns_ratio_np_ns=n)
        if topology == "T01":
            inductance, duty = _single_inductance(inputs, vin-vo, vo+vf, ripple, continuous=True)
        elif topology == "T02":
            if vo <= vin:
                raise ModelDomainError("Boost requires Vout > Vin")
            inductance, duty = _single_inductance(inputs, vin, vo+vf-vin, ripple)
        elif topology in {"T03", "T05", "T09", "T10"}:
            gain = n if topology in {"T09", "T10"} else 1.0
            off_v = gain*(vo+(2 if topology == "T05" else 1)*vf)
            inductance, duty = _single_inductance(inputs, vin, off_v, ripple, gain=gain)
            if topology == "T10" and off_v >= vin:
                raise ModelDomainError("Two-switch Flyback requires reflected output < Vin (CCM duty < 0.5)")
        elif topology in {"T06", "T07", "T08"}:
            second = targets.secondary_ripple_pp_a
            if second is None:
                raise ModelDomainError("Specify a separate L2 peak-to-peak ripple target")
            duty = (vo+vf)/(vin+vo+vf)
            average_sum = inputs.iout_a*(1+(vo+vf)/vin)
            duty *= min(1.0, 2*average_sum/(ripple+second))
            inductance = vin*duty/(fs*ripple)
            candidate = replace(candidate, secondary_inductance_h=vin*duty/(fs*second))
            assumptions.append("Uncoupled inductors: each L = Vin D/(fs ΔI); DCM uses charge balance of the switch-current sum, retaining circulating offsets.")
        elif topology in {"T12", "T13", "T14"}:
            inductance, duty = _single_inductance(inputs, vin/n-vo-vf, vo+vf, ripple, continuous=True)
            if targets.magnetizing_ripple_pp_a is not None:
                candidate = replace(candidate, magnetizing_inductance_h=vin*duty/(fs*targets.magnetizing_ripple_pp_a))
            assumptions.append("Output L uses the forward secondary pulse; Lm uses primary Vin * on-time / ΔIm. Reset time is checked by the production model.")
        elif topology in {"T15", "T16", "T17"}:
            amplitude = vin/2 if topology == "T15" else vin
            if amplitude/n <= vo+vf:
                raise ModelDomainError("Bridge secondary pulse cannot support the output voltage")
            duty = n*(vo+vf)/amplitude
            if ripple >= 2*inputs.iout_a:
                raise ModelDomainError("PWM bridge suggestion currently sizes strict CCM only; reduce output ripple below 2*Iout, or size the demagnetizing/DCM branch manually")
            inductance = (amplitude/n-vo-vf)*duty/(2*fs*ripple)
            if targets.magnetizing_ripple_pp_a is not None:
                candidate = replace(candidate, magnetizing_inductance_h=amplitude*duty/(2*fs*targets.magnetizing_ripple_pp_a))
            if candidate.magnetizing_inductance_h is None:
                raise ModelDomainError("Specify Lm or a magnetizing ripple target")
            im = amplitude*duty/(2*fs*candidate.magnetizing_inductance_h)
            if inputs.iout_a-ripple/2 < n*im/2:
                raise ModelDomainError("PWM bridge target enters the magnetizing-demagnetization branch; reduce output/magnetizing ripple or increase Lm. Automatic sizing covers ordinary CCM only")
            assumptions.append("PWM bridge output ripple repeats at 2*fsw. Lm peak-to-peak ripple = primary amplitude * D/(2*fsw*Lm); ordinary CCM branch only.")
        elif topology == "T19":
            second = targets.secondary_ripple_pp_a
            if second is None:
                raise ModelDomainError("Specify a separate phase-B ripple target")
            duty = 2*vo/vin
            if duty > 0.5 or max(ripple, second) >= inputs.iout_a:
                raise ModelDomainError("Series-capacitor Buck requires phase duty <= 0.5 and each ΔIpp < Iout (each phase mean is Iout/2)")
            inductance = (vin/2-vo)*duty/(fs*ripple)
            candidate = replace(candidate, secondary_inductance_h=(vin/2-vo)*duty/(fs*second))
            assumptions.append("Two 180-degree phases, each mean current Iout/2; each L = (Vin/2-Vout) D/(fs ΔI).")
        else:
            raise ModelDomainError(f"No component-sizing equations for {topology}")
        candidate = replace(candidate, inductance_h=inductance)
        _cancel(cancelled)
        result = calculate_stage(candidate, cancelled=cancelled)
    _check_achieved(result, targets)
    _cancel(cancelled)
    changed = tuple(key for key, value in asdict(candidate).items() if value != getattr(inputs, key))
    return ParameterProposal(inputs, targets, candidate, result, changed, tuple(assumptions))


def _check_achieved(result: StageResult, targets: SuggestionTargets) -> None:
    values = achieved_values(result)
    if result.duty > targets.max_duty+2e-6:
        raise ModelDomainError(f"Achieved duty {result.duty:.8g} exceeds maximum {targets.max_duty:.8g}; revise voltage ratio/turns ratio or maximum duty")
    for name in ("ripple_pp_a", "secondary_ripple_pp_a", "magnetizing_ripple_pp_a"):
        target = getattr(targets, name)
        if target is not None and (name not in values or not math.isclose(values[name], target, rel_tol=2e-4, abs_tol=1e-9)):
            raise ModelDomainError(f"Actual {name} misses its target; no component proposal is accepted")


def validate_suggestion_record(record: dict, result: StageResult) -> dict:
    """Validate persisted provenance against recomputed actual inputs/outcomes.

    This validates an audit record, not proof that an untrusted file ran the
    sizing algorithm. No stored achieved values are trusted as model results.
    """
    if not isinstance(record, dict) or set(record) != {"algorithm", "source_inputs", "source_design_id", "result_design_id", "targets", "changed_fields", "assumptions", "achieved"}:
        raise ValueError("Invalid parameter-suggestion record")
    if record["algorithm"] != SUGGESTION_VERSION or record["result_design_id"] != result.design_id:
        raise ValueError("Parameter suggestion belongs to a different algorithm or design")
    try:
        source = DesignInputs(**record["source_inputs"])
        targets = SuggestionTargets(**record["targets"])
        _validate(source)
        _validate_targets(targets)
    except (TypeError, KeyError, ValueError) as exc:
        raise ValueError("Invalid parameter-suggestion source or targets") from exc
    expected = [key for key, value in asdict(result.inputs).items() if value != getattr(source, key)]
    if record["source_design_id"] != source.design_id or record["changed_fields"] != expected:
        raise ValueError("Parameter-suggestion source identity or changed fields do not match")
    if not isinstance(record["assumptions"], list) or not all(isinstance(item, str) for item in record["assumptions"]):
        raise ValueError("Invalid parameter-suggestion assumptions")
    allowed = {"inductance_h"}
    if targets.secondary_ripple_pp_a is not None:
        allowed.add("secondary_inductance_h")
    if targets.magnetizing_ripple_pp_a is not None:
        allowed.add("magnetizing_inductance_h")
    if targets.size_turns_ratio:
        allowed.add("turns_ratio_np_ns")
    if set(expected)-allowed:
        raise ValueError("Parameter suggestion changes specifications or untargeted components")
    topology = _topology_id(result.inputs.topology)
    if targets.size_turns_ratio:
        vin, vo, vf = result.inputs.vin_v, result.inputs.vout_v, result.inputs.diode_drop_v
        ratio = result.inputs.turns_ratio_np_ns
        if topology in {"T09", "T10"}:
            expected_ratio = vin*targets.max_duty/((vo+vf)*(1-targets.max_duty))
        elif topology in RATIO_TOPOLOGIES:
            expected_ratio = (vin/2 if topology == "T15" else vin)*targets.max_duty/(vo+vf)
        elif topology == "T11" and targets.reflected_voltage_v is not None:
            expected_ratio = targets.reflected_voltage_v/(vo+vf)
        elif topology in {"T04", "T18"}:
            expected_ratio = ratio
            if not math.isclose(result.duty, targets.max_duty, rel_tol=1e-5, abs_tol=2e-6):
                raise ValueError("Joint ratio proposal no longer meets its duty target")
        else:
            raise ValueError("Invalid ratio-sizing target for this topology")
        if not math.isclose(ratio, expected_ratio, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("Ratio suggestion no longer matches its declared target")
    _check_achieved(result, targets)
    actual = achieved_values(result)
    historical = record["achieved"]
    if not isinstance(historical, dict) or set(historical) != set(actual):
        raise ValueError("Invalid parameter-suggestion achieved fields")
    # Historical numerical evidence may differ across solver/platform builds.
    # Identity and mode stay exact; numeric evidence allows 2 ppm or 1 nSI.
    for key, fresh in actual.items():
        saved = historical[key]
        if isinstance(fresh, str):
            matches = saved == fresh
        else:
            matches = (not isinstance(saved, bool) and isinstance(saved, Real)
                       and math.isfinite(saved)
                       and math.isclose(saved, fresh, rel_tol=2e-6, abs_tol=1e-9))
        if not matches:
            raise ValueError("Parameter-suggestion achieved values do not match current recomputation")
    return record
