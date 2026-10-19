from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from fpdf import FPDF

from .. import __version__
from ..core.spec import TankParameterMode
from ..models.devices import DeviceDatabase
from ..models.system import SystemAnalysis
from ..validation import validate_bundled_data


@dataclass(frozen=True)
class FormulaStep:
    name: str
    formula: str
    substitution: str
    result: str
    method: str = ""


def _value(value: float, unit: str = "", digits: int = 6) -> str:
    suffix = f" {unit}" if unit else ""
    return f"{float(value):.{digits}g}{suffix}"


def build_formula_steps(analysis: SystemAnalysis) -> dict[str, list[FormulaStep]]:
    spec = analysis.spec
    tank = analysis.tank
    point = analysis.nominal
    op = point.operating_point
    primary = point.primary
    sr = point.synchronous_rectifier
    db = DeviceDatabase()
    primary_device = db.get_primary(spec.primary_device)
    sr_device = db.get_sr(spec.sr_device)

    turns_ratio = spec.electrical_turns_ratio
    rload = spec.vout_v**2 / spec.pout_w
    omega_r = 2.0 * math.pi * tank.fr_hz
    omega_s = 2.0 * math.pi * op.switching_frequency_hz
    z_series = 1j * omega_s * tank.lr_h + 1.0 / (1j * omega_s * tank.cr_f)
    z_lm = 1j * omega_s * tank.lm_h
    z_parallel = 1.0 / (1.0 / op.rac_ohm + 1.0 / z_lm)
    transfer = z_parallel / (z_series + z_parallel)
    allowed_primary_v = primary_device.vds_max_v * spec.primary_voltage_derating
    allowed_sr_v = sr_device.vds_max_v * spec.sr_voltage_derating

    tank_steps = [
        FormulaStep(
            "Transformer turns ratio",
            "n := Np / Ns",
            f"n := {spec.primary_turns} / {spec.secondary_turns}",
            f"n = {_value(turns_ratio)}",
        ),
        FormulaStep(
            "DC load resistance",
            "Rload := Vo^2 / Po",
            f"Rload := {spec.vout_v:g}^2 / {spec.pout_w:g}",
            f"Rload = {_value(rload, 'ohm')}",
        ),
        FormulaStep(
            "FHA load reflected to primary",
            "Rac := (8/pi^2) * n^2 * Rload",
            f"Rac := (8/pi^2) * {turns_ratio:g}^2 * {rload:.6g}",
            f"Rac = {_value(tank.rac_nom_ohm, 'ohm')}",
        ),
    ]
    if spec.integrated_magnetics or TankParameterMode(spec.parameter_mode) == TankParameterMode.USER_DEFINED:
        tank_steps.extend([
            FormulaStep(
                "Characteristic impedance from entered tank",
                "Zr := sqrt(Lr / Cr)",
                f"Zr := sqrt({tank.lr_h:.6g} / {tank.cr_f:.6g})",
                f"Zr = {_value(tank.zr_ohm, 'ohm')}",
                "USER_DEFINED verification path; entered Lr, Cr and Lm are not optimized.",
            ),
            FormulaStep(
                "Resonant frequency from entered tank",
                "fr := 1 / (2*pi*sqrt(Lr*Cr))",
                f"fr := 1 / (2*pi*sqrt({tank.lr_h:.6g}*{tank.cr_f:.6g}))",
                f"fr = {_value(tank.fr_hz / 1e3, 'kHz')}",
            ),
            FormulaStep(
                "Inductance ratio from entered tank",
                "Ln := Lm / Lr",
                f"Ln := {tank.lm_h:.6g} / {tank.lr_h:.6g}",
                f"Ln = {_value(tank.ln_ratio)}",
            ),
            FormulaStep(
                "Loaded quality factor from entered tank",
                "Qe := Zr / Rac",
                f"Qe := {tank.zr_ohm:.6g} / {tank.rac_nom_ohm:.6g}",
                f"Qe = {_value(tank.q_full_load)}",
            ),
        ])
    else:
        tank_steps.extend([
            FormulaStep(
                "Characteristic impedance",
                "Zr := Qe * Rac",
                f"Zr := {tank.q_full_load:g} * {tank.rac_nom_ohm:.6g}",
                f"Zr = {_value(tank.zr_ohm, 'ohm')}",
            ),
            FormulaStep(
                "Resonant angular frequency",
                "wr := 2 * pi * fr",
                f"wr := 2 * pi * {tank.fr_hz:.6g}",
                f"wr = {_value(omega_r, 'rad/s')}",
            ),
            FormulaStep(
                "Resonant inductance",
                "Lr := Zr / wr",
                f"Lr := {tank.zr_ohm:.6g} / {omega_r:.6g}",
                f"Lr = {_value(tank.lr_h * 1e6, 'uH')}",
            ),
            FormulaStep(
                "Resonant capacitance",
                "Cr := 1 / (wr * Zr)",
                f"Cr := 1 / ({omega_r:.6g} * {tank.zr_ohm:.6g})",
                f"Cr = {_value(tank.cr_f * 1e9, 'nF')}",
            ),
            FormulaStep(
                "Magnetizing inductance",
                "Lm := Ln * Lr",
                f"Lm := {tank.ln_ratio:g} * {tank.lr_h * 1e6:.6g} uH",
                f"Lm = {_value(tank.lm_h * 1e6, 'uH')}",
            ),
        ])

    sections = {
        "Resonant tank synthesis": tank_steps,
        "Nominal operating point": [
            FormulaStep(
                "Required normalized gain",
                "Mreq := n * (Vo + Vrect) / (Kbridge * Vbus)",
                f"Mreq := {turns_ratio:g} * ({spec.vout_v:g} + {spec.rectifier_equivalent_drop_v:g}) / ({spec.bridge_gain:g} * {op.vbus_v:g})",
                f"Mreq = {_value(op.required_gain)}",
            ),
            FormulaStep(
                "Series tank impedance",
                "Zs := j*ws*Lr + 1/(j*ws*Cr)",
                f"Zs := j*{omega_s:.6g}*{tank.lr_h:.6g} + 1/(j*{omega_s:.6g}*{tank.cr_f:.6g})",
                f"Zs = {z_series.real:.6g} + j*{z_series.imag:.6g} ohm",
            ),
            FormulaStep(
                "Parallel branch impedance",
                "Zp := 1 / (1/Rac + 1/(j*ws*Lm))",
                f"Zp := 1 / (1/{op.rac_ohm:.6g} + 1/(j*{omega_s:.6g}*{tank.lm_h:.6g}))",
                f"Zp = {z_parallel.real:.6g} + j*{z_parallel.imag:.6g} ohm",
            ),
            FormulaStep(
                "Tank gain and frequency root",
                "M(fs) := abs(Zp / (Zs + Zp)); solve M(fs) = Mreq",
                f"abs(({z_parallel.real:.5g}+j*{z_parallel.imag:.5g}) / ({(z_series+z_parallel).real:.5g}+j*{(z_series+z_parallel).imag:.5g}))",
                f"fs = {_value(op.switching_frequency_hz / 1e3, 'kHz')}; M = {_value(abs(transfer))}; branch = {op.branch}",
                "Logarithmic scan (1600 samples) brackets every root, then Brent's method refines it.",
            ),
            FormulaStep(
                "Resonant current",
                "Ir,rms := Vbridge,1,rms / abs(Zin); Ir,pk := sqrt(2)*Ir,rms",
                f"Ir,rms := {op.bridge_fundamental_rms_v:.6g} / {abs(op.input_impedance_ohm):.6g}",
                f"Ir,rms = {_value(op.resonant_current_rms_a, 'A')}; Ir,pk = {_value(op.resonant_current_peak_a, 'A')}",
            ),
            FormulaStep(
                "Commutation current",
                "Icomm := max(0.75*Imag,pk, abs(Ir,pk*sin(phi)))",
                f"Icomm := max(0.75*{op.magnetizing_current_peak_a:.6g}, abs({op.resonant_current_peak_a:.6g}*sin({op.input_phase_deg:.6g} deg)))",
                f"Icomm = {_value(op.commutation_current_a, 'A')}",
            ),
        ],
        "Primary bridge and ZVS": [
            FormulaStep(
                "Primary conduction loss",
                "Pcond := Ir,rms^2 * Nseries * Rds,on(Tj) / Nparallel",
                f"Pcond := {op.resonant_current_rms_a:.6g}^2 * {spec.bridge_series_devices} * ({primary_device.rds_at(spec.primary_junction_temperature_c):.6g}/{spec.primary_parallel_devices})",
                f"Pcond = {_value(primary.conduction_w, 'W')}",
            ),
            FormulaStep(
                "Primary gate-drive loss",
                "Pgate := Ndevice * Nparallel * Qg * Vg * fs",
                f"Pgate := {spec.bridge_device_count} * {spec.primary_parallel_devices} * {primary_device.qg_c:.6g} * {primary_device.gate_voltage_v:.6g} * {op.switching_frequency_hz:.6g}",
                f"Pgate = {_value(primary.gate_drive_w, 'W')}",
            ),
            FormulaStep(
                "Primary turn-off loss",
                "Eoff := Eref*(Vbus/Vref)*(Ioff/Iref); Poff := Ndevice*Nparallel*Eoff*fs",
                f"Ioff := {op.commutation_current_a:.6g}*{spec.primary_turnoff_current_factor:.6g}/{spec.primary_parallel_devices}; fs := {op.switching_frequency_hz:.6g}",
                f"Poff = {_value(primary.turnoff_w, 'W')}",
            ),
            FormulaStep(
                "ZVS charge margin",
                "MQ := Icomm * tdead / (2*Nparallel*max(Qoss, Coss*Vbus))",
                f"MQ := {op.commutation_current_a:.6g} * {spec.primary_deadtime_s:.6g} / (2*{spec.primary_parallel_devices}*max({primary_device.qoss_c:.6g}, {primary_device.coss_er_f:.6g}*{op.vbus_v:.6g}))",
                f"MQ = {_value(primary.zvs_charge_margin)}",
            ),
            FormulaStep(
                "ZVS energy margin",
                "ME := 0.5*(Lr+Lm)*Icomm^2 / (2*Nparallel*0.5*Coss*Vbus^2)",
                f"ME := 0.5*({tank.lr_h:.6g}+{tank.lm_h:.6g})*{op.commutation_current_a:.6g}^2 / (2*{spec.primary_parallel_devices}*0.5*{primary_device.coss_er_f:.6g}*{op.vbus_v:.6g}^2)",
                f"ME = {_value(primary.zvs_energy_margin)}; effective margin = {_value(min(primary.zvs_charge_margin, primary.zvs_energy_margin))}",
            ),
            FormulaStep(
                "Residual Coss loss after incomplete ZVS",
                "r := clamp(1-min(MQ,ME),0,1)^2; Pcoss := Ndevice*Nparallel*Eoss*fs*r",
                f"r := {primary.zvs_residual_fraction:.6g}; Eoss := 0.5*{primary_device.coss_er_f:.6g}*{op.vbus_v:.6g}^2",
                f"Pcoss = {_value(primary.residual_coss_w, 'W')}",
            ),
            FormulaStep(
                "Primary dead-time diode loss",
                "tcharge := Qreq/Icomm; tdiode := max(tdead-tcharge,0); Pdiode := Ncomm*Vf*Icomm*tdiode*fs",
                f"tdead := {spec.primary_deadtime_s:.6g}; Icomm := {op.commutation_current_a:.6g}; Ncomm := {2*spec.bridge_series_devices}",
                f"Pdiode = {_value(primary.deadtime_diode_w, 'W')}",
            ),
            FormulaStep(
                "Primary voltage derating",
                "Vstress <= Vds,max * derating",
                f"{primary.voltage_stress_v:.6g} <= {primary_device.vds_max_v:.6g} * {spec.primary_voltage_derating:.6g}",
                f"{_value(primary.voltage_stress_v, 'V')} <= {_value(allowed_primary_v, 'V')} : {'PASS' if primary.voltage_stress_v <= allowed_primary_v else 'FAIL'}",
            ),
        ],
        "Synchronous rectifier and capacitors": [
            FormulaStep(
                "SR conduction loss",
                "Psr,cond := Is,rms^2 * 2*Rds,on(Tj) / Nparallel",
                f"Psr,cond := {op.secondary_current_rms_a:.6g}^2 * 2*{sr_device.rds_at(spec.sr_junction_temperature_c):.6g}/{spec.sr_parallel_devices_per_position}",
                f"Psr,cond = {_value(sr.conduction_w, 'W')}",
            ),
            FormulaStep(
                "SR dead-time diode loss",
                "Pdiode := 4*Vf*(1/2)*Is,pk*ws*tw^2*fs; tw := max(tdead+tadvance,0)",
                f"tw := max({spec.sr_deadtime_s:.6g}+{spec.sr_turnoff_advance_s:.6g},0); Is,pk := {op.secondary_current_peak_a:.6g}",
                f"Pdiode = {_value(sr.deadtime_diode_w, 'W')}",
            ),
            FormulaStep(
                "SR turn-off loss",
                "Ioff := Is,pk*sin(min(ws*tadvance,pi/2))/Nparallel; Poff := 4*Nparallel*Eoff*fs",
                f"Is,pk := {op.secondary_current_peak_a:.6g}; tadvance := {spec.sr_turnoff_advance_s:.6g}",
                f"Poff = {_value(sr.turnoff_w, 'W')}",
            ),
            FormulaStep(
                "SR output-capacitance loss",
                "Eoss := 0.5*Coss*Vstress^2; Pcoss := 4*Nparallel*Eoss*fs*Kloss",
                f"Vstress := {sr.voltage_stress_v:.6g}; Kloss := {spec.sr_coss_dissipation_factor:.6g}",
                f"Pcoss = {_value(sr.coss_w, 'W')}",
            ),
            FormulaStep(
                "SR gate-drive loss",
                "Pgate := 4*Nparallel*Qg*Vg*fs",
                f"Pgate := 4*{spec.sr_parallel_devices_per_position}*{sr_device.qg_c:.6g}*{sr_device.gate_voltage_v:.6g}*{op.switching_frequency_hz:.6g}",
                f"Pgate = {_value(sr.gate_drive_w, 'W')}",
            ),
            FormulaStep(
                "SR voltage derating",
                "Vsr := Kov * Vo; Vsr <= Vds,max * derating",
                f"Vsr := {spec.sr_voltage_overshoot_factor:.6g} * {spec.vout_v:.6g}; limit := {sr_device.vds_max_v:.6g} * {spec.sr_voltage_derating:.6g}",
                f"{_value(sr.voltage_stress_v, 'V')} <= {_value(allowed_sr_v, 'V')} : {'PASS' if sr.voltage_stress_v <= allowed_sr_v else 'FAIL'}",
            ),
            FormulaStep(
                "Resonant-capacitor voltage",
                "Xc := 1/(2*pi*fs*Cr); Vcr,rms := Ir,rms*Xc; Vcr,pk := Vbias + sqrt(2)*Vcr,rms",
                f"Xc := 1/(2*pi*{op.switching_frequency_hz:.6g}*{tank.cr_f:.6g})",
                f"Vcr,rms = {_value(point.resonant_capacitor.voltage_rms_v, 'V')}; Vcr,pk = {_value(point.resonant_capacitor.voltage_peak_v, 'V')}",
            ),
            FormulaStep(
                "Resonant-capacitor ESR loss",
                "Pcr := Ir,rms^2 * ESRcr",
                f"Pcr := {op.resonant_current_rms_a:.6g}^2 * {spec.resonant_cap_esr_ohm:.6g}",
                f"Pcr = {_value(point.resonant_capacitor.loss_w, 'W')}",
            ),
            FormulaStep(
                "Output-capacitor ripple",
                "icap(t) := (pi*Io/2)*sin(pi*t/Tripple) - Io; q(t) := integral(icap dt); v(t) := q/C + ESR*icap",
                f"Io={op.output_current_a:.6g} A; Tripple=1/(2*{op.switching_frequency_hz:.6g}); C={spec.output_capacitance_f:.6g} F; ESR={spec.output_cap_esr_ohm:.6g} ohm",
                f"DeltaVpp = {_value(point.output_capacitor.total_ripple_vpp, 'V')}; Pcap = {_value(point.output_capacitor.capacitor_loss_w, 'W')}",
                "The waveform is numerically integrated with 4096 samples; it is not replaced by a ripple shortcut.",
            ),
        ],
        "Magnetics, hold-up and totals": [
            FormulaStep(
                "Transformer peak flux density",
                "Bpk := Vpri,square / (4*Np*Amin*fs)",
                f"Bpk := {op.transformer_square_equivalent_v:.6g} / (4*{spec.primary_turns}*{analysis.transformer.core.amin_m2:.6g}*{op.switching_frequency_hz:.6g})",
                f"Bpk = {_value(point.transformer.b_peak_min_area_t, 'T')}; limit = {_value(spec.transformer_max_b_t, 'T')}",
            ),
            FormulaStep(
                "Transformer equivalent air gap",
                "g := mu0*Np^2*Ae/Lm - le/mur",
                f"g := 4*pi*1e-7*{spec.primary_turns}^2*{analysis.transformer.core.ae_m2:.6g}/{tank.lm_h:.6g} - {analysis.transformer.core.le_m:.6g}/{analysis.transformer.core.mu_r:.6g}",
                f"g = {_value(analysis.transformer.gap_total_mm, 'mm')}; fill = {_value(analysis.transformer.fill_factor*100, '%')}",
            ),
            FormulaStep(
                "Resonant-inductor peak flux density",
                "Bpk := Lr * Ir,pk / (N * Ae)",
                (f"Bpk := {tank.lr_h:.6g} * {op.resonant_current_peak_a:.6g} / ({analysis.resonant_inductor.turns} * {analysis.resonant_inductor.core.ae_m2:.6g})"
                 if analysis.resonant_inductor is not None else "NOT_APPLICABLE: no external Lr core"),
                f"Bpk = {_value(point.resonant_inductor.b_peak_t, 'T')}; limit = {_value(spec.resonant_inductor_max_b_t, 'T')}",
            ),
            FormulaStep(
                "Resonant-inductor equivalent air gap",
                "g := mu0*N^2*Ae/Lr - le/mur",
                (f"N := {analysis.resonant_inductor.turns}; Ae := {analysis.resonant_inductor.core.ae_m2:.6g}; Lr := {tank.lr_h:.6g}"
                 if analysis.resonant_inductor is not None else "NOT_APPLICABLE: no external Lr winding/gap"),
                (f"g = {_value(analysis.resonant_inductor.gap_total_mm, 'mm')}; fill = {_value(analysis.resonant_inductor.fill_factor*100, '%')}"
                 if analysis.resonant_inductor is not None else "NOT_APPLICABLE"),
            ),
            FormulaStep(
                "Magnetic core loss by iGSE",
                "Pv := (1/T)*integral(ki*abs(dB/dt)^alpha*(DeltaB)^(beta-alpha) dt); Pcore := Pv*Ve",
                f"waveform samples := {spec.magnetic_waveform_samples}; transformer material := {analysis.transformer.core.material}; inductor material := {analysis.resonant_inductor.core.material if analysis.resonant_inductor is not None else 'ABSENT'}",
                f"Pcore,xfmr = {_value(point.transformer.core_w, 'W')}; Pcore,Lr = {_value(point.resonant_inductor.core_w, 'W')}",
                "Temperature-interpolated material coefficients are applied to reconstructed B(t).",
            ),
            FormulaStep(
                "Litz winding loss by harmonic layer MMF",
                "i(t) -> FFT(Ih); Pcu := Pdc + Pskin + Pprox + Pbundle + Pterm (+ Pgap for Lr)",
                f"harmonics := 1..{spec.litz_max_harmonic}; transformer stack := P/2-S-P/2",
                f"Pcu,xfmr = {_value(point.transformer.primary_copper_w + point.transformer.secondary_copper_w, 'W')}; Pcu,Lr = {_value(point.resonant_inductor.copper_w, 'W')}",
                "Round-strand skin effect, layer MMF, transposition and termination terms remain separate in the ledger.",
            ),
            FormulaStep(
                "Magnetic thermal fixed-point iteration",
                "Tpred := Tamb + Ploss(T)*Rth; Tnext := 0.55*T + 0.45*Tpred",
                f"Tamb := {spec.ambient_temperature_c:.6g}; tolerance := {spec.magnetic_thermal_tolerance_c:.6g}; max iterations := {spec.magnetic_thermal_max_iterations}",
                f"Thot,xfmr = {_value(point.transformer.estimated_hotspot_c, 'degC')}; Thot,Lr = {_value(point.resonant_inductor.estimated_hotspot_c, 'degC')}",
            ),
            FormulaStep(
                "DC-link required capacitance",
                "Creq := 2*Pin*thold / (Vstart^2 - Vend^2)",
                f"Creq := 2*{spec.pout_w/spec.efficiency_assumption:.6g}*{spec.requested_hold_time_s:.6g} / ({spec.vbus_nom_v:.6g}^2-{spec.vbus_hold_end_v:.6g}^2)",
                f"Creq = {_value(analysis.bus_capacitor.required_capacitance_f * 1e6, 'uF')}; installed = {_value(spec.bus_capacitance_f * 1e6, 'uF')}",
            ),
            FormulaStep(
                "Total modeled loss",
                "Ploss := sum(non-overlapping loss buckets)",
                " + ".join(f"{float(value):.4g}" for value in point.breakdown().values()),
                f"Ploss = {_value(point.total_loss_w, 'W')}",
            ),
            FormulaStep(
                "Auxiliary loss model",
                "Paux := 5 W + 0.004*Po",
                f"Paux := 5 + 0.004*{op.pout_w:.6g}",
                f"Paux = {_value(point.auxiliary_w, 'W')}",
            ),
            FormulaStep(
                "Efficiency",
                "eta := Po / (Po + Ploss)",
                f"eta := {op.pout_w:.6g} / ({op.pout_w:.6g} + {point.total_loss_w:.6g})",
                f"eta = {_value(point.efficiency * 100, '%')}",
            ),
        ],
    }
    if _has_td_inputs(point):
        sections = _td_formula_steps(analysis, sections)
    if spec.integrated_magnetics:
        sections = _integrated_formula_steps(analysis, sections)
    return sections


