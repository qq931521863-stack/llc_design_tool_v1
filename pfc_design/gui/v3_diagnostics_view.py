"""Present V3 evidence from the shared calculation; never run another solver."""

from __future__ import annotations

from PySide6.QtWidgets import QPlainTextEdit

from pfc_design.engineering.pfc_v3 import PFCEngineeringV3Result


def install_v3_diagnostics(workbench) -> None:
    view = QPlainTextEdit()
    view.setReadOnly(True)
    view.setPlainText("Run TTPL analysis to populate V3 diagnostics.")
    workbench.v3_diagnostics_view = view
    workbench.control_lab.tabs.addTab(view, "V3 Convergence / Distortion / Dual Loop")


def _number(value) -> str:
    return "UNKNOWN" if value is None else f"{value:.6g}"


def show_v3_diagnostics(workbench, result: PFCEngineeringV3Result) -> None:
    convergence = result.line_cycle.convergence
    pf = result.pf_thd
    zero = result.zero_crossing
    smart = result.smart_control
    lines = [
        "PFC ENGINEERING V3 · shared analysis / final AC cycle",
        "Model evidence only; hardware validation remains UNKNOWN.",
        "",
        f"Convergence: {convergence.status.value}",
        f"Cycle Pin relative error: {_number(convergence.pin_cycle_relative_error)}",
        f"Bus relative error: {convergence.vbus_relative_error:.6g}",
        *convergence.notes,
        "",
        f"PF/THD: {pf.status.value} | PF={pf.pf:.7f} | THD={pf.thd_percent:.5f}%",
        f"DPF={pf.dpf:.7f} | distortion factor={pf.distortion_factor:.7f}",
        f"PF identity residual={pf.pf_identity_residual:.6g}",
        f"Window: {pf.convention.window}; harmonics: {pf.convention.included_harmonics}",
        *pf.notes,
        "",
        "Distortion localization (cause estimates, not measured attribution):",
    ]
    for region in result.distortion_regions:
        lines.append(
            f"{region.angle_start_deg:g}–{region.angle_end_deg:g} deg: "
            f"error={region.current_error_rms_a:.5g} A; "
            f"tracking-error energy share={100 * region.harmonic_contribution_estimate:.3g}%; "
            f"{region.dominant_cause.value} [{region.status.value}] {region.notes}"
        )
    lines.extend(
        [
            "",
            f"Zero crossing: {zero.status.value}",
            (
                f"Minimum realizable current={zero.minimum_realizable_current_a:.6g} A; "
                f"duty deadzone={zero.effective_duty_deadzone:.6g}"
            ),
            (
                f"Dead-time voltage error={zero.dead_time_voltage_error_v:.6g} V; "
                f"sensor offset={zero.sensor_offset_a:.6g} A"
            ),
            *zero.notes,
            "",
            f"Dual-loop separation: {smart.separation.status.value}",
            (
                f"Fc_i/Fc_v={_number(smart.separation.ratio)}; "
                f"2×fline={smart.separation.line_2x_hz:g} Hz; "
                f"Fc_v/(2×fline)={_number(smart.separation.voltage_fc_vs_2fline)}"
            ),
            *smart.separation.notes,
        ]
    )
    for loop in (smart.current, smart.voltage):
        lines.extend(
            [
                "",
                (
                    f"{loop.name}: {loop.status.value}; Fc={_number(loop.fc_hz)} Hz; "
                    f"PM={_number(loop.pm_deg)} deg; GM={_number(loop.gm_db)} dB"
                ),
                (
                    f"Ms={loop.ms:.6g}; Mt={loop.mt:.6g}; "
                    f"delay margin={_number(loop.delay_margin_s)} s; multiple crossings={loop.multi_crossover}"
                ),
            ]
        )
        if loop.phase_budget is None:
            lines.append("Phase budget: UNKNOWN (no crossover)")
        else:
            budget = loop.phase_budget
            lines.append(
                f"Phase budget: {'consistent' if budget.consistent else 'WARN inconsistent'}; "
                f"residual={budget.residual_deg:.6g} deg; tolerance={budget.tolerance_deg:g} deg"
            )
    workbench.v3_diagnostics_view.setPlainText("\n".join(lines))
    workbench.ac_performance_view.summary.appendPlainText(
        f"\nV3 convergence: {convergence.status.value}; PF/THD evidence: {pf.status.value}\n"
        "See Control / V3 diagnostics for convergence, distortion regions and model limits."
    )
