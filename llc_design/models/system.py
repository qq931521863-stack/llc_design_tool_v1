"""System-level LLC analysis, hardware synthesis and feasibility aggregation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
import hashlib
import json

import numpy as np
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from ..analysis.types import LLCModelResult

from ..core.engineering import (
    EnvelopeEvaluation,
    OperatingEnvelope,
    evaluate_operating_envelope,
)
from ..core.operating_point import LLCOperatingPoint, solve_operating_point
from ..core.spec import LLCDesignSpec
from ..core.tank import GainNotReachableError, TankDesign, design_tank
from ..magnetics.core import CoreDatabase
from ..magnetics.resonant_inductor import (ResonantInductorDesign,
                                           ResonantInductorLoss,
                                           design_resonant_inductor)
from ..magnetics.transformer import (TransformerDesign, TransformerLoss,
                                     design_transformer)
from .capacitors import (BusCapacitorResult, ResonantCapacitorResult,
                         bus_capacitor_result, output_capacitor_result,
                         resonant_capacitor_result, capacitor_results_from_waveform)
from .devices import DeviceDatabase
from .primary_bridge import PrimaryBridgeLoss, primary_bridge_loss
from .synchronous_rectifier import (SynchronousRectifierLoss,
                                     synchronous_rectifier_loss)
from ..core.waveform import OutputRippleResult


@dataclass(frozen=True)
class CoreLossFormulaInputs:
    """Scalar iGSE quadrature inputs from the exact accepted final TD cycle."""
    temperature_c: float
    k: float
    ki: float
    alpha: float
    beta: float
    correction: float
    volume_m3: float
    delta_b_t: float
    mean_abs_dbdt_alpha: float


@dataclass(frozen=True)
class TimeDomainFormulaInputs:
    """Small immutable audit summary, not a second waveform or loss model.

    Phasors are (real, imaginary) RMS pairs, orders 0..cutoff. Secondary
    current is signed INTO the winding dot, as consumed by the layer-MMF API.
    Project sessions persist request/candidate provenance, not this summary.
    """
    sample_count: int
    sample_step_s: float
    frequency_hz: float
    resonant_rms_a: float
    resonant_peak_a: float
    secondary_rms_a: float
    secondary_peak_a: float
    magnetizing_peak_a: float
    magnetizing_mean_a: float
    transformer: CoreLossFormulaInputs
    inductor: CoreLossFormulaInputs | None
    primary_rms_phasors_a: tuple[tuple[float, float], ...]
    secondary_rms_phasors_a: tuple[tuple[float, float], ...]


def _td_formula_inputs(analysis, waveform, transformer, inductor):
    from ..dynamics.periodic_samples import periodic_samples, transformer_flux
    from ..magnetics.spectrum import rms_phasors

    samples = periodic_samples(waveform, {"i_resonant": "A", "i_magnetizing": "A",
        "i_transformer_secondary": "A", "v_transformer_primary": "V",
        **({"v_resonant_inductor": "V"} if analysis.spec.integrated_magnetics else {})}, True)
    ir, im, isec = (samples.signals[key] for key in
                   ("i_resonant", "i_magnetizing", "i_transformer_secondary"))
    count = len(samples.time_s)
    # Match CoreMaterial.igse_density_w_m3's wrapped endpoint-exclusive grid.
    period = samples.time_s[-1] - samples.time_s[0] + float(np.median(np.diff(samples.time_s)))
    dt = period / count

    def core_inputs(core, flux, temperature):
        from scipy.special import gamma
        c = core.material_spec.coefficients_at(temperature)
        integral = 2 * math.sqrt(math.pi) * gamma((c.alpha + 1) / 2) / gamma((c.alpha + 2) / 2)
        ki = c.k / ((2 * math.pi)**(c.alpha - 1) * 2**(c.beta - c.alpha) * integral)
        rate = float(np.mean(np.abs((np.roll(flux, -1) - flux) / dt)**c.alpha))
        return CoreLossFormulaInputs(temperature, c.k, float(ki), c.alpha, c.beta,
            core.material_spec.core_loss_correction, core.ve_m3, float(np.ptp(flux)), rate)

    def phasors(values):
        return tuple((float(p.real), float(p.imag))
                     for p in rms_phasors(values, analysis.spec.litz_max_harmonic))

    bt = transformer_flux(samples, analysis.transformer.primary_turns,
                          analysis.transformer.core.ae_m2, analysis.tank.lm_h, analysis.spec)
    inductor_inputs = None
    if analysis.resonant_inductor is not None:
        bi = analysis.resonant_inductor.inductance_h * ir / (
            analysis.resonant_inductor.turns * analysis.resonant_inductor.core.ae_m2)
        inductor_inputs = core_inputs(analysis.resonant_inductor.core, bi, inductor.estimated_hotspot_c)
    if analysis.spec.integrated_magnetics:
        from ..core.integrated_magnetics import from_spec
        im = from_spec(analysis.spec).physical_magnetizing_current(ir, im)
    return TimeDomainFormulaInputs(count, dt, samples.frequency_hz,
        float(np.sqrt(np.mean(ir**2))), float(np.max(np.abs(ir))),
        float(np.sqrt(np.mean(isec**2))), float(np.max(np.abs(isec))),
        float(np.max(np.abs(im))), float(np.mean(im)),
        core_inputs(analysis.transformer.core, bt, transformer.estimated_hotspot_c),
        inductor_inputs,
        phasors(ir), phasors(-isec))


@dataclass(frozen=True)
class OperatingPointLoss:
    operating_point: LLCOperatingPoint
    primary: PrimaryBridgeLoss
    synchronous_rectifier: SynchronousRectifierLoss
    transformer: TransformerLoss
    resonant_inductor: ResonantInductorLoss
    resonant_capacitor: ResonantCapacitorResult
    output_capacitor: OutputRippleResult
    auxiliary_w: float
    total_loss_w: float
    efficiency: float
    semiconductor_source: str = "FHA_OPERATING_POINT"
    semiconductor_frequency_hz: float | None = None
    magnetic_source: str = "FHA_RECONSTRUCTED_WAVEFORM"
    capacitor_source: str = "FHA_RECONSTRUCTED_WAVEFORM"
    passive_frequency_hz: float | None = None
    commutation_source: str = "FHA_OPERATING_POINT_APPROXIMATION"
    loss_waveform_sha256: str | None = None
    td_formula_inputs: TimeDomainFormulaInputs | None = None

    @property
    def label(self) -> str:
        return f"{self.operating_point.vbus_v:.0f}V_{self.operating_point.load_fraction*100:.0f}pct"

    def component_sources(self) -> dict[str, str]:
        """Source of each component's numerical inputs, not a validity grade."""
        return {
            "semiconductor_current": self.semiconductor_source,
            "transformer": self.magnetic_source,
            "resonant_inductor": self.magnetic_source,
            "resonant_capacitor": self.capacitor_source,
            "output_capacitor": self.capacitor_source,
            "commutation": self.commutation_source,
            "auxiliary": "EMPIRICAL_POWER_ESTIMATE",
        }

    def breakdown(self) -> dict[str, float]:
        """Non-overlapping system loss buckets used for totals and plots."""
        return {
            "primary_conduction_w": self.primary.conduction_w,
            "primary_turnoff_w": self.primary.turnoff_w,
            "primary_gate_w": self.primary.gate_drive_w,
            "primary_coss_w": self.primary.residual_coss_w,
            "primary_deadtime_w": self.primary.deadtime_diode_w,
            "sr_conduction_w": self.synchronous_rectifier.conduction_w,
            "sr_deadtime_w": self.synchronous_rectifier.deadtime_diode_w,
            "sr_turnoff_w": self.synchronous_rectifier.turnoff_w,
            "sr_coss_w": self.synchronous_rectifier.coss_w,
            "sr_gate_w": self.synchronous_rectifier.gate_drive_w,
            "transformer_core_w": self.transformer.core_w,
            "transformer_primary_copper_w": self.transformer.primary_copper_w,
            "transformer_secondary_copper_w": self.transformer.secondary_copper_w,
            "resonant_inductor_core_w": self.resonant_inductor.core_w,
            "resonant_inductor_copper_w": self.resonant_inductor.copper_w,
            "resonant_capacitor_w": self.resonant_capacitor.loss_w,
            "output_capacitor_w": self.output_capacitor.capacitor_loss_w,
            "auxiliary_w": self.auxiliary_w,
        }

    def detailed_magnetics_breakdown(self) -> dict[str, float]:
        """Detailed non-overlapping magnetic-component loss mechanisms."""
        return {
            "transformer_core_w": self.transformer.core_w,
            "transformer_primary_dc_w": self.transformer.primary_dc_w,
            "transformer_primary_skin_w": self.transformer.primary_skin_w,
            "transformer_primary_proximity_w": self.transformer.primary_proximity_w,
            "transformer_primary_bundle_w": self.transformer.primary_bundle_w,
            "transformer_primary_termination_w": self.transformer.primary_termination_w,
            "transformer_secondary_dc_w": self.transformer.secondary_dc_w,
            "transformer_secondary_skin_w": self.transformer.secondary_skin_w,
            "transformer_secondary_proximity_w": self.transformer.secondary_proximity_w,
            "transformer_secondary_bundle_w": self.transformer.secondary_bundle_w,
            "transformer_secondary_termination_w": self.transformer.secondary_termination_w,
            "resonant_inductor_core_w": self.resonant_inductor.core_w,
            "resonant_inductor_dc_w": self.resonant_inductor.dc_copper_w,
            "resonant_inductor_skin_w": self.resonant_inductor.skin_effect_w,
            "resonant_inductor_proximity_w": self.resonant_inductor.external_proximity_w,
            "resonant_inductor_gap_fringing_w": self.resonant_inductor.gap_fringing_w,
            "resonant_inductor_bundle_w": self.resonant_inductor.bundle_circulating_w,
            "resonant_inductor_termination_w": self.resonant_inductor.termination_w,
        }