def _has_td_inputs(point) -> bool:
    return any(source.startswith("TIME_DOMAIN") for source in point.component_sources().values())


def _input(value: float | None) -> str:
    return "NOT_AVAILABLE" if value is None or not math.isfinite(value) else f"{value:.6g}"


def _td_formula_steps(analysis, sections):
    """Replace only affected substitutions; never reuse FHA inputs for TD W.

    Result objects retain the actual device/capacitor scalars. New analyses
    additionally retain an immutable iGSE/FFT input summary; older objects
    explicitly lack that evidence rather than reverse-fitting their losses.
    """
    s, p, tank = analysis.spec, analysis.nominal, analysis.tank
    op = p.operating_point
    primary, sr = p.primary, p.synchronous_rectifier
    db = DeviceDatabase()
    pd, sd = db.get_primary(s.primary_device), db.get_sr(s.sr_device)
    trace = getattr(p, "td_formula_inputs", None)
    semiconductor_td = p.semiconductor_source.startswith("TIME_DOMAIN")
    capacitor_td = p.capacitor_source.startswith("TIME_DOMAIN")
    magnetic_td = p.magnetic_source.startswith("TIME_DOMAIN")
    fs = _input(p.semiconductor_frequency_hz)
    pfs = _input(p.passive_frequency_hz)
    # Device RMS/peak fields were populated by the actual loss consumer, not
    # inferred backwards from P/R, and remain available in historical objects.
    ir = primary.device_rms_current_a * math.sqrt(2) * s.primary_parallel_devices
    isec = sr.device_rms_current_a * math.sqrt(2) * s.sr_parallel_devices_per_position
    ispk = sr.device_peak_current_a * s.sr_parallel_devices_per_position
    overrides = {}

    def edit(name, *, formula=None, substitution=None, method=None, result=None):
        original = next(step for group in sections.values() for step in group if step.name == name)
        changes = {key: value for key, value in {"formula": formula, "substitution": substitution,
                    "method": method, "result": result}.items() if value is not None}
        overrides[name] = replace(original, **changes)

    # Operating-point gain, phase and commutation do not become TD estimates
    # merely because the loss ledger was upgraded.
    for step in sections["Nominal operating point"]:
        edit(step.name, method="FHA reference retained, not a TD waveform measurement. " + step.method)
    for name in ("ZVS charge margin", "ZVS energy margin"):
        edit(name, method="FHA commutation current and charge/energy approximation retained; not TD ZVS verification.")
    for name in ("Transformer equivalent air gap", "Resonant-inductor equivalent air gap"):
        edit(name, method="Original FHA hardware synthesis/geometry retained; TD losses do not redesign the winding or gap.")
    edit("DC-link required capacitance", method="Original efficiency-assumption sizing retained; not recomputed from TD loss.")

    if semiconductor_td:
        current_note = ("TIME_DOMAIN_WAVEFORM: final complete cycle, RMS=sqrt(mean(i[k]^2)). "
                        "Rds and branch-sharing model remain engineering approximations.")
        edit("Primary conduction loss", substitution=f"Pcond := {ir:.6g}^2 * {s.bridge_series_devices} * ({pd.rds_at(s.primary_junction_temperature_c):.6g}/{s.primary_parallel_devices}); Tj={s.primary_junction_temperature_c:g} degC",
             method=current_note)
        edit("Primary gate-drive loss", substitution=f"Pgate := {s.bridge_device_count} * {s.primary_parallel_devices} * {pd.qg_c:.6g} * {pd.gate_voltage_v:.6g} * {fs}",
             method="TD event frequency; datasheet Qg and assumed gate voltage.")
        edit("Primary turn-off loss", substitution=f"Ioff := {op.commutation_current_a:.6g}*{s.primary_turnoff_current_factor:.6g}/{s.primary_parallel_devices}; Eoff := {pd.eoff_ref_j:.6g}*({op.vbus_v:g}/{pd.eoff_ref_v:g})*(Ioff/{pd.eoff_ref_i:g}); Poff := {s.bridge_device_count}*{s.primary_parallel_devices}*Eoff*{fs}",
             method="FHA commutation current retained; actual TD frequency. Scaled Eoff proxy, not sampled switching-energy integration.")
        edit("Residual Coss loss after incomplete ZVS", substitution=f"r := {primary.zvs_residual_fraction:.6g}; Eoss := 0.5*{pd.coss_er_f:.6g}*{op.vbus_v:.6g}^2; Pcoss := {s.bridge_device_count}*{s.primary_parallel_devices}*Eoss*{fs}*r",
             method="FHA ZVS residual fraction retained; TD event frequency. Residual-Coss turn-on counted once.")
        qreq = 2 * s.primary_parallel_devices * max(pd.qoss_c, pd.coss_er_f * op.vbus_v)
        charge = qreq / max(op.commutation_current_a, 1e-9)
        diode = max(s.primary_deadtime_s - charge, 0)
        edit("Primary dead-time diode loss", substitution=f"Qreq={qreq:.6g} C; tcharge={charge:.6g} s; tdiode={diode:.6g} s; Pdiode := {2*s.bridge_series_devices}*{pd.body_diode_vf_v:g}*{op.commutation_current_a:.6g}*{diode:.6g}*{fs}",
             method="FHA commutation current/charge-time proxy retained; TD event frequency, not measured diode current.")
        edit("SR conduction loss", substitution=f"Psr,cond := {isec:.6g}^2 * 2*{sd.rds_at(s.sr_junction_temperature_c):.6g}/{s.sr_parallel_devices_per_position}; Tj={s.sr_junction_temperature_c:g} degC", method=current_note)
        window = max(s.sr_deadtime_s + s.sr_turnoff_advance_s, 0)
        edit("SR dead-time diode loss", substitution=f"Pdiode := 4*{sd.body_diode_vf_v:g}*0.5*{ispk:.6g}*(2*pi*{fs})*{window:.6g}^2*{fs}",
             method="TD sampled secondary peak and frequency; sinusoidal near-zero slope/timing-window approximation retained, not actual branch integral.")
        edit("SR turn-off loss", substitution=f"Ioff := {ispk:.6g}*sin(min(2*pi*{fs}*{s.sr_turnoff_advance_s:.6g},pi/2))/{s.sr_parallel_devices_per_position}; Eoff := {sd.eoff_ref_j:.6g}*({s.vout_v:g}/{sd.eoff_ref_v:g})*(Ioff/{sd.eoff_ref_i:g}); Poff := 4*{s.sr_parallel_devices_per_position}*Eoff*{fs}",
             method="TD sampled secondary peak and frequency; sinusoidal turn-off advance and datasheet Eoff scaling remain proxies.")
        edit("SR output-capacitance loss", substitution=f"Eoss := 0.5*{sd.coss_er_f:.6g}*{sr.voltage_stress_v:.6g}^2; Pcoss := 4*{s.sr_parallel_devices_per_position}*Eoss*{fs}*{s.sr_coss_dissipation_factor:.6g}", method="TD event frequency; constant Coss and voltage-overshoot estimate retained.")
        edit("SR gate-drive loss", substitution=f"Pgate := 4*{s.sr_parallel_devices_per_position}*{sd.qg_c:.6g}*{sd.gate_voltage_v:.6g}*{fs}", method="TD event frequency; datasheet Qg and assumed gate voltage.")

    if capacitor_td:
        cr, co = p.resonant_capacitor, p.output_capacitor
        edit("Resonant-capacitor voltage",
             formula="Vcr,ac,rms := sqrt(mean((vcr[k]-mean(vcr))^2)); Vcr,pk := max(abs(vcr[k]+Vbridge,dc))",
             substitution=f"TD retained statistics: Vcr,ac,rms={cr.voltage_rms_v:.6g} V; mean(vcr+Vbridge,dc)={cr.voltage_dc_bias_v:.6g} V; max(abs(vcr+Vbridge,dc))={cr.voltage_peak_v:.6g} V; fs={pfs} Hz",
             method="Actual TD Cr voltage samples, not Ir*Xc. Physical half-bridge Vbus/2 bias added exactly once; full bridge bias is zero.")
        edit("Resonant-capacitor ESR loss", substitution=f"Pcr := {cr.current_rms_a:.6g}^2 * {s.resonant_cap_esr_ohm:.6g}", method="TIME_DOMAIN_WAVEFORM_CONSTANT_ESR: RMS from accepted i_resonant samples, constant ESR approximation.")
        edit("Output-capacitor ripple",
             formula="DeltaVc := ptp(v_output_cap_internal[k]); DeltaVesr := ptp(ESR*i_output_cap[k]); DeltaVo := ptp(v_output[k]); Pcap := mean(i_output_cap[k]^2)*ESR",
             substitution=f"DeltaVc={co.capacitive_ripple_vpp:.6g} V; DeltaVesr={co.esr_ripple_vpp:.6g} V; DeltaVo={co.total_ripple_vpp:.6g} V; Icap,rms={co.capacitor_current_rms_a:.6g} A; Pcap := {co.capacitor_current_rms_a:.6g}^2*{s.output_cap_esr_ohm:.6g}; fs={pfs} Hz",
             method="Actual TD internal and terminal voltage states. ptp=max-min; total is not the sum of independent maxima. No rectified-sine/4096-sample reconstruction.")

    extra = []
    if magnetic_td:
        if trace is None:
            flux_sub = "TD magnetizing input summary NOT_AVAILABLE in this historical analysis; no FHA substitution or inverse fit is used."
        else:
            flux_sub = f"Bpk,Amin := {tank.lm_h:.6g}*{trace.magnetizing_peak_a:.6g}/({s.primary_turns}*{analysis.transformer.core.amin_m2:.6g}); Bdc,Ae := {tank.lm_h:.6g}*{trace.magnetizing_mean_a:.6g}/({s.primary_turns}*{analysis.transformer.core.ae_m2:.6g})"
        edit("Transformer peak flux density", formula="B[k] := Lm*i_magnetizing[k]/(Np*Ae); Bpk,Amin := max(abs(B[k]))*Ae/Amin", substitution=flux_sub,
             method="TD magnetizing state retains DC offset; sampled Faraday/volt-second cross-check applied by consumer. Physical remanence and nonlinear saturation remain unmodeled.",
             result=f"Bpk,Amin = {_value(p.transformer.b_peak_min_area_t, 'T')}; Bdc,Ae = {_value(p.transformer.b_dc_t, 'T')}; limit = {_value(s.transformer_max_b_t, 'T')}")
        irpk = trace.resonant_peak_a if trace is not None else primary.device_peak_current_a * s.primary_parallel_devices
        if analysis.resonant_inductor is not None:
            edit("Resonant-inductor peak flux density", formula="B[k] := Lr*i_resonant[k]/(N*Ae); Bpk,Ae := max(abs(B[k])); Bpk,Amin := Bpk,Ae*Ae/Amin",
                 substitution=f"Bpk := {analysis.resonant_inductor.inductance_h:.6g}*{irpk:.6g}/({analysis.resonant_inductor.turns}*{analysis.resonant_inductor.core.ae_m2:.6g})",
                 method="TD sampled resonant peak, not the retained FHA peak; original hardware geometry retained.",
                 result=f"Bpk,Ae = {_value(p.resonant_inductor.b_peak_t, 'T')}; Bpk,Amin = {_value(p.resonant_inductor.b_peak_min_area_t, 'T')}; limit,Amin = {_value(s.resonant_inductor_max_b_t, 'T')}")
        core_sub = []
        core_entries = [("xfmr", trace.transformer if trace else None)]
        if analysis.resonant_inductor is not None:
            core_entries.append(("Lr", trace.inductor if trace else None))
        for label, inputs in core_entries:
            if inputs is None:
                core_sub.append(f"{label}: TD dB/dt quadrature inputs NOT_AVAILABLE; recorded loss is retained, not reverse-fitted.")
                continue
            core_sub.append(f"{label}: T={inputs.temperature_c:.6g} degC; k={inputs.k:.6g}; alpha={inputs.alpha:.6g}; beta={inputs.beta:.6g}; ki={inputs.ki:.6g}; mean(abs(dB/dt)^alpha)={inputs.mean_abs_dbdt_alpha:.6g}; DeltaB={inputs.delta_b_t:.6g} T; Pcore := {inputs.correction:.6g}*{inputs.ki:.6g}*{inputs.mean_abs_dbdt_alpha:.6g}*{inputs.delta_b_t:.6g}^({inputs.beta:.6g}-{inputs.alpha:.6g})*{inputs.volume_m3:.6g}")
        edit("Magnetic core loss by iGSE",
             formula="dBdt[k] := (B[(k+1) mod N]-B[k])/dt; Pcore := Kcorr*ki*mean(abs(dBdt)^alpha)*ptp(B)^(beta-alpha)*Ve",
             substitution="\n".join(core_sub), method="Same accepted final TD cycle and final thermal-iteration temperature as each loss. ki := k/((2*pi)^(alpha-1)*2^(beta-alpha)*integral_0^2pi(abs(cos(theta))^alpha dtheta)). No DC-bias loss correction.")
        copper = p.detailed_magnetics_breakdown()
        winding_sub = []
        for label, keys in (("primary", ("dc", "skin", "proximity", "bundle", "termination")),
                            ("secondary", ("dc", "skin", "proximity", "bundle", "termination")),
                            ("Lr", ("dc", "skin", "proximity", "gap_fringing", "bundle", "termination"))):
            if label == "Lr" and analysis.resonant_inductor is None:
                continue
            prefix = "resonant_inductor" if label == "Lr" else "transformer_" + label
            winding_sub.append(label + " W: " + "; ".join(f"{key}={copper[prefix+'_'+key+'_w']:.6g}" for key in keys))
        winding_sub.append(f"fs={pfs} Hz; xfmr T={p.transformer.estimated_hotspot_c:.6g} degC; Lr T={p.resonant_inductor.estimated_hotspot_c:.6g} degC")
        edit("Litz winding loss by harmonic layer MMF", substitution="\n".join(winding_sub),
             method=f"Actual TD current FFT, orders 0..{s.litz_max_harmonic}; stack P/2-S-P/2. Primary current = i_resonant, secondary INTO dot = -i_transformer_secondary. DC Joule term uses full sampled RMS; AC increments above cutoff are unmodeled. Scalar/phasor summary is not a standalone replacement for the layer-MMF solver.")
        if trace is None:
            phasor_sub = "TD harmonic phasors NOT_AVAILABLE in this historical analysis."
        else:
            phasor_sub = "\n".join(f"h={h}: Ip=({pr:.6g},{pi:.6g}) A; Is_into_dot=({sr:.6g},{si:.6g}) A"
                for h, ((pr, pi), (sr, si)) in enumerate(zip(trace.primary_rms_phasors_a, trace.secondary_rms_phasors_a)))
        extra.append(FormulaStep("TD winding harmonic inputs", "Ih := RMS-normalized FFT(i[k]); DC bin is the mean; phasors shown as (real,imaginary)",
            phasor_sub, f"AC cutoff = {s.litz_max_harmonic}; Lr uses the same Ip waveform", "Summary from the same accepted cycle, preserving relative phase; no fitted sine wave."))
        edit("Magnetic thermal fixed-point iteration", formula="Tpred := clamp(Tamb+Ploss(T)*Rth,Tamb,220 degC); Tnext := 0.55*T+0.45*Tpred",
             substitution=f"Tamb={s.ambient_temperature_c:g}; initial T=max({s.winding_temperature_c:g},Tamb); Rth,xfmr={s.transformer_rth_k_per_w:g}; Rth,Lr={s.resonant_inductor_rth_k_per_w:g}; tolerance={s.magnetic_thermal_tolerance_c:g} degC; max iterations={s.magnetic_thermal_max_iterations}",
             method="Re-evaluate actual TD iGSE/layer-MMF losses at each temperature; reported loss is evaluated again at the final temperature. Iteration cap is not proof of thermal convergence.")

    source = FormulaStep("Nominal loss input provenance", "Each loss bucket keeps its own source; FHA frequency/phase/commutation are separate from TD loss inputs.",
        f"semiconductor={p.semiconductor_source}; fs={fs} Hz\nmagnetic={p.magnetic_source}; capacitor={p.capacitor_source}; fs={pfs} Hz\ncommutation={p.commutation_source}\nwaveform SHA256={p.loss_waveform_sha256 or 'NOT_AVAILABLE'}",
        (f"Final cycle N={trace.sample_count}; dt={trace.sample_step_s:.9g} s; fs={trace.frequency_hz:.9g} Hz" if trace else "TD quadrature summary NOT_AVAILABLE; retained scalar loss inputs remain labeled."),
        "Only the selected points were upgraded. Source is numerical provenance, not a model-validity grade or hardware approval.")
    updated = {group: [overrides.get(step.name, step) for step in steps] for group, steps in sections.items()}
    return {"Nominal loss sources": [source], **updated, "TD harmonic audit": extra} if extra else {"Nominal loss sources": [source], **updated}


