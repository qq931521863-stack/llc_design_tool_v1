"""Explicit optional-physics inputs; the default ideal baseline stays unchanged."""
from __future__ import annotations

import csv
import io

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .psfb_dynamics import PSFBOptions
from .qr_dynamics import QROptions
from .two_switch_dynamics import T05Options


class AdvancedModelWidget(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self._loading = False
        self._topology = None
        self._exact = {}
        root = QVBoxLayout(self)
        note = QLabel("这些是明确参数的独立扩展模型。默认不启用；参数示例不代表实际器件或 TI 控制器。计算采用所选电路和控制策略，未知损耗仍须单独核对。")
        note.setWordWrap(True); root.addWidget(note)
        self.enabled = QCheckBox("启用当前拓扑的扩展物理模型")
        self.enabled.toggled.connect(self._changed); root.addWidget(self.enabled)
        self.t05 = QGroupBox("两开关 Buck-Boost：有限 Coss / Ron / 二极管与浮空节点")
        self.t05_form=QFormLayout(self.t05); self.t05_widgets={}
        self.t05_fields=[('coss_q1_f','Q1 Coss / nF',1e9),('coss_q2_f','Q2 Coss / nF',1e9),
            ('ron_q1_ohm','Q1 Ron / Ω',1),('ron_q2_ohm','Q2 Ron / Ω',1),
            ('diode_resistance_ohm','整流二极管动态电阻 / Ω',1),
            ('body_diode_drop_v','体二极管压降 / V',1),('body_diode_resistance_ohm','体二极管动态电阻 / Ω',1),
            ('inductor_resistance_ohm','电感绕组电阻 / Ω',1),('relative_tolerance','求解相对容差 / ppm',1e6)]
        for key,label,scale in self.t05_fields:
            widget=self._number(getattr(T05Options(),key)*scale)
            widget.valueChanged.connect(lambda _v,key=key:self._edited('t05.'+key))
            widget.lineEdit().textEdited.connect(lambda _text,key=key:self._text_edited('t05.'+key))
            self.t05_widgets[key]=widget;self.t05_form.addRow(label,widget)
        self.t05_iterations=QSpinBox();self.t05_iterations.setRange(1,200);self.t05_iterations.setValue(40)
        self.t05_iterations.valueChanged.connect(self._changed);self.t05_form.addRow('最大周期搜索迭代',self.t05_iterations)
        self.t05_source=QLineEdit(T05Options().parameter_source);self.t05_source.setMaxLength(1000)
        self.t05_source.textChanged.connect(self._changed);self.t05_form.addRow('参数来源 / 假设',self.t05_source)
        self.t05_form.addRow(QLabel('两只开关同时驱动；节点电容和体二极管分别显示。版本 t05-switched-1。'))
        root.addWidget(self.t05)
        self.qr=QGroupBox('QR Flyback：真实后续谷底、延迟与节点电荷')
        form=QFormLayout(self.qr)
        self.qr_policy=QComboBox();self.qr_policy.addItem('满足频率上限的最早谷底','first_feasible');self.qr_policy.addItem('指定谷底序号','selected')
        self.qr_policy.currentIndexChanged.connect(self._changed);form.addRow('谷底选择策略',self.qr_policy)
        self.qr_valley=QSpinBox();self.qr_valley.setRange(1,100);self.qr_valley.setValue(1)
        self.qr_max_valley=QSpinBox();self.qr_max_valley.setRange(1,100);self.qr_max_valley.setValue(100)
        for label,w in [('目标谷底',self.qr_valley),('最多搜索谷底',self.qr_max_valley)]:
            w.valueChanged.connect(self._changed);form.addRow(label,w)
        self.qr_widgets={}
        self.qr_fields=[('detector_delay_s','检测延迟 / ns',1e9),('gate_delay_s','门极延迟 / ns',1e9),('on_resistance_ohm','开通电阻 Ron / Ω',1)]
        self.qr_finite_ron=QCheckBox('求解有限 Ron 开通放电电流（未选时理想脉冲 RMS 未定义）')
        self.qr_finite_ron.toggled.connect(self._changed);form.addRow(self.qr_finite_ron)
        self.qr_root=QComboBox();self.qr_root.addItem('较低峰值电流根','lower_peak');self.qr_root.addItem('较高峰值电流根（有限 Ron）','higher_peak')
        self.qr_root.currentIndexChanged.connect(self._changed);form.addRow('有限 Ron 功率平衡根',self.qr_root)
        for key,label,scale in self.qr_fields:
            w=self._number((getattr(QROptions(),key) or (.1 if key=='on_resistance_ohm' else 0))*scale)
            w.valueChanged.connect(lambda _v,key=key:self._edited('qr.'+key));self.qr_widgets[key]=w;form.addRow(label,w)
            w.lineEdit().textEdited.connect(lambda _text,key=key:self._text_edited('qr.'+key))
        self.qr_capacitance=QComboBox()
        for label,key in [('常数 Cnode（左侧谐振电容）','constant'),('电荷曲线 Q(V)','charge'),('微分电容曲线 C(V)','differential')]:self.qr_capacitance.addItem(label,key)
        self.qr_capacitance.currentIndexChanged.connect(self._changed);form.addRow('节点电容模型',self.qr_capacitance)
        self.qr_table=QPlainTextEdit();self.qr_table.setPlaceholderText('每行两列，英文逗号分隔：电压 V, 电荷 C 或电容 F\n从 V=0 开始，覆盖 Vin+n(Vo+Vf)，禁止外推。')
        self.qr_table.setMaximumHeight(130);self.qr_table.textChanged.connect(self._changed);form.addRow('曲线数据（SI 单位）',self.qr_table)
        self.qr_source=QLineEdit(QROptions().capacitance_source);self.qr_source.setMaxLength(2000)
        self.qr_source.textChanged.connect(self._changed);form.addRow('数据来源 / 假设',self.qr_source)
        form.addRow(QLabel('版本 qr-dynamic-1；零压体二极管钳位、释放及后续振荡按状态求解。'))
        root.addWidget(self.qr)
        self.psfb=QGroupBox('PSFB：有限电容换流 / 分桥臂 ZVS')
        form=QFormLayout(self.psfb);self.psfb_widgets={}
        self.psfb_fields=[('coss_f','每只开关恒定 Coss / pF',1e12,300e-12),
            ('r_on_ohm','每只开关 Ron / Ω',1,.15),('body_diode_drop_v','体二极管压降 / V',1,.8),
            ('body_diode_resistance_ohm','体二极管动态电阻 / Ω',1,.05),
            ('transformer_capacitance_f','原边并联寄生电容 / pF',1e12,100e-12),
            ('rectifier_capacitance_f','整流节点寄生电容 / nF',1e9,1e-9),
            ('rectifier_resistance_ohm','副边二极管动态电阻 / Ω',1,.02),
            ('relative_tolerance','求解相对容差 / ppm',1e6,1e-8),
            ('zvs_voltage_tolerance_v','模型 ZVS 剩余电压上限 / V',1,1.)]
        for key,label,scale,default in self.psfb_fields:
            w=self._number(default*scale);w.valueChanged.connect(lambda _v,key=key:self._edited('psfb.'+key))
            w.lineEdit().textEdited.connect(lambda _text,key=key:self._text_edited('psfb.'+key))
            self.psfb_widgets[key]=w;form.addRow(label,w)
        self.psfb_iterations=QSpinBox();self.psfb_iterations.setRange(10,500);self.psfb_iterations.setValue(80)
        self.psfb_iterations.valueChanged.connect(self._changed);form.addRow('周期搜索最大评估次数',self.psfb_iterations)
        self.psfb_source=QLineEdit('Illustrative circuit constants; user must supply verified component data')
        self.psfb_source.setMaxLength(1000);self.psfb_source.textChanged.connect(self._changed);form.addRow('参数来源 / 假设',self.psfb_source)
        note=QLabel('上述预填值仅作独立电路示例。须在左侧明确输入正死区、Lm 与漏感；只支持所声明的正负载 CCM。ZVS 判定基于实际门极开通时的节点电压/方向，不代表硬件验证。版本 psfb-commutation-1。')
        note.setWordWrap(True);form.addRow(note);root.addWidget(self.psfb);root.addStretch()
        self.set_topology('T01')

    @staticmethod
    def _number(value):
        w=QDoubleSpinBox();w.setDecimals(12);w.setRange(0,1e12);w.setValue(value);return w

    def _edited(self,key):
        if not self._loading:self._exact.pop(key,None)
        self._changed()

    def _text_edited(self,key):
        # Retyping the rounded display may not emit valueChanged. An explicit
        # user edit relinquishes the restored precision, exactly once.
        if not self._loading and key in self._exact:self._edited(key)

    def _changed(self,*_):
        if not self._loading:self.changed.emit()

    def set_topology(self, topology):
        if topology != self._topology:
            previous=self._loading;self._loading=True
            self.enabled.setChecked(False);self._loading=previous
        self._topology=topology
        self.enabled.setEnabled(topology in {'T05','T11','T18'})
        self.t05.setVisible(topology=='T05');self.qr.setVisible(topology=='T11');self.psfb.setVisible(topology=='T18')

    def options(self):
        if not self.enabled.isChecked():return {}
        if self._topology=='T05':
            values={key:self._exact.get('t05.'+key,self.t05_widgets[key].value()/scale) for key,_,scale in self.t05_fields}
            return {'two_switch_options':T05Options(**values,max_iterations=self.t05_iterations.value(),parameter_source=self.t05_source.text())}
        if self._topology=='T11':
            values={key:self._exact.get('qr.'+key,self.qr_widgets[key].value()/scale) for key,_,scale in self.qr_fields}
            if not self.qr_finite_ron.isChecked():values['on_resistance_ohm']=None
            text=self.qr_table.toPlainText().strip()
            try:
                table=tuple(tuple(float(item.strip()) for item in row) for row in csv.reader(io.StringIO(text)) if row)
            except ValueError as exc:
                raise ValueError('QR 曲线必须是两列有限数值，使用英文逗号分隔') from exc
            return {'qr_options':QROptions(**values,valley_policy=self.qr_policy.currentData(),valley_index=self.qr_valley.value(),
                maximum_valley_index=self.qr_max_valley.value(),resistive_root_policy=self.qr_root.currentData(),capacitance_model=self.qr_capacitance.currentData(),
                capacitance_table=table,capacitance_source=self.qr_source.text())}
        if self._topology=='T18':
            values={key:self._exact.get('psfb.'+key,self.psfb_widgets[key].value()/scale) for key,_,scale,_ in self.psfb_fields}
            return {'psfb_options':PSFBOptions(**values,max_nfev=self.psfb_iterations.value(),parameter_source=self.psfb_source.text())}
        raise ValueError('当前拓扑没有此扩展模型')

    def restore(self, inputs):
        self._loading=True
        try:
            self.set_topology(inputs.topology)
            self.enabled.setChecked(inputs.two_switch_options is not None or inputs.qr_options is not None or inputs.psfb_options is not None)
            if inputs.two_switch_options is not None:
                o=inputs.two_switch_options
                for key,_,scale in self.t05_fields:
                    self.t05_widgets[key].setValue(getattr(o,key)*scale);self._exact['t05.'+key]=getattr(o,key)
                self.t05_iterations.setValue(o.max_iterations);self.t05_source.setText(o.parameter_source)
            if inputs.qr_options is not None:
                o=inputs.qr_options
                for key,_,scale in self.qr_fields:
                    value=getattr(o,key)
                    if value is not None:self.qr_widgets[key].setValue(value*scale);self._exact['qr.'+key]=value
                self.qr_policy.setCurrentIndex(self.qr_policy.findData(o.valley_policy));self.qr_valley.setValue(o.valley_index)
                self.qr_root.setCurrentIndex(self.qr_root.findData(o.resistive_root_policy))
                self.qr_max_valley.setValue(o.maximum_valley_index);self.qr_finite_ron.setChecked(o.on_resistance_ohm is not None)
                self.qr_capacitance.setCurrentIndex(self.qr_capacitance.findData(o.capacitance_model))
                self.qr_table.setPlainText('\n'.join(f'{v:.17g},{q:.17g}' for v,q in o.capacitance_table))
                self.qr_source.setText(o.capacitance_source)
            if inputs.psfb_options is not None:
                o=inputs.psfb_options
                for key,_,scale,_ in self.psfb_fields:
                    self.psfb_widgets[key].setValue(getattr(o,key)*scale);self._exact['psfb.'+key]=getattr(o,key)
                self.psfb_iterations.setValue(o.max_nfev);self.psfb_source.setText(o.parameter_source)
        finally:self._loading=False