@dataclass(frozen=True)
class SystemAnalysis:
    spec: LLCDesignSpec
    tank: TankDesign
    transformer: TransformerDesign
    resonant_inductor: ResonantInductorDesign | None
    bus_capacitor: BusCapacitorResult
    operating_points: tuple[OperatingPointLoss, ...]
    feasible: bool
    feasibility_reasons: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def nominal(self) -> OperatingPointLoss:
        return min(self.operating_points,
                   key=lambda x: abs(x.operating_point.vbus_v - self.spec.vbus_nom_v)
                   + 100.0 * abs(x.operating_point.load_fraction - 1.0))

    @property
    def worst_loss(self) -> OperatingPointLoss:
        return max(self.operating_points, key=lambda x: x.total_loss_w)

    @property
    def minimum_efficiency(self) -> OperatingPointLoss:
        return min(self.operating_points, key=lambda x: x.efficiency)


def default_work_points(spec: LLCDesignSpec) -> list[tuple[float, float]]:
    return [
        (spec.vbus_max_v, 1.00),
        (spec.vbus_nom_v, 1.00),
        (spec.vbus_min_normal_v, 1.00),
        (spec.vbus_hold_end_v, 1.00),
        (spec.vbus_nom_v, 0.50),
        (spec.vbus_nom_v, 0.25),
        (spec.vbus_nom_v, 0.10),
    ]