def _integrated_formula_steps(analysis, sections):
    """Bind the worksheet to the physical core and the exact two-port APR map."""
    from ..core.integrated_magnetics import from_spec
    from ..magnetics.magnetic_waveforms import transformer_current_waveforms

    spec, tank, point = analysis.spec, analysis.tank, analysis.nominal
    magnetic = from_spec(spec)
    physical_lm = magnetic.physical_magnetizing_h
    r = magnetic.effective_turns_ratio / magnetic.physical_turns_ratio
    core = analysis.transformer.core
    trace = getattr(point, "td_formula_inputs", None)
    td = point.magnetic_source.startswith("TIME_DOMAIN")
    if td and trace is None:
        flux_input = "Physical TD current summary NOT_AVAILABLE; retained B results are not reverse-fitted."
    else:
        if td:
            peak, mean = trace.magnetizing_peak_a, trace.magnetizing_mean_a
        else:
            _, _, physical_current = transformer_current_waveforms(
                spec, point.operating_point, spec.magnetic_waveform_samples)
            peak = float(max(abs(physical_current.min()), abs(physical_current.max())))
            mean = float(physical_current.mean())
        flux_input = (f"Lmu={physical_lm:.9g} H; r={r:.9g}; Np={spec.primary_turns}; "
                      f"Ae={core.ae_m2:.9g} m2; Amin={core.amin_m2:.9g} m2; "
                      f"max(abs(imu,physical))={peak:.9g} A; mean(imu,physical)={mean:.9g} A")
    replacements = {
        "Transformer turns ratio": {"formula": "n_phys := Np / Ns",
            "substitution": f"n_phys := {spec.primary_turns}/{spec.secondary_turns}",
            "result": f"n_phys = {_value(spec.turns_ratio)}; n_eff = {_value(spec.electrical_turns_ratio)}",
            "method": "Actual winding ratio and APR electrical ratio are distinct; load/gain use n_eff."},
        "FHA load reflected to primary": {"formula": "Rac := (8/pi^2)*n_eff^2*Rload"},
        "Characteristic impedance from entered tank": {"method": "Integrated two-port characterization supplies Lr/Lm; Cr is entered or chosen for requested fr. No target is substituted for measured/declared leakage."},
        "Transformer peak flux density": {
            "formula": "r := n_eff/n_phys; imu,physical[k] := (1-r)*ir[k]+r*im_APR[k]; B[k] := Lmu*imu,physical[k]/(Np*Ae)",
            "substitution": flux_input,
            "result": f"Bpk,Amin = {_value(point.transformer.b_peak_min_area_t, 'T')}; Bdc,Ae = {_value(point.transformer.b_dc_t, 'T')}",
            "method": ("Accepted TD physical-current mapping retains DC offset." if td else "FHA magnetic-loss triangular-current reconstruction; distinct from the GUI FHA sine-state/square-clamp display. This is not the displayed waveform's loss or an exact time-domain Faraday claim.") +
                   " APR Lm*im is not silently treated as the physical core flux."},
        "Transformer equivalent air gap": {"formula": "g := mu0*Np^2*Ae/Lmu_physical - le/mur",
            "substitution": f"Lmu_physical={physical_lm:.9g} H; Np={spec.primary_turns}; Ae={core.ae_m2:.9g}; le={core.le_m:.9g}; mur={core.mu_r:.9g}",
            "method": "Physical main-flux reluctance estimate. APR Lm is not the core magnetizing inductance; hardware fringing/measurement still need validation."},
        "Resonant-inductor peak flux density": {"formula": "NOT_APPLICABLE", "substitution": "Integrated leakage has no separate Lr core or turns.", "result": "No external core B limit", "method": "Physical transformer B and winding fields are checked in the shared magnetic model."},
        "Resonant-inductor equivalent air gap": {"formula": "NOT_APPLICABLE", "substitution": "No external Lr core/gap is synthesized.", "result": "No external winding/gap/BOM", "method": "Target leakage and characterized two-port parameters are recorded separately."},
        "Magnetic thermal fixed-point iteration": {
            "substitution": f"Tamb={spec.ambient_temperature_c:g}; initial T=max({spec.winding_temperature_c:g},Tamb); Rth,shared={spec.transformer_rth_k_per_w:g}; tolerance={spec.magnetic_thermal_tolerance_c:g}; max iterations={spec.magnetic_thermal_max_iterations}",
            "result": f"Thot,shared = {_value(point.transformer.estimated_hotspot_c, 'degC')}; external Lr thermal node ABSENT",
            "method": "Only the shared transformer core and primary/secondary winding losses heat this magnetic component; no second Lr loss or thermal node."},
    }
    updated = {group: [replace(step, **replacements[step.name]) if step.name in replacements else step
                       for step in steps] for group, steps in sections.items()}
    # Remove misleading absent-part phrases from the remaining real shared
    # core/copper trace while retaining the original numerical consumer inputs.
    for group, steps in updated.items():
        updated[group] = [replace(step,
            substitution=step.substitution.replace(f"; Lr T={point.resonant_inductor.estimated_hotspot_c:.6g} degC", "; external Lr ABSENT"),
            result=step.result.replace(f"AC cutoff = {spec.litz_max_harmonic}; Lr uses the same Ip waveform", f"AC cutoff = {spec.litz_max_harmonic}; shared windings only"))
            for step in steps]
    target = spec.integrated_target_lr_h
    target_text = (f"Target Lr={target:.9g} H; relative error={abs(tank.lr_h-target)/target:.9g}; tolerance={spec.integrated_target_tolerance:g}"
                   if target is not None else "No leakage target requested; characterized response only")
    evidence = [
        FormulaStep("Two-winding inductance characterization",
            "[vp,vs]^T := [[Lp,M],[M,Ls]]*[dip/dt,dis/dt]^T; M^2 < Lp*Ls",
            f"Lp={magnetic.lp_h:.9g} H; Ls={magnetic.ls_h:.9g} H; M={magnetic.mutual_h:.9g} H; k={magnetic.coupling:.9g}",
            f"source={spec.integrated_inductance_source.value}; frequency={spec.integrated_measurement_frequency_hz:g} Hz; Np:Ns={spec.integrated_characterized_primary_turns}:{spec.integrated_characterized_secondary_turns}",
            spec.integrated_measurement_note + "; two windings/full-bridge secondary only; no implicit center-tap mapping."),
        FormulaStep("Exact all-primary-referred equivalent",
            "Lr := Lp-M^2/Ls; Lm_APR := M^2/Ls; n_eff := M/Ls",
            f"Lp={magnetic.lp_h:.9g}; M={magnetic.mutual_h:.9g}; Ls={magnetic.ls_h:.9g}",
            f"Lr={tank.lr_h:.9g} H; Lm_APR={tank.lm_h:.9g} H; n_eff={spec.electrical_turns_ratio:.9g}",
            "Lshort equals Lr only with this simultaneous Lm/n_eff transformation."),
        FormulaStep("Physical T-equivalent",
            "Lmu := n_phys*M; Lsigma_p := Lp-Lmu; Lsigma_s := Ls-M/n_phys",
            f"n_phys={spec.turns_ratio:.9g}; M={magnetic.mutual_h:.9g} H",
            f"Lmu={physical_lm:.9g} H; Lsigma_p={magnetic.primary_leakage_h:.9g} H; Lsigma_s={magnetic.secondary_leakage_h:.9g} H",
            "Both leakage terms remain physical; balanced-MMF leakage energy is not generally Lshort."),
        FormulaStep("Leakage target versus characterization", "error := abs(Lr_characterized-Lr_target)/Lr_target",
            target_text, "Independent Lr core/copper/thermal/BOM count = 0",
            "Matching a target is not evidence that a selected winding geometry realizes the characterized transformer; see feasibility/geometry checks."),
    ]
    screen = analysis.transformer.integrated_leakage
    if screen is not None:
        evidence.append(FormulaStep("Winding-field leakage realizability",
            "Lbalanced := sum(mu0*MLT*thickness*(F0^2+F0*F1+F1^2)/(3*width)); Lbalanced_characterized := Lp+n_phys^2*Ls-2*n_phys*M",
            f"regions={len(screen.regions)}; configured={screen.balanced_geometry_h:.9g} H; characterized={screen.characterized_balanced_h:.9g} H; relative error={screen.relative_error:.9g}; required P-S spacing={screen.required_interwinding_gap_m:.9g} m; minimum insulation={screen.minimum_insulation_m:.9g} m",
            f"{screen.status}; required build={screen.required_radial_build_m:.9g} m; window={screen.available_window_height_m:.9g} m; APR Lr bounds=[{screen.geometry_apr_lr_lower_h:.9g}, {screen.geometry_apr_lr_upper_h:.9g}] H",
            "Includes conductor and insulation regions. Bounds do not infer a unique primary/secondary leakage split. " + " ".join(screen.notes)))
    return {"Integrated magnetic identity": evidence, **updated}


