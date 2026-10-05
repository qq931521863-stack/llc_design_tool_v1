"""Sparse measured PI experiment map and next-scan planning; no hardware connection."""
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from power_control_tools.fra.tuning_session import (
    OperatingPoint, PIParameters, Scan, Session, Targets, export_scan_csv, import_csv,
    metrics, score, suggestions,
)


def number(value, minimum=1e-9, maximum=1e12, decimals=9):
    widget = QDoubleSpinBox()
    widget.setDecimals(decimals)
    widget.setRange(minimum, maximum)
    widget.setValue(value)
    return widget


class ScanImportDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.scan = None
        self.setWindowTitle("绑定实测 FRA / Bind measured scan")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("列号从 1 开始；必须明确单位与控制器。仅支持 PI；其他控制器请使用原 FRA 工作区。"))
        form = QFormLayout()
        layout.addLayout(form)
        self.name = QLineEdit(Path(path).stem)
        form.addRow("测量名称", self.name)
        self.cols = []
        for label, value in (("频率列", 1), ("幅值列", 2), ("相位列", 3)):
            w = QSpinBox(); w.setRange(1, 999); w.setValue(value)
            form.addRow(label, w); self.cols.append(w)
        self.skip = QSpinBox(); self.skip.setRange(0, 999); self.skip.setValue(1)
        form.addRow("跳过表头行数", self.skip)
        self.delimiter = QComboBox(); self.delimiter.addItems([",", ";", "Tab"])
        form.addRow("分隔符", self.delimiter)
        self.units = []
        for label, values in (("频率单位", ["Hz", "kHz", "rad/s"]), ("幅值单位", ["dB", "linear"]), ("相位单位", ["deg", "rad"])):
            w = QComboBox(); w.addItems(values); form.addRow(label, w); self.units.append(w)
        self.kp, self.ti, self.ts = number(0.1), number(0.001), number(0.00001)
        for label, w in (("Kp", self.kp), ("Ti (s), Ki=Kp/Ti", self.ti), ("Ts (s)", self.ts)):
            form.addRow(label, w)
        self.method = QComboBox(); self.method.addItems(["tustin", "backward_euler"])
        form.addRow("PI 离散化", self.method)
        self.input_unit, self.output_unit = QLineEdit("V"), QLineEdit("duty")
        form.addRow("控制器输入单位 / scaling", self.input_unit)
        form.addRow("控制器输出单位 / scaling", self.output_unit)
        self.vin, self.load = number(400), number(1000, minimum=0)
        self.mode, self.group = QLineEdit("CCM"), QLineEdit("bench-1")
        for label, w in (("Vin (V)", self.vin), ("负载 (W)", self.load), ("模式", self.mode), ("工况组 / 硬件配置", self.group)):
            form.addRow(label, w)
        self.confirm = QCheckBox("确认是有符号完整环路增益 L，负反馈特征式 1+L=0")
        form.addRow(self.confirm)
        self.offset = number(0, minimum=-1080, maximum=1080, decimals=3)
        form.addRow("注入相位修正 (°)", self.offset)
        self.excluded = QLineEdit()
        form.addRow("排除噪声点序号（排序后，从1起，逗号分隔）", self.excluded)
        self.flags = QLineEdit()
        form.addRow("质量问题（非空则禁用预测）", self.flags)
        self.error = QLabel(); self.error.setWordWrap(True); layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def validate(self):
        try:
            measurement = import_csv(self.path, columns=tuple(w.value() - 1 for w in self.cols),
                frequency_unit=self.units[0].currentText(), magnitude_unit=self.units[1].currentText(),
                phase_unit=self.units[2].currentText(), delimiter="\t" if self.delimiter.currentText() == "Tab" else self.delimiter.currentText(), skip_rows=self.skip.value())
            controller = PIParameters(self.kp.value(), self.ti.value(), self.ts.value(), self.method.currentText(), self.input_unit.text(), self.output_unit.text())
            op = OperatingPoint(self.vin.value(), self.load.value(), self.mode.text(), self.group.text())
            excluded = [int(v.strip()) - 1 for v in self.excluded.text().split(",") if v.strip()]
            self.scan = Scan(self.name.text(), measurement, controller, op,
                "signed_loop_gain" if self.confirm.isChecked() else "unknown",
                "negative_feedback_L" if self.confirm.isChecked() else "unknown",
                self.offset.value(), excluded, [self.flags.text()] if self.flags.text().strip() else [])
        except (ValueError, OSError) as exc:
            self.error.setText(str(exc))
            return
        self.accept()


class FRATuningMapWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FRA 实测迭代调参 · Kp × Ti")
        self.resize(1500, 950)
        self.session = Session()
        self.predictions = []
        root = QWidget(); self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        intro = QLabel("导入基线 → 选择局部参数建议 → 用户自行验证并应用 → 复测导入。圆点=实测，叉号=预测；空白=未知。没有硬件自动控制。")
        intro.setWordWrap(True); layout.addWidget(intro)
        row = QHBoxLayout(); layout.addLayout(row)
        for label, callback in (("导入新一轮 CSV", self.import_scan), ("保存会话 JSON", self.save_session), ("打开会话 JSON", self.open_session), ("导出当前实测 CSV", self.export_current)):
            button = QPushButton(label); button.clicked.connect(callback); row.addWidget(button)
        self.scan_selector = QComboBox(); self.scan_selector.currentIndexChanged.connect(self.select_scan)
        row.addWidget(self.scan_selector)
        targets = QHBoxLayout(); layout.addLayout(targets)
        self.fc, self.pm, self.gm = number(1000), number(50), number(6)
        for label, w in (("目标 Fc (Hz)", self.fc), ("最低 PM (°)", self.pm), ("最低 GM (dB)", self.gm)):
            targets.addWidget(QLabel(label)); targets.addWidget(w)
            w.valueChanged.connect(self.targets_changed)
        self.summary = QLabel("尚未导入实测点"); self.summary.setWordWrap(True); layout.addWidget(self.summary)
        self.figure = Figure(figsize=(13, 6), constrained_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure); layout.addWidget(self.canvas, 1)
        self.details = QPlainTextEdit(); self.details.setReadOnly(True); self.details.setMaximumHeight(190); layout.addWidget(self.details)
        self.refresh()

    def import_scan(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import measured scan", "", "CSV (*.csv *.txt);;All files (*)")
        if not path:
            return
        dialog = ScanImportDialog(path, self)
        if self.session.current:
            previous = self.session.current
            for w, value in ((dialog.kp, previous.controller.kp), (dialog.ti, previous.controller.ti_s), (dialog.ts, previous.controller.sample_period_s), (dialog.vin, previous.operating_point.vin_v), (dialog.load, previous.operating_point.load_w)):
                w.setValue(value)
            dialog.method.setCurrentText(previous.controller.method)
            dialog.input_unit.setText(previous.controller.input_unit); dialog.output_unit.setText(previous.controller.output_unit)
            dialog.mode.setText(previous.operating_point.mode); dialog.group.setText(previous.operating_point.label)
            # Measurement meaning requires explicit reconfirmation for every imported file.
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.add_scan(dialog.scan)

    def add_scan(self, scan):
        self.session.add(scan)
        self.sync_selector()
        self.refresh()

    def sync_selector(self):
        self.scan_selector.blockSignals(True)
        self.scan_selector.clear()
        self.scan_selector.addItems([s.name for s in self.session.scans])
        self.scan_selector.setCurrentIndex(self.session.current_index)
        self.scan_selector.blockSignals(False)

    def select_scan(self, index):
        if 0 <= index < len(self.session.scans):
            self.session.current_index = index
            self.refresh()

    def targets_changed(self):
        self.session.targets = Targets(self.fc.value(), self.pm.value(), self.gm.value())
        self.refresh()

    def metric_text(self, scan):
        m = metrics(scan, targets=self.session.targets)
        if m is None:
            return "Unknown (semantics/quality/excluded or above-Nyquist samples)"
        value = lambda v: "unknown" if v is None else f"{v:.5g}"
        return f"Fc={value(m.main_crossover_hz)} Hz; worst PM={value(m.worst_phase_margin_deg)}°; worst GM={value(m.worst_gain_margin_db)} dB; {m.status}"

    def refresh(self):
        self.figure.clear()
        grid = self.figure.add_gridspec(2, 2)
        map_ax = self.figure.add_subplot(grid[:, 0])
        mag_ax = self.figure.add_subplot(grid[0, 1])
        phase_ax = self.figure.add_subplot(grid[1, 1])
        map_ax.set_xlabel("Kp"); map_ax.set_ylabel("Ti (s)")
        map_ax.set_xscale("log"); map_ax.set_yscale("log")
        map_ax.set_title("Measured circles / predicted crosses; blank = unknown")
        mag_ax.set_ylabel("Magnitude (dB)"); phase_ax.set_ylabel("Phase (deg)"); phase_ax.set_xlabel("Frequency (Hz)")
        self.predictions = []
        scan = self.session.current
        if scan:
            group = self.session.group()
            baseline, best = group[0], self.session.best()
            self.summary.setText(f"工况 {scan.operating_point.label}: Vin={scan.operating_point.vin_v:g} V, Load={scan.operating_point.load_w:g} W, {scan.operating_point.mode} | 基线={baseline.name} | 当前={scan.name} | 实测评分最佳={best.name if best else '未知'}（仅当前工况）")
            lines = [f"Current: {self.metric_text(scan)}", f"Exact PI: b={scan.controller.digital().b}; a=(1,-1); Ts={scan.controller.sample_period_s:g} s; Ki={scan.controller.ki:g}"]
            if best:
                c = best.controller
                lines.append(f"Best measured point in this group: {best.name}; Kp={c.kp:.7g}, Ti={c.ti_s:.7g} s, Ki={c.ki:.7g}. Relative target score only, not global optimum.")
            baseline_metrics = metrics(baseline, targets=self.session.targets)
            current_metrics = metrics(scan, targets=self.session.targets)
            if baseline_metrics and current_metrics and baseline is not scan:
                changes = []
                for label, key, unit in (("Fc", "main_crossover_hz", "Hz"), ("PM", "worst_phase_margin_deg", "°"), ("GM", "worst_gain_margin_db", "dB")):
                    old, new = getattr(baseline_metrics, key), getattr(current_metrics, key)
                    changes.append(f"{label}: {new-old:+.5g} {unit}" if old is not None and new is not None else f"{label}: unknown")
                lines.append("Measured change vs baseline: " + "; ".join(changes))
            blocker = scan.prediction_blocker()
            if blocker:
                lines.append(blocker)
            mismatch = self.session.mismatch()
            if mismatch and mismatch["status"] == "mismatch":
                lines.append("Prediction disagreement: recheck setup and repeat this point before another parameter change.")
                self.predictions = []
            else:
                self.predictions = suggestions(scan, self.session.targets)
            for i, s in enumerate(group):
                color = "tab:red" if s is scan else "tab:blue"
                map_ax.scatter(s.controller.kp, s.controller.ti_s, marker="o", c=color, s=70, zorder=3)
                map_ax.annotate(f"M{i+1}: {s.name}\n{self.metric_text(s).split(';')[0]}", (s.controller.kp, s.controller.ti_s), xytext=(4, 4), textcoords="offset points", fontsize=8)
                f, h = s.measurement.frequency_hz, s.response()
                mag_ax.semilogx(f, 20 * np.log10(np.abs(h)), label=s.name, linewidth=2 if s is scan else 1)
                phase_ax.semilogx(f, np.unwrap(np.angle(h)) * 180 / np.pi)
            for p in self.predictions:
                map_ax.scatter(p.controller.kp, p.controller.ti_s, marker="x", c="gray", s=50)
                map_ax.annotate(f"P Fc={p.result.main_crossover_hz:.0f}\nPM={p.result.worst_phase_margin_deg:.1f}°", (p.controller.kp, p.controller.ti_s), xytext=(5, -22), textcoords="offset points", fontsize=7, color="gray")
            if self.predictions:
                p = self.predictions[0]
                map_ax.scatter(p.controller.kp, p.controller.ti_s, marker="*", facecolors="none", edgecolors="darkorange", s=250)
                mask = scan.usable_mask()
                mag_ax.semilogx(scan.measurement.frequency_hz[mask], 20 * np.log10(np.abs(p.response[mask])), "--", color="darkorange", label="Next hypothesis")
                phase_ax.semilogx(scan.measurement.frequency_hz[mask], np.unwrap(np.angle(p.response[mask])) * 180 / np.pi, "--", color="darkorange")
                lines += [f"下一复测候选（非全局最优）: Kp={p.controller.kp:.7g}, Ti={p.controller.ti_s:.7g} s, Ki={p.controller.ki:.7g}", p.reason,
                          f"Predicted Fc={p.result.main_crossover_hz:.5g} Hz; PM={p.result.worst_phase_margin_deg:.5g}°; GM={p.result.worst_gain_margin_db if p.result.worst_gain_margin_db is not None else 'unknown'} dB"]
                if p.score >= score(metrics(scan), self.session.targets):
                    lines.append("No ranked improvement in this local grid; next point is exploratory, not an optimized setting.")
            else:
                lines.append("No valid candidate: verify measurement meaning, quality and crossover frequency coverage.")
            if mismatch:
                lines.append(f"复测与上一轮预测核验: {mismatch}")
            lines.append("Unknown GM is not a pass. Finite-band FRD does not establish global stability, time-domain limits or hardware safety. Confirm parameter bounds, protections and measurement quality before applying manually.")
            self.details.setPlainText("\n".join(lines))
            mag_ax.legend(fontsize=8)
        else:
            self.summary.setText("尚未导入实测点")
            self.details.setPlainText("Start with a CSV and bind the exact PI / operating point. Sessions retain raw units and mapped rows. No filled/interpolated performance surface is inferred from sparse scans.")
        map_ax.margins(x=.22, y=.18)
        mag_ax.axhline(0, color="gray", linewidth=.7); phase_ax.axhline(-180, color="gray", linewidth=.7)
        for ax in (map_ax, mag_ax, phase_ax):
            ax.grid(True, alpha=.25)
        self.canvas.draw_idle()

    def save_session(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save FRA session", "fra-session.json", "JSON (*.json)")
        if path:
            try:
                Path(path).write_text(self.session.to_json(), encoding="utf-8")
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "Save failed", str(exc))

    def open_session(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open FRA session", "", "JSON (*.json)")
        if not path:
            return
        try:
            loaded = Session.from_json(Path(path).read_text(encoding="utf-8"))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, "Open failed", str(exc)); return
        self.session = loaded
        for w, value in ((self.fc, loaded.targets.crossover_hz), (self.pm, loaded.targets.phase_margin_deg), (self.gm, loaded.targets.gain_margin_db)):
            w.blockSignals(True); w.setValue(value); w.blockSignals(False)
        self.sync_selector(); self.refresh()

    def export_current(self):
        if not self.session.current:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export measured scan", "fra-measured.csv", "CSV (*.csv)")
        if path:
            try:
                Path(path).write_text(export_scan_csv(self.session.current), encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "Export failed", str(exc))
