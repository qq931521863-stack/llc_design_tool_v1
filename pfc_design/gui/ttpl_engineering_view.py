"""Engineering-first single-phase TTPL workspace.

The legacy TTPL control laboratory is retained as the detailed control/sensing/
line-cycle engine.  This wrapper adds the missing specification -> hardware
sizing stage and makes the product workflow explicit instead of presenting PFC
as one large control page.
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from pfc_design.engineering import TTPLDesignResult, TTPLDesignSpec, analyze_ttpl_design
from .control_lab_view import PFCControlLabView


class TTPLPowerStageDesignView(QWidget):
    """Specification-to-hardware sizing page for the TTPL power stage."""

    apply_requested = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.result: TTPLDesignResult | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)

        header = QHBoxLayout()
        title = QLabel("TTPL Power Stage Design")
        title.setStyleSheet("font-size:16px;font-weight:650;")
        header.addWidget(title)
        hint = QLabel("Requirements → Lboost / current stress / duty envelope / DC-bus capacitance")
        hint.setStyleSheet("color:#667085;")
        header.addWidget(hint)
        header.addStretch(1)
        self.run_button = QPushButton("计算功率级")
        self.apply_button = QPushButton("应用到 Control / AC / Switching")
        self.apply_button.setEnabled(False)
        header.addWidget(self.run_button)
        header.addWidget(self.apply_button)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_inputs())
        splitter.addWidget(self._build_results())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 1180])
        root.addWidget(splitter, 1)

        self.run_button.clicked.connect(self.calculate)
        self.apply_button.clicked.connect(self._apply)
        self.calculate()

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

        grid = QGroupBox("Electrical specification")
        form = QFormLayout(grid)
        self.vin_min = self._spin(20, 400, 2, 176, " V")
        self.vin_nom = self._spin(20, 400, 2, 230, " V")
        self.vin_max = self._spin(20, 400, 2, 264, " V")
        self.line_hz = self._spin(40, 70, 2, 50, " Hz")
        self.vbus = self._spin(100, 1000, 2, 400, " V")
        self.pout = self._spin(10, 100000, 1, 3300, " W")
        self.efficiency = self._spin(0.70, 1.0, 4, 0.97)
        self.fsw = self._spin(5, 500, 2, 65, " kHz")
        for label, widget in (
            ("Vin min RMS", self.vin_min),
            ("Vin nominal RMS", self.vin_nom),
            ("Vin max RMS", self.vin_max),
            ("Line frequency", self.line_hz),
            ("DC bus", self.vbus),
            ("Output power", self.pout),
            ("Efficiency estimate", self.efficiency),
            ("Switching frequency", self.fsw),
        ):
            form.addRow(label, widget)
        layout.addWidget(grid)

        sizing = QGroupBox("Sizing constraints")
        form = QFormLayout(sizing)
        self.ripple_ratio = self._spin(0.01, 2.0, 3, 0.30)
        self.bus_ripple = self._spin(0.1, 100, 2, 12, " Vpp")
        self.hold_up_ms = self._spin(0, 100, 2, 10, " ms")
        self.hold_end = self._spin(50, 999, 2, 320, " V")
        self.duty_min = self._spin(0.0, 0.9, 4, 0.01)
        self.duty_max = self._spin(0.01, 1.0, 4, 0.98)
        self.min_pulse_us = self._spin(0.0, 50.0, 4, 0.0, " µs")
        for label, widget in (
            ("Max ΔIL / Ipk(low-line)", self.ripple_ratio),
            ("Allowed 2×line bus ripple", self.bus_ripple),
            ("Hold-up time", self.hold_up_ms),
            ("Hold-up end voltage", self.hold_end),
            ("Duty minimum", self.duty_min),
            ("Duty maximum", self.duty_max),
            ("Minimum effective pulse", self.min_pulse_us),
        ):
            form.addRow(label, widget)
        layout.addWidget(sizing)

        note = QLabel(
            "Sizing uses explicit CCM boost equations. The line-cycle page remains the authority for nonlinear zero-crossing/minimum-pulse behaviour; switching simulation and hardware evidence are separate validation stages."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#667085;padding:6px;")
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _build_results(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 2, 2, 2)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(250)
        self.summary.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        layout.addWidget(self.summary)

        self.figure = Figure(figsize=(11, 7))
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas, 1)
        return page

    def design_spec(self) -> TTPLDesignSpec:
        return TTPLDesignSpec(
            vin_min_rms_v=self.vin_min.value(),
            vin_nom_rms_v=self.vin_nom.value(),
            vin_max_rms_v=self.vin_max.value(),
            line_frequency_hz=self.line_hz.value(),
            bus_voltage_v=self.vbus.value(),
            output_power_w=self.pout.value(),
            efficiency=self.efficiency.value(),
            switching_frequency_hz=self.fsw.value() * 1e3,
            inductor_ripple_ratio_peak=self.ripple_ratio.value(),
            bus_ripple_pp_v=self.bus_ripple.value(),
            hold_up_time_s=self.hold_up_ms.value() * 1e-3,
            hold_up_end_voltage_v=self.hold_end.value(),
            duty_min=self.duty_min.value(),
            duty_max=self.duty_max.value(),
            minimum_effective_pulse_s=self.min_pulse_us.value() * 1e-6,
        )

    def calculate(self) -> None:
        try:
            self.result = analyze_ttpl_design(self.design_spec())
        except Exception as exc:
            QMessageBox.warning(self, "TTPL Power Stage Design", str(exc))
            return
        self.apply_button.setEnabled(True)
        self._show_result(self.result)

    def _apply(self) -> None:
        if self.result is None:
            self.calculate()
        if self.result is not None:
            self.apply_requested.emit(self.result)

    def _show_result(self, result: TTPLDesignResult) -> None:
        spec = result.spec
        points = "\n".join(
            f"  {p.label:<8} {p.vin_rms_v:7.2f} Vrms  Iin={p.input_current_rms_a:7.3f} Arms / {p.input_current_peak_a:7.3f} Apk  D@Vpk={p.duty_at_line_peak:7.4f}"
            for p in result.work_points
        )
        warnings = "\n".join(f"  - {item}" for item in result.warnings) or "  - none"
        self.summary.setPlainText(
            "TTPL ENGINEERING DESIGN\n"
            "=" * 76 + "\n"
            f"Input range       : {spec.vin_min_rms_v:.2f} / {spec.vin_nom_rms_v:.2f} / {spec.vin_max_rms_v:.2f} Vrms\n"
            f"Output            : {spec.bus_voltage_v:.2f} Vdc / {spec.output_power_w/1000:.3f} kW\n"
            f"Switching         : {spec.switching_frequency_hz/1e3:.3f} kHz\n"
            f"Required Lboost   : {result.required_inductance_uh:.3f} µH\n"
            f"Ripple target/max : {result.ripple_target_pp_a:.3f} / {result.ripple_max_pp_a:.3f} App @ {result.ripple_max_angle_deg:.2f}° low-line\n"
            f"Estimated IL peak : {result.estimated_inductor_peak_a:.3f} A\n"
            f"Bus C (ripple)    : {result.required_bus_cap_ripple_f*1e6:.1f} µF\n"
            f"Bus C (hold-up)   : {result.required_bus_cap_hold_up_f*1e6:.1f} µF\n"
            f"Bus C recommended : {result.recommended_bus_capacitance_uf:.1f} µF\n"
            f"Bus ripple @ C    : {result.predicted_bus_ripple_pp_v:.3f} Vpp\n"
            f"Bus-cap 2ω Irms   : {result.bus_capacitor_ripple_current_rms_a:.3f} A\n"
            f"High-line headroom: {result.boost_headroom_v:.3f} V\n\n"
            "INPUT WORK POINTS\n" + points + "\n\n"
            "WARNINGS / MODEL BOUNDARIES\n" + warnings
        )

        trace = result.nominal_trace
        self.figure.clear()
        ax1 = self.figure.add_subplot(221)
        ax2 = self.figure.add_subplot(222)
        ax3 = self.figure.add_subplot(223)
        ax4 = self.figure.add_subplot(224)

        ax1.plot(trace.angle_deg, trace.vin_abs_v, label="|Vin|")
        ax1.set_title("Nominal rectified line voltage")
        ax1.set_xlabel("Line angle (deg)")
        ax1.set_ylabel("V")
        ax1.grid(True, alpha=0.3)

        ax2.plot(trace.angle_deg, trace.duty)
        ax2.set_title("Boost duty envelope")
        ax2.set_xlabel("Line angle (deg)")
        ax2.set_ylabel("Duty")
        ax2.grid(True, alpha=0.3)

        ax3.plot(trace.angle_deg, trace.average_inductor_current_a, label="Iavg")
        ax3.plot(trace.angle_deg, trace.inductor_peak_a, linestyle="--", label="Ipeak")
        ax3.plot(trace.angle_deg, trace.inductor_valley_a, linestyle=":", label="Ivalley")
        ax3.set_title("Inductor current envelope")
        ax3.set_xlabel("Line angle (deg)")
        ax3.set_ylabel("A")
        ax3.grid(True, alpha=0.3)
        ax3.legend(fontsize=8)

        ax4.plot(trace.angle_deg, trace.ripple_pp_a)
        ax4.axhline(result.ripple_target_pp_a, linestyle="--", linewidth=1.0, label="Low-line design target")
        ax4.set_title("Switching ripple, nominal line")
        ax4.set_xlabel("Line angle (deg)")
        ax4.set_ylabel("ΔIL pp (A)")
        ax4.grid(True, alpha=0.3)
        ax4.legend(fontsize=8)

        self.figure.tight_layout()
        self.canvas.draw_idle()


class _EngineeringControlLabView(PFCControlLabView):
    """Existing control lab with the engineering sizing efficiency propagated."""

    def __init__(self, parent=None) -> None:
        self.engineering_efficiency = 0.97
        super().__init__(parent)

    def _config(self):
        config = super()._config()
        stage = replace(config.power_stage, efficiency=float(self.engineering_efficiency))
        return replace(config, power_stage=stage)


class TTPLWorkbenchView(QWidget):
    """Structured TTPL engineering workflow with compatibility delegation."""

    analysis_requested = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.power_stage_view = TTPLPowerStageDesignView()
        self.control_lab = _EngineeringControlLabView()
        self.tabs.addTab(self.power_stage_view, "1. Power Stage / Sizing")
        self.tabs.addTab(self.control_lab, "2. Control / Sensing / AC / Switching")
        root.addWidget(self.tabs)

        self.control_lab.analysis_requested.connect(self.analysis_requested.emit)
        self.power_stage_view.apply_requested.connect(self._apply_design_to_control)

    def _apply_design_to_control(self, result: TTPLDesignResult) -> None:
        spec = result.spec
        self.control_lab.engineering_efficiency = spec.efficiency
        self.control_lab.vin_rms.setValue(spec.vin_nom_rms_v)
        self.control_lab.line_hz.setValue(spec.line_frequency_hz)
        self.control_lab.vbus.setValue(spec.bus_voltage_v)
        self.control_lab.pout.setValue(spec.output_power_w)
        self.control_lab.fsw.setValue(spec.switching_frequency_hz / 1e3)
        self.control_lab.inductance.setValue(result.required_inductance_uh)
        self.control_lab.cbus.setValue(result.recommended_bus_capacitance_uf)
        self.control_lab.duty_min.setValue(spec.duty_min)
        self.control_lab.duty_max.setValue(spec.duty_max)
        self.control_lab.min_pulse_us.setValue(spec.minimum_effective_pulse_s * 1e6)
        if hasattr(self.control_lab, "inductor_editor"):
            self.control_lab.inductor_editor.sync_from_stage()
        self.tabs.setCurrentWidget(self.control_lab)

    # Compatibility surface used by PFCMainWindow.
    def _request(self) -> None:
        if self.tabs.currentWidget() is self.power_stage_view:
            self.power_stage_view.calculate()
        else:
            self.control_lab._request()

    def set_busy(self, busy: bool) -> None:
        self.control_lab.set_busy(busy)
        self.power_stage_view.run_button.setEnabled(not busy)
        self.power_stage_view.apply_button.setEnabled((not busy) and self.power_stage_view.result is not None)

    def set_result(self, result) -> None:
        self.control_lab.set_result(result)


__all__ = ["TTPLPowerStageDesignView", "TTPLWorkbenchView"]