class _FormulaPdf(FPDF):
    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "", 8)
        self.set_text_color(95, 105, 120)
        self.cell(0, 5, "Power Design Toolkit - auditable LLC worksheet", align="R")
        self.ln(7)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(110, 120, 135)
        self.cell(0, 5, f"V{__version__}  |  Page {self.page_no()}/{{nb}}", align="C")

    def section(self, title: str) -> None:
        self.set_fill_color(25, 62, 105)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 8, title, fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def formula_step(self, index: int, step: FormulaStep) -> None:
        self.set_fill_color(242, 246, 251)
        self.set_draw_color(184, 199, 217)
        self.set_text_color(30, 43, 60)
        self.set_font("Helvetica", "B", 9)
        self.cell(0, 6, f"{index}. {step.name}", border="LTR", fill=True, new_x="LMARGIN", new_y="NEXT")
        self.set_font("Courier", "", 8)
        self.multi_cell(
            0, 4.5, step.formula, border="LR", fill=True,
            new_x="LMARGIN", new_y="NEXT",
        )
        self.set_text_color(71, 84, 101)
        self.multi_cell(
            0, 4.5, step.substitution, border="LR", fill=True,
            new_x="LMARGIN", new_y="NEXT",
        )
        self.set_text_color(9, 102, 69)
        self.set_font("Courier", "B", 8)
        self.multi_cell(
            0, 5, step.result, border="LBR", fill=True,
            new_x="LMARGIN", new_y="NEXT",
        )
        if step.method:
            self.set_text_color(93, 104, 120)
            self.set_font("Helvetica", "I", 7)
            self.multi_cell(
                0, 4, f"Method: {step.method}",
                new_x="LMARGIN", new_y="NEXT",
            )
        self.ln(2)


