"""Power Stage Designer desktop workflow; computations remain in numerical modules."""
from __future__ import annotations

import json
import threading
from dataclasses import asdict

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QByteArray, QRectF, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from llc_design.gui.contact import add_toolbar_contacts
from llc_design.gui.workers import FunctionWorker
from llc_design.models.devices import DeviceDatabase

from .models import DesignInputs
from .project import project_document, read_project, save_project, write_json
from .report import write_report, write_waveform_csv
from .schematic import layout, schematic_size, svg_schematic
from .suggestions import (
    MAGNETIZING_TOPOLOGIES,
    NUMERICAL_TOPOLOGIES,
    RATIO_TOPOLOGIES,
    TWO_INDUCTOR_TOPOLOGIES,
    SuggestionTargets,
    suggest_parameters,
)
from .topologies import all_topologies, calculate_stage, supported_topologies

PRESETS = {
    "T01": DesignInputs("T01", 12, 5, 3, 100e3, 47e-6),
    "T02": DesignInputs("T02", 24, 48, 2, 100e3, 100e-6),
    "T09": DesignInputs("T09", 48, 12, 1, 100e3, 1e-3, turns_ratio_np_ns=4),
}

# Value shown = SI value * scale. Units stay at this UI boundary only.
INPUT_FIELDS = [
    ("vin_v", "输入电压 / V", 1, 12), ("vout_v", "输出电压幅值 / V", 1, 5),
    ("iout_a", "输出电流 / A", 1, 3), ("fsw_hz", "开关频率 / kHz", 1e-3, 100e3),
    ("inductance_h", "电感 L / 原边 Lp / µH", 1e6, 47e-6),
    ("capacitance_f", "输出有效电容 / µF", 1e6, 100e-6),
    ("turns_ratio_np_ns", "变比 Np / Ns", 1, 1), ("diode_drop_v", "二极管压降 / V", 1, 0),
    ("secondary_inductance_h", "第二电感 / µH（0=未指定）", 1e6, 0),
    ("transfer_capacitance_f", "传能电容 / µF（0=未指定）", 1e6, 0),
    ("magnetizing_inductance_h", "励磁电感 / µH（0=未指定）", 1e6, 0),
    ("reset_turns_ratio_np_nr", "复位匝比 Np / Nr", 1, 1),
    ("resonant_capacitance_f", "谐振电容 / nF（0=未指定）", 1e9, 0),
    ("dead_time_s", "死区 / ns", 1e9, 0),
    ("leakage_inductance_h","漏感 / µH（T04副边/T18原边）",1e6,0),
    ("primary_capacitance_f","原边输出电容 / µF（Fly-Buck）",1e6,0),
    ("primary_output_current_a","原边负载 / A（−1未填；0空载）",1,-1),
]
for _topology in ("T03","T05","T06","T07","T08","T10","T12","T13","T14","T15","T16","T17"):
    PRESETS[_topology]=DesignInputs(_topology,48,5,2,100e3,470e-6,capacitance_f=1e-3,
        turns_ratio_np_ns=2,secondary_inductance_h=100e-6,transfer_capacitance_f=1e-3,magnetizing_inductance_h=1e-3)
PRESETS["T19"]=DesignInputs("T19",12,1.2,10,500e3,1e-6,secondary_inductance_h=1.3e-6,transfer_capacitance_f=20e-6)

PRESETS["T04"]=DesignInputs("T04",24,5,.2,100e3,47e-6,capacitance_f=100e-6,
    turns_ratio_np_ns=1,diode_drop_v=.4,leakage_inductance_h=1e-6,primary_capacitance_f=47e-6,primary_output_current_a=.1)
PRESETS["T11"]=DesignInputs("T11",325,12,1,100e3,1e-3,turns_ratio_np_ns=10,resonant_capacitance_f=100e-12)
PRESETS["T18"]=DesignInputs("T18",400,48,5,100e3,100e-6,turns_ratio_np_ns=4,
    magnetizing_inductance_h=1e-3,leakage_inductance_h=20e-6,diode_drop_v=.7)

OPTIONAL_INPUTS = {"secondary_inductance_h", "transfer_capacitance_f", "magnetizing_inductance_h", "resonant_capacitance_f","leakage_inductance_h","primary_capacitance_f"}


def _number(value=0, *, minimum=0, maximum=1e12, decimals=9):
    box = QDoubleSpinBox(); box.setRange(minimum, maximum); box.setDecimals(decimals)
    box.setValue(value); box.setKeyboardTracking(False); box.setAccelerated(True)
    return box


class SchematicView(QWidget):
    component_selected = Signal(str)

    def __init__(self):
        super().__init__(); self.setMinimumHeight(235)
        self.topology_id = "T01"; self.selected = "Q1"; self.device_keys = frozenset()
        self.renderer = QSvgRenderer(self)
        self.refresh()

    def refresh(self):
        self.renderer.load(QByteArray(svg_schematic(self.topology_id, selected=self.selected, device_keys=self.device_keys).encode()))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.renderer.render(painter, QRectF(0, 0, self.width(), self.height()))

    def mousePressEvent(self, event):
        width,height=schematic_size(self.topology_id,device_keys=self.device_keys)
        x, y = event.position().x()*width/self.width(), event.position().y()*height/self.height()
        for element in layout(self.topology_id, device_keys=self.device_keys)[0]:
            if element.contains(x,y):
                self.component_selected.emit(element.key); return
        super().mousePressEvent(event)


