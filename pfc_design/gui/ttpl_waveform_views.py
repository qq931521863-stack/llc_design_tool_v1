"""First-class TTPL AC/PF/THD and switching/zero-crossing views.

These views consume the existing validated ``pfc_design.control.waveforms``
results.  They do not create a second solver or reinterpret the control model;
they lift the mature time-domain outputs out of the historical monolithic
Control Lab so the PFC product workflow matches the LLC engineering structure.
"""
from __future__ import annotations

from collections.abc import Callable
import math

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QFontDatabase

from pfc_design.control import (
    PFCControlLabConfig,
    PFCLineCycleWaveforms,
    PFCSwitchingWaveforms,
    build_pfc_switching_waveforms,
)
from pfc_design.control.waveforms import PFC_PWM_STATE_NAMES


ResultTuple = tuple[object, PFCLineCycleWaveforms, PFCSwitchingWaveforms]


def _last_cycle_slice(line: PFCLineCycleWaveforms, line_hz: float) -> slice:
    time = np.asarray(line.time_s, dtype=float)
    if time.size < 2:
        return slice(0, len(time))
    start_time = time[-1] - 1.0 / max(line_hz, 1e-12)
    start = int(np.searchsorted(time, start_time, side="left"))
    return slice(max(start, 0), len(time))


def _normalised_angle(line: PFCLineCycleWaveforms, sl: slice) -> np.ndarray:
    angle = np.asarray(line.signals["line_angle_deg"], dtype=float)[sl]
    if angle.size == 0:
        return angle
    # The stored final sample can wrap from 359.99 to ~0 deg.  Build a monotonic
    # display axis while preserving the physical 0...360 electrical angle.
    unwrapped = np.degrees(np.unwrap(np.radians(angle), period=2.0 * math.pi))
    unwrapped -= unwrapped[0]
    return unwrapped