def build_formula_pdf(analysis: SystemAnalysis, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    steps = build_formula_steps(analysis)
    provenance = validate_bundled_data()

    pdf = _FormulaPdf(format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    pdf.set_fill_color(14, 39, 70)
    pdf.rect(0, 0, pdf.w, 62, "F")
    pdf.set_y(16)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 10, "LLC Design Calculation Worksheet", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(190, 213, 238)
    pdf.cell(0, 7, "Mathcad-style formula, substitution and result trace", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(70)
    pdf.set_text_color(30, 40, 54)
    pdf.set_font("Helvetica", "", 10)
    spec = analysis.spec
    has_td = any(_has_td_inputs(point) for point in analysis.operating_points)
    summary = [
        ("Toolkit", f"Power Design Toolkit V{__version__}"),
        ("Generated", datetime.now(timezone.utc).date().isoformat()),
        ("Topology", f"{spec.primary_topology.value} + {spec.secondary_topology.value}"),
        ("Power stage", f"{spec.vbus_nom_v:g} Vdc -> {spec.vout_v:g} V / {spec.pout_w/1000:g} kW"),
        ("Result", "PASS" if analysis.feasible else "FAIL"),
        ("Evidence", "Software regression only; hardware verification is not claimed"),
    ]
    for label, value in summary:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(38, 7, label)
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 7, value, new_x="LMARGIN", new_y="NEXT")
    if has_td:
        pdf.multi_cell(0, 5, "Mixed-source loss worksheet: TD coverage is stated per operating point. "
                       "The feasibility decision and commutation/ZVS screens retain FHA assumptions.",
                       new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)
    pdf.set_fill_color(255, 246, 220)
    pdf.set_draw_color(225, 177, 76)
    pdf.set_font("Helvetica", "B", 9)
    pdf.multi_cell(
        0,
        5,
        "Engineering boundary: bundled devices, cores and material curves are reference data. "
        "Replace them with exact datasheet and measured models before hardware release.",
        border=1,
        fill=True,
        new_x="LMARGIN",
        new_y="NEXT",
    )

    step_number = 1
    for section, section_steps in steps.items():
        pdf.add_page()
        pdf.section(section)
        for step in section_steps:
            pdf.formula_step(step_number, step)
            step_number += 1

    pdf.add_page()
    pdf.section("Operating-point results")
    pdf.set_text_color(30, 43, 60)
    pdf.set_font("Helvetica", "B", 7)
    widths = (27, 20, 24, 22, 23, 23, 25)
    headers = (("Point", "Load", "fs loss/kHz", "Ir loss/A", "Phase FHA", "Loss/W", "Efficiency")
               if has_td else ("Point", "Load", "fs/kHz", "Ir,rms/A", "Phase/deg", "Loss/W", "Efficiency"))
    for width, header in zip(widths, headers):
        pdf.cell(width, 6, header, border=1, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 7)
    for item in analysis.operating_points:
        item_op = item.operating_point
        is_td = item.semiconductor_source.startswith("TIME_DOMAIN")
        loss_fs = item.semiconductor_frequency_hz if is_td else item_op.switching_frequency_hz
        loss_ir = (item.primary.device_rms_current_a * math.sqrt(2) * spec.primary_parallel_devices
                   if is_td else item_op.resonant_current_rms_a)
        values = (
            item.label,
            f"{item_op.load_fraction*100:.0f}%",
            f"{loss_fs/1e3:.3f}" if loss_fs is not None and math.isfinite(loss_fs) else "UNKNOWN",
            f"{loss_ir:.3f}",
            f"{item_op.input_phase_deg:.2f}",
            f"{item.total_loss_w:.3f}",
            f"{item.efficiency*100:.3f}%",
        )
        for width, value in zip(widths, values):
            pdf.cell(width, 6, value, border=1, align="C")
        pdf.ln()

    if has_td:
        pdf.ln(3)
        for item in analysis.operating_points:
            semi_fs = (item.semiconductor_frequency_hz if item.semiconductor_source.startswith("TIME_DOMAIN")
                       else item.operating_point.switching_frequency_hz)
            passive_fs = (item.passive_frequency_hz if item.magnetic_source.startswith("TIME_DOMAIN")
                          or item.capacitor_source.startswith("TIME_DOMAIN") else item.operating_point.switching_frequency_hz)
            pdf.multi_cell(0, 4, f"{item.label}: semiconductor={item.semiconductor_source}; "
                f"magnetic={item.magnetic_source}; capacitor={item.capacitor_source}; "
                f"commutation={item.commutation_source}. FHA fs={item.operating_point.switching_frequency_hz:.9g} Hz; "
                f"loss fs={_input(semi_fs)} Hz; passive fs={_input(passive_fs)} Hz. "
                f"Waveform SHA256={item.loss_waveform_sha256 or ('NOT_AVAILABLE' if _has_td_inputs(item) else 'NOT_APPLICABLE (FHA)')}",
                new_x="LMARGIN", new_y="NEXT")

    pdf.ln(5)
    pdf.section("Loss ledger at nominal point")
    pdf.set_text_color(30, 43, 60)
    pdf.set_font("Helvetica", "", 8)
    for name, value in analysis.nominal.breakdown().items():
        pdf.cell(76, 5, name.replace("_", " "))
        pdf.cell(28, 5, f"{float(value):.6g} W", align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(5)
    if has_td and pdf.will_page_break(32):
        pdf.add_page()
    pdf.section("Feasibility decision trace")
    pdf.set_text_color(30, 43, 60)
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(
        0,
        5,
        "PASS requires every requested operating point to solve and all hard screens to pass: "
        "magnetic geometry/flux/hotspot, minimum inductive angle, primary ZVS margin >= 1, "
        "MOSFET voltage derating, resonant-capacitor voltage/current margins, output ripple and "
        "DC-link hold-up. Preferred-margin and data-range findings remain warnings.",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    if has_td:
        pdf.multi_cell(0, 5, "This decision is the retained FHA hardware/synthesis screen. "
            "TD loss updates do not establish a new full-envelope feasibility or ZVS result. "
            "Nominal-only TD coverage must not be read as all-points TD verification.",
            new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(0, 5, f"Decision: {'PASS' if analysis.feasible else 'FAIL'}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 7)
    if analysis.feasibility_reasons:
        for reason in analysis.feasibility_reasons:
            pdf.multi_cell(0, 4, f"FAIL - {reason}", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.multi_cell(0, 4, "No hard-screen failures were recorded.", new_x="LMARGIN", new_y="NEXT")
    for warning in analysis.warnings:
        pdf.multi_cell(0, 4, f"WARN - {warning}", new_x="LMARGIN", new_y="NEXT")

    pdf.add_page()
    pdf.section("Evidence and model limits")
    pdf.set_text_color(30, 43, 60)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(
        0,
        5,
        ("The formula chain uses the same SystemAnalysis object as the GUI. TD loss inputs and immutable "
        "quadrature/FFT summaries come from the accepted final complete waveform cycle, identified by SHA256. "
        "TD current/frequency, capacitor voltage/current states, magnetic flux derivatives and winding "
        "harmonics are not replaced by FHA substitutions. Event-energy, timing, material, thermal, "
        "constant-ESR and winding-geometry models remain approximations. FHA gain/phase, commutation/ZVS, "
        "hardware synthesis and hold-up sizing retain their explicitly labeled original inputs. "
        "Source listings show every non-upgraded point; partial coverage does not verify the full envelope. "
        "Historical objects without a TD quadrature summary are marked NOT_AVAILABLE, never reverse-fitted."
        if has_td else
        "The formula chain above is recomputed from the same SystemAnalysis object used by the GUI. "
        "Frequency is a numerical root of the FHA complex-impedance gain equation. Output-capacitor "
        "ripple is a sampled integration of one rectified-current lobe. Magnetic loss uses reconstructed "
        "waveforms, temperature iteration, iGSE and detailed winding-loss models; their reported loss "
        "components are included in the loss ledger rather than replaced by a closed-form shortcut."),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "Data provenance", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    for dataset in provenance.datasets:
        pdf.multi_cell(
            0,
            5,
            f"{dataset.dataset}: status={dataset.status.value}; "
            f"hardware_release={dataset.verified_for_hardware}; source={dataset.source}",
            new_x="LMARGIN",
            new_y="NEXT",
        )
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "Falsification conditions", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(
        0,
        5,
        "Downgrade the result if the software baseline changes outside its recorded tolerances, "
        "if exact component data cannot reproduce the input records, or if external simulation or "
        "bench measurements differ beyond an approved engineering tolerance.",
        new_x="LMARGIN",
        new_y="NEXT",
    )

    pdf.output(output)
    return output


__all__ = ["FormulaStep", "build_formula_pdf", "build_formula_steps"]
