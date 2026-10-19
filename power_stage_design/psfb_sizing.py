"""Target-driven T18 sizing against its selected ideal or finite-Coss model.

Only explicitly targeted Lout/Lm/Np:Ns are changed. Ideal DCM/BCM has an
exact charge/flux inverse; other cases use a bounded, damped log-space solve
of the production model's actual full-period current extrema and phase duty.
"""
from __future__ import annotations

import math
from dataclasses import replace
from numbers import Real

import numpy as np

from .models import DesignInputs, ModelDomainError, StageResult
from .topologies import _topology_id, _validate, calculate_stage

_MAX_EVALUATIONS = 64


class _BudgetExhausted(ModelDomainError):
    pass


def _positive(name, value):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value <= 0:
        raise ModelDomainError(f"{name} must be finite and positive")


def _cancel(cancelled):
    if cancelled is not None and cancelled():
        raise InterruptedError("PSFB parameter suggestion cancelled")


def _seed(inputs, ripple, duty, magnetic, ratio):
    """Charge/flux seed; exact only for the ideal diode-secondary DCM/BCM.

    H=1/(2fs), U=Vout+Vf, c=min(1,2Io/ripple), alpha=1+Llk/Lm.
    During conduction, Lo volt-second closure gives integral(Vp)=n*U*c*H,
    hence Lm=n*U*c*H/DeltaIm. Eliminating the leakage/magnetizing currents
    gives E=Vin/(n*alpha), Le=Lo+Llk/(n²*alpha). A DCM triangle then has
    D=(U/E)*c and Le=U*(1-U/E)*c*H/ripple. In CCM, rectifier overlap adds
    phase loss and alters the output ripple; the actual model must correct it.
    """
    u, h = inputs.vout_v+inputs.diode_drop_v, .5/inputs.fsw_hz
    lk, n, lm = inputs.leakage_inductance_h, inputs.turns_ratio_np_ns, inputs.magnetizing_inductance_h
    conduction = min(1., 2*inputs.iout_a/ripple)
    if ratio:
        n = ((inputs.vin_v*duty*h-lk*magnetic)/(u*conduction*h) if magnetic is not None
             else inputs.vin_v*duty/((1+lk/lm)*u*conduction))
        if ripple < 2*inputs.iout_a:
            n *= .9  # Start below the no-overlap phase limit; solve actual duty.
    if n <= 0:
        raise ModelDomainError("No PSFB ratio solution: leakage volt-seconds for the requested magnetizing ripple consume the phase pulse")
    if magnetic is not None:
        lm = n*u*conduction*h/magnetic
    alpha = 1+lk/lm
    nominal_duty = n*alpha*u/inputs.vin_v
    if nominal_duty >= 1:
        raise ModelDomainError("No PSFB solution: Np/Ns and Llk/Lm leave no positive output-inductor energizing voltage")
    lo = u*(1-nominal_duty)*conduction*h/ripple-lk/(n*n*alpha)
    if lo <= 0:
        raise ModelDomainError("No PSFB ripple solution: fixed leakage already exceeds the required effective output inductance")
    updates = {"inductance_h": lo}
    if magnetic is not None:
        updates["magnetizing_inductance_h"] = lm
    if ratio:
        updates["turns_ratio_np_ns"] = n
    return replace(inputs, **updates)


