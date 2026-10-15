"""Shared topology-specific three-phase stress/loss view."""

import json
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from llc_design.magnetics.material import MaterialDatabase
from llc_design.magnetics.transformer_designer import load_transformer_core_presets
from pfc_design.three_phase.performance import (
    LossParameters,
    evaluate_two_level,
    evaluate_vienna,
)


class ThreePhasePerformanceView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = None
        self.report = None
        self._result_dirty = True
        root = QVBoxLayout(self)
        self.note = QLabel(
            "每相电感、每个功率器件、电容 RMS/PK 与损耗。0 表示损耗参数未提供；总损耗和效率只包含已配置项。"
        )
        self.note.setWordWrap(True)
        root.addWidget(self.note)
        splitter = QSplitter()
        root.addWidget(splitter, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        form = QFormLayout(panel)
        self.form = form
        self.vienna_kind = QComboBox()
        self.vienna_kind.addItem("反串双 MOSFET / 每相 2 管", "back_to_back")
        self.vienna_kind.addItem("二极管桥 + 单 MOSFET / 每相 1 管", "diode_bridge")
        form.addRow("Vienna 中点开关结构", self.vienna_kind)
        self.inputs = {}
        for key, label, value, scale, lo, hi in (
            ("rds_on_ohm", "Rds(Tj) / mΩ", 0, 1000, 0, 5000),
            ("switch_energy_j", "每管 Eon+Eoff / µJ", 0, 1e6, 0, 1e6),
            ("energy_reference_v", "能量参考电压 / V", 400, 1, 1, 10000),
            ("energy_reference_a", "能量参考电流 / A", 20, 1, 0.1, 10000),
            ("bridge_diode_vf_v", "中点桥二极管 Vf / V", 0, 1, 0, 10),
            ("bridge_diode_resistance_ohm", "中点桥二极管 Rd / mΩ", 0, 1000, 0, 1000),
            ("diode_vf_v", "Vienna 外二极管 Vf / V", 0, 1, 0, 10),
            ("diode_resistance_ohm", "外二极管 Rd / mΩ", 0, 1000, 0, 1000),
            ("capacitor_esr_ohm", "每个电容组 ESR / mΩ", 0, 1000, 0, 1000),
            ("hf_ac_resistance_ratio", "电感 HF Rac/Rdc", 1, 1, 1, 1000),
            ("turns", "每相电感匝数 N", 0, 1, 0, 10000),
            ("ae_mm2", "Ae / mm²", 0, 1, 0, 100000),
            ("ve_mm3", "Ve / mm³", 0, 1, 0, 1e7),
            ("core_temperature_c", "磁芯温度 / °C", 100, 1, -50, 200),
        ):
            w = QDoubleSpinBox()
            w.setDecimals(5)
            w.setRange(lo, hi)
            w.setValue(value * scale)
            w.setKeyboardTracking(False)
            self.inputs[key] = (w, scale)
            form.addRow(label, w)
        self.material = QComboBox()
        for m in MaterialDatabase().materials:
            self.material.addItem(m.grade, m.key)
        self.material.setCurrentIndex(self.material.findData("TDK_PC40_2002"))
        form.addRow("材料模型", self.material)
        self.preset = QComboBox()
        self.preset.addItem("选择磁芯自动填 Ae/Ve；匝数需按实际电感设计", None)
        for key, p in load_transformer_core_presets().items():
            self.preset.addItem(p.part_number + " · " + p.material_grade, key)
        self.preset.currentIndexChanged.connect(self._apply_core)
        form.addRow("磁芯参数", self.preset)
        run = QPushButton("按当前损耗参数重新计算")
        run.clicked.connect(self.calculate)
        form.addRow(run)
        self.export_button = QPushButton("导出完整性能快照 JSON")
        self.export_button.clicked.connect(self.export)
        self.export_button.setEnabled(False)
        form.addRow(self.export_button)
        scroll.setWidget(panel)
        splitter.addWidget(scroll)
        self.tabs = QTabWidget()
        splitter.addWidget(self.tabs)
        splitter.setSizes([310, 900])
        self.figure = Figure(figsize=(10, 8), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.tabs.addTab(self.canvas, "开关周期 / 器件应力 / 损耗")
        wave_page = QWidget()
        wave_layout = QVBoxLayout(wave_page)
        self.device_choice = QComboBox()
        self.device_choice.currentTextChanged.connect(self._plot_device)
        wave_layout.addWidget(self.device_choice)
        self.device_figure = Figure(figsize=(10, 7), layout="constrained")
        self.device_canvas = FigureCanvasQTAgg(self.device_figure)
        wave_layout.addWidget(self.device_canvas)
        self.tabs.addTab(wave_page, "开关电流 / 电容电流波形")
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.tabs.addTab(self.summary, "PF / ITHD / 明细 / 边界")
        self.status = QLabel("先运行主电路分析。")
        root.addWidget(self.status)
        for widget, _ in self.inputs.values():
            widget.valueChanged.connect(self._mark_dirty)
        self.material.currentIndexChanged.connect(self._mark_dirty)
        self.vienna_kind.currentIndexChanged.connect(self._mark_dirty)
        self.vienna_kind.currentIndexChanged.connect(self._update_topology_fields)
        self._update_topology_fields()

    def _apply_core(self):
        key = self.preset.currentData()
        if not key:
            return
        p = load_transformer_core_presets()[key]
        for k in ("ae_mm2", "ve_mm3"):
            self.inputs[k][0].setValue(getattr(p, k))
        self.material.setCurrentIndex(self.material.findData(p.material_key))
        self.status.setText(
            "已加载几何与材料，请重新计算；匝数和气隙需按实际电感设计。"
        )

    def _mark_dirty(self, *_):
        self._result_dirty = True
        self.export_button.setEnabled(False)
        if self.report is not None:
            self.status.setText(
                "Loss inputs changed. Recalculate before exporting; plots show the previous snapshot."
            )

    def _update_topology_fields(self, *_):
        vienna = self.source is not None and self.source[0] == "vienna"
        bridge = vienna and self.vienna_kind.currentData() == "diode_bridge"
        self.form.setRowVisible(self.vienna_kind, vienna)
        for key in ("diode_vf_v", "diode_resistance_ohm"):
            self.form.setRowVisible(self.inputs[key][0], vienna)
        for key in ("bridge_diode_vf_v", "bridge_diode_resistance_ohm"):
            self.form.setRowVisible(self.inputs[key][0], bridge)

    def set_two_level(self, result):
        self.source = ("two_level", result)
        self._update_topology_fields()
        self.calculate()

    def set_vienna(self, config, line):
        self.source = ("vienna", config, line)
        self._update_topology_fields()
        self.calculate()

    def calculate(self):
        if self.source is None:
            return
        self._result_dirty = True
        self.export_button.setEnabled(False)
        try:
            loss = LossParameters(
                **{k: w.value() / scale for k, (w, scale) in self.inputs.items()},
                material_key=self.material.currentData(),
                vienna_switch_kind=self.vienna_kind.currentData(),
            )
            self.report = (
                evaluate_two_level(self.source[1], loss)
                if self.source[0] == "two_level"
                else evaluate_vienna(self.source[1], self.source[2], loss)
            )
            r = self.report
            self.summary.setPlainText(
                json.dumps(
                    {k: v for k, v in r.items() if k != "replay"},
                    indent=2,
                    ensure_ascii=False,
                )
            )
            self.figure.clear()
            ax = self.figure.subplots(2, 2)
            replay = r["replay"]
            for j, ph in enumerate("abc"):
                ax[0, 0].plot(
                    np.array(replay["local_time_s"]) * 1e6,
                    np.array(replay["local_current_abc"])[:, j],
                    label=ph,
                )
                ax[0, 1].step(
                    np.array(replay["local_time_s"]) * 1e6,
                    np.array(replay["local_gates_abc"])[:, j] + 2 * j,
                    where="mid",
                    label=ph,
                )
            ax[0, 0].set_ylabel("Inductor current / A")
            ax[0, 1].set_ylabel("Switch states (offset)")
            for axis in ax[0]:
                axis.set_xlabel("Local switching period / us")
                axis.legend()
            keys = list(r["device_stress"])
            x = np.arange(len(keys))
            ax[1, 0].bar(
                x - 0.18,
                [r["device_stress"][k]["rms_a"] for k in keys],
                0.36,
                label="RMS",
            )
            ax[1, 0].bar(
                x + 0.18,
                [r["device_stress"][k]["peak_a"] for k in keys],
                0.36,
                label="PK",
            )
            ax[1, 0].set_xticks(x, keys, rotation=65, fontsize=6)
            ax[1, 0].set_ylabel("Device current / A")
            ax[1, 0].legend()
            losses = {k: v for k, v in r["loss_breakdown_w"].items() if v is not None}
            ax[1, 1].bar(list(losses), list(losses.values()))
            ax[1, 1].tick_params(axis="x", labelrotation=65, labelsize=6)
            ax[1, 1].set_ylabel("Included loss / W")
            for axis in ax.ravel():
                axis.grid(True, alpha=0.25)
            self._result_dirty = False
            self.export_button.setEnabled(True)
            self.canvas.draw_idle()
            self.device_choice.blockSignals(True)
            self.device_choice.clear()
            self.device_choice.addItems(list(r["device_stress"]))
            self.device_choice.blockSignals(False)
            self._plot_device()
            self.status.setText(
                "已按主分析快照计算；缺少 " + ", ".join(r["missing_loss_inputs"])
                if r["missing_loss_inputs"]
                else "损耗参数已配置；仍需实测校核。"
            )
        except (ValueError, KeyError) as exc:
            self.report = None
            self.summary.clear()
            self.figure.clear()
            self.device_figure.clear()
            self.canvas.draw_idle()
            self.device_canvas.draw_idle()
            self.status.setText("性能计算失败：" + str(exc))

    def _plot_device(self, *_):
        if self.report is None:
            return
        replay = self.report["replay"]
        key = self.device_choice.currentText()
        if key not in replay["local_device_current_a"]:
            return
        self.device_figure.clear()
        axes = self.device_figure.subplots(3, 1)
        time = np.array(replay["local_time_s"]) * 1e6
        axes[0].plot(time, replay["local_device_current_a"][key], label=key)
        axes[0].set_ylabel("Device current / A")
        phase = "abc".index(key[0])
        axes[1].plot(
            time,
            np.array(replay["local_current_abc"])[:, phase],
            label="Inductor phase " + key[0],
        )
        axes[1].set_ylabel("Inductor / A")
        for name, value in replay["local_capacitor_current_a"].items():
            axes[2].plot(time, value, label="Cap " + name)
        axes[2].set_ylabel("Capacitor current / A")
        axes[2].set_xlabel("Local switching period / us (line cycle at 90 deg)")
        for ax in axes:
            ax.grid(True, alpha=0.25)
            ax.legend()
        self.device_canvas.draw_idle()

    def export(self):
        if self.report is None or self._result_dirty:
            return
        name, _ = QFileDialog.getSaveFileName(
            self, "导出性能", "pfc_performance.json", "JSON (*.json)"
        )
        if name:
            try:
                Path(name).write_text(
                    json.dumps(self.report, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError as exc:
                self.status.setText(str(exc))
