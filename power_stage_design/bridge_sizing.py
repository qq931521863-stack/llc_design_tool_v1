"""Inverse sizing for symmetric T15/T16/T17 center-tapped-secondary bridges.

The ordinary CCM and joint Lout/Lm demagnetizing branches are solved from
charge and flux balance. Only the final, unchanged production model supplies
waveforms and reported results; no extra parasitics or clamp states are assumed.
"""
from __future__ import annotations

import math
from dataclasses import replace
from numbers import Real

from .models import DesignInputs, ModelDomainError, StageResult
from .topologies import TOPOLOGY_NAMES, _topology_id, _validate, calculate_stage


def _positive(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value <= 0:
        raise ModelDomainError(f"{name} must be finite and positive")


def _charge_duty(load: float, ripple: float, magnetic: float) -> float:
    """Positive root of magnetic*u² + ripple*u = 2*load, without cancellation."""
    return 4*load/(ripple + math.hypot(ripple, math.sqrt(8*magnetic*load)))


def size_symmetric_bridge(
    inputs: DesignInputs,
    ripple_pp_a: float,
    *,
    max_duty: float,
    magnetizing_ripple_pp_a: float | None = None,
    size_turns_ratio: bool = False,
    cancelled=None,
) -> StageResult:
    """Return actual rechecked T15/T16/T17 components in bounded time.

    ``max_duty`` is an upper bound at a fixed ratio, and an equality when
    ``size_turns_ratio`` is true. Duty is the active fraction of a half-cycle.
    Lm and Np/Ns are changed only when explicitly targeted. One production
    calculation suffices, including at BCM; there is no iterative search.

    Primary pulse amplitude A is Vin/2 for T15, Vin for T16/T17. The ratio
    n=Np/Ns uses one secondary half and, for T16, one primary half.
    Let H=1/(2*fs), U=Vout+Vf, a=A/n, R=target output ripple,
    d0=U/a and u=D/d0. On-time volt seconds give L=(a-U)*D*H/R.
    In a demagnetizing half-cycle, iL starts at l, rises to l+R,
    freewheels to j=n*Im_peak, then shares L+Lm/n² until l (or zero).
    Ampere-turns/flux give l+j=n*A*D*H/Lm. Integrating those three
    current intervals gives 2*Io = R*u + n²*U*H/Lm*u². Partial CCM
    has u>=1/2 and Im_pp=n*U*H/Lm; DCM has u<1/2 and
    Im_pp=2*A*D*H/Lm. These distinctions matter even when R>2*Io:
    that inequality alone does not determine the joint magnetic mode.
    """
    def check_cancel():
        if cancelled is not None and cancelled():
            raise InterruptedError("Symmetric bridge parameter suggestion cancelled")

    check_cancel()
    _validate(inputs)
    topology_id = _topology_id(inputs.topology)
    if topology_id not in ("T15", "T16", "T17"):
        raise ModelDomainError("Symmetric bridge sizing applies only to T15, T16 and T17")
    name = TOPOLOGY_NAMES[topology_id]
    _positive("ripple_pp_a", ripple_pp_a)
    _positive("max_duty", max_duty)
    if max_duty >= 1 or type(size_turns_ratio) is not bool:
        raise ModelDomainError(f"{name} requires max_duty < 1 and a boolean size_turns_ratio")
    if inputs.iout_a <= 0:
        raise ModelDomainError(f"A positive output load is required for {name} sizing")
    if magnetizing_ripple_pp_a is not None:
        _positive("magnetizing_ripple_pp_a", magnetizing_ripple_pp_a)
    elif inputs.magnetizing_inductance_h is None:
        raise ModelDomainError("Specify Lm or a magnetizing ripple target")

    amplitude = inputs.vin_v/2 if topology_id == "T15" else inputs.vin_v
    load, ripple = inputs.iout_a, ripple_pp_a
    output = inputs.vout_v + inputs.diode_drop_v
    half_period = .5/inputs.fsw_hz
    magnetic_target = magnetizing_ripple_pp_a
    n = amplitude*max_duty/output if size_turns_ratio else inputs.turns_ratio_np_ns
    nominal_duty = n*output/amplitude
    if nominal_duty >= 1:
        raise ModelDomainError("Bridge secondary pulse cannot support the output voltage")
    lm = (n*output*half_period/magnetic_target if magnetic_target is not None
          else inputs.magnetizing_inductance_h)
    magnetic = n*n*output*half_period/lm
    duty = nominal_duty

    if 2*load < ripple + magnetic:
        # Ordinary CCM's two-diode freewheel would require a negative branch
        # current. Resolve the joint states instead of clipping either current.
        if size_turns_ratio:
            base_ratio = n
            duty = max_duty
            if magnetic_target is None:
                denominator = 2*load - base_ratio*amplitude*duty*half_period/lm
                if denominator <= 0:
                    raise ModelDomainError(f"No {name} ratio solution: the specified duty and fixed Lm alone transfer at least the requested load energy")
                n = base_ratio*ripple/denominator
            else:
                n = base_ratio*(ripple + base_ratio*magnetic_target)/(2*load)
                if n <= 2*base_ratio:
                    lm = n*output*half_period/magnetic_target
                else:
                    denominator = 2*load - base_ratio*magnetic_target/2
                    if denominator <= 0:
                        raise ModelDomainError(f"No {name} ratio solution: the requested duty and magnetizing ripple alone supply at least the output load")
                    n = base_ratio*ripple/denominator
                    lm = 2*amplitude*duty*half_period/magnetic_target
        else:
            u = _charge_duty(load, ripple, magnetic)
            if magnetic_target is not None and u < .5:
                u = 2*load/(ripple + n*magnetic_target/2)
                lm = 2*amplitude*nominal_duty*u*half_period/magnetic_target
            duty = nominal_duty*u

        nominal_duty = n*output/amplitude
        if nominal_duty >= 1:
            raise ModelDomainError(f"No {name} ratio solution: the required Np/Ns leaves no positive output-inductor energizing voltage")
        u = duty/nominal_duty
        magnetic_rise = n*amplitude*duty*half_period/lm
        low = magnetic_rise*(1 - .5/u) if u >= .5 else 0.
        demagnetizing_start = magnetic_rise-low
        tolerance = 8*max(math.ulp(low+ripple), math.ulp(demagnetizing_start))
        if low+ripple < demagnetizing_start-tolerance:
            raise ModelDomainError(f"{name} target requires a negative two-diode freewheel duration: Lm is too small or the magnetizing-ripple target too large; the required commutation-capacitance sub-mode is not modeled")

    if duty > max_duty+2e-6:
        raise ModelDomainError(f"Achieved duty {duty:.8g} exceeds maximum {max_duty:.8g}; revise turns ratio or maximum duty")
    inductance = (amplitude/n-output)*duty*half_period/ripple
    updates = {"inductance_h": inductance}
    if size_turns_ratio:
        updates["turns_ratio_np_ns"] = n
    if magnetic_target is not None:
        updates["magnetizing_inductance_h"] = lm
    candidate = replace(inputs, **updates)
    check_cancel()
    result = calculate_stage(candidate, cancelled=cancelled)
    # Acceptance is based on actual complete-period results, never only the
    # inverse equations or a conventional output-only CCM/DCM classification.
    actual = result.metrics["inductor_ripple_pp_a"]
    if not math.isclose(actual, ripple, rel_tol=2e-6, abs_tol=1e-9):
        raise ModelDomainError(f"Actual {name} output ripple misses its target")
    if magnetic_target is not None and not math.isclose(
        result.stress["Lm"]["current_pp_a"], magnetic_target, rel_tol=2e-6, abs_tol=1e-9
    ):
        raise ModelDomainError(f"Actual {name} magnetizing ripple misses its target")
    if result.duty > max_duty+2e-6:
        raise ModelDomainError(f"Actual {name} duty exceeds its specified maximum")
    if size_turns_ratio and not math.isclose(result.duty, max_duty, rel_tol=2e-6, abs_tol=1e-9):
        raise ModelDomainError(f"Actual {name} ratio solution misses its duty target")
    check_cancel()
    return result


def size_full_bridge(
    inputs: DesignInputs,
    ripple_pp_a: float,
    *,
    max_duty: float,
    magnetizing_ripple_pp_a: float | None = None,
    size_turns_ratio: bool = False,
    cancelled=None,
) -> StageResult:
    """T17-only compatibility entry point for symmetric bridge sizing."""
    _validate(inputs)
    if _topology_id(inputs.topology) != "T17":
        raise ModelDomainError("Full Bridge sizing applies only to T17")
    return size_symmetric_bridge(
        inputs, ripple_pp_a, max_duty=max_duty,
        magnetizing_ripple_pp_a=magnetizing_ripple_pp_a,
        size_turns_ratio=size_turns_ratio, cancelled=cancelled,
    )
