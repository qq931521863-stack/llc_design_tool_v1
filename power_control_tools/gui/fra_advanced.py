from __future__ import annotations

import math

import numpy as np
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from power_control_tools.fra.auto_design import AutoDesignResult, auto_design_controller
from power_control_tools.fra.fitting import FRAFitResult, closed_loop_step_from_fitted_loop, fit_rational_frequency_response
from power_control_tools.fra.analysis import magnitude_phase
from power_control_tools.models import ControllerKind, DiscretizationMethod


_AUTO_KINDS = (
    ControllerKind.PI,
    ControllerKind.PIF,
    ControllerKind.PID,
    ControllerKind.TWO_P_TWO_Z,
    ControllerKind.THREE_P_THREE_Z,
)


class FRAAutoDesignDialog(QDialog):
    """One-click target-Fc/PM controller synthesis on the host's raw FRA plant."""

    def __init__(self, host) -> None:
        super().__init__(host)
        self.host = host
        self.result: AutoDesignResult | None = None
        self.setWindowTitle("FRA Auto Design — Target Fc / PM")
        self.resize(980, 760)

        root = QVBoxLayout(self)
        settings = QGroupBox("自动设计目标")
        form = QFormLayout(settings)
        self.kind = QComboBox()
        labels = {
            ControllerKind.PI: "PI",
            ControllerKind.PIF: "PIF",
            ControllerKind.PID: "PID",
            ControllerKind.TWO_P_TWO_Z: "2P2Z",
            ControllerKind.THREE_P_THREE_Z: "3P3Z",
        }
        for item in _AUTO_KINDS:
            self.kind.addItem(labels[item], item)
        self.fs = QDoubleSpinBox(); self.fs.setRange(1_000.0, 1_000_000.0); self.fs.setDecimals(1); self.fs.setSuffix(" Hz")
        self.fc = QDoubleSpinBox(); self.fc.setRange(1.0, 500_000.0); self.fc.setDecimals(2); self.fc.setSuffix(" Hz")
        self.pm = QDoubleSpinBox(); self.pm.setRange(5.0, 85.0); self.pm.setValue(60.0); self.pm.setDecimals(1); self.pm.setSuffix(" °")
        self.min_gm = QDoubleSpinBox(); self.min_gm.setRange(0.0, 30.0); self.min_gm.setValue(6.0); self.min_gm.setDecimals(1); self.min_gm.setSuffix(" dB")
        self.max_ms = QDoubleSpinBox(); self.max_ms.setRange(1.0, 5.0); self.max_ms.setValue(2.0); self.max_ms.setDecimals(2)
        self.method = QComboBox()
        for item in DiscretizationMethod:
            self.method.addItem(item.value, item)
        form.addRow("Controller", self.kind)
        form.addRow("Fs", self.fs)
        form.addRow("Target Fc", self.fc)
        form.addRow("Target PM", self.pm)
        form.addRow("Minimum GM", self.min_gm)
        form.addRow("Maximum Ms", self.max_ms)
        form.addRow("S→Z", self.method)
        root.addWidget(settings)

        buttons = QHBoxLayout()
        self.run_button = QPushButton("AUTO DESIGN")
        self.apply_button = QPushButton("应用到 FRA Loop Designer")
        self.apply_button.setEnabled(False)
        close_button = QPushButton("关闭")
        self.run_button.clicked.connect(self.run_design)
        self.apply_button.clicked.connect(self.apply_result)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

        self.output = QPlainTextEdit(); self.output.setReadOnly(True)
        root.addWidget(self.output, 1)
        self._load_defaults()

    def _load_defaults(self) -> None:
        self.host.recalculate()
        fs = 40_000.0
        if self.host.current_new_controller is not None:
            fs = self.host.current_new_controller.sample_rate_hz
        elif self.host.current_old_controller is not None:
            fs = self.host.current_old_controller.sample_rate_hz
        self.fs.setValue(fs)
        if self.host.current_metrics is not None and self.host.current_metrics.main_crossover_hz is not None:
            fc = float(self.host.current_metrics.main_crossover_hz)
        elif self.host.measurement is not None:
            f = self.host.measurement.frequency_hz
            fc = math.sqrt(float(f[0]) * float(f[-1]))
        else:
            fc = 1_000.0
        self.fc.setValue(fc)

    def _plant_arrays(self) -> tuple[np.ndarray, np.ndarray]:
        self.host.recalculate()
        if self.host.measurement is None or self.host.current_plant is None:
            raise ValueError("请先在 FRA Loop Designer 导入并解析 FRA 数据。")
        f = self.host.measurement.frequency_hz
        plant = np.asarray(self.host.current_plant, dtype=complex)
        mask = self.host.current_plant_valid_mask
        if mask is None:
            mask = np.ones_like(f, dtype=bool)
        f_use = np.asarray(f[mask], dtype=float)
        plant_use = np.asarray(plant[mask], dtype=complex)
        if len(f_use) < 4:
            raise ValueError("可用 Equivalent Plant 频点不足。")
        return f_use, plant_use

    def run_design(self) -> None:
        try:
            f, plant = self._plant_arrays()
            result = auto_design_controller(
                f,
                plant,
                controller_kind=self.kind.currentData(),
                sample_rate_hz=self.fs.value(),
                discretization_method=self.method.currentData(),
                target_crossover_hz=self.fc.value(),
                target_phase_margin_deg=self.pm.value(),
                min_gain_margin_db=self.min_gm.value(),
                max_ms=self.max_ms.value(),
            )
            self.result = result
            self.apply_button.setEnabled(result.selected is not None and result.selected.controller.implementable)
            lines = [
                "FRA AUTO DESIGN",
                "=" * 72,
                f"Status: {result.status}",
                f"Requested Fc: {result.requested_crossover_hz:.8g} Hz",
                f"Requested PM: {result.requested_phase_margin_deg:.6g} deg",
                f"Fallback used: {'YES' if result.fallback_used else 'NO'}",
                f"Message: {result.message}",
                "",
            ]
            if result.selected is not None:
                c = result.selected
                lines += [
                    "SELECTED",
                    f"Trial Fc: {c.requested_crossover_hz:.8g} Hz",
                    f"Achieved Fc: {c.achieved_crossover_hz if c.achieved_crossover_hz is not None else 'N/A'}",
                    f"PM: {c.achieved_phase_margin_deg if c.achieved_phase_margin_deg is not None else 'N/A'} deg",
                    f"GM: {c.gain_margin_db if c.gain_margin_db is not None else 'not observed'} dB",
                    f"Ms: {c.ms:.6g}",
                    f"Mt: {c.mt:.6g}",
                    f"Controller poles: {list(c.controller.poles)}",
                    "Parameters:",
                ]
                for key, value in c.parameters.items():
                    lines.append(f"  {key} = {value:.12g}")
            lines += ["", "TRIAL SUMMARY"]
            for i, c in enumerate(result.candidates, 1):
                lines.append(
                    f"#{i:02d} Fc_try={c.requested_crossover_hz:.7g} Hz | "
                    f"Fc={c.achieved_crossover_hz if c.achieved_crossover_hz is not None else float('nan'):.7g} | "
                    f"PM={c.achieved_phase_margin_deg if c.achieved_phase_margin_deg is not None else float('nan'):.5g} | "
                    f"Ms={c.ms:.4g} | {'PASS' if c.accepted else 'review'}"
                )
            self.output.setPlainText("\n".join(lines))
        except Exception as exc:
            self.result = None
            self.apply_button.setEnabled(False)
            QMessageBox.critical(self, "Auto Design 失败", str(exc))

    def apply_result(self) -> None:
        if self.result is None or self.result.selected is None:
            return
        selected = self.result.selected
        host = self.host
        # Auto Design always applies as a new explicit controller structure so
        # the user can continue manual slider refinement after one-click design.
        if hasattr(host, "new_mode"):
            index = host.new_mode.findData("structure")
            if index >= 0:
                host.new_mode.setCurrentIndex(index)
        index = host.new_kind.findData(selected.controller_kind)
        if index >= 0:
            host.new_kind.setCurrentIndex(index)
        host.new_fs.setValue(selected.controller.sample_rate_hz)
        method_index = host.new_method.findData(self.method.currentData())
        if method_index >= 0:
            host.new_method.setCurrentIndex(method_index)
        p = selected.parameters
        if "kp" in p: host.new_kp.setValue(p["kp"])
        if "ti_s" in p: host.new_ti.setValue(p["ti_s"])
        if "td_s" in p: host.new_td.setValue(max(p["td_s"], 1e-9))
        if "lpf_pole_hz" in p: host.new_lpf.setValue(p["lpf_pole_hz"])
        if "gain" in p: host.new_gain.setValue(p["gain"])
        for i in range(1, 4):
            if f"fz{i}_hz" in p:
                getattr(host, f"new_fz{i}").setValue(p[f"fz{i}_hz"])
            if f"fp{i}_hz" in p:
                getattr(host, f"new_fp{i}").setValue(p[f"fp{i}_hz"])
        host.recalculate()
        QMessageBox.information(self, "Auto Design 已应用", "自动设计参数已回写到 FRA Loop Designer，可继续用 Slider 手动微调并导出 C99。")


