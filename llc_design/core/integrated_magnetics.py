"""Linear two-winding integrated LLC magnetic contract (SI units).

Measurements describe the same transformer, dot convention and frequency:
Lp and Ls are open-circuit self-inductances; Lshort is primary inductance
with secondary shorted. The APR equivalent is exact for this linear matrix.
It is not a geometric construction recipe or a hardware qualification.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class IntegratedMagnetics:
    lp_h: float
    ls_h: float
    mutual_h: float
    physical_turns_ratio: float

    def __post_init__(self):
        values = (self.lp_h, self.ls_h, self.mutual_h, self.physical_turns_ratio)
        if any(not math.isfinite(x) or x <= 0 for x in values):
            raise ValueError("integrated magnetics requires finite positive Lp, Ls, M and physical Np/Ns")
        if self.mutual_h**2 >= self.lp_h * self.ls_h:
            raise ValueError("integrated inductance matrix must be positive definite: M² < Lp Ls")
        # A passive matrix alone does not prove a nonnegative physical T at the
        # declared actual turns ratio. Do not silently clip a negative leakage.
        tolerance = 1e-12 * max(self.lp_h, self.ls_h)
        if self.primary_leakage_h < -tolerance or self.secondary_leakage_h < -tolerance:
            raise ValueError("physical Np/Ns is incompatible with nonnegative primary/secondary leakage")

    @classmethod
    def from_open_short(cls, lp_h, ls_h, primary_short_h, physical_turns_ratio):
        if (primary_short_h is None or not math.isfinite(primary_short_h)
                or not 0 < primary_short_h < lp_h):
            raise ValueError("open/short characterization requires 0 < Lshort < Lp")
        return cls(lp_h, ls_h, math.sqrt((lp_h-primary_short_h)*ls_h), physical_turns_ratio)

    @property
    def lr_h(self):
        return self.lp_h - self.mutual_h**2 / self.ls_h

    @property
    def lm_h(self):
        return self.mutual_h**2 / self.ls_h

    @property
    def effective_turns_ratio(self):
        return self.mutual_h / self.ls_h

    @property
    def physical_magnetizing_h(self):
        return self.physical_turns_ratio * self.mutual_h

    @property
    def primary_leakage_h(self):
        return self.lp_h - self.physical_magnetizing_h

    @property
    def secondary_leakage_h(self):
        return self.ls_h - self.mutual_h / self.physical_turns_ratio

    @property
    def coupling(self):
        return self.mutual_h / math.sqrt(self.lp_h*self.ls_h)

    def physical_magnetizing_current(self, resonant_current, apr_magnetizing_current):
        r = self.effective_turns_ratio / self.physical_turns_ratio
        return (1-r)*resonant_current + r*apr_magnetizing_current

    def physical_core_voltage(self, apr_voltage, apr_lr_voltage):
        # d(Lmu * imu)/dt = (Lmu/Lm) r Vapr + (Lmu/Lr)(1-r) Vlr.
        r = self.effective_turns_ratio / self.physical_turns_ratio
        return self.physical_magnetizing_h * (r*apr_voltage/self.lm_h
                                              + (1-r)*apr_lr_voltage/self.lr_h)

    def flux_density(self, resonant_current, apr_magnetizing_current, turns, area_m2):
        return (self.physical_magnetizing_h
                * self.physical_magnetizing_current(resonant_current, apr_magnetizing_current)
                / (turns*area_m2))


def from_spec(spec) -> IntegratedMagnetics:
    from .spec import IntegratedInductanceSource
    if (spec.integrated_characterized_primary_turns != spec.primary_turns
            or spec.integrated_characterized_secondary_turns != spec.secondary_turns):
        raise ValueError("integrated characterization turns do not match physical Np/Ns; re-characterize after winding changes")
    if spec.integrated_lp_h is None or spec.integrated_ls_h is None:
        raise ValueError("integrated magnetics requires characterized open-circuit Lp and Ls")
    if IntegratedInductanceSource(spec.integrated_inductance_source) == IntegratedInductanceSource.OPEN_SHORT:
        return IntegratedMagnetics.from_open_short(spec.integrated_lp_h, spec.integrated_ls_h,
                                                   spec.integrated_lshort_h, spec.turns_ratio)
    if spec.integrated_mutual_h is None:
        raise ValueError("integrated matrix requires characterized mutual inductance M")
    return IntegratedMagnetics(spec.integrated_lp_h, spec.integrated_ls_h,
                               spec.integrated_mutual_h, spec.turns_ratio)


def add_physical_waveform_signals(raw, *, lr_h, lm_h, effective_ratio,
                                  physical_ratio, physical_lmu_h, frequency_hz,
                                  voltage_projection_name=""):
    """Annotate APR traces and expose physical winding/core quantities in place."""
    if physical_ratio <= 0 or physical_lmu_h <= 0:
        return
    ir, im = raw["i_resonant"][2], raw["i_magnetizing"][2]
    vp, vlr = raw["v_transformer_primary"][2], raw["v_resonant_inductor"][2]
    r = effective_ratio/physical_ratio
    imu = (1-r)*ir+r*im
    vmu = physical_lmu_h*((1-r)*vlr/lr_h+r*vp/lm_h)
    for key, title, desc in (
            ("i_magnetizing", "APR 励磁电流 Im", "APR equivalent state; not physical core magnetizing current"),
            ("v_transformer_primary", "APR 励磁支路电压 Vp", "APR shunt voltage; not physical transformer primary-terminal voltage"),
            ("v_resonant_inductor", "集成漏感 APR 电压 VLr", "Equivalent leakage voltage of the shared transformer, no separate core"),
            ("energy_lr", "APR 漏感等效储能", "APR energy coordinate; physical leakage energy is distributed in the shared transformer")):
        old = raw[key]
        raw[key] = (title, old[1], old[2], old[3], desc, old[5])
    core_label = f"实体磁芯电压 Vμ（{voltage_projection_name} 投影）" if voltage_projection_name else "实体磁芯感应电压 Vμ"
    core_description = (f"{voltage_projection_name} display projection from square clamp/KVL traces; not derivative-consistent with sinusoidal current states and not Faraday validated"
        if voltage_projection_name else "Lμ*dIμ/dt; excludes physical primary leakage voltage")
    terminal_description = (f"{voltage_projection_name} display projection of primary terminal voltage including leakage; not a time-domain state solution"
        if voltage_projection_name else "Physical primary winding terminal voltage, including integrated leakage")
    raw.update({
        "i_magnetizing_physical": ("实体励磁电流 Iμ", "A", imu, "transformer", "Ir-Is/Np:Ns; physical ampere-turn residual", frequency_hz),
        "v_transformer_core": (core_label, "V", vmu, "transformer", core_description, frequency_hz),
        "v_transformer_terminal": ("实体变压器原边端电压", "V", vp+vlr, "transformer", terminal_description, frequency_hz),
    })


def require_consistent_tank(spec, tank):
    """Reject stale external APR tanks paired with a different characterization."""
    if spec.integrated_magnetics:
        magnetic = from_spec(spec)
        if not (math.isclose(tank.lr_h, magnetic.lr_h, rel_tol=1e-10, abs_tol=1e-18)
                and math.isclose(tank.lm_h, magnetic.lm_h, rel_tol=1e-10, abs_tol=1e-18)):
            raise ValueError("integrated tank Lr/Lm must match the characterized matrix; re-characterize instead of overriding APR values")
