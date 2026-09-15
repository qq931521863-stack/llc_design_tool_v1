"""LLC shared-ngspice closed-loop verification workbench.

The widget is intentionally a thin GUI shell.  It does not design or
rediscretize a controller.  The host window supplies the exact H(z) already
selected in LLC Digital Control / Control Tools / FRA and executes the solver in
its worker thread.  Results enter the existing WaveformBundle viewer.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
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

from ...core.spec import LLCDesignSpec
from ...dynamics.waveforms import WaveformBundle
from .waveform_view import WaveformView


class ClosedLoopWaveformView(WaveformView):
    """Existing waveform viewer plus a digital-control trace group."""

    GROUPS = WaveformView.GROUPS + (
        (
            "闭环控制",
            (
                "control_reference",
                "v_output",
                "control_feedback",
                "control_error",
                "control_output",
                "switching_frequency",
            ),
        ),
    )


class ClosedLoopVerificationView(QWidget):
    analysis_requested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._controller_label = "尚未选择控制器"
        self._controller_sample_rate_hz: float | None = None
        self._ngspice_available = False

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)

        headline = QLabel(
            "数字闭环验证：Power Stage = shared-ngspice，Controller = 当前 Exact H(z)。"
            "本页不会重新计算控制器系数。"
        )
        headline.setWordWrap(True)
        root.addWidget(headline)

        splitter = QSplitter()
        root.addWidget(splitter, 1)

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 6, 0)

        status_box = QGroupBox("模型 / Controller")
        status_form = QFormLayout(status_box)
        self.engine_status = QLabel("ngspice: checking")
        self.engine_status.setWordWrap(True)
        self.controller_status = QLabel(self._controller_label)
        self.controller_status.setWordWrap(True)
        self.model_status = QLabel("FULL_BRIDGE LLC / ideal switching correlation + explicit damping")
        self.model_status.setWordWrap(True)
        status_form.addRow("Engine", self.engine_status)
        status_form.addRow("Controller", self.controller_status)
        status_form.addRow("Power Model", self.model_status)
        panel_layout.addWidget(status_box)

        scenario = QGroupBox("Transient Scenario")
        form = QFormLayout(scenario)
        self.duration_ms = QDoubleSpinBox()
        self.duration_ms.setRange(0.10, 200.0)
        self.duration_ms.setDecimals(3)
        self.duration_ms.setValue(5.0)
        self.duration_ms.setSuffix(" ms")
        self.step_time_ms = QDoubleSpinBox()
        self.step_time_ms.setRange(0.0, 199.0)
        self.step_time_ms.setDecimals(3)
        self.step_time_ms.setValue(2.0)
        self.step_time_ms.setSuffix(" ms")
        self.reference_step_v = QDoubleSpinBox()
        self.reference_step_v.setRange(-100.0, 100.0)
        self.reference_step_v.setDecimals(4)
        self.reference_step_v.setValue(1.0)
        self.reference_step_v.setSuffix(" V")
        self.vbus_v = QDoubleSpinBox()
        self.vbus_v.setRange(20.0, 2000.0)
        self.vbus_v.setDecimals(2)
        self.vbus_v.setValue(400.0)
        self.vbus_v.setSuffix(" V")
        self.load_percent = QDoubleSpinBox()
        self.load_percent.setRange(1.0, 150.0)
        self.load_percent.setDecimals(1)
        self.load_percent.setValue(100.0)
        self.load_percent.setSuffix(" %")
        form.addRow("Duration", self.duration_ms)
        form.addRow("Vref step time", self.step_time_ms)
        form.addRow("Vref Δ", self.reference_step_v)
        form.addRow("Vbus", self.vbus_v)
        form.addRow("Load", self.load_percent)
        panel_layout.addWidget(scenario)

        numeric = QGroupBox("SPICE Numeric")
        form = QFormLayout(numeric)
        self.output_step_us = QDoubleSpinBox()
        self.output_step_us.setRange(0.01, 100.0)
        self.output_step_us.setDecimals(3)
        self.output_step_us.setValue(0.25)
        self.output_step_us.setSuffix(" µs")
        self.max_step_us = QDoubleSpinBox()
        self.max_step_us.setRange(0.005, 50.0)
        self.max_step_us.setDecimals(3)
        self.max_step_us.setValue(0.08)
        self.max_step_us.setSuffix(" µs")
        form.addRow("Output step", self.output_step_us)
        form.addRow("Maximum solver Δt", self.max_step_us)
        panel_layout.addWidget(numeric)

        buttons = QHBoxLayout()
        self.run_button = QPushButton("RUN CLOSED LOOP")
        self.run_button.clicked.connect(self._request)
        buttons.addWidget(self.run_button)
        panel_layout.addLayout(buttons)

        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumBlockCount(1500)
        panel_layout.addWidget(self.summary, 1)
        splitter.addWidget(panel)

        self.waveform_view = ClosedLoopWaveformView()
        # This workbench owns execution controls; the embedded viewer is used
        # only for the common plotting/cursors/statistics contract.
        self.waveform_view.fast_button.hide()
        self.waveform_view.harmonic_button.hide()
        self.waveform_view.detail_button.hide()
        self.waveform_view.vbus.setEnabled(False)
        self.waveform_view.load_percent.setEnabled(False)
        splitter.addWidget(self.waveform_view)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 1300])

        self._refresh_run_enable()

    def set_nominal_spec(self, spec: LLCDesignSpec) -> None:
        self.vbus_v.setValue(float(spec.vbus_nom_v))
        self.load_percent.setValue(100.0)
        self.waveform_view.set_nominal_work_point(float(spec.vbus_nom_v))

    def set_engine_available(self, available: bool, detail: str = "") -> None:
        self._ngspice_available = bool(available)
        if available:
            text = "READY — shared libngspice detected"
        else:
            text = "NOT AVAILABLE — install shared ngspice/libngspice"
        if detail:
            text += f"\n{detail}"
        self.engine_status.setText(text)
        self._refresh_run_enable()

    def set_controller_info(self, label: str, sample_rate_hz: float | None = None) -> None:
        self._controller_label = str(label) if label else "尚未选择控制器"
        self._controller_sample_rate_hz = None if sample_rate_hz is None else float(sample_rate_hz)
        text = self._controller_label
        if self._controller_sample_rate_hz is not None:
            text += f"\nFs={self._controller_sample_rate_hz/1e3:.6g} kHz"
        self.controller_status.setText(text)
        self._refresh_run_enable()

    def _refresh_run_enable(self) -> None:
        has_controller = self._controller_sample_rate_hz is not None and self._controller_sample_rate_hz > 0.0
        self.run_button.setEnabled(self._ngspice_available and has_controller)

    def set_busy(self, busy: bool) -> None:
        self.run_button.setEnabled((not busy) and self._ngspice_available and self._controller_sample_rate_hz is not None)

    def options(self) -> dict[str, float]:
        duration_s = self.duration_ms.value() * 1e-3
        step_time_s = self.step_time_ms.value() * 1e-3
        if step_time_s >= duration_s:
            step_time_s = 0.5 * duration_s
        return {
            "duration_s": duration_s,
            "step_time_s": step_time_s,
            "reference_step_v": self.reference_step_v.value(),
            "vbus_v": self.vbus_v.value(),
            "load_fraction": self.load_percent.value() / 100.0,
            "output_step_s": self.output_step_us.value() * 1e-6,
            "max_step_s": self.max_step_us.value() * 1e-6,
        }

    def _request(self) -> None:
        self.analysis_requested.emit(self.options())

    def set_result(self, bundle: WaveformBundle, control_result) -> None:
        self.waveform_view.set_bundle(bundle)
        # Default directly to the new control group after a closed-loop run.
        self.waveform_view.group_combo.setCurrentIndex(len(self.waveform_view.GROUPS) - 1)
        d = control_result.diagnostics
        samples = control_result.samples
        final = samples[-1] if samples else None
        lines = [
            "CLOSED-LOOP VERIFICATION",
            "=" * 68,
            f"Engine: {control_result.metadata.get('engine', 'shared-ngspice')}",
            f"Controller: {control_result.metadata.get('controller_name', '')}",
            f"Source: {control_result.metadata.get('controller_source', '')}",
            f"Fs_ctrl: {float(control_result.metadata.get('sample_rate_hz', 0.0))/1e3:.6g} kHz",
            f"Control samples: {len(samples)}",
            f"SPICE accepted points: {control_result.metadata.get('shared_senddata_points', 'N/A')}",
            "",
            f"Regulation error: {d.regulation_error_v:+.6g} V",
            f"Output tail std: {d.output_std_v:.6g} V",
            f"Overshoot: {d.overshoot_percent:.6g} %",
            f"Settling: {d.settling_time_s*1e3:.6g} ms" if d.settling_time_s is not None else "Settling: not reached",
            f"Controller saturation: {d.controller_saturation_fraction*100:.4g} %",
            f"FM saturation: {d.modulator_saturation_fraction*100:.4g} %",
            f"Limit-cycle flag: {d.limit_cycle_detected}",
        ]
        if final is not None:
            lines += [
                "",
                f"Final Vout: {final.plant_output_v:.7g} V",
                f"Final error: {final.error_v:+.7g} V",
                f"Final controller: {final.controller_output:+.7g}",
                f"Final fsw: {final.frequency_actual_hz/1e3:.7g} kHz",
            ]
        if d.notes:
            lines += ["", "Notes:"] + [f"- {item}" for item in d.notes]
        lines += [
            "",
            "Model boundary:",
            "- Exact discrete H(z) is executed sample-by-sample; no re-discretization here.",
            "- V1 power stage is an ideal-switching correlation model with explicit small damping.",
            "- Sensing is engineering-unit sampling plus timing; detailed analog/ADC aperture fidelity is a later layer.",
            "- This transient is a design verification aid, not hardware/vendor-SPICE certification.",
        ]
        self.summary.setPlainText("\n".join(lines))


__all__ = ["ClosedLoopVerificationView", "ClosedLoopWaveformView"]