class FRAModelFitDialog(QDialog):
    """Optional low-order rational identification of measured FRA responses."""

    def __init__(self, host) -> None:
        super().__init__(host)
        self.host = host
        self.result: FRAFitResult | None = None
        self.setWindowTitle("FRA Model Identification — Rational Fit")
        self.resize(1250, 900)

        root = QVBoxLayout(self)
        settings = QGroupBox("模型辨识")
        form = QFormLayout(settings)
        self.target = QComboBox()
        self.target.addItem("Equivalent Plant", "plant")
        self.target.addItem("Current New Open Loop", "loop")
        self.max_order = QSpinBox(); self.max_order.setRange(1, 5); self.max_order.setValue(5)
        self.fit_delay = QCheckBox("Fit pure delay"); self.fit_delay.setChecked(True)
        self.focus = QDoubleSpinBox(); self.focus.setRange(0.0, 500_000.0); self.focus.setDecimals(2); self.focus.setSuffix(" Hz")
        form.addRow("Fit Target", self.target)
        form.addRow("Max Poles", self.max_order)
        form.addRow("Delay", self.fit_delay)
        form.addRow("Control Focus", self.focus)
        root.addWidget(settings)

        row = QHBoxLayout()
        run = QPushButton("FIT MODEL")
        close = QPushButton("关闭")
        run.clicked.connect(self.run_fit)
        close.clicked.connect(self.accept)
        row.addWidget(run); row.addWidget(close)
        root.addLayout(row)

        tabs = QTabWidget()
        self.fit_fig = Figure(figsize=(8, 6)); self.fit_canvas = FigureCanvasQTAgg(self.fit_fig); tabs.addTab(self.fit_canvas, "Measured vs Fit")
        self.step_fig = Figure(figsize=(8, 5)); self.step_canvas = FigureCanvasQTAgg(self.step_fig); tabs.addTab(self.step_canvas, "Approx Closed-Loop Step")
        self.details = QPlainTextEdit(); self.details.setReadOnly(True); tabs.addTab(self.details, "Model / Errors")
        root.addWidget(tabs, 1)
        self._load_defaults()

    def _load_defaults(self) -> None:
        self.host.recalculate()
        if self.host.current_metrics is not None and self.host.current_metrics.main_crossover_hz is not None:
            self.focus.setValue(float(self.host.current_metrics.main_crossover_hz))

    def _target_arrays(self) -> tuple[np.ndarray, np.ndarray, str]:
        self.host.recalculate()
        if self.host.measurement is None:
            raise ValueError("请先导入 FRA 数据。")
        f = np.asarray(self.host.measurement.frequency_hz, dtype=float)
        if self.target.currentData() == "loop":
            if self.host.current_loop is None or self.host.current_usable_mask is None:
                raise ValueError("当前没有有效的新开环响应。")
            mask = np.asarray(self.host.current_usable_mask, dtype=bool)
            return f[mask], np.asarray(self.host.current_loop, dtype=complex)[mask], "New Open Loop"
        if self.host.current_plant is None:
            raise ValueError("当前没有有效的 Equivalent Plant。")
        mask = self.host.current_plant_valid_mask
        if mask is None:
            mask = np.ones_like(f, dtype=bool)
        return f[mask], np.asarray(self.host.current_plant, dtype=complex)[mask], "Equivalent Plant"

    def run_fit(self) -> None:
        try:
            f, measured, name = self._target_arrays()
            focus = self.focus.value() if self.focus.value() > 0.0 else None
            result = fit_rational_frequency_response(
                f,
                measured,
                max_poles=self.max_order.value(),
                focus_hz=focus,
                fit_delay=self.fit_delay.isChecked(),
                fit_target=name,
            )
            self.result = result
            fitted = result.fitted_response
            mm, mp = magnitude_phase(measured)
            fm, fp = magnitude_phase(fitted)
            ratio = fitted / np.where(np.abs(measured) > 1e-300, measured, 1e-300 + 0j)
            err_mag = 20.0 * np.log10(np.maximum(np.abs(ratio), 1e-300))
            err_phase = np.angle(ratio, deg=True)

            self.fit_fig.clear()
            ax = self.fit_fig.add_subplot(211); ap = self.fit_fig.add_subplot(212, sharex=ax)
            ax.semilogx(f, mm, label="Measured"); ax.semilogx(f, fm, "--", label="Rational Fit")
            ap.semilogx(f, mp, label="Measured"); ap.semilogx(f, fp, "--", label="Rational Fit")
            ax.set_ylabel("Magnitude (dB)"); ap.set_ylabel("Phase (deg)"); ap.set_xlabel("Frequency (Hz)")
            ax.grid(True, which="both"); ap.grid(True, which="both"); ax.legend(); ap.legend(); self.fit_fig.tight_layout(); self.fit_canvas.draw_idle()

            step_text = "Step is only defined here when fitting the New Open Loop."
            self.step_fig.clear(); sax = self.step_fig.add_subplot(111)
            if self.target.currentData() == "loop" and result.metrics.confidence != "LOW":
                step = closed_loop_step_from_fitted_loop(result.model)
                if step.stable and len(step.time_s):
                    sax.plot(step.time_s * 1e3, step.response)
                    sax.set_xlabel("Time (ms)"); sax.set_ylabel("Closed-loop response"); sax.grid(True)
                    sax.set_title(
                        f"Fit-derived step | Overshoot={step.overshoot_percent:.3g}% | "
                        f"Ts={step.settling_time_s*1e3:.4g} ms" if step.settling_time_s is not None else "Fit-derived step"
                    )
                    step_text = step.note
                else:
                    sax.text(0.5, 0.5, step.note, ha="center", va="center", transform=sax.transAxes, wrap=True)
                    step_text = step.note
            else:
                sax.text(0.5, 0.5, step_text, ha="center", va="center", transform=sax.transAxes, wrap=True)
            self.step_fig.tight_layout(); self.step_canvas.draw_idle()

            m = result.metrics; model = result.model
            lines = [
                "FRA RATIONAL MODEL IDENTIFICATION",
                "=" * 72,
                f"Target: {result.fit_target}",
                f"Selected order: {result.selected_order} (max requested {result.requested_max_order})",
                f"Confidence: {m.confidence}",
                f"Delay: {model.delay_s*1e6:.6g} us",
                f"Real poles (Hz): {list(model.real_pole_hz)}",
                f"Complex pole pairs (fn Hz, zeta): {list(model.complex_pole_pairs)}",
                f"Zeros (rad/s): {list(model.zeros_rad_s)}",
                "",
                f"Gain RMS error: {m.magnitude_rms_db:.6g} dB",
                f"Gain MAX error: {m.magnitude_max_db:.6g} dB",
                f"Phase RMS error: {m.phase_rms_deg:.6g} deg",
                f"Phase MAX error: {m.phase_max_deg:.6g} deg",
                f"Focus Gain RMS: {m.focus_magnitude_rms_db if m.focus_magnitude_rms_db is not None else 'N/A'} dB",
                f"Focus Phase RMS: {m.focus_phase_rms_deg if m.focus_phase_rms_deg is not None else 'N/A'} deg",
                "",
                "Numerator coefficients in normalized x=s/wref (ascending):",
                str(model.numerator_coefficients),
                f"Reference frequency: {model.reference_frequency_hz:.8g} Hz",
                "",
                result.note,
                step_text,
                "",
                "IMPORTANT: raw FRA remains the stability authority; the fitted model is for interpretation/time-domain approximation.",
                "",
                f"Error arrays: gain RMS={np.sqrt(np.mean(err_mag**2)):.6g} dB, phase RMS={np.sqrt(np.mean(err_phase**2)):.6g} deg",
            ]
            self.details.setPlainText("\n".join(lines))
        except Exception as exc:
            self.result = None
            QMessageBox.critical(self, "Model Fit 失败", str(exc))


def install_advanced_fra_actions(window) -> None:
    """Attach V1.5/V2 actions to an existing FRA Loop Designer window."""
    toolbar = window.addToolBar("FRA Advanced")
    toolbar.setMovable(False)
    auto_action = QAction("Auto Design", window)
    fit_action = QAction("Model ID / Fit", window)
    auto_action.setToolTip("输入目标 Fc / PM，自动设计控制器；若相位裕量不足自动降低 Fc")
    fit_action.setToolTip("将 Equivalent Plant 或新开环 FRA 拟合为最高 5 阶稳定有理模型")
    auto_action.triggered.connect(lambda checked=False: FRAAutoDesignDialog(window).exec())
    fit_action.triggered.connect(lambda checked=False: FRAModelFitDialog(window).exec())
    toolbar.addAction(auto_action)
    toolbar.addAction(fit_action)


__all__ = ["FRAAutoDesignDialog", "FRAModelFitDialog", "install_advanced_fra_actions"]
