"""DAB forward TPS/SPS voltage-control workspace with explicit source ownership."""

import json
import math
from dataclasses import asdict
from pathlib import Path
from threading import Event

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from dab_design.control import DABVoltageControlConfig, simulate_dab_voltage_loop
from dab_design.optimization import TPSConstraints, optimize_tps
from dab_design.sensing import DABSenseConfig
from dab_design.workspace import (
    SESSION_SCHEMA,
    controller_from_payload,
    controller_payload,
    research_bundle,
    scan_workpoints,
    source_identity,
    synthesize_candidate,
    validate_session,
    write_report,
)
from llc_design.control.phase_budget import phase_budget
from llc_design.gui.widgets.bode_cursor import BodeCursor, BodeCursorTrace
from llc_design.gui.widgets.control_block_diagram import (
    BlockSpec,
    ConnectionSpec,
    ControlBlockDiagram,
)
from llc_design.gui.workers import FunctionWorker
from power_control_tools.codegen import render_c99_single_file
from power_control_tools.models import DigitalTransferFunction
from power_control_tools.research_export import export_research_script


class DABControlView(QWidget):
    def __init__(self, stage_provider, parent=None):
        super().__init__(parent)
        self.stage_provider = stage_provider
        self._worker = None
        self._cancel = Event()
        self.snapshot = None
        self.custom_controller = None
        self._revision = 0
        self._request_revision = 0
        self._request_context = {}
        self._result = None
        self._cursor = None
        self.candidates = []
        self.workpoints = []
        self.scan_results = []
        self.applied_candidate = None
        root = QVBoxLayout(self)
        # Keep the native FigureCanvas out of a scroll viewport. With Qt 6.11's
        # offscreen backend, selecting a clipped canvas tab under QScrollArea
        # reproducibly crashes during a later paint event, even for a blank
        # canvas and fully synchronous results. Only controls scroll here.
        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setMinimumHeight(120)
        self.page_scroll.setMaximumHeight(260)
        header = QWidget()
        header_layout = QVBoxLayout(header)
        self.page_scroll.setWidget(header)
        self.parameters_toggle = QCheckBox("Show model / sensing / timing parameters")
        self.parameters_toggle.setChecked(True)
        self.parameters_toggle.toggled.connect(self.page_scroll.setVisible)
        root.addWidget(self.parameters_toggle)
        root.addWidget(self.page_scroll)
        self.diagram = ControlBlockDiagram()
        self.diagram.set_diagram(
            [
                BlockSpec("sum", "Vref - Vmeas", "Incremental error [V]", 0, 0),
                BlockSpec("pi", "Exact digital C(z)", "Pcmd [W]", 180, 0),
                BlockSpec("tps", "TPS / SPS + limits", "Forward power mapping", 360, 0),
                BlockSpec("plant", "DAB + Co + load", "Vout [V]", 540, 0),
                BlockSpec(
                    "sense", "Sensing H(z)", "Digital LPF + sample delay", 360, 100
                ),
            ],
            [
                ConnectionSpec("sum", "pi"),
                ConnectionSpec("pi", "tps"),
                ConnectionSpec("tps", "plant"),
                ConnectionSpec("plant", "sense"),
                ConnectionSpec("sense", "sum", "feedback", True),
            ],
        )
        self.diagram.setMinimumHeight(150)
        self.diagram.setMaximumHeight(180)
        header_layout.addWidget(self.diagram)
        panel = QWidget()
        parameters = QHBoxLayout(panel)
        forms = []
        for title in (
            "Operating point / controller",
            "Sensing / timing",
            "Simulation / constraints",
        ):
            group = QGroupBox(title)
            forms.append(QFormLayout(group))
            parameters.addWidget(group)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumHeight(170)
        scroll.setMaximumHeight(250)
        header_layout.addWidget(scroll)
        self.parameter_scroll = scroll
        self.inputs = {}
        fields = [
            (0, "vb", "Vout / V", 400, 10, 5000),
            (0, "power", "Pcmd / W", 3500, 1, 1e6),
            (0, "sample_hz", "Control sample / Hz", 5000, 100, 1e5),
            (0, "kp_w_per_v", "PI Kp / W/V", 100, 0.00000001, 1e6),
            (0, "ti_s", "PI Ti / s", 0.02, 0.00000001, 100),
            (0, "frequency_min_hz", "Bode minimum / Hz", 0.05, 0.000001, 1e5),
            (0, "frequency_max_hz", "Bode maximum requested / Hz", 20000, 0.00001, 1e6),
            (1, "calibration_gain", "Sense calibration / V/V", 1, 0.00001, 100),
            (1, "lowpass_hz", "Digital LPF / Hz (0=off)", 0, 0, 1e5),
            (1, "divider_gain", "ADC divider / V/V", 0.006, 0.00000001, 1),
            (1, "adc_reference_v", "ADC reference / V", 3.3, 0.1, 20),
            (2, "duration_s", "Duration / s", 0.4, 0.001, 100),
            (2, "disturbance_time_s", "Step time / s", 0.15, 0.0001, 100),
            (2, "load_step_ratio", "Load step factor", 1.2, 0.001, 100),
            (2, "reference_step_v", "Reference step / V", 5, -1000, 1000),
            (2, "peak", "TPS optimization peak / A", 100, 1, 1e4),
            (2, "tolerance", "TPS power tolerance / %", 0.5, 0.01, 10),
        ]
        for group, key, label, value, lo, hi in fields:
            w = QDoubleSpinBox()
            w.setDecimals(12)
            w.setRange(lo, hi)
            w.setValue(value)
            w.setKeyboardTracking(False)
            self.inputs[key] = w
            forms[group].addRow(label, w)
        for key, label, value, low, high in (
            ("delay_ticks", "Command delay / ticks", 1, 0, 10),
            ("sense_delay_ticks", "Measurement delay / ticks", 0, 0, 10),
            ("adc_bits", "ADC bits (linear model)", 12, 1, 32),
        ):
            w = QSpinBox()
            w.setRange(low, high)
            w.setValue(value)
            self.inputs[key] = w
            forms[1].addRow(label, w)
        self.load = QComboBox()
        self.load.addItem("Resistance", "resistance")
        self.load.addItem("Constant power", "constant_power")
        forms[2].addRow("Load model", self.load)
        self.actuator = QComboBox()
        self.actuator.addItem("TPS: Pcmd [W] → D1/D2/D3", "tps_power")
        self.actuator.addItem("SPS: Pcmd [W] → D (0<D<0.5)", "sps_power")
        forms[0].addRow("Forward actuator", self.actuator)
        self.cp = QCheckBox("Periodic RLC power (Cp); no Cp transient states")
        forms[2].addRow(self.cp)
        self.proxy = QCheckBox("Commutation-polarity proxy (not ZVS proof)")
        forms[2].addRow(self.proxy)
        note = QLabel(
            "ADC divider/bits check nominal headroom and LSB. Aperture, quantization, ripple aliasing and fractional timing are excluded. LPF is digital; command is held for each energy-update interval."
        )
        note.setWordWrap(True)
        forms[1].addRow(note)
        self.action_buttons = []
        rows = [
            [
                ("Run voltage loop", self.run_control),
                ("TPS optimization", self.run_optimization),
                ("Digital controller designer", self.open_controller),
                ("Cancel", self.cancel),
            ],
            [
                ("Export snapshot JSON", self.export_snapshot),
                ("Export report HTML", self.export_report),
                ("Export research (.py / .m)", self.export_research),
                ("Export controller C99", self.export_c99),
            ],
        ]
        for row in rows:
            buttons = QHBoxLayout()
            root.addLayout(buttons)
            for text, callback in row:
                b = QPushButton(text)
                b.clicked.connect(callback)
                buttons.addWidget(b)
                self.action_buttons.append(b)
        self.export_button = self.action_buttons[4]
        self.export_buttons = self.action_buttons[4:]
        for b in self.export_buttons:
            b.setEnabled(False)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.tabs.addTab(self.summary, "Model / signed margins / poles")
        self.optimization_table = QTableWidget(0, 7)
        self.optimization_table.setHorizontalHeaderLabels(
            [
                "Method",
                "Pout / W",
                "Power error / %",
                "Irms / A",
                "Ipeak / A",
                "Feasible",
                "Violations",
            ]
        )
        self.optimization_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tabs.addTab(self.optimization_table, "TPS optimization comparison")
        self.fig = Figure(figsize=(10, 6), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.tabs.addTab(self.canvas, "Selectable Bode / time response")
        self.code = QPlainTextEdit()
        self.code.setReadOnly(True)
        self.tabs.addTab(self.code, "Exact coefficients / C99")
        self.phase_view = QPlainTextEdit()
        self.phase_view.setReadOnly(True)
        self.tabs.addTab(self.phase_view, "Cursor phase budget")
        self._build_candidates()
        traces = QHBoxLayout()
        root.addLayout(traces)
        self.trace_checks = {}
        for key in ("G", "H", "C", "L", "T", "R"):
            box = QCheckBox(key)
            box.setChecked(key in ("G", "C", "L", "T"))
            box.toggled.connect(self._plot_loop)
            self.trace_checks[key] = box
            traces.addWidget(box)
        self.cursor_label = QLabel("Bode cursor: click/drag either gain or phase plot")
        traces.addWidget(self.cursor_label, 1)
        self.status = QLabel(
            "Define a forward TPS/SPS point and run the single-rate loop model."
        )
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.inputs["kp_w_per_v"].valueChanged.connect(self._use_pi)
        self.inputs["ti_s"].valueChanged.connect(self._use_pi)
        for w in self.inputs.values():
            w.valueChanged.connect(self.mark_stale)
        for w in (self.load, self.actuator):
            w.currentIndexChanged.connect(self.mark_stale)
        self.cp.toggled.connect(self.mark_stale)
        self.proxy.toggled.connect(self.mark_stale)

    def _build_candidates(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QHBoxLayout()
        layout.addLayout(controls)
        self.target_fc = QDoubleSpinBox()
        self.target_fc.setRange(0.1, 40000)
        self.target_fc.setValue(100)
        self.target_pm = QDoubleSpinBox()
        self.target_pm.setRange(1, 179)
        self.target_pm.setValue(60)
        for label, w in (("Fc / Hz", self.target_fc), ("PM / deg", self.target_pm)):
            controls.addWidget(QLabel(label))
            controls.addWidget(w)
            w.valueChanged.connect(self.mark_stale)
        self.synthesize_button = QPushButton("Generate 3×3 PI candidates")
        self.synthesize_button.clicked.connect(self.generate_candidates)
        controls.addWidget(self.synthesize_button)
        self.apply_button = QPushButton("Apply selected exact C(z)")
        self.apply_button.clicked.connect(self.apply_candidate)
        self.apply_button.setEnabled(False)
        layout.addWidget(self.apply_button)
        self.candidate_table = QTableWidget(0, 8)
        self.candidate_table.setHorizontalHeaderLabels(
            [
                "Fc target",
                "PM target",
                "Kp",
                "Ti / s",
                "Actual Fc",
                "Actual PM",
                "Worst signed GM",
                "Model screen",
            ]
        )
        self.candidate_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.candidate_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.candidate_table.itemSelectionChanged.connect(self._candidate_selected)
        layout.addWidget(self.candidate_table)
        row = QHBoxLayout()
        layout.addLayout(row)
        for text, callback in (
            ("Retain current workpoint", self.retain_workpoint),
            ("Scan retained points with exact current C(z)", self.run_scan),
            ("Clear retained points", self.clear_workpoints),
        ):
            b = QPushButton(text)
            b.clicked.connect(callback)
            row.addWidget(b)
        self.scan_view = QPlainTextEdit()
        self.scan_view.setReadOnly(True)
        layout.addWidget(self.scan_view)
        candidate_scroll = QScrollArea()
        candidate_scroll.setWidgetResizable(True)
        candidate_scroll.setWidget(page)
        self.tabs.addTab(candidate_scroll, "PI candidates / retained workpoints")

    def control_config(self):
        sense = DABSenseConfig(
            **{
                key: self.inputs[key].value()
                for key in (
                    "calibration_gain",
                    "lowpass_hz",
                    "divider_gain",
                    "adc_reference_v",
                    "adc_bits",
                )
            },
            delay_ticks=self.inputs["sense_delay_ticks"].value(),
        )
        keys = (
            "sample_hz",
            "kp_w_per_v",
            "ti_s",
            "delay_ticks",
            "duration_s",
            "disturbance_time_s",
            "load_step_ratio",
            "reference_step_v",
            "frequency_min_hz",
            "frequency_max_hz",
        )
        control = DABVoltageControlConfig(
            **{key: self.inputs[key].value() for key in keys},
            sensing=sense,
            include_cp=self.cp.isChecked(),
            load_model=self.load.currentData(),
            actuator=self.actuator.currentData(),
        )
        control.validate()
        return control

    def _source(self):
        return source_identity(
            self.stage_provider(),
            self.control_config(),
            self.inputs["vb"].value(),
            self.inputs["power"].value(),
            self.custom_controller,
        )

    def mark_stale(self, *_):
        self._revision += 1
        if self.applied_candidate is not None:
            self.applied_candidate = {**self.applied_candidate, "historical": True}
        self.apply_button.setEnabled(False)
        for b in self.export_buttons:
            b.setEnabled(False)
        if self.snapshot is not None or self._worker is not None or self.candidates:
            self.status.setText(
                "Inputs/controller changed. Displayed results are historical; recompute before current export or Apply."
            )

    def _use_pi(self):
        self.custom_controller = None

    def set_operating_point(self, vb, power):
        self.inputs["vb"].setValue(vb)
        self.inputs["power"].setValue(max(1, power))

    def _start(self, func, *args, context=None, cancellable=False):
        if self._worker is not None:
            return
        self._request_context = context or {}
        self._request_revision = self._revision
        self._cancel = Event()
        self.snapshot = None
        self._result = None
        self.summary.clear()
        self.code.clear()
        self.phase_view.clear()
        if self._cursor:
            self._cursor.disconnect()
            self._cursor = None
        self.fig.clear()
        self.canvas.draw_idle()
        self.optimization_table.setRowCount(0)
        for b in (
            self.action_buttons[:2]
            + [self.synthesize_button, self.apply_button]
            + self.export_buttons
        ):
            b.setEnabled(False)
        worker = FunctionWorker(
            func, *args, **({"cancelled": self._cancel.is_set} if cancellable else {})
        )
        self._worker = worker

        def current():
            return (
                isValid(self) and self._worker is worker and not self._cancel.is_set()
            )

        worker.signals.result.connect(
            lambda result: self._display(result) if current() else None
        )
        worker.signals.error.connect(
            lambda error: (
                self.status.setText("Analysis failed: " + error) if current() else None
            )
        )
        worker.signals.finished.connect(lambda: self._finished(worker))
        self.status.setText("Computing…")
        worker.start(QThreadPool.globalInstance())

    def cancel(self):
        if self._worker is not None:
            self._cancel.set()
            self.mark_stale()
            self.status.setText("Cancel requested; late results will be discarded.")

    def _finished(self, worker):
        if self._worker is not worker:
            return
        self._worker = None
        worker.signals.deleteLater()
        if not isValid(self):
            return
        for b in self.action_buttons[:2] + [self.synthesize_button]:
            b.setEnabled(True)
        if self._cancel.is_set():
            self.status.setText("Cancelled; no result accepted.")

    def run_control(self):
        try:
            stage = self.stage_provider()
            control = self.control_config()
            context = {
                "analysis_kind": "voltage_loop",
                "stage": asdict(stage),
                "control": asdict(control),
                "controller": controller_payload(
                    self.custom_controller or control.controller()
                ),
                "source": self._source(),
                "candidate_evidence": self.applied_candidate
                if self.applied_candidate is not None
                and not self.applied_candidate.get("historical", False)
                and self.applied_candidate.get("applied_source") == self._source()
                else None,
                "operating_point": [
                    self.inputs["vb"].value(),
                    self.inputs["power"].value(),
                ],
            }
            self._start(
                simulate_dab_voltage_loop,
                stage,
                *context["operating_point"],
                control,
                self.custom_controller,
                context=context,
                cancellable=True,
            )
        except (ValueError, TypeError) as exc:
            self.status.setText(str(exc))

    def run_optimization(self):
        try:
            limits = TPSConstraints(
                self.inputs["tolerance"].value() / 100,
                self.inputs["peak"].value(),
                0.02,
                0 if self.proxy.isChecked() else None,
            )
            stage = self.stage_provider()
            self._start(
                optimize_tps,
                stage,
                self.inputs["vb"].value(),
                self.inputs["power"].value(),
                limits,
                cancellable=True,
                context={
                    "analysis_kind": "tps_optimization",
                    "stage": asdict(stage),
                    "source": self._source(),
                },
            )
        except (ValueError, TypeError) as exc:
            self.status.setText(str(exc))

    def open_controller(self):
        from power_control_tools.gui.main_window import ControlToolsMainWindow

        self.designer = ControlToolsMainWindow()
        self.designer.digital_design_updated.connect(self._import_controller)
        self.designer.fs.setValue(self.inputs["sample_hz"].value())
        self.designer.show()

    def _import_controller(self, digital, label):
        from llc_design.control.digital_loop import (
            DigitalTransferFunction as LLCTransfer,
        )

        try:
            fs = digital.sample_rate_hz
            w = self.inputs["sample_hz"]
            if not w.minimum() <= fs <= w.maximum():
                raise ValueError("Imported sample rate outside workspace input range")
            controller = LLCTransfer(
                np.asarray(digital.b), np.asarray(digital.a), 1 / fs, label
            )
            self.inputs["sample_hz"].setValue(fs)
            self.custom_controller = controller
            self.mark_stale()  # Mandatory even if sample rate did not change.
            self.status.setText(
                "Imported exact "
                + label
                + "; previous results/candidates invalidated. Run to analyze original coefficients."
            )
        except (ValueError, TypeError) as exc:
            self.status.setText(str(exc))

    def generate_candidates(self):
        if self._worker is not None:
            return
        try:
            stage = self.stage_provider()
            control = self.control_config()
            source = self._source()
            fc = self.target_fc.value()
            pm = self.target_pm.value()
            candidates = []
            for margin in sorted({max(1, min(179, x)) for x in (pm - 10, pm, pm + 10)}):
                for crossover in (fc / 1.5, fc, fc * 1.5):
                    candidates.append(
                        synthesize_candidate(
                            stage,
                            control,
                            self.inputs["vb"].value(),
                            self.inputs["power"].value(),
                            crossover,
                            margin,
                            source=source,
                        )
                    )
            self.candidates = candidates
            self._candidate_revision = self._revision
            self.candidate_table.setRowCount(len(candidates))
            for row, candidate in enumerate(candidates):
                summary = candidate.get("summary", {})
                values = [
                    candidate["fc"],
                    candidate["pm"],
                    candidate.get("kp"),
                    candidate.get("ti_s"),
                    summary.get("crossover_hz"),
                    summary.get("phase_margin_deg"),
                    min(summary.get("gain_margins_db", []), default=None),
                    candidate["status"],
                ]
                for col, value in enumerate(values):
                    self.candidate_table.setItem(
                        row,
                        col,
                        QTableWidgetItem("unknown" if value is None else str(value)),
                    )
            self.candidate_table.resizeColumnsToContents()
            self.status.setText(
                "Candidates are previews. Select a passing row and explicitly Apply; missing margins never pass."
            )
        except (ValueError, TypeError) as exc:
            self.candidates = []
            self.candidate_table.setRowCount(0)
            self.apply_button.setEnabled(False)
            self.status.setText(str(exc))

    def _candidate_selected(self):
        row = self.candidate_table.currentRow()
        current = getattr(self, "_candidate_revision", -1) == self._revision
        self.apply_button.setEnabled(
            current
            and 0 <= row < len(self.candidates)
            and self.candidates[row]["can_apply"]
        )

    def apply_candidate(self):
        row = self.candidate_table.currentRow()
        if not 0 <= row < len(self.candidates):
            return
        candidate = self.candidates[row]
        try:
            if (
                not candidate["can_apply"]
                or self._candidate_revision != self._revision
                or candidate["source"] != self._source()
            ):
                raise ValueError(
                    "Candidate is rejected or stale; regenerate from current inputs"
                )
            for key, value in (
                ("kp_w_per_v", candidate["kp"]),
                ("ti_s", candidate["ti_s"]),
            ):
                if (
                    not self.inputs[key].minimum()
                    <= value
                    <= self.inputs[key].maximum()
                ):
                    raise ValueError(f"Candidate {key} exceeds workspace range")
            self.custom_controller = controller_from_payload(candidate["controller"])
            for key, value in (
                ("kp_w_per_v", candidate["kp"]),
                ("ti_s", candidate["ti_s"]),
            ):
                w = self.inputs[key]
                w.blockSignals(True)
                w.setValue(value)
                w.blockSignals(False)
            self.mark_stale()
            self.applied_candidate = {
                **candidate,
                "applied_source": self._source(),
                "historical": False,
            }
            self.status.setText(
                "Applied the candidate's exact C(z). Run to generate current analysis/export evidence."
            )
        except (ValueError, TypeError) as exc:
            self.status.setText(str(exc))

    def retain_workpoint(self):
        if len(self.workpoints) >= 200:
            self.status.setText("At most 200 retained workpoints")
            return
        point = {
            "name": f"Point {len(self.workpoints) + 1}",
            "voltage_v": self.inputs["vb"].value(),
            "power_w": self.inputs["power"].value(),
        }
        self.workpoints.append(point)
        if self.snapshot is not None and "workpoint_scan" in self.snapshot:
            self.mark_stale()
        self.scan_view.setPlainText(json.dumps(self.workpoints, indent=2))

    def clear_workpoints(self):
        if self.snapshot is not None and "workpoint_scan" in self.snapshot:
            self.mark_stale()
        self.workpoints = []
        self.scan_results = []
        self.scan_view.clear()

    def run_scan(self):
        if not self.workpoints:
            self.status.setText("Retain operating points before scanning.")
            return
        try:
            stage = self.stage_provider()
            control = self.control_config()
            controller = self.custom_controller or control.controller()
            self._start(
                scan_workpoints,
                stage,
                control,
                controller,
                list(self.workpoints),
                cancellable=True,
                context={
                    "analysis_kind": "workpoint_scan",
                    "workpoints": list(self.workpoints),
                    "source": self._source(),
                    "controller": controller_payload(controller),
                    "stage": asdict(stage),
                    "control": asdict(control),
                },
            )
        except (ValueError, TypeError) as exc:
            self.status.setText(str(exc))

    def _display(self, result):
        if isinstance(result, list):
            self.scan_results = result
            self.snapshot = {
                "run_context": self._request_context,
                "workpoint_scan": result,
            }
            self.scan_view.setPlainText(
                json.dumps(self.snapshot, indent=2, ensure_ascii=False)
            )
            self._completed(
                "Workpoint scan complete; each point retains its validity/rejection reason.",
                loop=False,
            )
            return
        if hasattr(result, "to_dict"):
            self._result = None
            self.code.clear()
            self.snapshot = {**result.to_dict(), "run_context": self._request_context}
            self.summary.setPlainText(
                json.dumps(self.snapshot, indent=2, ensure_ascii=False)
            )
            self.optimization_table.setRowCount(3)
            for row, (name, candidate) in enumerate(
                (
                    ("Reference formula", result.baseline),
                    ("Coarse grid", result.grid),
                    ("Constrained search", result.optimized),
                )
            ):
                values = [name, "—", "—", "—", "—", "No", "No feasible grid candidate"]
                if candidate is not None:
                    m = candidate.metrics
                    values = [
                        name + ": " + candidate.method,
                        f"{m.power_w:.6g}",
                        f"{100 * (m.power_w / result.target_w - 1):.4g}",
                        f"{m.irms_a:.6g}",
                        f"{m.ipeak_a:.6g}",
                        "Yes" if candidate.feasible else "No",
                        "; ".join(candidate.violations),
                    ]
                for col, value in enumerate(values):
                    self.optimization_table.setItem(row, col, QTableWidgetItem(value))
            self.optimization_table.resizeColumnsToContents()
            self.tabs.setTabEnabled(self.tabs.indexOf(self.optimization_table), True)
            for w in (self.canvas, self.code):
                self.tabs.setTabEnabled(self.tabs.indexOf(w), False)
            self.tabs.setCurrentWidget(self.optimization_table)
            self._completed(
                "Feasible PWM-quantized candidate found."
                if result.optimized.feasible
                else "No feasible candidate found within the search; inspect violations.",
                loop=False,
            )
            return
        t, data, metrics, loop = result
        self._result = result
        self.snapshot = {
            "run_context": self._request_context,
            "metrics": metrics,
            "time_s": t.tolist(),
            "signals": {k: v.tolist() for k, v in data.items()},
            "frequency_hz": loop.frequency_hz.tolist(),
            "responses": {
                k: [[float(v.real), float(v.imag)] for v in value]
                for k, value in self._responses(loop).items()
            },
        }
        self.summary.setPlainText(json.dumps(metrics, indent=2, ensure_ascii=False))
        self._plot_loop()
        digital = DigitalTransferFunction(
            tuple(loop.controller_b),
            tuple(loop.controller_a),
            1 / loop.sample_time_s,
            name="DAB voltage controller",
            source="DAB completed-source "
            + self._request_context.get("source", "direct analysis"),
        )
        self.code.setPlainText(render_c99_single_file(digital, prefix="DAB_VOLTAGE"))
        self.tabs.setTabEnabled(self.tabs.indexOf(self.optimization_table), False)
        for w in (self.canvas, self.code):
            self.tabs.setTabEnabled(self.tabs.indexOf(w), True)
        self.tabs.setCurrentWidget(self.canvas)
        # Let the result use the small-screen height. The persistent toggle above
        # the run actions makes returning to all engineering inputs explicit.
        self.parameters_toggle.setChecked(False)
        self._completed(
            "Completed: declared discrete-model poles "
            + ("stable" if loop.stable else "UNSTABLE")
            + ". C99 contains only the exact controller; application owns saturation, permits and hardware timing."
        )

    @staticmethod
    def _responses(loop):
        return {
            "G": loop.plant_response,
            "H": loop.sense_response,
            "C": loop.controller_response,
            "L": loop.open_response,
            "T": loop.closed_response,
            "R": loop.output_reference_response,
        }

    def _plot_loop(self, *_):
        if self._result is None:
            return
        if self._cursor:
            self._cursor.disconnect()
            self._cursor = None
        t, data, _metrics, loop = self._result
        self.fig.clear()
        axes = self.fig.subplots(2, 2)
        traces = []
        for key, response in self._responses(loop).items():
            if not self.trace_checks[key].isChecked():
                continue
            gain = 20 * np.log10(np.maximum(abs(response), 1e-20))
            phase = np.unwrap(np.angle(response)) * 180 / np.pi
            (line,) = axes[0, 0].semilogx(loop.frequency_hz, gain, label=key)
            axes[0, 1].semilogx(
                loop.frequency_hz, phase, label=key, color=line.get_color()
            )
            traces.append(
                BodeCursorTrace(key, loop.frequency_hz, gain, phase, line.get_color())
            )
        axes[0, 0].set_ylabel("Gain / dB")
        axes[0, 1].set_ylabel("Phase / deg")
        if traces:
            axes[0, 0].legend()
            axes[0, 1].legend()
        axes[1, 0].plot(t, data["vout_v"], label="Vout")
        axes[1, 0].plot(t, data["vref_v"], "--", label="Vref")
        axes[1, 0].plot(t, data["measured_v"], ":", label="Vmeas")
        axes[1, 0].legend()
        axes[1, 1].plot(t, data["pcmd_w"], label="Pcmd")
        axes[1, 1].plot(t, data["pload_w"], label="Pload")
        axes[1, 1].legend()
        for ax in axes.ravel():
            ax.grid(True, alpha=0.3)
        if traces:
            self._cursor = BodeCursor(
                self.canvas,
                axes[0, 0],
                axes[0, 1],
                traces,
                on_changed=self._cursor_changed,
            )
        self.canvas.draw_idle()

    def _cursor_changed(self, measurement):
        if self._result is None:
            return
        loop = self._result[3]
        f = measurement.frequency_hz
        responses = self._responses(loop)
        entries = phase_budget(
            loop.frequency_hz,
            responses,
            {
                "G": "Energy plant + command delay (hold included)",
                "C": "Exact controller",
                "H": "Digital measurement filter + delay",
            },
            f,
            ("G", "C", "H"),
        )
        self.cursor_label.setText(
            f"Cursor {f:.7g} Hz; phase budget tab shows G × C × H"
        )
        self.phase_view.setPlainText(
            json.dumps(
                {
                    "frequency_hz": f,
                    "factors": [asdict(x) for x in entries],
                    "sum_gain_db": sum(x.gain_db for x in entries),
                    "sum_phase_deg": sum(x.phase_deg for x in entries),
                    "scope": "Each physical factor occurs once; sampled-grid log interpolation at off-grid cursor.",
                },
                indent=2,
            )
        )

    def _completed(self, status, loop=True):
        current = self._revision == self._request_revision
        self.export_button.setEnabled(current)
        self.action_buttons[5].setEnabled(current)
        for b in self.action_buttons[6:]:
            b.setEnabled(current and loop)
        if not current:
            status += " Inputs changed during calculation. Historical result; current exports disabled."
        self.status.setText(status)

    def _export_path(self, title, name, extension, file_filter=None):
        if self.snapshot is None or not self.export_button.isEnabled():
            return None
        source = self.snapshot.get("run_context", {}).get("source")
        try:
            if source is not None and source != self._source():
                self.mark_stale()
                return None
        except (ValueError, TypeError):
            self.mark_stale()
            return None
        return QFileDialog.getSaveFileName(
            self, title, name, file_filter or f"{extension.upper()} (*.{extension})"
        )[0]

    def export_snapshot(self):
        path = self._export_path(
            "Export completed snapshot", "dab_control.json", "json"
        )
        if path:
            try:
                Path(path).write_text(
                    json.dumps(
                        self.snapshot, indent=2, ensure_ascii=False, allow_nan=False
                    ),
                    encoding="utf-8",
                )
            except (ValueError, OSError) as exc:
                self.status.setText(str(exc))

    def export_report(self):
        path = self._export_path("Export completed report", "dab_control.html", "html")
        if path:
            try:
                write_report(path, self.snapshot)
            except (ValueError, OSError) as exc:
                self.status.setText(str(exc))

    def export_research(self):
        if self._result is None:
            return
        path = self._export_path(
            "Export standalone research script",
            "dab_control.py",
            "py",
            "Python (*.py);;MATLAB (*.m)",
        )
        if path:
            try:
                export_research_script(
                    research_bundle(
                        self._result[3],
                        self.snapshot["run_context"]
                        | {"metrics": self.snapshot["metrics"]},
                    ),
                    path,
                    "matlab" if Path(path).suffix.lower() == ".m" else "python",
                )
            except (ValueError, OSError) as exc:
                self.status.setText(str(exc))

    def export_c99(self):
        if self._result is None:
            return
        path = self._export_path("Export exact C99 controller", "dab_voltage.h", "h")
        if path:
            try:
                Path(path).write_text(
                    "/* UNVERIFIED HARDWARE: exact controller structure with float32 SOS coefficients.\n"
                    " * Application must implement sensing, TPS/SPS mapping, saturation, anti-windup,\n"
                    " * timing and safety permits. Host equivalence is not TI/hardware acceptance. */\n"
                    + self.code.toPlainText(),
                    encoding="utf-8",
                )
            except OSError as exc:
                self.status.setText(str(exc))

    def session_data(self):
        return {
            "schema": SESSION_SCHEMA,
            "stage": asdict(self.stage_provider()),
            "control": asdict(self.control_config()),
            "operating_point": [
                self.inputs["vb"].value(),
                self.inputs["power"].value(),
            ],
            "controller": controller_payload(self.custom_controller),
            "workpoints": self.workpoints,
            "ui": {
                "target_fc_hz": self.target_fc.value(),
                "target_pm_deg": self.target_pm.value(),
                "peak_a": self.inputs["peak"].value(),
                "power_tolerance_percent": self.inputs["tolerance"].value(),
                "commutation_proxy": self.proxy.isChecked(),
                "visible_traces": [
                    key
                    for key, checkbox in self.trace_checks.items()
                    if checkbox.isChecked()
                ],
            },
            "historical_result": {
                "last_result": self.snapshot,
                "last_scan": self.scan_results,
                "applied_candidate": self.applied_candidate,
            },
        }

    def validate_session_widgets(self, data):
        _stage, control, vb, power, controller = validate_session(data)
        values = asdict(control)
        sense = values.pop("sensing")
        values.update(
            sense_delay_ticks=sense.pop("delay_ticks"),
            **sense,
            vb=vb,
            power=power,
            peak=data["ui"]["peak_a"],
            tolerance=data["ui"]["power_tolerance_percent"],
        )
        for key, w in self.inputs.items():
            if key in values and not w.minimum() <= values[key] <= w.maximum():
                raise ValueError(f"{key} exceeds workspace input range")
            if (
                key in values
                and isinstance(w, QDoubleSpinBox)
                and not math.isclose(
                    values[key],
                    round(values[key], w.decimals()),
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            ):
                raise ValueError(f"{key} exceeds workspace input precision")
        for w, key in (
            (self.target_fc, "target_fc_hz"),
            (self.target_pm, "target_pm_deg"),
        ):
            if not w.minimum() <= data["ui"][key] <= w.maximum():
                raise ValueError(f"{key} exceeds workspace input range")
            if not math.isclose(
                data["ui"][key],
                round(data["ui"][key], w.decimals()),
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise ValueError(f"{key} exceeds workspace input precision")
        return values, control, controller

    def restore_session(self, data):
        if self._worker is not None:
            raise ValueError("Cancel or finish running work before restoring a session")
        values, control, controller = self.validate_session_widgets(data)
        for key, w in self.inputs.items():
            if key in values:
                w.blockSignals(True)
                w.setValue(values[key])
                w.blockSignals(False)
        for w, value in (
            (self.load, control.load_model),
            (self.actuator, control.actuator),
        ):
            w.blockSignals(True)
            w.setCurrentIndex(w.findData(value))
            w.blockSignals(False)
        self.cp.blockSignals(True)
        self.cp.setChecked(control.include_cp)
        self.cp.blockSignals(False)
        self.proxy.setChecked(data["ui"]["commutation_proxy"])
        for key, checkbox in self.trace_checks.items():
            checkbox.setChecked(key in data["ui"]["visible_traces"])
        self.target_fc.setValue(data["ui"]["target_fc_hz"])
        self.target_pm.setValue(data["ui"]["target_pm_deg"])
        self.custom_controller = controller
        self.workpoints = list(data["workpoints"])
        self.candidates = []
        self.candidate_table.setRowCount(0)
        history = data["historical_result"] or {}
        self.snapshot = history.get("last_result")
        self.scan_results = history.get("last_scan", [])
        saved_candidate = history.get("applied_candidate")
        self.applied_candidate = (
            {**saved_candidate, "historical": True}
            if isinstance(saved_candidate, dict)
            else None
        )
        self._result = None
        self.code.clear()
        self.phase_view.clear()
        self.fig.clear()
        self.canvas.draw_idle()
        self.summary.setPlainText(
            "Historical saved result; recompute to establish current evidence.\n"
            + json.dumps(self.snapshot, indent=2, ensure_ascii=False)
        )
        self.scan_view.setPlainText(json.dumps(self.workpoints, indent=2))
        self.mark_stale()
        self.status.setText(
            "Restored stage/control/exact C(z)/workpoints. Saved results are historical; Run to recompute current evidence."
        )

    def closeEvent(self, event):
        if self._worker is not None:
            self.cancel()
            event.ignore()
            return
        if self._cursor:
            self._cursor.disconnect()
        super().closeEvent(event)