class PowerStageMainWindow(QMainWindow):
    workspace_switch_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Power Stage Designer · 功率级与模拟环路")
        self.resize(1360, 900)
        self.result = None; self.loop_result = None; self._loop_settings = None;self.corner_sweep=None;self._sweep_request=None;self._sweep_enabled=False;self._sweep_generation=0
        self._generation = 0; self._loading = False; self._restored_loop = None
        self._exact_inputs = {}; self._exact_loop = {}; self._exact_comp = {}; self._hidden_comp = {}
        self._loop_generation=0;self._jobs={};self._cancel_events=[];self._synthesis_events=[];self._closing=False;self._restore_corner_request_pending=False
        self._suggestion_generation=0;self._suggestion_events=[]
        self.parameter_proposal=None;self._applied_suggestion=None
        self._pool=QThreadPool(self);self._pool.setMaxThreadCount(1)
        self.database = DeviceDatabase()
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(120)
        self._timer.timeout.connect(self.calculate)
        toolbar = self.addToolBar("工作区")
        toolbar.addAction("返回工作区", lambda: self.workspace_switch_requested.emit("home"))
        toolbar.addAction("重算", self.calculate)
        self.save_action = toolbar.addAction("保存项目", self._save_dialog)
        toolbar.addAction("打开项目", self._open_dialog)
        self.report_action = toolbar.addAction("导出报告", self._report_dialog)
        self.csv_action = toolbar.addAction("导出波形 CSV", self._csv_dialog)
        add_toolbar_contacts(toolbar)
        central=QWidget(); root=QVBoxLayout(central); self.setCentralWidget(central)
        self.status=QLabel("输入参数以开始计算"); self.status.setWordWrap(True); root.addWidget(self.status)
        splitter=QSplitter();root.addWidget(splitter,1)
        left=QWidget();form=QFormLayout(left);self._input_form=form
        self.topology_combo=QComboBox();self.topology_combo.setObjectName("psd_topology")
        for key,name in all_topologies().items():
            self.topology_combo.addItem(f"{name}"+("（既有LLC工作区）" if key in {"T20","T21"} else "（待接入）" if key not in supported_topologies() else ""),key)
        # Keep long topology labels readable in the popup without widening the
        # permanently visible parameter column on smaller desktop screens.
        metrics=self.topology_combo.fontMetrics()
        self.topology_combo.view().setMinimumWidth(max(
            metrics.horizontalAdvance(self.topology_combo.itemText(index))
            for index in range(self.topology_combo.count()))+48)
        form.addRow("拓扑",self.topology_combo)
        preset=QPushButton("加载参考工况");preset.clicked.connect(self.load_preset);form.addRow(preset);self.preset_button=preset
        self.llc_button=QPushButton("进入对应桥型的 LLC 工作区");self.llc_button.clicked.connect(self.open_llc_workspace);form.addRow(self.llc_button)
        self.input_widgets={}
        for key,label,scale,default in INPUT_FIELDS:
            box=_number(default*scale,minimum=-1 if key=="primary_output_current_a" else 0);box.setObjectName("psd_"+key)
            self.input_widgets[key]=box;form.addRow(label,box);box.valueChanged.connect(lambda _value, key=key: self._numeric_input_changed(key))
        self.mode_combo=QComboBox();self.mode_combo.addItem("自动判断 CCM / DCM","auto")
        self.mode_combo.addItem("要求 CCM","ccm");self.mode_combo.addItem("要求 DCM","dcm")
        self.mode_combo.addItem("要求边界模式 BCM","bcm");form.addRow("导通模式",self.mode_combo)
        self.mode_combo.currentIndexChanged.connect(self.invalidate)
        self.topology_combo.currentIndexChanged.connect(self._topology_changed)
        self.device_combo=QComboBox();self.device_combo.addItem("未指定器件",None)
        seen=set()
        for device in self.database.primary+self.database.sr:
            if device.part_number not in seen:
                self.device_combo.addItem(device.part_number, device);seen.add(device.part_number)
        form.addRow("主开关器件",self.device_combo)
        self.device_combo.currentIndexChanged.connect(self._refresh_device)
        self.device_status=QLabel("器件参数来自既有数据库；通用参考件须核对数据手册。")
        self.device_status.setWordWrap(True);form.addRow(self.device_status)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(left);scroll.setMinimumWidth(330)
        splitter.addWidget(scroll)
        self.tabs=QTabWidget();splitter.addWidget(self.tabs);splitter.setStretchFactor(1,1)
        stage=QWidget();stage_layout=QVBoxLayout(stage)
        self.schematic=SchematicView();self.schematic.component_selected.connect(self.select_component)
        self.schematic_toggle=QPushButton("收起原理图 · 扩大波形区域")
        self.schematic_toggle.setObjectName("psd_toggle_schematic")
        self.schematic_toggle.setCheckable(True);self.schematic_toggle.setChecked(True)
        def toggle_schematic(visible):
            self.schematic.setVisible(visible)
            self.schematic_toggle.setText("收起原理图 · 扩大波形区域" if visible else "显示原理图")
        self.schematic_toggle.toggled.connect(toggle_schematic)
        stage_layout.addWidget(self.schematic_toggle)
        stage_layout.addWidget(self.schematic)
        self.component_combo=QComboBox();self.component_combo.setObjectName("psd_component")
        self.component_combo.currentIndexChanged.connect(self._show_component);stage_layout.addWidget(self.component_combo)
        self.reference_label=QLabel();self.reference_label.setWordWrap(True);stage_layout.addWidget(self.reference_label)
        self.component_status=QLabel();self.component_status.setWordWrap(True);stage_layout.addWidget(self.component_status)
        self.current_plot=pg.PlotWidget(title="器件电流");self.current_plot.setLabel("left","电流",units="A")
        self.voltage_plot=pg.PlotWidget(title="器件电压");self.voltage_plot.setLabel("left","电压",units="V")
        for plot in (self.current_plot,self.voltage_plot):
            # Axes/title consume about 72 px. Never squeeze the actual trace
            # ViewBox to zero on 720p screens; let this tab scroll instead.
            plot.setMinimumHeight(180)
            plot.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Ignored)
            plot.setLabel("bottom","时间",units="s");plot.showGrid(x=True,y=True,alpha=.2);stage_layout.addWidget(plot,1)
        self.stress_table=QTableWidget(0,7);self.stress_table.setHorizontalHeaderLabels(["信号","最小","最大","平均","整周期 RMS","峰峰值","交流 RMS"])
        self.stress_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.stress_table.setMinimumHeight(110);self.stress_table.setMaximumHeight(115);stage_layout.addWidget(self.stress_table)
        self.stage_scroll=QScrollArea();self.stage_scroll.setObjectName("psd_stage_scroll")
        self.stage_scroll.setWidgetResizable(True);self.stage_scroll.setWidget(stage)
        self.tabs.addTab(self.stage_scroll,"原理图 · 波形 · 应力")
        self.loop_tab=self._build_loop_tab();self.tabs.addTab(self.loop_tab,"模拟环路")
        self.evidence=QTextBrowser();self.tabs.addTab(self.evidence,"模型范围与依据")
        self.tabs.addTab(self._build_corners_tab(),"多工况极值")
        from .toolbox_gui import ToolBoxWidget
        self.toolbox=ToolBoxWidget();self.tabs.addTab(self.toolbox,"工具箱")
        self.suggestion_tab=self._build_suggestion_tab();self.tabs.addTab(self.suggestion_tab,"参数建议")
        from .advanced_gui import AdvancedModelWidget
        self.advanced_model=AdvancedModelWidget();self.advanced_model.changed.connect(self.invalidate)
        advanced_scroll=QScrollArea();advanced_scroll.setWidgetResizable(True);advanced_scroll.setWidget(self.advanced_model)
        self.tabs.addTab(advanced_scroll,"扩展物理参数")
        self.load_preset()

    def _topology_changed(self):
        self._update_input_visibility();self.invalidate(); self.schematic.topology_id=self.topology_combo.currentData();self.schematic.device_keys=frozenset();self.schematic.refresh()

    def _update_input_visibility(self):
        topology=self.topology_combo.currentData()
        if hasattr(self,"advanced_model"):self.advanced_model.set_topology(topology)
        is_llc=topology in {"T20","T21"}
        for box in self.input_widgets.values():self._input_form.setRowVisible(box,not is_llc)
        for widget in (self.mode_combo,self.device_combo,self.device_status,self.preset_button):self._input_form.setRowVisible(widget,not is_llc)
        applicable={"turns_ratio_np_ns": {"T04","T09","T10","T11","T12","T13","T14","T15","T16","T17","T18"},
            "secondary_inductance_h":{"T06","T07","T08","T19"},"transfer_capacitance_f":{"T06","T07","T08","T12","T15","T19"},
            "magnetizing_inductance_h":{"T12","T13","T14","T15","T16","T17","T18"},
            "reset_turns_ratio_np_nr":{"T13"},"resonant_capacitance_f":{"T11"},"dead_time_s":{"T11","T12","T18"},"leakage_inductance_h":{"T04","T18"},"primary_capacitance_f":{"T04"},"primary_output_current_a":{"T04"}}
        for key,allowed in applicable.items():self._input_form.setRowVisible(self.input_widgets[key],not is_llc and topology in allowed)
        self.llc_button.setVisible(topology in {"T20","T21"})
        if hasattr(self,"suggestion_tab"):self._update_suggestion_visibility()
        self._input_form.labelForField(self.input_widgets["fsw_hz"]).setText("最大开关频率 / kHz" if topology=="T11" else "开关频率 / kHz")
        main_inductance_label=("原边励磁电感 Lm / µH" if topology in {"T04","T09","T10","T11"}
            else "输出电感 Lo / µH" if topology in {"T12","T13","T14","T15","T16","T17","T18"}
            else "第一相电感 L1 / µH" if topology=="T19" else "电感 L1 / µH")
        self._input_form.labelForField(self.input_widgets["inductance_h"]).setText(main_inductance_label)

    def _numeric_input_changed(self, key):
        if not self._loading:self._exact_inputs.pop(key,None)
        self.invalidate()

    def current_inputs(self):
        values={key:self._exact_inputs.get(key,box.value()/scale) for key,_,scale,_ in INPUT_FIELDS for box in [self.input_widgets[key]]}
        for key in OPTIONAL_INPUTS:
            if values[key]==0:values[key]=None
        if values["primary_output_current_a"]==-1:values["primary_output_current_a"]=None
        return DesignInputs(topology=self.topology_combo.currentData(),mode=self.mode_combo.currentData(),**values,**self.advanced_model.options())

    def set_inputs(self, inputs):
        self._loading=True
        try:
            index=self.topology_combo.findData(inputs.topology)
            if index<0:raise ValueError("Unknown topology")
            self.topology_combo.setCurrentIndex(index)
            for key,_,scale,_ in INPUT_FIELDS:
                self.input_widgets[key].setValue((-1 if key=="primary_output_current_a" and getattr(inputs,key) is None else getattr(inputs,key) or 0)*scale)
            self.mode_combo.setCurrentIndex(self.mode_combo.findData(inputs.mode))
            self._exact_inputs={key:getattr(inputs,key) for key,_,_,_ in INPUT_FIELDS}
            self.advanced_model.restore(inputs)
        finally:self._loading=False
        self._update_input_visibility();self.invalidate();self._timer.stop();self.calculate()

    def open_llc_workspace(self):
        topology=self.topology_combo.currentData()
        if topology not in {"T20","T21"}:return
        callback=getattr(self,"open_existing_llc",None)
        if callback is not None:callback(topology);return
        from llc_design.core.spec import LLCDesignSpec
        from llc_design.gui.launcher import WorkspaceApplicationController
        self._linked_application=WorkspaceApplicationController(LLCDesignSpec(),power_stage_window=self)
        self._linked_application._show_psd_llc(topology);self.hide()

    def load_preset(self):
        topology=self.topology_combo.currentData()
        preset=PRESETS.get(topology)
        if preset is None:
            # Topology-specific presets are extended with real models; no hidden
            # guessed operating point is used when there is no known example.
            self.status.setText("此拓扑尚无参考工况，请按模型范围输入参数。");return
        self.set_inputs(preset)

    def invalidate(self, *_):
        if self._loading:return
        if hasattr(self,"advanced_model"):self._update_suggestion_visibility()
        self._generation+=1;self._loop_generation+=1
        self._invalidate_suggestions(clear_record=True)
        self.invalidate_corners()
        for event in self._cancel_events:event.set()
        self.result=None;self.loop_result=None;self._loop_settings=None
        for action in (self.save_action,self.report_action,self.csv_action):action.setEnabled(False)
        self.current_plot.clear();self.voltage_plot.clear();self.stress_table.setRowCount(0)
        self.component_status.clear();self.reference_label.clear()
        self.component_combo.clear()
        self.schematic.topology_id=self.topology_combo.currentData();self.schematic.device_keys=frozenset();self.schematic.refresh()
        self.loop_gain_plot.clear();self.loop_phase_plot.clear();self.evidence.clear()
        if hasattr(self,"toolbox"):self.toolbox.set_stage_result(None)
        self.status.setText("参数已改变，旧结果与导出已失效。正在重算…")
        self._timer.start()

    def calculate(self):
        self._timer.stop();self._generation+=1;self._loop_generation+=1
        for cancellation in self._cancel_events:cancellation.set()
        self.invalidate_corners()
        self.result=None;self.loop_result=None;self._loop_settings=None
        self.current_plot.clear();self.voltage_plot.clear();self.stress_table.setRowCount(0)
        self.loop_gain_plot.clear();self.loop_phase_plot.clear();self.evidence.clear()
        if hasattr(self,"toolbox"):self.toolbox.set_stage_result(None)
        for action in (self.save_action,self.report_action,self.csv_action):action.setEnabled(False)
        try:
            inputs=self.current_inputs()
        except (ValueError,TypeError,ArithmeticError) as exc:
            self.status.setText("输入无效："+str(exc));return
        if inputs.topology in {"T20","T21"}:
            self.status.setText("此桥型使用既有 LLC 工作区的谐振模型、波形、器件、控制与报告。点击左侧进入；原LLC参数保留，半桥参考匝数30:8、全桥30:4。")
            return
        if inputs.topology in {"T04","T11","T18"} or inputs.two_switch_options is not None:
            token=self._generation;event=threading.Event();self._cancel_events.append(event)
            def compute():
                try:return token,calculate_stage(inputs,cancelled=event.is_set),None
                except (ValueError,TypeError,ArithmeticError,InterruptedError) as exc:return token,None,str(exc)
            worker=FunctionWorker(compute);key=("stage",token);self._jobs[key]=worker
            worker.signals.result.connect(self._on_stage_calculated)
            worker.signals.error.connect(self._background_error)
            worker.signals.finished.connect(lambda:self._release_job(key,event))
            self.status.setText("正在求解周期工作点…修改输入或关闭窗口会取消旧计算。")
            self._pool.start(worker);return
        try:
            result=calculate_stage(inputs)
        except (ValueError,TypeError,ArithmeticError) as exc:
            self.status.setText(f"未得到有效设计：{exc}");return
        self._apply_stage_result(result)

    def _on_stage_calculated(self,payload):
        token,result,error=payload
        if self._closing or token!=self._generation:return
        if error:
            self.status.setText("未得到有效设计："+error);return
        self._apply_stage_result(result)

    def _apply_stage_result(self,result):
        self.result=result
        if hasattr(self,"toolbox"):self.toolbox.set_stage_result(result)
        display_mode={"QR-DYNAMIC-DCM":"QR 断续模式（扩展模型）"}.get(result.mode,result.mode)
        self.status.setText(f"{all_topologies()[result.topology_id]} · {display_mode} · D={result.duty:.6g} · f={1/next(iter(result.waveforms.values())).current_a.period_s/1e3:.5g} kHz")
        self.status.setToolTip("设计身份："+result.design_id)
        self.component_combo.blockSignals(True);self.component_combo.clear()
        for key,wave in result.waveforms.items():
            label=("体二极管" if key.startswith("Dbody") else "复位二极管" if key.startswith("Dreset")
                else "钳位二极管" if key.startswith("Dclamp") else "整流 / 续流二极管" if key.startswith("D")
                else "开关通道" if key.startswith("Q") else "励磁状态" if key=="Lm"
                else "漏感" if key=="Lleak" else "电感" if key.startswith("L")
                else "电容" if key.startswith("C") else "原边绕组" if key.startswith("primary")
                else "副边绕组" if key.startswith("secondary") else "复位绕组" if key.startswith("reset")
                else "绕组电阻" if key=="Rwinding" else "理想高阻电压探针" if key.startswith("node_") else wave.label)
            self.component_combo.addItem(f"{key} · {label}",key)
            self.component_combo.setItemData(self.component_combo.count()-1,wave.label,Qt.ItemDataRole.ToolTipRole)
        self.component_combo.blockSignals(False)
        self.schematic.topology_id=result.topology_id;self.schematic.device_keys=frozenset(result.waveforms);self._show_component();self._refresh_device()
        self._refresh_evidence()
        if self._applied_suggestion is not None:
            self._show_suggestion_summary(self._applied_suggestion,result.inputs,"已应用建议；当前结果已重新计算。",result)
        for action in (self.save_action,self.report_action,self.csv_action):action.setEnabled(True)
        self.loop_status.setText("功率级已更新，点击分析以建立本工况的环路结果。")
        if self._restore_corner_request_pending:
            self._restore_corner_request_pending=False;self.run_corners()

    def _refresh_evidence(self):
        if self.result is None:return
        result=self.result
        self.evidence.setPlainText(json.dumps({"inputs":asdict(result.inputs),"model":result.model_version,
            "design_id":result.design_id,"mode":result.mode,"metrics":result.metrics,
            "diagnostics":result.diagnostics,"assumptions":result.assumptions,"warnings":result.warnings,
            "provenance":result.provenance,"parameter_suggestion":self._applied_suggestion,
            "unapplied_parameter_proposal":self.parameter_proposal.record() if self.parameter_proposal else None},ensure_ascii=False,indent=2))

    def select_component(self,key):
        index=self.component_combo.findData(key)
        if index>=0:self.component_combo.setCurrentIndex(index)

    def _show_component(self):
        self.current_plot.clear();self.voltage_plot.clear();self.stress_table.setRowCount(0)
        self.component_status.clear()
        if self.result is None:return
        key=self.component_combo.currentData()
        if key not in self.result.waveforms:return
        wave=self.result.waveforms[key];self.schematic.selected=key;self.schematic.refresh()
        self.reference_label.setText(f"电流：{wave.current_a.reference}；电压：{wave.voltage_v.reference}")
        from .devices import assess_component
        assessment=assess_component(self.result,key,selected_main_device=self.device_combo.currentData())
        partial=assessment["known_conduction_loss_w"]
        self.component_status.setText(assessment["assessment"]+(f" 100°C 导通项 {partial:.5g} W；完整损耗/热/SOA未知。" if partial is not None else ""))
        self.stress_table.setRowCount(2)
        for row,signal,plot,label,color in [(0,wave.current_a,self.current_plot,"电流 / A","#0ea5e9"),(1,wave.voltage_v,self.voltage_plot,"电压 / V","#f59e0b")]:
            if row==0 and assessment.get("statistics_scope")=="excludes_ideal_turn_on_impulse":label="电流（不含理想脉冲）/ A"
            t,y=signal.sample(count=800,periods=2);plot.plot(t,y,pen=pg.mkPen(color,width=2))
            stats=signal.statistics()
            for col,value in enumerate([label,*[f"{stats[k]:.8g}" for k in ("min","max","mean","rms","peak_to_peak","ac_rms")]]):
                self.stress_table.setItem(row,col,QTableWidgetItem(value))

    def _refresh_device(self,*_):
        self.invalidate_corners()
        self._show_component()
        device=self.device_combo.currentData()
        if device is None or self.result is None:
            self.device_status.setText("未指定主开关器件；损耗与热结论尚未计算。");return
        info=self.device_assessment()
        self.device_status.setText(f"VDS 额定 {device.vds_max_v:g} V；ID 额定 {device.id_cont_a:g} A。\n"+info["assessment"]+"\n额定比较不含温升、SOA 和动态尖峰；不等于器件通过。")

    def device_assessment(self):
        device=self.device_combo.currentData()
        if device is None or self.result is None:return None
        from .devices import assess_component
        if "Q1" not in self.result.stress:return {"assessment":"此模型没有 Q1 映射；拒绝自动比较。"}
        return assess_component(self.result,"Q1",selected_main_device=device)

    def _build_suggestion_tab(self):
        tab=QWidget();root=QVBoxLayout(tab);form=QFormLayout();root.addLayout(form)
        note=QLabel("按当前工况和明确目标计算 L / Lm / Np:Ns，先预览验算，确认后应用。目标是绝对峰峰值 A；不自动改变电容、漏感或器件。")
        note.setWordWrap(True);root.addWidget(note)
        self.suggestion_ripple=_number(.9,minimum=1e-9)
        self.suggestion_duty=_number(.45,minimum=.000001,maximum=.999999)
        self.suggestion_second=_number(.6,minimum=1e-9)
        self.suggestion_magnetizing=_number(.1,minimum=0)
        self.suggestion_reflected=_number(120,minimum=1e-9)
        self.suggestion_ratio=QCheckBox("根据最大占空比计算 Np/Ns（QR 使用反射电压目标）")
        self._suggestion_form=form
        form.addRow("主电感 L / Flyback Lm 的 ΔIpp / A",self.suggestion_ripple)
        form.addRow("最大模型占空比（桥型为半周期有效比例）",self.suggestion_duty)
        form.addRow("第二电感 / 第二相 ΔIpp / A",self.suggestion_second)
        form.addRow("单独励磁电感 ΔIm,pp / A（0 保留 Lm）",self.suggestion_magnetizing)
        form.addRow(self.suggestion_ratio)
        form.addRow("QR 原边反射输出电压目标 / V",self.suggestion_reflected)
        for widget in (self.suggestion_ripple,self.suggestion_duty,self.suggestion_second,self.suggestion_magnetizing,self.suggestion_reflected):
            widget.valueChanged.connect(lambda *_:self._invalidate_suggestions())
        self.suggestion_ratio.toggled.connect(lambda *_:self._invalidate_suggestions())
        self.suggestion_scope=QLabel();self.suggestion_scope.setWordWrap(True);root.addWidget(self.suggestion_scope)
        buttons=QHBoxLayout();root.addLayout(buttons)
        self.suggest_button=QPushButton("计算参数建议");self.suggest_button.setObjectName("psd_suggest_parameters")
        self.apply_suggestion_button=QPushButton("应用已验算建议");self.apply_suggestion_button.setObjectName("psd_apply_suggestion")
        self.apply_suggestion_button.setEnabled(False)
        self.cancel_suggestion_button=QPushButton("取消建议计算")
        for button in (self.suggest_button,self.apply_suggestion_button,self.cancel_suggestion_button):buttons.addWidget(button)
        self.suggest_button.clicked.connect(self.propose_parameters)
        self.apply_suggestion_button.clicked.connect(self.apply_parameter_proposal)
        self.cancel_suggestion_button.clicked.connect(self.cancel_parameter_suggestion)
        self.suggestion_output=QTextBrowser();root.addWidget(self.suggestion_output,1)
        return tab

    def _update_suggestion_visibility(self):
        topology=self.topology_combo.currentData();form=self._suggestion_form
        form.setRowVisible(self.suggestion_second,topology in TWO_INDUCTOR_TOPOLOGIES)
        form.setRowVisible(self.suggestion_magnetizing,topology in MAGNETIZING_TOPOLOGIES)
        form.setRowVisible(self.suggestion_ratio,topology in RATIO_TOPOLOGIES | NUMERICAL_TOPOLOGIES)
        form.setRowVisible(self.suggestion_reflected,topology=="T11")
        self.suggest_button.setEnabled(topology not in {"T20","T21"})
        scope="目标只约束当前输入电压/负载；仍需多工况和磁芯/饱和/损耗核验。"
        advanced=hasattr(self,"advanced_model") and self.advanced_model.enabled.isChecked()
        if topology in {"T04","T18"}:scope+=" 此拓扑使用有限漏感模型数值求解；勾选变比时联合求解 L 和 Np/Ns，可能无解或不收敛。"
        if topology=="T18":
            scope+=(" 已启用有限 Coss/Ron 换流模型：每个候选保留实际死区与器件参数，并报告各开关门极时刻 Vds/ZVS 判据；不保证实现 ZVS 或硬件性能。" if advanced
                    else " 理想有限电感模型不验证 ZVS；如需门极时刻换流检查，请在“扩展物理参数”明确提供 Coss/Ron 等参数并启用。")
        if topology=="T11":
            scope+=" QR 固定功率下仅凭最大占空比不能唯一确定变比，需明确反射电压目标。"
            scope+=(" 候选逐次求解所选谷底策略、延迟、节点 Q(V)/C(V) 或常数 C，以及实际开通路径；频率上限和数据覆盖限制保持有效。" if advanced
                    else " 未启用扩展模型时采用恒定 Cnode：正谷底可选谷，反射电压不低于输入时仅支持首个零电压谷底，超出频率上限即报无解。")
        if topology in {"T15","T16"}:scope+=" 桥型建议限普通 CCM；输出纹波频率为 2×fsw，Np/Ns 的 Ns 指半副边；单管占空比为此比例的一半。"
        if topology=="T17":scope+=" 全桥建议按实际电荷/磁通平衡求解普通 CCM、联合退磁 CCM/BCM/DCM；输出纹波频率 2×fsw，Ns 指半副边。固定变比时占空比是上限，勾选变比时是实际目标；每项均以最终波形复算。"
        if topology=="T05":
            scope+=(" 两管同时驱动；候选保留 Coss/Ron、体二极管和绕组参数，逐次求解浮空节点与实际 CCM/DCM 周期，不替换为四开关模型。" if advanced
                    else " 两管同时驱动的理想模型仅支持 CCM/BCM；DCM 请在“扩展物理参数”明确提供有限 Coss/Ron 等参数并启用。")
        if topology in {"T20","T21"}:scope="LLC 参数设计请进入对应的既有 LLC 工作区。"
        self.suggestion_scope.setText(scope)

    def _suggestion_targets(self):
        topology=self.topology_combo.currentData()
        return SuggestionTargets(self.suggestion_ripple.value(),self.suggestion_duty.value(),
            secondary_ripple_pp_a=self.suggestion_second.value() if topology in TWO_INDUCTOR_TOPOLOGIES else None,
            magnetizing_ripple_pp_a=(self.suggestion_magnetizing.value() or None) if topology in MAGNETIZING_TOPOLOGIES else None,
            size_turns_ratio=self.suggestion_ratio.isChecked() and topology in RATIO_TOPOLOGIES | NUMERICAL_TOPOLOGIES,
            reflected_voltage_v=self.suggestion_reflected.value() if topology=="T11" and self.suggestion_ratio.isChecked() else None)

    def _invalidate_suggestions(self,*,clear_record=False):
        self._suggestion_generation+=1
        for event in self._suggestion_events:event.set()
        self.parameter_proposal=None
        if clear_record:self._applied_suggestion=None
        if hasattr(self,"apply_suggestion_button"):
            self.apply_suggestion_button.setEnabled(False)
            self.suggestion_output.clear()
        if hasattr(self,"evidence"):self._refresh_evidence()

    def cancel_parameter_suggestion(self):
        self._invalidate_suggestions()
        self.suggestion_output.setPlainText("已取消建议计算；迟到结果不会应用。")

    def propose_parameters(self):
        if self._timer.isActive():self.calculate()
        self._invalidate_suggestions();token=self._suggestion_generation
        try:
            inputs=self.current_inputs();targets=self._suggestion_targets()
        except (ValueError,TypeError,ArithmeticError) as exc:
            self.suggestion_output.setPlainText("参数建议未生成：输入无效；"+str(exc));return
        event=threading.Event();self._suggestion_events.append(event);self._cancel_events.append(event)
        def compute():
            try:return token,suggest_parameters(inputs,targets,cancelled=event.is_set),None
            except (ValueError,TypeError,ArithmeticError,InterruptedError) as exc:return token,None,str(exc)
        worker=FunctionWorker(compute);key=("parameters",token);self._jobs[key]=worker
        worker.signals.result.connect(self._on_parameter_proposal)
        worker.signals.error.connect(lambda message,token=token:self._parameter_proposal_error(token,message))
        worker.signals.finished.connect(lambda:self._release_job(key,event))
        self.suggestion_output.setPlainText("正在计算并验算实际纹波、占空比和有效域…输入改变或取消后将丢弃旧建议。")
        self._pool.start(worker)

    def _parameter_proposal_error(self,token,message):
        if not self._closing and token==self._suggestion_generation:
            self.suggestion_output.setPlainText("参数建议未生成："+message.splitlines()[-1])

    def _on_parameter_proposal(self,payload):
        token,proposal,error=payload
        if self._closing or token!=self._suggestion_generation:return
        if error:self.suggestion_output.setPlainText("无有效建议，当前输入未改变："+error);return
        if proposal.source_design_id!=self.current_inputs().design_id:return
        self.parameter_proposal=proposal;self.apply_suggestion_button.setEnabled(True)
        self._show_suggestion_summary(proposal.record(),proposal.inputs,"已通过模型验算，尚未应用。",proposal.result)
        self._refresh_evidence()

    def _show_suggestion_summary(self,record,inputs,status,result=None):
        from .suggestions import achieved_values
        topology=inputs.topology
        main_label="原边励磁电感 Lm" if topology in {"T04","T09","T10","T11"} else "输出电感 Lo" if topology in {"T12","T13","T14","T15","T16","T17","T18"} else "第一相电感 L1" if topology=="T19" else "电感 L1"
        labels={"inductance_h":(main_label,1e6,"µH"),
                "secondary_inductance_h":("第二电感 / 第二相电感",1e6,"µH"),
                "magnetizing_inductance_h":("励磁电感 Lm",1e6,"µH"),
                "turns_ratio_np_ns":("变比 Np / Ns",1,"")}
        lines=[status,"", "元件建议："]
        for key in record["changed_fields"]:
            label,scale,unit=labels[key]
            old=record["source_inputs"][key];new=getattr(inputs,key)
            previous="未指定" if old is None else f"{old*scale:.9g} {unit}"
            lines.append(f"• {label}：{previous} → {new*scale:.9g} {unit}")
        if not record["changed_fields"]:lines.append("• 当前元件值已经满足这些目标，无需更改。")
        targets=record["targets"];values=achieved_values(result) if result is not None else record["achieved"]
        lines.extend(["",f"目标：主电感 ΔIpp = {targets['ripple_pp_a']:.9g} A；模型占空比 ≤ {targets['max_duty']*100:.7g}%。",
                      f"实际校核：{values['mode']}；占空比 {values['duty']*100:.7g}%；开关频率 {values['switching_frequency_hz']/1e3:.8g} kHz。",
                      f"• 主电感 ΔIpp = {values['ripple_pp_a']:.9g} A"])
        for key,label in (("secondary_ripple_pp_a","第二电感 ΔIpp"),("magnetizing_ripple_pp_a","励磁 ΔIm,pp")):
            if targets[key] is not None:lines.append(f"• {label}：目标 {targets[key]:.9g} A；实际 {values[key]:.9g} A")
        if targets["reflected_voltage_v"] is not None:lines.append(f"• QR 反射电压目标 {targets['reflected_voltage_v']:.9g} V")
        lines.extend(["",self.suggestion_scope.text()])
        if result is not None and result.warnings:
            lines.extend(["模型提示：",*result.warnings])
        lines.extend(["","应用和保存保留完整数值精度。完整目标、来源与验算记录见“模型范围与依据”和导出报告。"])
        self.suggestion_output.setPlainText("\n".join(lines))

    def apply_parameter_proposal(self):
        proposal=self.parameter_proposal
        if self._closing or proposal is None:return
        if proposal.source_design_id!=self.current_inputs().design_id:
            self._invalidate_suggestions();return
        record=proposal.record()
        self.set_inputs(proposal.inputs)
        self._applied_suggestion=record
        self._show_suggestion_summary(record,proposal.inputs,"已应用建议参数。",proposal.result)
        self._refresh_evidence()

    def _build_loop_tab(self):
        from .loop import (
            INTEGRATOR_BRANCH_NETWORKS,
            INTEGRATOR_BRANCH_NOTE,
            Compensator,
            LoopPlant,
        )
        tab=QWidget();root=QVBoxLayout(tab);split=QSplitter();root.addWidget(split,1)
        settings=QWidget();form=QFormLayout(settings)
        self.loop_control=QComboBox();self.loop_control.addItem("电压模式 VMC","vmc");self.loop_control.addItem("峰值电流模式 CMC","peak_cmc")
        form.addRow("控制方式",self.loop_control)
        self.network_combo=QComboBox()
        for name,key in [("Type II","type_ii"),("Type II 跨导","type_ii_gm"),("Type III","type_iii"),("隔离 Type II 齐纳钳位","isolated_zener"),("隔离 Type II 内环","isolated_inner")]:self.network_combo.addItem(name,key)
        self.network_combo.setCurrentIndex(2);form.addRow("补偿网络",self.network_combo)
        self.integrator_branch_button=QPushButton("采用积分支路并验算")
        self.integrator_branch_button.setObjectName("psd_integrator_branch")
        self.integrator_branch_button.setEnabled(False)
        self.integrator_branch_button.clicked.connect(self.apply_integrator_branch)
        self.network_combo.currentIndexChanged.connect(lambda: self.integrator_branch_button.setEnabled(self.network_combo.currentData() in INTEGRATOR_BRANCH_NETWORKS))
        form.addRow(self.integrator_branch_button)
        self.integrator_branch_help=QLabel(INTEGRATOR_BRANCH_NOTE)
        self.integrator_branch_help.setWordWrap(True);form.addRow(self.integrator_branch_help)

        self.loop_widgets={};self.comp_widgets={}
        # Every non-ideal baseline input is editable; names carry physical units.
        plant_fields=[("dcr_ohm","L DCR / Ω",1),("esr_ohm","C1 ESR / Ω",1),("capacitance2_f","C2 有效值 / µF",1e6),("esr2_ohm","C2 ESR / Ω",1),("ramp_v","VMC 锯齿幅度 / V",1),("sense_ohm","电流检测电阻 / Ω",1),("sense_gain","电流检测增益",1),("slope_v","附加斜坡每周期 / V",1),("slope_multiplier","斜坡倍数 SLM",1)]
        self._loop_scales={}
        for key,label,scale in plant_fields:
            box=_number(getattr(LoopPlant(),key)*scale);self.loop_widgets[key]=box;self._loop_scales[key]=scale;form.addRow(label,box)
        self.slope_mode=QComboBox();self.slope_mode.addItem("固定附加斜坡","fixed");self.slope_mode.addItem("倍数 SLM","multiplier");form.addRow("斜坡模式",self.slope_mode)
        comp_fields=[("r1_ohm","反馈上臂 R1 / kΩ",1e-3),("r_lower_ohm","反馈下臂 / kΩ",1e-3),("r2_ohm","补偿 R2 / kΩ",1e-3),("r3_ohm","前馈 R3 / kΩ",1e-3),("c1_f","补偿 C1 / nF",1e9),("c2_f","高频 C2 / nF",1e9),("c3_f","Type III C3 / nF",1e9),("cff_f","Type II 前馈 Cff / nF",1e9),("open_loop_gain_db","误差放大器 AOL / dB",1),("gbw_hz","误差放大器 GBW / MHz",1e-6),("gm_s","跨导 gm / mS",1e3),("ctr","光耦 CTR",1),("opto_pole_hz","光耦极点 / kHz",1e-3),("rled_ohm","LED 电阻 / kΩ",1e-3),("rpullup_ohm","光耦上拉 / kΩ",1e-3)]
        self._comp_scales={}
        for key,label,scale in comp_fields:
            box=_number(getattr(Compensator(),key)*scale);self.comp_widgets[key]=box;self._comp_scales[key]=scale;form.addRow(label,box)
        self.ota_output_resistance=QLineEdit();self.ota_output_resistance.setPlaceholderText("留空：按 AOL/gm 推导")
        form.addRow("OTA 输出电阻 / Ω",self.ota_output_resistance);self.ota_output_resistance.textChanged.connect(self.invalidate_loop)
        self.target_fc=_number(3000);form.addRow("目标交越 / Hz",self.target_fc)
        self.target_pm=_number(60,maximum=150);form.addRow("目标相位裕量 / °",self.target_pm)
        self.target_fc.valueChanged.connect(self.invalidate_loop);self.target_pm.valueChanged.connect(self.invalidate_loop)
        synth=QPushButton("建议元件并重新验算");synth.clicked.connect(self.synthesize_loop);form.addRow(synth)
        cancel=QPushButton("取消元件搜索");cancel.clicked.connect(self.cancel_synthesis);form.addRow(cancel)
        analyze=QPushButton("分析当前元件");analyze.setObjectName("psd_analyze_loop");analyze.clicked.connect(self.analyze_loop);form.addRow(analyze)
        for key,box in self.loop_widgets.items():box.valueChanged.connect(lambda _value,key=key:self._loop_numeric_changed(key,False))
        for key,box in self.comp_widgets.items():box.valueChanged.connect(lambda _value,key=key:self._loop_numeric_changed(key,True))
        for combo in (self.loop_control,self.network_combo,self.slope_mode):combo.currentIndexChanged.connect(self.invalidate_loop)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(settings);scroll.setMinimumWidth(330);split.addWidget(scroll)
        charts=QWidget();charts_layout=QVBoxLayout(charts)
        self.loop_status=QLabel("仅适用于所列模型的 CCM 小信号范围。");self.loop_status.setWordWrap(True);charts_layout.addWidget(self.loop_status)
        self.loop_gain_plot=pg.PlotWidget(title="Bode 幅值分解");self.loop_phase_plot=pg.PlotWidget(title="Bode 相位分解")
        for plot,label in [(self.loop_gain_plot,"幅度 / dB"),(self.loop_phase_plot,"相位 / °")]:
            plot.setLogMode(x=True);plot.setLabel("bottom","频率",units="Hz");plot.setLabel("left",label);plot.addLegend();plot.showGrid(x=True,y=True,alpha=.2);charts_layout.addWidget(plot,1)
        split.addWidget(charts);split.setStretchFactor(1,1)
        return tab

    def apply_integrator_branch(self):
        from .loop import integrator_branch_settings
        try:
            _,current=self.loop_parameters()
            selected=integrator_branch_settings(current)
        except (ValueError, TypeError) as exc:
            self.loop_status.setText("积分支路未应用："+str(exc));return
        for key in ("r2_ohm","c2_f","cff_f"):
            box=self.comp_widgets[key];box.blockSignals(True)
            box.setValue(getattr(selected,key)*self._comp_scales[key]);box.blockSignals(False)
            self._exact_comp[key]=getattr(selected,key)
        self.analyze_loop()

    def _loop_numeric_changed(self,key,comp):
        if not self._loading:(self._exact_comp if comp else self._exact_loop).pop(key,None)
        self.invalidate_loop()

    def invalidate_loop(self,*_):
        self.invalidate_corners()
        self._loop_generation+=1
        for event in self._synthesis_events:event.set()
        self.loop_result=None;self._loop_settings=None
        self.loop_gain_plot.clear();self.loop_phase_plot.clear()
        self.loop_status.setText("环路参数已改变，旧环路结果已失效。")

    def loop_parameters(self):
        from .loop import Compensator, LoopPlant
        if self.result is None:raise ValueError("请先获得有效功率级结果")
        if self.result.mode.upper() not in {"CCM","FCCM"}:raise ValueError("模拟环路只支持严格 CCM")
        inp=self.result.inputs
        mapping={"T01":"buck","T02":"boost","T03":"inverting_buck_boost","T09":"flyback","T13":"forward","T14":"forward"}
        if self.result.topology_id not in mapping:raise ValueError("该拓扑尚无匹配的模拟环路模型")
        if inp.diode_drop_v:raise ValueError("当前模拟小信号模型按理想整流器推导；请使用 Vf=0 的匹配工况")
        plant=LoopPlant(topology=mapping[self.result.topology_id],control_mode=self.loop_control.currentData(),
            vin_v=inp.vin_v,vout_v=(-inp.vout_v if self.result.topology_id=="T03" else inp.vout_v),iout_a=inp.iout_a,
            fsw_hz=inp.fsw_hz,inductance_h=inp.inductance_h,capacitance_f=inp.capacitance_f,
            turns_ratio=inp.turns_ratio_np_ns,slope_mode=self.slope_mode.currentData(),
            **{key:self._exact_loop.get(key,box.value()/self._loop_scales[key]) for key,box in self.loop_widgets.items()})
        ota_text=self.ota_output_resistance.text().strip()
        self._hidden_comp["output_resistance_ohm"]=float(ota_text) if ota_text else None
        comp=Compensator(network=self.network_combo.currentData(),**self._hidden_comp,**{key:self._exact_comp.get(key,box.value()/self._comp_scales[key]) for key,box in self.comp_widgets.items()})
        return plant,comp

    def analyze_loop(self):
        from .loop import analyze_loop
        self.invalidate_loop()
        try:
            plant,comp=self.loop_parameters();result=analyze_loop(plant,comp)
        except (ValueError,NotImplementedError,ArithmeticError) as exc:
            self.loop_status.setText(f"环路未得到有效结果：{exc}");return
        self.loop_result=result;self._loop_settings={"plant":asdict(plant),"compensator":asdict(comp)}
        st=result.stability
        fmt=lambda value: "未知" if value is None else f"{value:.6g}"
        self.loop_status.setText(f"{st.status} · 交越 {fmt(st.main_crossover_hz)} Hz · PM {fmt(st.phase_margin_deg)}° · GM {fmt(st.gain_margin_db)} dB · Ms {fmt(st.ms)}\n"+"；".join(result.warnings))
        for name,response,color in [("功率级",result.plant_response,"#0ea5e9"),("补偿器",result.compensator_response,"#f59e0b"),("误差放大器开环",result.amplifier_response,"#64748b"),("返回比",result.loop_response,"#22c55e")]:
            self.loop_gain_plot.plot(result.frequency_hz,20*np.log10(np.maximum(abs(response),1e-300)),pen=pg.mkPen(color,width=2),name=name)
            self.loop_phase_plot.plot(result.frequency_hz,np.unwrap(np.angle(response))*180/np.pi,pen=pg.mkPen(color,width=2),name=name)

    def synthesize_loop(self):
        from .loop import synthesize_compensator
        self.invalidate_loop()
        try:
            plant,comp=self.loop_parameters()
        except (ValueError,NotImplementedError,ArithmeticError) as exc:
            self.loop_status.setText(f"元件建议不可用：{exc}");return
        generation=self._loop_generation
        cancel=threading.Event();self._cancel_events.append(cancel);self._synthesis_events.append(cancel)
        fc,pm=self.target_fc.value(),self.target_pm.value()
        def compute():
            try:
                outcome=synthesize_compensator(plant,fc,pm,network=comp.network,template=comp,cancelled=cancel.is_set)
                return generation,outcome
            except InterruptedError:
                return generation,None
        worker=FunctionWorker(compute);self._jobs[generation]=worker
        worker.signals.result.connect(self._on_synthesis)
        worker.signals.error.connect(self._synthesis_error)
        worker.signals.finished.connect(lambda gen=generation,event=cancel:self._release_job(gen,event))
        self.loop_status.setText("正在搜索并验算实际元件…可继续修改输入或取消。")
        self._pool.start(worker)

    def _release_job(self,generation,event):
        self._jobs.pop(generation,None)
        if event in self._cancel_events:self._cancel_events.remove(event)
        if event in self._synthesis_events:self._synthesis_events.remove(event)
        if event in self._suggestion_events:self._suggestion_events.remove(event)

    def _background_error(self,message):
        sender=self.sender()
        key=next((key for key,worker in self._jobs.items() if worker.signals is sender),None)
        if self._closing or not isinstance(key,tuple):return
        kind,token=key
        if kind=="stage" and token==self._generation:self.status.setText("计算失败，结果不可用："+message.splitlines()[-1])
        if kind=="open" and token==(self._generation,self._loop_generation,self._sweep_generation):self.status.setText("项目未载入，当前设计保留："+message.splitlines()[-1])
        if kind=="corners" and token==self._sweep_generation:self.corner_status.setText("扫描失败，结果不可用："+message.splitlines()[-1])

    def _synthesis_error(self,message):
        sender=self.sender()
        generation=next((key for key,worker in self._jobs.items() if worker.signals is sender),None)
        if not self._closing and generation==self._loop_generation:
            self.loop_status.setText("元件搜索失败；没有应用结果。"+message.splitlines()[-1])

    def cancel_synthesis(self):
        self._loop_generation+=1
        for event in self._synthesis_events:event.set()
        self.loop_status.setText("已请求取消元件搜索；该搜索的结果不会应用。")

    def _on_synthesis(self,payload):
        generation,synthesis=payload
        if self._closing or generation!=self._loop_generation or synthesis is None:return
        if not synthesis.achievable or synthesis.compensator is None:
            self.loop_status.setText("目标不可达："+synthesis.reason);return
        for key,box in self.comp_widgets.items():box.setValue(getattr(synthesis.compensator,key)*self._comp_scales[key])
        self._exact_comp={key:getattr(synthesis.compensator,key) for key in self.comp_widgets}
        self._hidden_comp={key:value for key,value in asdict(synthesis.compensator).items() if key not in self.comp_widgets and key!="network"}
        self.analyze_loop()

    def _build_corners_tab(self):
        tab=QWidget();root=QVBoxLayout(tab);form=QFormLayout();root.addLayout(form)
        self.corner_fields={}
        for key,label,value in [("input_voltages_v","输入电压列表 / V","10, 12, 14"),("output_currents_a","负载电流列表 / A","0.5, 1.5, 3"),("inductance_factors","L 容差倍数","0.9, 1, 1.1"),("capacitance_factors","输出有效 C1 容差倍数","0.8, 1"),
                               ("secondary_inductance_factors","第二电感 L2 容差倍数","1"),
                               ("loop_capacitance2_factors","环路输出有效 C2 容差倍数","1"),
                               ("loop_esr_factors","环路 ESR1 容差倍数","1"),
                               ("loop_esr2_factors","环路 ESR2 容差倍数","1"),
                               ("loop_dcr_factors","环路电感 DCR 容差倍数","1")]:
            line=QLineEdit(value);self.corner_fields[key]=line;form.addRow(label,line);line.textChanged.connect(self.invalidate_corners)
        note=QLabel("仅评估显式组合点（全部维度合计最多500点），每项指标分别给出极值。倍数乘以各自当前有效标称值；保持同一个补偿器。L2 仅用于 SEPIC/ZETA/Ćuk/串联电容 Buck。环路 C2/ESR/DCR 只影响环路，不改变理想功率级波形；非1倍数要求已计算匹配环路及非零标称值。若选器件，导通损耗按100°C估算；完整损耗仍未知。")
        note.setWordWrap(True);root.addWidget(note)
        row=QHBoxLayout();run=QPushButton("扫描所列工况");run.clicked.connect(self.run_corners);row.addWidget(run)
        cancel=QPushButton("取消扫描");cancel.clicked.connect(self.cancel_corners);row.addWidget(cancel);root.addLayout(row)
        self.corner_status=QLabel("尚未扫描");self.corner_status.setWordWrap(True);root.addWidget(self.corner_status)
        self.corner_table=QTableWidget(0,5);self.corner_table.setHorizontalHeaderLabels(["指标","最小值","最小工况","最大值","最大工况"])
        self.corner_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);root.addWidget(self.corner_table,1)
        return tab

    def invalidate_corners(self,*_):
        self._sweep_generation+=1;self.corner_sweep=None
        if hasattr(self,"_sweep_cancel"):self._sweep_cancel.set()
        if hasattr(self,"corner_table"):
            self.corner_table.setRowCount(0);self.corner_status.setText("工况或设计已改变；请重新扫描。")

    def cancel_corners(self):
        self.invalidate_corners();self.corner_status.setText("已取消扫描；旧扫描结果不会应用。")

    def _parse_corner_request(self):
        from .corners import CornerRequest, validate_corner_request
        request=CornerRequest(**{key:tuple(float(value.strip()) for value in line.text().split(',')) for key,line in self.corner_fields.items()})
        validate_corner_request(request)
        return request

    def run_corners(self):
        from .corners import analyze_corners
        self.invalidate_corners()
        if self.result is None:
            self.corner_status.setText("请先获得有效功率级结果");return
        try:
            request=self._parse_corner_request()
        except ValueError as exc:
            self.corner_status.setText("扫描输入无效，请核对逗号分隔的数字及组合数："+str(exc));return
        self._sweep_request=request;self._sweep_enabled=True
        token=self._sweep_generation;base=self.result.inputs;settings=self._loop_settings
        selected=self.device_combo.currentData();event=threading.Event();self._sweep_cancel=event
        self._cancel_events.append(event)
        def compute():
            try:return token,analyze_corners(base,request,loop_settings=settings,selected_device=selected,cancelled=event.is_set),None
            except (ValueError,InterruptedError) as exc:return token,None,str(exc)
        worker=FunctionWorker(compute);job_key=("corners",token);self._jobs[job_key]=worker
        worker.signals.result.connect(self._on_corners)
        worker.signals.error.connect(self._background_error)
        worker.signals.finished.connect(lambda:self._release_job(job_key,event))
        self.corner_status.setText("正在计算所列工况…");self._pool.start(worker)

    def _on_corners(self,payload):
        token,result,error=payload
        if self._closing or token!=self._sweep_generation:return
        if error:
            self.corner_status.setText("扫描未完成："+error);return
        self.corner_sweep=result
        valid=sum(point.error is None for point in result.points);loop_invalid=sum(point.loop_error is not None for point in result.points)
        self.corner_status.setText(f"已评估{len(result.points)}个显式工况，有效功率级{valid}，环路不适用/失败{loop_invalid}。未扫描区域与完整损耗保持未知。")
        self.corner_table.setRowCount(len(result.extrema))
        def label(index):
            point=result.points[index];inp=point.inputs;plant=point.loop_plant
            text=f"{index+1}: {inp.vin_v:g}V / {inp.iout_a:g}A / L={inp.inductance_h*1e6:g}µH / C1={inp.capacitance_f*1e6:g}µF"
            if inp.secondary_inductance_h is not None:
                text+=f" / L2={inp.secondary_inductance_h*1e6:g}µH"
            if plant is not None:
                text+=f"\n环路 C2={plant.capacitance2_f*1e6:g}µF / ESR1={plant.esr_ohm:g}Ω / ESR2={plant.esr2_ohm:g}Ω / DCR={plant.dcr_ohm:g}Ω"
            return text
        for row,(key,bounds) in enumerate(result.extrema.items()):
            lo,hi=bounds['minimum'],bounds['maximum']
            metric_labels={"duty":"占空比", "output_ripple_pp_v":"输出纹波峰峰值 / V", "current_rms_a":"电流有效值 / A", "current_max_a":"最大电流 / A", "current_min_a":"最小电流 / A", "voltage_max_v":"最大电压 / V", "voltage_min_v":"最小电压 / V", "known_conduction_loss_w":"已知导通损耗 / W", "phase_margin_deg":"相位裕量 / °", "gain_margin_db":"增益裕量 / dB", "crossover_hz":"交越频率 / Hz", "ms":"灵敏度峰值 Ms"}
            owner,separator,metric=key.partition(".")
            display=("环路 · " if owner=="loop" else owner+" · ")+metric_labels.get(metric,metric) if separator else metric_labels.get(key,key)
            for col,value in enumerate([display,f"{lo['value']:.7g}",label(lo['point_index']),f"{hi['value']:.7g}",label(hi['point_index'])]):self.corner_table.setItem(row,col,QTableWidgetItem(value))
            self.corner_table.item(row,0).setToolTip("指标标识："+key)
            for column,bound in ((2,lo),(4,hi)):
                item=self.corner_table.item(row,column)
                item.setToolTip(item.text()+"\n工况身份："+bound["point_id"])
        self.corner_table.resizeRowsToContents()

    def save_to(self,path):
        if self.result is None:raise ValueError("当前没有有效结果，不能保存过期设计")
        device=self.device_combo.currentData()
        return save_project(self.result.inputs,path,loop_settings=self._loop_settings,selected_device=asdict(device) if device else None,corner_request=asdict(self._parse_corner_request()) if self._sweep_enabled else None,parameter_suggestion=self._applied_suggestion)

    def open_from(self,path):
        inputs,document=read_project(path)
        self._restore_project(inputs,document)

    def _restore_project(self,inputs,document):
        self._restore_corner_request_pending=False;self._sweep_enabled=False;self._sweep_request=None
        self.set_inputs(inputs)
        self._applied_suggestion=document.get("parameter_suggestion")
        if self._applied_suggestion is not None:
            targets=self._applied_suggestion["targets"]
            for widget,key in ((self.suggestion_ripple,"ripple_pp_a"),(self.suggestion_duty,"max_duty"),
                (self.suggestion_second,"secondary_ripple_pp_a"),(self.suggestion_magnetizing,"magnetizing_ripple_pp_a"),
                (self.suggestion_reflected,"reflected_voltage_v")):
                if targets[key] is not None or key=="magnetizing_ripple_pp_a":
                    widget.blockSignals(True)
                    widget.setValue(targets[key] or 0)
                    widget.blockSignals(False)
            self.suggestion_ratio.blockSignals(True);self.suggestion_ratio.setChecked(targets["size_turns_ratio"]);self.suggestion_ratio.blockSignals(False)
            status="已恢复应用过的建议；当前结果已重新计算。" if self.result is not None else "已恢复应用过的建议；正在核对重算结果。"
            self._show_suggestion_summary(self._applied_suggestion,inputs,status,self.result)
            self._refresh_evidence()
        saved=document.get("loop_settings")
        if saved:
            self.loop_control.setCurrentIndex(self.loop_control.findData(saved["plant"]["control_mode"]))
            self.slope_mode.setCurrentIndex(self.slope_mode.findData(saved["plant"]["slope_mode"]))
            self.network_combo.setCurrentIndex(self.network_combo.findData(saved["compensator"]["network"]))
            for key,box in self.loop_widgets.items():box.setValue(saved["plant"][key]*self._loop_scales[key])
            for key,box in self.comp_widgets.items():box.setValue(saved["compensator"][key]*self._comp_scales[key])
            self._hidden_comp={key:value for key,value in saved["compensator"].items() if key not in self.comp_widgets and key!="network"}
            ota=self._hidden_comp.get("output_resistance_ohm")
            self.ota_output_resistance.setText("" if ota is None else format(ota,".17g"))
            self._exact_loop={key:saved["plant"][key] for key in self.loop_widgets}
            self._exact_comp={key:saved["compensator"][key] for key in self.comp_widgets}
            self.analyze_loop()
        selected=document.get("selected_device")
        if selected:
            from .project import _validate_device
            device=_validate_device(selected)
            # Restore the saved parameter snapshot even if the library changed.
            self.device_combo.addItem(device.part_number+"（项目快照）",device)
            self.device_combo.setCurrentIndex(self.device_combo.count()-1)
        else:self.device_combo.setCurrentIndex(0)
        request=document.get("corner_request")
        if request:
            for key,line in self.corner_fields.items():line.setText(", ".join(format(value,".17g") for value in request[key]))
            if self.result is None:self._restore_corner_request_pending=True
            else:self.run_corners()

    def report_to(self,path):
        if self.result is None:raise ValueError("当前没有有效结果，不能导出旧报告")
        device=self.device_combo.currentData()
        return write_report(self.result,path,loop_result=self.loop_result,loop_settings=self._loop_settings,
                            selected_device=asdict(device) if device else None,device_assessment=self.device_assessment(),corner_sweep=self.corner_sweep,parameter_suggestion=self._applied_suggestion)

    def _modal_revision(self):
        if self._closing:
            return None
        # A native chooser runs a nested event loop. Bind the click to all
        # project/report inputs and accepted results, not its eventual path.
        return (self._generation,self._loop_generation,self._sweep_generation,
                id(self.result),id(self.loop_result),id(self.corner_sweep),id(self._applied_suggestion))

    def _save_dialog(self):
        token=self._modal_revision()
        if token is None or self.result is None:
            return
        path,_=QFileDialog.getSaveFileName(self,"保存可重算项目","power-stage.json","JSON (*.json)")
        if path and token==self._modal_revision():self._guard(lambda:self._start_project_save(path))

    def _start_project_save(self,path):
        if self.result is None:raise ValueError("当前没有有效结果，不能保存过期设计")
        token=(self._generation,self._loop_generation,self._sweep_generation)
        inputs=self.result.inputs;settings=self._loop_settings
        suggestion=self._applied_suggestion
        selected=self.device_combo.currentData()
        device=asdict(selected) if selected else None
        request=asdict(self._parse_corner_request()) if self._sweep_enabled else None
        event=threading.Event();self._cancel_events.append(event)
        def compute():
            try:
                document=project_document(inputs,loop_settings=settings,selected_device=device,
                    corner_request=request,parameter_suggestion=suggestion,cancelled=event.is_set)
                return token,path,document,None
            except (ValueError,OSError,InterruptedError) as exc:return token,path,None,str(exc)
        worker=FunctionWorker(compute);key=("save",id(worker));self._jobs[key]=worker
        worker.signals.result.connect(self._on_project_saved)
        worker.signals.error.connect(lambda message,token=token:self._project_save_error(token,message))
        worker.signals.finished.connect(lambda:self._release_job(key,event))
        self.status.setText("正在校验保存快照…输入改变后旧快照不会写入文件。")
        self._pool.start(worker)

    def _project_save_error(self,token,message):
        if not self._closing and token==(self._generation,self._loop_generation,self._sweep_generation):
            self.status.setText("项目未保存："+message.splitlines()[-1])

    def _on_project_saved(self,payload):
        token,path,document,error=payload
        if self._closing or token!=(self._generation,self._loop_generation,self._sweep_generation):return
        if error:self.status.setText("项目未保存："+error);return
        try:write_json(document,path)
        except (ValueError,OSError) as exc:self.status.setText("项目未保存："+str(exc));return
        self.status.setText("项目已保存："+str(path))

    def _open_dialog(self):
        token=self._modal_revision()
        if token is None:
            return
        path,_=QFileDialog.getOpenFileName(self,"打开项目","","JSON (*.json)")
        if path and token==self._modal_revision():self._start_project_open(path)

    def _start_project_open(self,path):
        self._invalidate_suggestions()
        self._generation+=1;token=(self._generation,self._loop_generation,self._sweep_generation)
        for cancellation in self._cancel_events:cancellation.set()
        event=threading.Event();self._cancel_events.append(event)
        def compute():
            try:return token,read_project(path,cancelled=event.is_set),None
            except (ValueError,OSError,InterruptedError) as exc:return token,None,str(exc)
        worker=FunctionWorker(compute);key=("open",token);self._jobs[key]=worker
        worker.signals.result.connect(self._on_project_opened);worker.signals.error.connect(self._background_error)
        worker.signals.finished.connect(lambda:self._release_job(key,event))
        self.status.setText("正在校验并重算项目；校验成功后替换当前设计。")
        self._pool.start(worker)

    def _on_project_opened(self,payload):
        token,value,error=payload
        if self._closing or token!=(self._generation,self._loop_generation,self._sweep_generation):return
        if error:self.status.setText("项目未载入，当前设计保留："+error);return
        self._restore_project(*value)

    def _report_dialog(self):
        token=self._modal_revision()
        if token is None or self.result is None:
            return
        path,_=QFileDialog.getSaveFileName(self,"导出可打印报告","power-stage-report.html","HTML (*.html)")
        if path and token==self._modal_revision():self._guard(lambda:self.report_to(path))

    def _csv_dialog(self):
        result=self.result
        generation=self._generation
        if self._closing or result is None:return
        path,_=QFileDialog.getSaveFileName(self,"导出当前波形","power-stage-waveforms.csv","CSV (*.csv)")
        if path and not self._closing and generation==self._generation and result is self.result:
            self._guard(lambda:write_waveform_csv(result,path))

    def _guard(self,action):
        try:action()
        except (OSError,ValueError,TypeError) as exc:QMessageBox.warning(self,"操作未完成",str(exc))

    def showEvent(self,event):
        self._closing=False
        super().showEvent(event)

    def closeEvent(self,event):
        self._invalidate_suggestions()
        self._timer.stop();self._generation+=1;self._loop_generation+=1;self._closing=True
        for cancellation in self._cancel_events:cancellation.set()
        self._pool.waitForDone()
        if not self.toolbox.stop_background_work():
            self._closing=False
            self.status.setText("工具箱计算仍在退出；请稍后再次关闭。")
            event.ignore();return
        super().closeEvent(event)