class LLCSystemAnalyzer:
    def __init__(self, core_database: CoreDatabase | None = None,
                 device_database: DeviceDatabase | None = None):
        self.core_db = core_database or CoreDatabase()
        self.device_db = device_database or DeviceDatabase()

    def evaluate_envelope(
        self,
        spec: LLCDesignSpec,
        envelope: OperatingEnvelope | None = None,
        *,
        zvs_level: int = 2,
    ) -> EnvelopeEvaluation:
        """Operating-envelope + constraint + worst-case evaluation (additive)."""

        primary = self.device_db.get_primary(spec.primary_device)
        return evaluate_operating_envelope(
            spec,
            envelope,
            device_qoss_c=primary.qoss_c,
            device_coss_f=primary.coss_er_f,
            zvs_level=zvs_level,
        )

    def with_time_domain_losses(
        self, analysis: SystemAnalysis, results: Iterable[LLCModelResult],
        *, output_voltage_tolerance_v: float = 0.05,
    ) -> SystemAnalysis:
        """Apply accepted TD semiconductor and passive inputs to an existing result.

        Semiconductor currents/frequency, capacitor ESR/ripple, and magnetic
        flux/current harmonics consume the supplied arrays. Existing material,
        winding and thermal models remain approximations. Commutation/ZVS and
        hardware synthesis/feasibility retain their original FHA assumptions.
        Every requested point must be present, matching and converged. The
        output must also meet the requested voltage (default TD trim tolerance
        0.05 V); periodic convergence alone does not prove regulation.
        """
        from ..analysis.types import FidelityLevel
        from .waveform_loss import (
            bridge_loss_with_provenance,
            rectifier_loss_with_provenance,
        )

        if not math.isfinite(output_voltage_tolerance_v) or output_voltage_tolerance_v <= 0:
            raise ValueError("output-voltage tolerance must be finite and positive")
        supplied = tuple(results)
        if not supplied:
            raise ValueError("time-domain loss requested without any model results")
        points = list(analysis.operating_points)
        seen: set[tuple[float, float]] = set()
        for result in supplied:
            request = result.request
            if request.spec != analysis.spec:
                raise ValueError("time-domain loss input does not match the system specification")
            if request.output_voltage_target_v != analysis.spec.vout_v:
                raise ValueError("time-domain output target does not match the system specification")
            if result.fidelity != FidelityLevel.SWITCHED_TIME_DOMAIN:
                raise ValueError("time-domain loss requires a switched time-domain result")
            if not result.convergence.converged or not math.isfinite(result.convergence.residual_norm):
                raise ValueError("time-domain loss refused: solver did not converge")
            if not math.isclose(result.metrics.switching_frequency_hz,
                                result.waveform.switching_frequency_hz, rel_tol=1e-9):
                raise ValueError("time-domain metric and waveform frequency do not match")
            if (not math.isfinite(result.metrics.output_voltage_v)
                    or abs(result.metrics.output_voltage_v - request.output_voltage_target_v)
                    > output_voltage_tolerance_v):
                raise ValueError("time-domain loss refused: output regulation target was not met")
            key = (request.bus_voltage_v, request.load_fraction)
            if key in seen:
                raise ValueError("duplicate time-domain loss operating point")
            seen.add(key)
            index = next((i for i, point in enumerate(points)
                          if (point.operating_point.vbus_v,
                              point.operating_point.load_fraction) == key), None)
            if index is None:
                raise ValueError(f"time-domain loss operating point {key} is absent from system analysis")
            point = points[index]
            primary = bridge_loss_with_provenance(
                analysis.spec, analysis.tank, point.operating_point,
                self.device_db.get_primary(analysis.spec.primary_device),
                waveform=result.waveform, td_converged=True,
            ).legacy
            sr = rectifier_loss_with_provenance(
                analysis.spec, point.operating_point,
                self.device_db.get_sr(analysis.spec.sr_device),
                waveform=result.waveform, td_converged=True,
            ).legacy
            assert sr is not None  # the selected path is synchronous rectification
            transformer = analysis.transformer.loss(
                analysis.spec, point.operating_point, waveform=result.waveform, td_converged=True)
            inductor = (analysis.resonant_inductor.loss(
                analysis.spec, point.operating_point, waveform=result.waveform, td_converged=True)
                if analysis.resonant_inductor is not None else ResonantInductorLoss(0, 0, 0, 0, 0))
            cr, co = capacitor_results_from_waveform(
                analysis.spec, point.operating_point, result.waveform, td_converged=True)
            if abs(float(np.mean(result.waveform.signal("v_output").values))
                   - request.output_voltage_target_v) > output_voltage_tolerance_v:
                raise ValueError("time-domain waveform output regulation target was not met")
            total = (primary.total_w + sr.total_w + transformer.total_w + inductor.total_w
                     + cr.loss_w + co.capacitor_loss_w + point.auxiliary_w)
            identity = hashlib.sha256()
            identity.update(json.dumps(asdict(request), sort_keys=True, default=str).encode())
            identity.update(repr((request.bus_voltage_v, request.load_fraction,
                                  result.waveform.model_name,
                                  result.waveform.switching_frequency_hz)).encode())
            identity.update(np.asarray(result.waveform.time_s, dtype="<f8").tobytes())
            for signal_key in sorted(result.waveform.signals):
                identity.update(signal_key.encode())
                identity.update(result.waveform.signal(signal_key).unit.encode())
                identity.update(np.asarray(result.waveform.signal(signal_key).values, dtype="<f8").tobytes())
            pout = point.operating_point.pout_w
            points[index] = replace(
                point, primary=primary, synchronous_rectifier=sr,
                transformer=transformer, resonant_inductor=inductor,
                resonant_capacitor=cr, output_capacitor=co,
                magnetic_source="TIME_DOMAIN_WAVEFORM_IGSE_LITZ",
                capacitor_source="TIME_DOMAIN_WAVEFORM_CONSTANT_ESR",
                passive_frequency_hz=result.waveform.switching_frequency_hz,
                loss_waveform_sha256=identity.hexdigest(),
                td_formula_inputs=_td_formula_inputs(analysis, result.waveform, transformer, inductor),
                total_loss_w=total, efficiency=pout / (pout + total),
                semiconductor_source="TIME_DOMAIN_WAVEFORM",
                semiconductor_frequency_hz=result.waveform.switching_frequency_hz,
            )
        note = (
            "Selected semiconductor currents/frequency, capacitor constant-ESR/ripple, and magnetic "
            "iGSE/current-harmonic inputs use converged ideal TD waveforms. "
            "All waveform-derived losses use the final complete cycle; supplied cycles must repeat within 1 ppm. "
            "Transformer flux uses Lm*Im/(Np*Ae), retaining model DC offset; sampled volt-second balance "
            "must be within 1 ppm and Faraday cross-check within dt*total_variation(V). "
            "Physical remanence/nonlinear saturation is unknown; iGSE has no DC-bias loss correction. "
            f"AC winding increments retain harmonics 1..{analysis.spec.litz_max_harmonic}; "
            "AC copper above that cutoff is unmodeled. "
            "Material/geometry/thermal parameters remain engineering estimates; "
            "commutation/ZVS and hardware feasibility remain FHA-based. "
            "Auxiliary power remains empirical; this is not hardware sign-off."
        )
        return replace(analysis, operating_points=tuple(points),
                       warnings=tuple(dict.fromkeys((*analysis.warnings, note))))

    def analyze(self, spec: LLCDesignSpec,
                work_points: Iterable[tuple[float, float]] | None = None,
                preferred_transformer_core: str | None = None,
                preferred_inductor_core: str | None = None) -> SystemAnalysis:
        spec.validate()
        tank = design_tank(spec)
        point_requests = list(work_points or default_work_points(spec))
        solved: list[LLCOperatingPoint] = []
        solve_errors: list[str] = []
        for vbus, load in point_requests:
            try:
                solved.append(solve_operating_point(spec, tank, vbus, load))
            except GainNotReachableError as exc:
                solve_errors.append(f"{vbus:.0f} V / {load*100:.0f}% load: {exc}")
        if not solved:
            raise GainNotReachableError("no requested operating point can be solved")

        if spec.integrated_magnetics:
            if (preferred_transformer_core is not None and
                    preferred_transformer_core.casefold() != spec.integrated_characterized_core.casefold()):
                raise ValueError("changing the characterized integrated core requires re-characterization")
            preferred_transformer_core = spec.integrated_characterized_core
        transformer = design_transformer(
            spec, tank, solved, self.core_db, preferred_transformer_core)
        if spec.integrated_magnetics and preferred_inductor_core is not None:
            raise ValueError("integrated magnetics has no separate resonant-inductor core")
        resonant_inductor = (None if spec.integrated_magnetics else design_resonant_inductor(
            spec, tank, solved, self.core_db, preferred_inductor_core))
        primary_device = self.device_db.get_primary(spec.primary_device)
        sr_device = self.device_db.get_sr(spec.sr_device)

        point_results: list[OperatingPointLoss] = []
        for op in solved:
            p_primary = primary_bridge_loss(spec, tank, op, primary_device)
            p_sr = synchronous_rectifier_loss(spec, op, sr_device)
            p_transformer = transformer.loss(spec, op)
            p_inductor = (resonant_inductor.loss(spec, op) if resonant_inductor is not None
                          else ResonantInductorLoss(0, 0, 0, 0, 0))
            p_cr = resonant_capacitor_result(spec, tank, op)
            p_co = output_capacitor_result(spec, op)
            p_aux = 5.0 + 0.004 * op.pout_w
            total = (p_primary.total_w + p_sr.total_w + p_transformer.total_w
                     + p_inductor.total_w + p_cr.loss_w
                     + p_co.capacitor_loss_w + p_aux)
            efficiency = op.pout_w / (op.pout_w + total) if op.pout_w > 0 else 0.0
            point_results.append(OperatingPointLoss(
                operating_point=op, primary=p_primary,
                synchronous_rectifier=p_sr, transformer=p_transformer,
                resonant_inductor=p_inductor, resonant_capacitor=p_cr,
                output_capacitor=p_co, auxiliary_w=p_aux,
                total_loss_w=total, efficiency=efficiency))

        bus = bus_capacitor_result(spec)
        reasons: list[str] = list(solve_errors)
        warnings: list[str] = []
        if spec.integrated_magnetics:
            warnings.append("Integrated two-winding linear characterization: APR Lr/Lm/neff are derived together; no separate Lr core, copper, thermal node or BOM. Material/geometry losses remain estimates, not hardware qualification.")
            warnings.append(f"Characterization: {spec.integrated_measurement_note}; {spec.integrated_measurement_frequency_hz:g} Hz. Frequency, bias, temperature dependence and stray capacitance require hardware verification.")
            if spec.integrated_target_lr_h is not None:
                error = abs(tank.lr_h/spec.integrated_target_lr_h - 1.0)
                if error > spec.integrated_target_tolerance:
                    reasons.append(f"integrated characterized Lr differs from target by {error*100:.2f}% (limit {spec.integrated_target_tolerance*100:.2f}%)")
        full_load = [p for p in point_results if p.operating_point.load_fraction >= 0.999]
        hard_zvs_points = [p for p in point_results if p.operating_point.load_fraction >= 0.25]

        if not transformer.feasible:
            reasons.extend(f"transformer: {reason}" for reason in transformer.reasons)
        if resonant_inductor is not None and not resonant_inductor.feasible:
            reasons.extend(f"resonant inductor: {reason}" for reason in resonant_inductor.reasons)
        for point in full_load:
            op = point.operating_point
            if op.input_phase_deg < spec.minimum_inductive_angle_deg:
                reasons.append(f"{point.label}: inductive angle is only {op.input_phase_deg:.2f}°")
        for point in hard_zvs_points:
            margin = min(point.primary.zvs_charge_margin,
                         point.primary.zvs_energy_margin)
            if margin < 1.0:
                reasons.append(f"{point.label}: primary ZVS margin {margin:.2f} < 1.0")
            elif margin < spec.primary_zvs_margin_required:
                warnings.append(f"{point.label}: primary ZVS margin {margin:.2f} is below preferred margin")

        max_primary_v = max(p.primary.voltage_stress_v for p in point_results)
        if max_primary_v > primary_device.vds_max_v * spec.primary_voltage_derating:
            reasons.append("primary MOSFET voltage derating failed")
        max_sr_v = max(p.synchronous_rectifier.voltage_stress_v for p in point_results)
        if max_sr_v > sr_device.vds_max_v * spec.sr_voltage_derating:
            reasons.append("SR MOSFET voltage derating failed")
        if max(p.primary.device_peak_current_a for p in point_results) > 0.8 * primary_device.id_cont_a:
            warnings.append("primary device peak-current screen exceeds 80% of reference rating")
        if max(p.synchronous_rectifier.device_peak_current_a for p in point_results) > 0.8 * sr_device.id_cont_a:
            warnings.append("SR device peak-current screen exceeds 80% of reference rating")

        for point in full_load:
            if point.resonant_capacitor.voltage_margin < 1.0:
                reasons.append(f"{point.label}: resonant capacitor voltage rating failed")
            if point.resonant_capacitor.current_margin < 1.0:
                reasons.append(f"{point.label}: resonant capacitor current rating failed")
            if point.output_capacitor.total_ripple_vpp > spec.output_ripple_limit_vpp:
                reasons.append(f"{point.label}: output ripple exceeds limit")

        if bus.hold_time_to_limit_s + 1e-12 < spec.requested_hold_time_s:
            reasons.append("installed bus capacitance does not meet requested hold-up time")
        if bus.predicted_end_voltage_v < spec.vbus_hold_end_v:
            reasons.append("bus voltage falls below LLC hold-up endpoint")

        if point_results[-1].operating_point.load_fraction <= 0.10:
            light = point_results[-1]
            margin = min(light.primary.zvs_charge_margin,
                         light.primary.zvs_energy_margin)
            if margin < spec.primary_zvs_margin_required:
                warnings.append("10% load point may require burst/skip control for robust ZVS")

        for point in point_results:
            if point.transformer.estimated_hotspot_c > spec.magnetic_hotspot_limit_c:
                reasons.append(f"{point.label}: transformer hotspot {point.transformer.estimated_hotspot_c:.1f} °C exceeds limit")
            if point.resonant_inductor.estimated_hotspot_c > spec.magnetic_hotspot_limit_c:
                reasons.append(f"{point.label}: resonant-inductor hotspot {point.resonant_inductor.estimated_hotspot_c:.1f} °C exceeds limit")
            warnings.extend(f"{point.label} transformer material: {w}" for w in point.transformer.material_warnings)
            warnings.extend(f"{point.label} inductor material: {w}" for w in point.resonant_inductor.material_warnings)

        warnings.append("Core geometry and ferrite coefficients are engineering reference records; verify the exact ordering code and fit manufacturer curves before release.")
        warnings.append("Magnetic core loss uses temperature-interpolated iGSE on reconstructed B(t); minor-loop and DC-bias corrections require measured material data.")
        warnings.append("Litz loss uses exact round-strand skin effect, harmonic layer-MMF proximity loss, transposition and termination penalties; 2D/3D FEA remains the final verification method.")
        if spec.integrated_magnetics:
            warnings.append("Integrated transformer leakage is included through the full APR Lr/Lm/effective-ratio transformation and physical winding/core state mapping; winding geometry and hardware realization still require characterization. No independent resonant-inductor core or gap-fringing loss is added.")
        else:
            warnings.append("Resonant-inductor gap-fringing winding loss is enabled through a distance-based calibrated field term; transformer leakage contribution to Lr remains separate.")

        # Low-risk wire: identify the worst ZVS corner among solved work points
        # using the Level-2 charge-balance definition (does not change feasibility).
        try:
            import math as _math

            from ..core.zvs_margin import evaluate_zvs_margin
            worst_label = None
            worst_margin = float("inf")
            worst_zvs = None
            for point in point_results:
                zvs = evaluate_zvs_margin(
                    spec, tank, point.operating_point, device=primary_device, level=2)
                if _math.isnan(zvs.zvs_margin):
                    continue
                if zvs.zvs_margin < worst_margin:
                    worst_margin = zvs.zvs_margin
                    worst_label = point.label
                    worst_zvs = zvs
            if worst_zvs is not None and worst_label is not None:
                warnings.append(
                    f"Worst ZVS charge-balance corner {worst_label}: "
                    f"ZVS_MARGIN={worst_zvs.zvs_margin:.3f}, "
                    f"MODEL={worst_zvs.model_source}, "
                    f"Qavail={worst_zvs.q_available:.3e} C, "
                    f"Qreq={worst_zvs.q_required:.3e} C"
                )
        except Exception as exc:
            warnings.append(f"ZVS charge-balance summary unavailable: {exc}")

        return SystemAnalysis(
            spec=spec, tank=tank, transformer=transformer,
            resonant_inductor=resonant_inductor, bus_capacitor=bus,
            operating_points=tuple(point_results), feasible=not reasons,
            feasibility_reasons=tuple(reasons), warnings=tuple(warnings))

    def engineering_validation(self, spec: LLCDesignSpec, **kwargs):
        """Envelope + critical FHA↔TD + evidence report (does not replace analyze())."""
        from ..analysis.engineering_validation import run_engineering_validation
        return run_engineering_validation(spec, analyzer=self, **kwargs)
