"""PFC semiconductor loss rollup with explicit device counting notes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pfc_design.engineering.capacitor_ripple import PFCCapacitorRipple

from pfc_design.engineering.device_loss import TTPLHFDeviceLoss, TTPLSlowDeviceLoss


@dataclass(frozen=True)
class PFCDeviceLossRollup:
    workpoint: str
    vin_rms_v: float
    hf: TTPLHFDeviceLoss
    slow: TTPLSlowDeviceLoss
    inductor_self_loss_w: float | None = None
    waveform_source: str = "ideal_sizing_half_cycle"
    capacitor_current_rms_a: float | None = None
    power_factor: float | None = None
    thd_percent: float | None = None
    capacitor_ripple: PFCCapacitorRipple | None = None

    def hf_buckets(self) -> dict[str, float]:
        return {
            "hf_active_conduction_w": self.hf.active_conduction_w,
            "hf_active_switching_w": self.hf.active_switching_w,
            "hf_sr_conduction_w": self.hf.sr_conduction_w,
            "hf_sr_switching_w": self.hf.sr_switching_w,
            "hf_deadtime_reverse_w": self.hf.deadtime_reverse_w,
            "hf_measured_diode_w": self.hf.diode_conduction_w,
            "hf_coss_w": self.hf.coss_w,
            "hf_gate_drive_w": self.hf.gate_drive_w,
        }

    def slow_buckets(self) -> dict[str, float]:
        return {
            "slow_conduction_w": self.slow.conduction_w,
            "slow_gate_drive_w": self.slow.gate_drive_w,
            "slow_measured_diode_w": self.slow.diode_conduction_w,
            "slow_switching_w": self.slow.switching_w,
            "slow_coss_w": self.slow.coss_w,
        }

    def device_subtotal_w(self) -> float:
        return self.hf.total_w + self.slow.total_w

    def system_device_total_w(self) -> float:
        """Semiconductor-only system total (excludes magnetics inductor-self)."""
        return self.device_subtotal_w()

    def verify_sums(self, *, atol: float = 1e-9) -> tuple[bool, float, float]:
        hf_sum = sum(self.hf_buckets().values())
        slow_sum = sum(self.slow_buckets().values())
        ok = abs(hf_sum - self.hf.total_w) <= atol * max(1.0, abs(self.hf.total_w)) and abs(
            slow_sum - self.slow.total_w
        ) <= atol * max(1.0, abs(self.slow.total_w))
        return ok, hf_sum - self.hf.total_w, slow_sum - self.slow.total_w

    def format_text(self) -> str:
        lines = [
            "PFC DEVICE LOSS ROLLUP (TTPL)",
            "=" * 72,
            f"Work point : {self.workpoint} @ Vin={self.vin_rms_v:.3g} Vrms",
            f"Waveform source : {self.waveform_source}",
            "",
            "Counting notes (do not multiply these rows again):",
            "  HF half-bridge: active+SR conduction/switching already cover both",
            "    high-frequency positions for one shared MOSFET choice; Coss/gate",
            "    terms include the two-device factor used by evaluate_hf_device().",
            "  Slow leg: conduction + gate already cover the complete two-device",
            "    line-frequency leg from evaluate_slow_device().",
            "",
            f"HF device   : {self.hf.device.manufacturer} {self.hf.device.part_number}",
            f"{'Bucket':<32} {'Loss (W)':>12}",
            "-" * 46,
        ]
        for key, value in self.hf_buckets().items():
            lines.append(f"{key:<32} {value:12.5f}")
        lines.append(f"{'HF device subtotal':<32} {self.hf.total_w:12.5f}")
        lines.extend([
            "",
            f"Slow device : {self.slow.device.manufacturer} {self.slow.device.part_number}",
            f"{'Bucket':<32} {'Loss (W)':>12}",
            "-" * 46,
        ])
        for key, value in self.slow_buckets().items():
            lines.append(f"{key:<32} {value:12.5f}")
        lines.append(f"{'Slow leg subtotal':<32} {self.slow.total_w:12.5f}")
        lines.extend([
            "",
            f"{('Modeled semiconductor subtotal' if self.waveform_source == 'shared_ngspice_signed_switching_cycle' else 'Semiconductor system total'):<32} {self.system_device_total_w():12.5f}",
        ])
        if self.waveform_source == "shared_ngspice_signed_switching_cycle":
            lines.extend(["Qrr/recovery: UNMODELED, not zero hardware loss; generic memoryless diode has no stored charge.",
                          "Selected Eon/Eoff may embed unknown recovery; no extra Qrr term is asserted.",
                          "THD scope: line harmonic orders 2..25 relative to fundamental; PWM-band distortion excluded."])
        if self.capacitor_current_rms_a is not None:
            lines.extend([
                (f"Signed switching capacitor RMS : {self.capacitor_current_rms_a:.6g} A (direct branch, same identity/time weights)" if self.waveform_source == "shared_ngspice_signed_switching_cycle" else f"Averaged capacitor RMS : {self.capacitor_current_rms_a:.6g} A (HF moments shown separately)"),
                f"Same-cycle PF / THD : {self.power_factor:.6g} / {self.thd_percent:.6g}%",
                "Capacitor ESR is excluded from this semiconductor subtotal; see Capacitor / Thermal for the constant-ESR screen.",
            ])
        if self.capacitor_ripple is not None:
            cap = self.capacitor_ripple
            scope = "local CCM reconstruction" if cap.complete else "PARTIAL model; complete RMS unknown"
            lines.extend([
                f"Known-model capacitor RMS : {cap.known_model_current_rms_a:.6g} A ({scope})",
                f"Known PWM ripple RMS : {cap.known_switching_current_rms_a:.6g} A; unresolved samples: {cap.unresolved_samples}/{len(cap.valid_sample_mask)}",
                "Local ideal PWM/triangular model; zero-crossing commutation and frequency-dependent ESR require separate evidence.",
            ])
        if self.inductor_self_loss_w is not None:
            lines.extend([
                f"{'Inductor-self (component)':<32} {self.inductor_self_loss_w:12.5f}",
                "Note: inductor-self loss is NOT added into semiconductor system total.",
            ])
        ok, d_hf, d_slow = self.verify_sums()
        if not ok:
            lines.append(f"WARNING: bucket mismatch Δhf={d_hf:.3e} Δslow={d_slow:.3e}")
        else:
            lines.append("Bucket sums match device subtotals.")
        return "\n".join(lines)


__all__ = ["PFCDeviceLossRollup"]
