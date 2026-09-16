"""First-class TTPL shared-ngspice closed-loop verification page."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from pfc_design.control import PFCControlLabAnalysis, build_pfc_control_handoff
from power_sim.spice import (
    TTPLClosedLoopScenario,
    TTPLSharedNgSpiceConfig,
    TTPLSharedNgSpiceResult,
    find_ngspice_shared_library,
)


class TTPLNgSpiceClosedLoopView(QWidget):
    """Configure and visualize the exact-H(z) TTPL shared-ngspice path."""

    run_requested = Signal(object, object, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.analysis: PFCControlLabAnalysis | None = None
        self.result: TTPLSharedNgSpiceResult | None = None
        self.library = find_ngspice_shared_library()

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)

        header = QHBoxLayout()
        title = QLabel("TTPL Shared-ngspice Closed-Loop Verification")
        title.setStyleSheet("font-size:16px;font-weight:650;")
        header.addWidget(title)
        hint = QLabel("Exact H(z) → multi-rate PFC control → real four-switch ngspice state")
        hint.setStyleSheet("color:#667085;")
        header.addWidget(hint)
        header.addStretch(1)
        self.run_button = QPushButton("Run Shared-ngspice")
        self.run_button.setEnabled(False)
        header.addWidget(self.run_button)
        root.addLayout(header)

        boundary = QLabel(
            "The controller coefficients are consumed from the frozen Exact H(z) handoff. No Kp/Ti reconstruction or S2Z is allowed. "
            "V1 uses ideal controlled switches and engineering-unit sensing; vendor Coss/Qrr, EMI network, detailed gate-driver delays and hardware correlation remain separate validation layers."
        )
        boundary.setWordWrap(True)
        root.addWidget(boundary)

        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        splitter = QSplitter()
        splitter.addWidget(self._build_inputs())
        splitter.addWidget(self._build_results())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 1180])
        root.addWidget(splitter, 1)

        self.run_button.clicked.connect(self._run)
        self._refresh_status()

    @staticmethod
    def _spin(lo: float, hi: float, decimals: int, value: float, suffix: str = "") -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(lo, hi)
        widget.setDecimals(decimals)
        widget.setValue(value)
        widget.setSuffix(suffix)
        widget.setKeyboardTracking(False)
        return widget

    def _build_inputs(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 2, 6, 2)

        sim_group = QGroupBox("Shared-ngspice transient")
        form = QFormLayout(sim_group)
        self.duration_ms = self._spin(0.05, 100.0, 3, 0.50, " ms")
        self.phase_deg = self._spin(-360.0, 360.0, 2, 90.0, " deg")
        self.max_step_us = self._spin(0.01, 10.0, 4, 0.0, " us")
        self.output_step_us = self._spin(0.01, 20.0, 4, 0.0, " us")
        self.timeout_s = self._spin(1.0, 600.0, 1, 120.0, " s")
        for label, widget in (
            ("Duration", self.duration_ms),
            ("Input phase at t=0", self.phase_deg),
            ("Max timestep (0=auto)", self.max_step_us),
            ("Output step (0=auto)", self.output_step_us),
            ("Wall timeout", self.timeout_s),
        ):
            form.addRow(label, widget)
        layout.addWidget(sim_group)

        scenario_group = QGroupBox("Bus-reference scenario")
        form = QFormLayout(scenario_group)
        self.vref_initial = self._spin(50.0, 1000.0, 2, 400.0, " V")
        self.vref_final = self._spin(50.0, 1000.0, 2, 400.0, " V")
        self.step_ms = self._spin(0.0, 100.0, 3, 0.25, " ms")
        form.addRow("Initial Vbus ref", self.vref_initial)
        form.addRow("Final Vbus ref", self.vref_final)
        form.addRow("Reference step", self.step_ms)
        layout.addWidget(scenario_group)
        layout.addStretch(1)
        return page

    def _build_results(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(250)
        self.summary.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.summary.setPlainText("Run TTPL Control / Exact H(z) first.")
        layout.addWidget(self.summary)
        self.figure = Figure(figsize=(11, 8))
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas, 1)
        return page

    def _refresh_status(self) -> None:
        if self.library is None:
            self.status.setText(
                "shared libngspice not found. Install libngspice or set NGSPICE_SHARED_LIB; the analytical/averaged PFC stages remain available."
            )
            self.run_button.setEnabled(False)
        elif self.analysis is None:
            self.status.setText(f"shared ngspice available: {self.library}. Run Control / Sensing / Bode first.")
            self.run_button.setEnabled(False)
        else:
            handoff = build_pfc_control_handoff(self.analysis)
            self.status.setText(
                f"Ready · lib={self.library} · current H(z) {handoff.current.sample_rate_hz:g} Hz · "
                f"voltage H(z) {handoff.voltage.sample_rate_hz:g} Hz · fsw={handoff.switching_frequency_hz:g} Hz · no re-discretization"
            )
            self.run_button.setEnabled(True)

    def set_analysis(self, analysis: PFCControlLabAnalysis) -> None:
        self.analysis = analysis
        self.vref_initial.setValue(analysis.config.power_stage.bus_voltage_v)
        self.vref_final.setValue(analysis.config.power_stage.bus_voltage_v)
        self._refresh_status()

    def _run(self) -> None:
        if self.analysis is None or self.library is None:
            return
        initial = self.vref_initial.value()
        final = self.vref_final.value()
        step_time = self.step_ms.value() * 1e-3
        if math.isclose(initial, final, rel_tol=0.0, abs_tol=1e-12):
            scenario = TTPLClosedLoopScenario(initial)
        else:
            scenario = TTPLClosedLoopScenario(initial, final, step_time)
        max_step = None if self.max_step_us.value() <= 0.0 else self.max_step_us.value() * 1e-6
        output_step = None if self.output_step_us.value() <= 0.0 else self.output_step_us.value() * 1e-6
        simulation = TTPLSharedNgSpiceConfig(
            duration_s=self.duration_ms.value() * 1e-3,
            input_phase_deg=self.phase_deg.value(),
            output_step_s=output_step,
            max_step_s=max_step,
            wall_timeout_s=self.timeout_s.value(),
        )
        self.run_requested.emit(self.analysis, scenario, simulation)

    def set_simulation_result(self, result: TTPLSharedNgSpiceResult) -> None:
        self.result = result
        metrics = result.metrics
        pf_text = "n/a (run >= 1 line cycle)" if metrics.power_factor is None else f"{metrics.power_factor:.6f}"
        pin_text = "n/a" if metrics.real_input_power_w is None else f"{metrics.real_input_power_w:.2f} W"
        irms_text = "n/a" if metrics.input_current_rms_a is None else f"{metrics.input_current_rms_a:.4f} A"
        self.summary.setPlainText(
            "TTPL SHARED-NGSPICE CLOSED LOOP\n"
            + "=" * 76
            + "\n"
            + f"Exact H(z) match       : {result.metadata.get('exact_hz_match_checked')}\n"
            + f"Controller redigitized : {result.metadata.get('controller_re_discretized')}\n"
            + f"Final Vbus             : {metrics.final_bus_voltage_v:.4f} V\n"
            + f"Tail Vbus ripple       : {metrics.bus_ripple_pp_v:.4f} Vpp\n"
            + f"Peak |IL|              : {metrics.peak_inductor_current_a:.4f} A\n"
            + f"Iin RMS                : {irms_text}\n"
            + f"Pin                     : {pin_text}\n"
            + f"PF                      : {pf_text}\n"
            + f"Duty range              : {metrics.duty_min:.5f} .. {metrics.duty_max:.5f}\n"
            + f"Current saturation      : {100.0*metrics.current_controller_saturation_fraction:.2f}% samples\n"
            + f"Shared SendData points  : {result.metadata.get('shared_senddata_points')}\n\n"
            + "MODEL BOUNDARY\n"
            + str(result.metadata.get("nonlinear_controller_semantics", ""))
        )
        self._plot(result)
        self.status.setText("Shared-ngspice run completed. Exact H(z) coefficient identity was checked before simulation.")

    def _plot(self, result: TTPLSharedNgSpiceResult) -> None:
        vectors = result.vectors
        t = np.asarray(vectors.get("time", []), dtype=float)
        self.figure.clear()
        if t.size == 0:
            self.canvas.draw_idle()
            return
        t_us = t * 1e6
        line = np.asarray(vectors.get("v(line)", np.zeros_like(t)))
        neutral = np.asarray(vectors.get("v(neutral)", np.zeros_like(t)))
        vac = line - neutral
        il = np.asarray(vectors.get("i(lboost)", np.zeros_like(t)))
        vbus = np.asarray(vectors.get("v(bus)", np.zeros_like(t)))
        sw = np.asarray(vectors.get("v(sw)", np.zeros_like(t)))

        ax1 = self.figure.add_subplot(411)
        ax2 = self.figure.add_subplot(412, sharex=ax1)
        ax3 = self.figure.add_subplot(413, sharex=ax1)
        ax4 = self.figure.add_subplot(414, sharex=ax1)
        ax1.plot(t_us, vac, label="Vac")
        ax1.plot(t_us, vbus, label="Vbus")
        ax2.plot(t_us, il, label="IL")
        ax3.plot(t_us, sw, label="Switch node")
        ax4.plot(t_us, vectors.get("v(g_hf_h)", np.zeros_like(t)), label="HF high gate")
        ax4.plot(t_us, vectors.get("v(g_hf_l)", np.zeros_like(t)), label="HF low gate")
        ax4.plot(t_us, vectors.get("v(g_lf_p)", np.zeros_like(t)), label="LF positive gate")
        ax4.plot(t_us, vectors.get("v(g_lf_n)", np.zeros_like(t)), label="LF negative gate")
        for axis in (ax1, ax2, ax3, ax4):
            axis.grid(True, alpha=0.3)
            axis.legend(fontsize=8, ncol=2)
        ax1.set_ylabel("V")
        ax2.set_ylabel("A")
        ax3.set_ylabel("V")
        ax4.set_ylabel("gate")
        ax4.set_xlabel("Time (us)")
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def set_busy(self, busy: bool) -> None:
        self.run_button.setEnabled((not busy) and self.analysis is not None and self.library is not None)


__all__ = ["TTPLNgSpiceClosedLoopView"]