class TTPLACPerformanceView(QWidget):
    """Settled AC-line-cycle, PF/THD and harmonic engineering view."""

    def __init__(self, config_provider: Callable[[], PFCControlLabConfig], parent=None) -> None:
        super().__init__(parent)
        self.config_provider = config_provider
        self.result: ResultTuple | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)
        header = QHBoxLayout()
        title = QLabel("TTPL AC Line / PF / THD")
        title.setStyleSheet("font-size:16px;font-weight:650;")
        header.addWidget(title)
        hint = QLabel("Settled final line cycle · current tracking · bus ripple · integer harmonics")
        hint.setStyleSheet("color:#667085;")
        header.addWidget(hint)
        header.addStretch(1)
        root.addLayout(header)

        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(220)
        self.summary.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.summary.setPlainText("Run the TTPL control/time-domain analysis to populate AC metrics.")
        root.addWidget(self.summary)

        tabs = QTabWidget()
        self.line_figure = Figure(figsize=(12, 8))
        self.line_canvas = FigureCanvasQTAgg(self.line_figure)
        line_page = QWidget()
        line_layout = QVBoxLayout(line_page)
        line_layout.addWidget(self.line_canvas)
        tabs.addTab(line_page, "Line Cycle")

        self.harmonic_figure = Figure(figsize=(12, 8))
        self.harmonic_canvas = FigureCanvasQTAgg(self.harmonic_figure)
        harmonic_page = QWidget()
        harmonic_layout = QVBoxLayout(harmonic_page)
        harmonic_layout.addWidget(self.harmonic_canvas)
        tabs.addTab(harmonic_page, "PF / THD / Harmonics")
        root.addWidget(tabs, 1)

    def set_result(self, result: ResultTuple) -> None:
        self.result = result
        _, line, _ = result
        config = self.config_provider()
        metrics = line.metrics
        warnings = "\n".join(f"  - {item}" for item in line.warnings) or "  - none"
        self.summary.setPlainText(
            "TTPL SETTLED AC PERFORMANCE\n"
            + "=" * 78
            + "\n"
            + f"Vin RMS             : {metrics.input_voltage_rms_v:.3f} V\n"
            + f"Iin RMS / peak      : {metrics.input_current_rms_a:.4f} / {metrics.input_current_peak_a:.4f} A\n"
            + f"Pin / apparent      : {metrics.real_input_power_w:.3f} W / {metrics.apparent_power_va:.3f} VA\n"
            + f"PF                  : {metrics.power_factor:.7f}\n"
            + f"Displacement factor : {metrics.displacement_factor:.7f}\n"
            + f"Distortion factor   : {metrics.distortion_factor:.7f}\n"
            + f"Current THD         : {metrics.current_thd_percent:.5f} %\n"
            + f"Fundamental Irms    : {metrics.fundamental_current_rms_a:.4f} A\n"
            + f"Vbus avg / ripple   : {metrics.bus_voltage_average_v:.3f} V / {metrics.bus_voltage_ripple_pp_v:.3f} Vpp\n"
            + f"Cbus current RMS    : {metrics.bus_capacitor_current_rms_a:.4f} A\n"
            + f"Duty min / max      : {metrics.duty_min:.5f} / {metrics.duty_max:.5f}\n"
            + f"Current error RMS   : {metrics.current_error_rms_a:.5f} A\n"
            + f"Zero-cross err RMS  : {metrics.zero_cross_current_error_rms_a:.5f} A\n"
            + f"Minimum-pulse share : {100.0*metrics.minimum_pulse_fraction:.4f} %\n"
            + f"Control model       : {config.power_stage.vin_rms_v:.2f} Vrms / {config.power_stage.output_power_w/1000:.3f} kW\n\n"
            + "WARNINGS\n"
            + warnings
        )
        self._plot_line_cycle(line, config)
        self._plot_harmonics(line)

    def _plot_line_cycle(self, line: PFCLineCycleWaveforms, config: PFCControlLabConfig) -> None:
        sl = _last_cycle_slice(line, config.power_stage.line_frequency_hz)
        angle = _normalised_angle(line, sl)
        s = line.signals

        self.line_figure.clear()
        ax1 = self.line_figure.add_subplot(221)
        ax1b = ax1.twinx()
        ax1.plot(angle, np.asarray(s["vac"], dtype=float)[sl], label="Vac")
        ax1b.plot(angle, np.asarray(s["i_input_signed"], dtype=float)[sl], label="Iac", linestyle="--")
        ax1.set_title("Grid voltage / input current")
        ax1.set_ylabel("Vac (V)")
        ax1b.set_ylabel("Iac (A)")
        ax1.grid(True, alpha=0.3)

        ax2 = self.line_figure.add_subplot(222)
        ax2b = ax2.twinx()
        ax2.plot(angle, np.asarray(s["vbus"], dtype=float)[sl], label="Vbus")
        ax2b.plot(angle, np.asarray(s["bus_cap_current"], dtype=float)[sl], label="Icap", linestyle="--")
        ax2.set_title("DC bus / capacitor current")
        ax2.set_ylabel("Vbus (V)")
        ax2b.set_ylabel("Icap (A)")
        ax2.grid(True, alpha=0.3)

        ax3 = self.line_figure.add_subplot(223)
        ax3.plot(angle, np.asarray(s["i_ref"], dtype=float)[sl], label="Iref")
        ax3.plot(angle, np.asarray(s["i_input_signed"], dtype=float)[sl], label="Iac")
        ax3.plot(angle, np.asarray(s["current_error"], dtype=float)[sl], label="error", linewidth=1.0)
        ax3.set_title("Current tracking")
        ax3.set_xlabel("Electrical angle (deg)")
        ax3.set_ylabel("A")
        ax3.grid(True, alpha=0.3)
        ax3.legend(fontsize=8)

        ax4 = self.line_figure.add_subplot(224)
        ax4.plot(angle, np.asarray(s["duty_total"], dtype=float)[sl], label="Duty total")
        ax4.plot(angle, np.asarray(s["duty_ff"], dtype=float)[sl], label="Duty FF", linestyle="--")
        ax4.plot(angle, np.asarray(s["minimum_pulse_active"], dtype=float)[sl], label="Min-pulse active", linewidth=1.0)
        ax4.plot(angle, np.asarray(s["zero_cross_active"], dtype=float)[sl], label="ZC active", linewidth=1.0)
        ax4.set_title("Duty / nonlinear constraints")
        ax4.set_xlabel("Electrical angle (deg)")
        ax4.set_ylabel("pu / flag")
        ax4.grid(True, alpha=0.3)
        ax4.legend(fontsize=8)

        self.line_figure.tight_layout()
        self.line_canvas.draw_idle()

    def _plot_harmonics(self, line: PFCLineCycleWaveforms) -> None:
        metrics = line.metrics
        orders = np.asarray(metrics.harmonic_orders, dtype=int)
        amps = np.asarray(metrics.harmonic_current_rms_a, dtype=float)
        fundamental = max(metrics.fundamental_current_rms_a, 1e-12)
        percent = 100.0 * amps / fundamental

        self.harmonic_figure.clear()
        ax1 = self.harmonic_figure.add_subplot(211)
        ax1.bar(orders, amps)
        ax1.set_title("Input-current integer harmonics")
        ax1.set_xlabel("Harmonic order")
        ax1.set_ylabel("Irms (A)")
        ax1.grid(True, axis="y", alpha=0.3)

        ax2 = self.harmonic_figure.add_subplot(212)
        ax2.bar(orders, percent)
        ax2.set_title(
            f"Harmonics relative to fundamental · PF={metrics.power_factor:.6f} · THD={metrics.current_thd_percent:.4f}%"
        )
        ax2.set_xlabel("Harmonic order")
        ax2.set_ylabel("% of I1")
        ax2.grid(True, axis="y", alpha=0.3)
        self.harmonic_figure.tight_layout()
        self.harmonic_canvas.draw_idle()