def _solve(inputs, seeds, ripple, duty, magnetic, ratio, cancelled):
    """Damped Newton, finite-difference Jacobian, at most 64 actual evaluations.

    Invalid physical trials are rejected and the step is reduced. They never
    become artificial residuals or an ideal-model fallback. Do not retain a
    history of large finite-Coss waveform results in the search.
    """
    dynamic = inputs.psfb_options is not None
    names = ["inductance_h"]
    if dynamic and magnetic is not None:
        names.append("magnetizing_inductance_h")
    if ratio:
        names.append("turns_ratio_np_ns")
    lower = np.log([1e-6 if name == "turns_ratio_np_ns" else 1e-12 for name in names])
    upper = np.log([inputs.vin_v/(inputs.vout_v+inputs.diode_drop_v)*(1-1e-10)
                    if name == "turns_ratio_np_ns" else 1e3 for name in names])
    calls = 0
    last_error = "none; evaluated physical operating points were valid"

    def evaluate(x):
        nonlocal calls, last_error
        _cancel(cancelled)
        if np.any(x <= lower) or np.any(x >= upper):
            raise ModelDomainError("PSFB sizing reached its bounds (1 pH–1000 H; 1e-6 < Np/Ns < Vin/(Vout+Vf))")
        if calls >= _MAX_EVALUATIONS:
            raise _BudgetExhausted(f"PSFB sizing exhausted its {_MAX_EVALUATIONS}-evaluation budget; last physical constraint: {last_error}")
        changes = dict(zip(names, map(float, np.exp(x))))
        if not dynamic and magnetic is not None:
            n = changes.get("turns_ratio_np_ns", inputs.turns_ratio_np_ns)
            changes["magnetizing_inductance_h"] = n*(inputs.vout_v+inputs.diode_drop_v)*.5/inputs.fsw_hz/magnetic
        calls += 1
        try:
            result = calculate_stage(replace(inputs, **changes), cancelled=cancelled)
        except ModelDomainError as exc:
            last_error = str(exc)
            raise
        residual = [math.log(result.stress["L1"]["current_pp_a"]/ripple)]
        if dynamic and magnetic is not None:
            residual.append(math.log(result.stress["Lm"]["current_pp_a"]/magnetic))
        if ratio:
            residual.append(result.duty/duty-1)
        return np.asarray(residual), result

    for seed in seeds:
        x = np.log([getattr(seed, name) for name in names])
        try:
            residual, result = evaluate(x)
        except _BudgetExhausted:
            raise
        except ModelDomainError as exc:
            last_error = str(exc)
            continue
        break
    else:
        raise ModelDomainError(f"No valid PSFB sizing starting point: {last_error}")

    for _ in range(16):
        _cancel(cancelled)
        if max(abs(residual)) <= 1e-6:
            return result
        columns = []
        for index in range(len(names)):
            for step in (5e-4, -5e-4):
                shifted = x.copy()
                shifted[index] += step
                try:
                    other, _trial = evaluate(shifted)
                except _BudgetExhausted:
                    raise
                except ModelDomainError:
                    continue
                columns.append((other-residual)/step)
                del _trial
                break
            else:
                raise ModelDomainError(f"PSFB target search cannot perturb {names[index]} inside the selected model: {last_error}")
        jacobian = np.column_stack(columns)
        direction, _, rank, _ = np.linalg.lstsq(jacobian, -residual, rcond=None)
        if rank < len(names) or not np.isfinite(direction).all():
            raise ModelDomainError("PSFB targets do not define a locally solvable component set in the selected physical model")
        direction *= min(1., .7/max(abs(direction)))
        for scale in (1., .5, .25, .125, .0625, .03125):
            trial_x = x+scale*direction
            try:
                trial_residual, trial = evaluate(trial_x)
            except _BudgetExhausted:
                raise
            except ModelDomainError:
                continue
            if np.linalg.norm(trial_residual) < np.linalg.norm(residual):
                x, residual, result = trial_x, trial_residual, trial
                break
            del trial
        else:
            raise ModelDomainError(f"No decreasing valid PSFB sizing step; revise the targets or fixed physical parameters. Last physical constraint: {last_error}")
    raise ModelDomainError(f"PSFB target search did not converge within 16 iterations/{_MAX_EVALUATIONS} evaluations; revise targets or fixed physical parameters")


def size_phase_shifted_bridge(
    inputs: DesignInputs,
    ripple_pp_a: float,
    *,
    max_duty: float,
    magnetizing_ripple_pp_a: float | None = None,
    size_turns_ratio: bool = False,
    cancelled=None,
) -> StageResult:
    """Return a rechecked T18 proposal, without changing untargeted inputs.

    max_duty is an upper bound at fixed Np/Ns and an actual phase-duty target
    when sizing Np/Ns. Lm changes only with an explicit absolute DeltaIm target.
    Ideal CCM/BCM/DCM and the opt-in finite-Coss model's positive-current CCM
    domain remain distinct. Finite-Coss extrema include resonant intervals;
    its Lm cannot be sized by assuming ideal monotonic magnetizing current.
    Each numerical evaluation runs the selected full forward model. Finite-Coss
    proposals can take minutes; callers should run asynchronously and pass the
    cancellation callback. No waveform-result history is retained.
    """
    _cancel(cancelled)
    _validate(inputs)
    if _topology_id(inputs.topology) != "T18":
        raise ModelDomainError("PSFB sizing applies only to T18")
    _positive("ripple_pp_a", ripple_pp_a)
    _positive("max_duty", max_duty)
    if max_duty >= 1 or type(size_turns_ratio) is not bool:
        raise ModelDomainError("PSFB requires max_duty < 1 and a boolean size_turns_ratio")
    if inputs.iout_a <= 0 or inputs.leakage_inductance_h is None:
        raise ModelDomainError("PSFB sizing requires positive load and explicit leakage_inductance_h")
    magnetic = magnetizing_ripple_pp_a
    if magnetic is not None:
        _positive("magnetizing_ripple_pp_a", magnetic)
    elif inputs.magnetizing_inductance_h is None:
        raise ModelDomainError("Specify Lm or an explicit magnetizing ripple target")
    if inputs.psfb_options is not None:
        if inputs.mode.casefold() not in ("auto", "ccm"):
            raise ModelDomainError("Finite-Coss PSFB sizing supports positive-current CCM only; use the ideal model for DCM/BCM")
        dead = 2*inputs.fsw_hz*inputs.dead_time_s
        if size_turns_ratio and not dead+1e-6 < max_duty < 1-dead-1e-6:
            raise ModelDomainError("PSFB phase-duty target lies outside separated dead-time windows")

    seeds = []
    seed_error = None
    try:
        seeds.append(_seed(inputs, ripple_pp_a, max_duty, magnetic, size_turns_ratio))
    except ModelDomainError as exc:
        seed_error = exc
    if inputs.psfb_options is None and ripple_pp_a >= 2*inputs.iout_a:
        if seed_error is not None:
            raise seed_error
        result = calculate_stage(seeds[0], cancelled=cancelled)
    else:
        fallback = inputs
        if magnetic is not None:
            fallback = replace(inputs, magnetizing_inductance_h=(inputs.turns_ratio_np_ns*
                (inputs.vout_v+inputs.diode_drop_v)*.5/inputs.fsw_hz/magnetic))
        seeds.append(fallback)
        result = _solve(inputs, seeds, ripple_pp_a, max_duty, magnetic, size_turns_ratio, cancelled)

    for key, target, label in (("L1", ripple_pp_a, "output"), ("Lm", magnetic, "magnetizing")):
        if target is not None and not math.isclose(result.stress[key]["current_pp_a"], target, rel_tol=2e-6, abs_tol=1e-9):
            raise ModelDomainError(f"Actual PSFB {label} current extrema miss the requested ripple; no proposal accepted")
    if result.duty > max_duty+2e-6:
        raise ModelDomainError(f"Achieved duty {result.duty:.8g} exceeds maximum {max_duty:.8g}; revise turns ratio or maximum duty")
    if size_turns_ratio and not math.isclose(result.duty, max_duty, rel_tol=2e-6, abs_tol=1e-9):
        raise ModelDomainError("Actual PSFB phase duty misses its ratio-sizing target; no proposal accepted")
    _cancel(cancelled)
    return result