class TTPLSwitchingValidationView(QWidget):
    """Local switching reconstruction plus zero-crossing state-machine view."""

    def __init__(self, config_provider: Callable[[], PFCControlLabConfig], parent=None) -> None:
        super().__init__(parent)
        self.config_provider = config_provider
        self.result: ResultTuple | None = None
        self.switching: PFCSwitchingWaveforms | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)
        header = QHBoxLayout()
        title = QLabel("TTPL Switching / Zero Crossing")
        title.setStyleSheet("font-size:16px;font-weight:650;")
        header.addWidget(title)
        header.addStretch(1)

        self.angle = QDoubleSpinBox()
        self.angle.setRange(0.0, 359.99)
        self.angle.setDecimals(2)
        self.angle.setValue(60.0)
        self.angle.setSuffix(" deg")
        self.cycles = QSpinBox()
        self.cycles.setRange(1, 20)
        self.cycles.setValue(3)
        self.samples = QSpinBox()
        self.samples.setRange(100, 10000)
        self.samples.setValue(600)
        self.rebuild_button = QPushButton("Rebuild Workpoint")
        for label, widget in (
            ("Line angle", self.angle),
            ("Cycles", self.cycles),
            ("Samples/cycle", self.samples),
        ):
            header.addWidget(QLabel(label))
            header.addWidget(widget)
        header.addWidget(self.rebuild_button)
        root.addLayout(header)

        self.summary = QLabel("Run the TTPL control/time-domain analysis first.")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        tabs = QTabWidget()
        self.switch_figure = Figure(figsize=(12, 8))
        self.switch_canvas = FigureCanvasQTAgg(self.switch_figure)
        switch_page = QWidget()
        switch_layout = QVBoxLayout(switch_page)
        switch_layout.addWidget(self.switch_canvas)
        tabs.addTab(switch_page, "Local Switching")

        self.zero_figure = Figure(figsize=(12, 8))
        self.zero_canvas = FigureCanvasQTAgg(self.zero_figure)
        zero_page = QWidget()
        zero_layout = QVBoxLayout(zero_page)
        zero_layout.addWidget(self.zero_canvas)
        tabs.addTab(zero_page, "Zero Crossing")
        root.addWidget(tabs, 1)

        self.rebuild_button.clicked.connect(self.rebuild)

    def set_result(self, result: ResultTuple) -> None:
        self.result = result
        switching = result[2]
        self.switching = switching
        self.angle.setValue(float(switching.line_angle_deg) % 360.0)
        self._show_switching(switching)
        self._plot_zero_crossing(result[1], self.config_provider())

    def rebuild(self) -> None:
        if self.result is None:
            self.summary.setText("Run the TTPL control/time-domain analysis first.")
            return
        _, line, _ = self.result
        try:
            switching = build_pfc_switching_waveforms(
                self.config_provider(),
                line_cycle=line,
                line_angle_deg=self.angle.value(),
                cycles=self.cycles.value(),
                samples_per_cycle=self.samples.value(),
            )
        except Exception as exc:
            self.summary.setText(f"Switching reconstruction failed: {exc}")
            return
        self.switching = switching
        self._show_switching(switching)

    def _show_switching(self, switching: PFCSwitchingWaveforms) -> None:
        s = switching.signals
        time_us = np.asarray(switching.time_s, dtype=float) * 1e6
        state_code = int(round(float(np.asarray(s["pwm_state_code"])[0])))
        state_name = PFC_PWM_STATE_NAMES.get(state_code, f"state {state_code}")
        source = "n/a" if switching.source_time_s is None else f"{switching.source_time_s*1e3:.4f} ms"
        duty = float(np.asarray(s["duty_command"])[0])
        vin = float(np.asarray(s["vac_instantaneous"])[0])
        vbus = float(np.asarray(s["vbus_workpoint"])[0])
        self.summary.setText(
            f"Angle={switching.line_angle_deg:.2f} deg · fsw={switching.switching_frequency_hz/1e3:.3f} kHz · "
            f"source t={source} · {state_name} · Duty={duty:.5f} · Vac={vin:.3f} V · Vbus={vbus:.3f} V"
        )

        self.switch_figure.clear()
        ax1 = self.switch_figure.add_subplot(411)
        ax1.plot(time_us, s["hf_high_gate"], label="HF high")
        ax1.plot(time_us, s["hf_low_gate"], label="HF low")
        ax1.plot(time_us, s["lf_polarity_gate"], label="LF polarity", linestyle="--")
        ax1.set_ylabel("Gate/state")
        ax1.set_title("Gate timing")
        ax1.grid(True, alpha=0.3)
        ax1.legend(fontsize=8, ncol=3)

        ax2 = self.switch_figure.add_subplot(412, sharex=ax1)
        ax2.plot(time_us, s["switch_node_voltage"], label="Vsw")
        ax2.plot(time_us, s["inductor_voltage"], label="VL", linestyle="--")
        ax2.set_ylabel("V")
        ax2.set_title("Switch node / inductor voltage")
        ax2.grid(True, alpha=0.3)
        ax2.legend(fontsize=8)

        ax3 = self.switch_figure.add_subplot(413, sharex=ax1)
        ax3.plot(time_us, s["inductor_current"], label="IL")
        ax3.plot(time_us, s["inductor_current_average"], label="IL avg", linestyle="--")
        ax3.plot(time_us, s["high_side_current"], label="I high", linewidth=1.0)
        ax3.plot(time_us, s["low_side_current"], label="I low", linewidth=1.0)
        ax3.set_ylabel("A")
        ax3.set_title("Inductor / device currents")
        ax3.grid(True, alpha=0.3)
        ax3.legend(fontsize=8, ncol=4)

        ax4 = self.switch_figure.add_subplot(414, sharex=ax1)
        ax4.plot(time_us, s["boost_output_current"], label="Iboost,out")
        ax4.plot(time_us, s["bus_cap_current"], label="Icap")
        ax4.set_xlabel("Local switching time (us)")
        ax4.set_ylabel("A")
        ax4.set_title("Boost output / bus capacitor current")
        ax4.grid(True, alpha=0.3)
        ax4.legend(fontsize=8)
        self.switch_figure.tight_layout()
        self.switch_canvas.draw_idle()

    def _plot_zero_crossing(self, line: PFCLineCycleWaveforms, config: PFCControlLabConfig) -> None:
        sl = _last_cycle_slice(line, config.power_stage.line_frequency_hz)
        angle = np.mod(np.asarray(line.signals["line_angle_deg"], dtype=float)[sl], 360.0)
        order = np.argsort(angle)
        angle = angle[order]
        s = {key: np.asarray(value, dtype=float)[sl][order] for key, value in line.signals.items()}

        self.zero_figure.clear()
        ax1 = self.zero_figure.add_subplot(411)
        ax1b = ax1.twinx()
        ax1.plot(angle, s["vac"], label="Vac")
        ax1b.plot(angle, s["i_input_signed"], label="Iac", linestyle="--")
        ax1b.plot(angle, s["i_ref"], label="Iref", linestyle=":")
        ax1.set_ylabel("Vac (V)")
        ax1b.set_ylabel("A")
        ax1.set_title("Full-cycle zero-crossing context")
        ax1.grid(True, alpha=0.3)

        ax2 = self.zero_figure.add_subplot(412, sharex=ax1)
        ax2.plot(angle, s["current_error"], label="Current error")
        ax2.plot(angle, s["current_pi_reset_strobe"], label="PI reset", linewidth=1.0)
        ax2.set_ylabel("A / flag")
        ax2.set_title("Tracking / PI reset")
        ax2.grid(True, alpha=0.3)
        ax2.legend(fontsize=8)

        ax3 = self.zero_figure.add_subplot(413, sharex=ax1)
        ax3.plot(angle, s["duty_total"], label="Duty")
        ax3.plot(angle, s["effective_duty_min"], label="Effective Dmin", linestyle="--")
        ax3.plot(angle, s["minimum_pulse_active"], label="Min pulse active", linewidth=1.0)
        ax3.set_ylabel("pu / flag")
        ax3.set_title("Duty / minimum-pulse constraint")
        ax3.grid(True, alpha=0.3)
        ax3.legend(fontsize=8)

        ax4 = self.zero_figure.add_subplot(414, sharex=ax1)
        ax4.step(angle, s["pwm_state_code"], where="mid", label="PWM state")
        ax4.plot(angle, s["zero_cross_active"], label="ZC active", linewidth=1.0)
        ax4.plot(angle, s["zc_deadband_fraction"], label="Deadband fraction", linewidth=1.0)
        ax4.set_xlabel("Electrical angle (deg)")
        ax4.set_ylabel("state / flag")
        ax4.set_title("Zero-crossing state machine")
        ax4.grid(True, alpha=0.3)
        ax4.legend(fontsize=8, ncol=3)
        self.zero_figure.tight_layout()
        self.zero_canvas.draw_idle()

    def set_busy(self, busy: bool) -> None:
        self.rebuild_button.setEnabled(not busy)
        self.angle.setEnabled(not busy)
        self.cycles.setEnabled(not busy)
        self.samples.setEnabled(not busy)


__all__ = ["TTPLACPerformanceView", "TTPLSwitchingValidationView"]
