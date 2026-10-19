"""One desktop toolbox tab, calling the shared SI-unit numerical calculators.

Forms preserve explicit empty/unknown FET values, invalidate results on every
edit, and export only the current calculator result. No formula lives in Qt.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from llc_design import __version__
from llc_design.control.design_session import strict_json_loads
from llc_design.gui.workers import FunctionWorker
from llc_design.models.devices import DeviceDatabase

from .models import ModelDomainError
from .project import write_json
from .toolbox import (
    MODEL_VERSION,
    UNIT_CATEGORIES,
    CalculatorResult,
    CapacitorBranch,
    FETOperatingPoint,
    FETParameters,
    FilterInputs,
    acdc_bulk_capacitor,
    analog_scaling,
    capacitor_current_sharing,
    compare_fets,
    convert_units,
    design_divider,
    differential_filter,
    digital_scaling,
    divider_output,
    fet_losses,
    fet_parameters_from_device,
    hold_up_capacitance,
    load_step_capacitance,
    rc_snubber,
    rcd_snubber,
    stage_import_values,
)
from .toolbox_presentation import choice_label, details_text, result_text, status_label

INPUT_SCHEMA = "psd-toolbox-inputs-2"
MAX_INPUT_BYTES = 256_000
AUTO_UPDATE_DELAY_MS = 75


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    default: object
    scale: float = 1.0  # displayed = SI * scale
    options: tuple = ()
    optional: bool = False
    kind: str = "number"


def number(key, label, default, scale=1., optional=False):
    return Field(key, label, default, scale, optional=optional)


def choice(key, label, default, options):
    return Field(key, label, default, options=tuple(options), kind="choice")


FET_OP_FIELDS = [number("rms_current_a", "整周期电流 RMS / A", 2), number("turn_on_current_a", "开通瞬间电流 / A", 2),
    number("turn_off_current_a", "关断瞬间电流 / A", 4), number("drain_voltage_v", "关断电压 Vds / V", 48),
    number("frequency_hz", "开关频率 / kHz", 100e3, 1e-3), choice("role", "FET 角色", "main", ["main", "sr"]),
    number("dead_time_on_s", "开通死区 / ns", 50e-9, 1e9), number("dead_time_off_s", "关断死区 / ns", 50e-9, 1e9)]
FET_DEVICE_FIELDS = [Field("name", "器件名称", "Manual example", kind="text"),
    number("rds_on_ohm", "指定温度/驱动 Rds(on) / mΩ", .02, 1e3, True),
    number("gate_voltage_v", "栅极驱动电压 / V", 10, optional=True),
    number("gate_charge_c", "总栅电荷 Qg / nC", 30e-9, 1e9, True),
    number("coss_f", "Coss / pF（与 Eoss 二选一）", 100e-12, 1e12, True),
    number("eoss_j", "Eoss / µJ（留空=未知）", None, 1e6, True),
    number("qgs_c", "Qgs / nC", 8e-9, 1e9, True), number("qg_threshold_c", "Qg(th) / nC", 3e-9, 1e9, True),
    number("qgd_c", "Qgd / nC", 10e-9, 1e9, True), number("gate_resistance_ohm", "总栅电阻 / Ω", 4, optional=True),
    number("miller_voltage_v", "米勒平台电压 / V", 4, optional=True),
    number("threshold_voltage_v", "阈值电压 / V", 2, optional=True),
    number("body_diode_drop_v", "体二极管正向压降 / V", .8, optional=True),
    number("temperature_c", "结温条件 / °C", 100, optional=True),
    Field("source", "器件参数来源", "Manual demonstration values; replace with selected-device datasheet/measurement", kind="text")]


def _fet_fields():
    result = FET_OP_FIELDS + [Field("compare", "比较 A / B 器件", False, kind="bool")]
    for prefix in ("a", "b"):
        for field in FET_DEVICE_FIELDS:
            result.append(replace(field, key=prefix+"_"+field.key, label=prefix.upper()+" · "+field.label,
                                  default=("Manual "+prefix.upper()) if field.key == "name" else field.default))
    return result


def _fet_run(v):
    op = FETOperatingPoint(**{field.key: v[field.key] for field in FET_OP_FIELDS})
    devices = [FETParameters(**{field.key: v[prefix+"_"+field.key] for field in FET_DEVICE_FIELDS})
               for prefix in (("a", "b") if v["compare"] else ("a",))]
    return compare_fets(op, devices) if v["compare"] else fet_losses(op, devices[0])


def _sharing_run(v):
    count = v.pop("branch_count")
    branches = [CapacitorBranch(v[f"c{k}"], v[f"esr{k}"], v[f"esl{k}"], f"C{k}") for k in range(1, count+1)]
    return capacitor_current_sharing(branches, v["total_rms_a"], v["frequency_hz"])


def _load_run(v):
    if v["control"] == "CMC":
        v["inductance_h"] = None
    return load_step_capacitance(**v)


def _divider_run(v):
    if v.pop("operation") == "evaluate":
        return divider_output(v["reference_v"], v["high_ohm"], v["low_ohm"],
            resistor_tolerance=v["resistor_tolerance"], reference_tolerance=v["reference_tolerance"], target_v=v["target_v"])
    return design_divider(**{key: value for key, value in v.items() if key not in ("high_ohm", "low_ohm")})


def _units_run(v):
    return convert_units(**v)


def _filter_run(v):
    # LC has no source-side capacitor. None records an absent component rather
    # than silently feeding hidden editor values or a fabricated capacitance.
    if v["configuration"] == "lc":
        for key in ("filter_capacitance_f", "filter_esr_ohm", "filter_esl_h"):
            v.setdefault(key, None)
    return differential_filter(FilterInputs(**v))


# Tool definitions are UI adapters only: all computation is in toolbox.py.
TOOLS: dict[str, tuple[str, str, list[Field], Callable]] = {
    "X01": ("FET 损耗与器件比较", "主开关 / SR 模型；空白器件数据保持未知。内置器件仅参考数据，导入不补造栅电荷。", _fet_fields(), _fet_run),
    "X02": ("负载阶跃电容", "TI VMC / CMC 两种闭环估算。L 仅用于 VMC；PM 建议 ≥60°，电解 ≥65°。", [
        choice("control", "控制方式", "CMC", ["CMC", "VMC"]), number("delta_current_a", "负载变化 ΔI / A", 2.5),
        number("delta_voltage_v", "允许电压变化 ΔV / V", .15), number("crossover_hz", "交越频率 / kHz", 10e3, 1e-3),
        number("phase_margin_deg", "相位裕量 / °", 60), number("esr_ohm", "电容 ESR / mΩ", .05, 1e3),
        number("inductance_h", "VMC 电感 / µH", 47e-6, 1e6)], _load_run),
    "X03": ("并联电容电流分配", "单频正弦 RMS 电流，三支路 C/ESR/ESL。复数电流满足 KCL；幅值不能直接相加。", [
        choice("branch_count", "电容支路数", 3, [2, 3]), number("total_rms_a", "单频总电流 / A RMS", 3),
        number("frequency_hz", "分析频率 / kHz", 100e3, 1e-3),
        *[field for k in range(1,4) for field in (number(f"c{k}", f"C{k} 有效容量 / µF", (10 if k == 3 else 100)*1e-6, 1e6),
             number(f"esr{k}", f"C{k} ESR / mΩ", .01*k, 1e3), number(f"esl{k}", f"C{k} ESL / nH", 2e-9*k, 1e9))]], _sharing_run),
    "X04": ("AC/DC 整流母线电容", "理想全波整流，恒功率放电。RMS 遵循印刷式13；图示 2.49 A 与公式 2.622526 A 差异明确保留。", [
        number("input_power_w", "从母线取走的功率 / W", 100), number("minimum_voltage_v", "母线最低电压 / V", 85),
        number("ripple_fraction", "电压纹波 / %", .2, 100), number("line_frequency_hz", "最低工频 / Hz", 47)], lambda v: acdc_bulk_capacitor(**v)),
    "X05": ("Flyback RCD 吸收", "漏感耗散钳位；Vsn 相对输入母线。需要用户实测/设计漏感，不能导入励磁电感冒充漏感。", [
        number("leakage_inductance_h", "原边漏感 / µH", 2e-6, 1e6), number("peak_primary_current_a", "原边关断峰值 / A", 3),
        number("frequency_hz", "开关频率 / kHz", 100e3, 1e-3), number("reflected_voltage_v", "反射电压 n(Vo+Vf) / V", 100),
        number("clamp_ratio", "钳位 / 反射电压比 (>1)", 1.5), number("ripple_fraction", "钳位纹波 / %", .05, 100)], lambda v: rcd_snubber(**v)),
    "X06": ("RC 吸收", "测量加电容前后振铃，f0 > f1。R/C 为起始值；附加功耗估算假设完整硬开关充放电。", [
        number("ringing_frequency_hz", "原始振铃 f0 / MHz", 30e6, 1e-6), number("loaded_frequency_hz", "加电容后 f1 / MHz", 15e6, 1e-6),
        number("added_capacitance_f", "试验附加电容 / pF", 300e-12, 1e12),
        number("switching_frequency_hz", "开关频率 / kHz（可空）", 100e3, 1e-3, True),
        number("voltage_swing_v", "电压摆幅 / V（可空）", 48, optional=True)], lambda v: rc_snubber(**v)),
    "X07": ("输出分压与标准阻值", "正算/反算，E24/E48/E96，未知臂可选两电阻并联。最终实际阻值重新计算输出与容差。", [
        choice("operation", "操作", "design", ["design", "evaluate"]), number("reference_v", "参考电压 / V", 2.5),
        number("target_v", "目标输出电压 / V", 12), choice("known_arm", "反算已知臂", "low", ["low", "high"]),
        number("known_resistance_ohm", "已知臂 / kΩ", 10e3, 1e-3), choice("series", "未知臂标准系列", None, [None, "E24", "E48", "E96"]),
        Field("parallel", "未知臂使用两电阻并联", False, kind="bool"), number("high_ohm", "正算上臂 / kΩ", 38e3, 1e-3),
        number("low_ohm", "正算下臂 / kΩ", 10e3, 1e-3), number("resistor_tolerance", "电阻容差 / %", .01, 100),
        number("reference_tolerance", "参考电压容差 / %", .01, 100)], _divider_run),
    "X08": ("模拟调压网络", "R1 上臂，R2 下臂，R3 接 Vadj。调整电压 0..Vadj,max 对应输出从最大下降至最小。", [
        number("minimum_output_v", "最低输出电压 / V", 5), number("maximum_output_v", "最高输出电压 / V", 12),
        number("reference_v", "参考电压 / V", 1.25), number("maximum_adjust_v", "最大调整电压 / V", 3.3),
        number("top_resistance_ohm", "上臂 R1 / kΩ", 100e3, 1e-3)], lambda v: analog_scaling(**v)),
    "X09": ("数字档位调压网络", "位值1闭合开漏电阻到地。输出为设定值阵列，并非数字反馈控制器。输出所有档位与端点。", [
        number("minimum_output_v", "最低输出电压 / V", 5), number("maximum_output_v", "最高输出电压 / V", 12),
        number("reference_v", "参考电压 / V", 1.25), choice("bits", "位数", 4, range(1,17)),
        number("top_resistance_ohm", "上臂 R1 / kΩ", 100e3, 1e-3)], lambda v: digital_scaling(**v)),
    "X10": ("单位转换", "10类基线单位；另加独立功率增益 dB。磁通密度不是磁通；PS 公制马力和机械 hp 分开。", [
        choice("category", "物理维度", "flux_density", UNIT_CATEGORIES), number("value", "输入数值", 1000),
        choice("from_unit", "输入单位", "G", ["T", "mT", "G"]), choice("to_unit", "输出单位", "mT", ["T", "mT", "G"])], _units_run),
    "X12": ("差模 LC / π 滤波器", "源侧CF，串联LF，变换器侧CIN/OUT并联串联RD+CD阻尼。两方向频响/阻抗，保留恒功率负阻；不作EMI通过结论。", [
        choice("configuration", "网络", "pi", ["pi", "lc"]), number("inductance_h", "LF / µH", 4.7e-6, 1e6),
        number("inductor_dcr_ohm", "LF DCR / mΩ", .001, 1e3), number("inductor_srf_hz", "LF 自谐振频率 / MHz", 16e6, 1e-6),
        number("filter_capacitance_f", "源侧 CF / µF（π）", 10e-6, 1e6), number("filter_esr_ohm", "CF ESR / mΩ", .005, 1e3),
        number("filter_esl_h", "CF ESL / nH", 4e-9, 1e9), number("converter_capacitance_f", "变换器 CIN/OUT / µF", 4.7e-6, 1e6),
        number("converter_esr_ohm", "CIN/OUT ESR / mΩ", .005, 1e3), number("converter_esl_h", "CIN/OUT ESL / nH", 4e-9, 1e9),
        number("damping_capacitance_f", "阻尼 CD / µF", 68e-6, 1e6), number("damping_resistance_ohm", "阻尼 RD / Ω", .5),
        number("damping_esr_ohm", "CD ESR / mΩ", .025, 1e3), number("damping_esl_h", "CD ESL / nH", 7e-9, 1e9),
        number("input_voltage_v", "输入电压 / V", 12), number("input_current_a", "输入电流 / A", 1),
        number("minimum_frequency_hz", "频响起始频率 / Hz", 10), number("maximum_frequency_hz", "频响终止频率 / MHz", 100e6, 1e-6),
        choice("points", "频率点数", 401, [101, 401, 1001, 4001]),
        number("damping_frequency_hz", "阻尼评估频率 / kHz（空=解RC谐振）", None, 1e-3, True),
        number("noise_frequency_hz", "噪声评估频率 / kHz", 500e3, 1e-3), number("ripple_amplitude_v", "同口径噪声幅度 / mV", .015, 1e3),
        number("limit_dbuv", "噪声限值 / dBµV", 65)], _filter_run),
    "G04": ("恒功率保持时间（补充）", "恒功率能量关系，复用已有母线电容内核；此算例不能替代 AC/DC 整流电容验收。", [
        number("start_voltage_v", "起始电压 / V", 400), number("end_voltage_v", "结束电压 / V", 300),
        number("input_power_w", "从电容取走的功率 / W", 100), number("hold_time_s", "保持时间 / ms", .02, 1e3)], lambda v: hold_up_capacitance(**v)),
}


class _InputPage(QWidget):
    changed = Signal()
    validation_failed = Signal(str)

    def __init__(self, tool_id, database, parent=None):
        super().__init__(parent)
        self.tool_id = tool_id
        self.fields = TOOLS[tool_id][2]
        self.widgets = {}
        self._loading = False
        self._exact_values = {field.key: field.default for field in self.fields if field.kind == "number"}
        self._form = QFormLayout(self)
        if tool_id == "X01":
            for prefix in ("a", "b"):
                combo = QComboBox()
                combo.addItem("手动参数 / 快照", None)
                for device in database.primary + database.sr:
                    combo.addItem(device.part_number, device)
                button = QPushButton("载入 " + prefix.upper() + " 器件参数")
                row = QWidget(); layout = QHBoxLayout(row); layout.setContentsMargins(0,0,0,0)
                layout.addWidget(combo); layout.addWidget(button)
                button.clicked.connect(lambda _checked=False, p=prefix, c=combo: self.load_device(p, c.currentData()))
                self._form.addRow("现有器件库 " + prefix.upper(), row)
        for field in self.fields:
            if field.kind == "choice":
                widget = QComboBox()
                for option in field.options:
                    widget.addItem(choice_label(option), option)
                widget.setCurrentIndex(widget.findData(field.default))
                widget.currentIndexChanged.connect(self._changed)
            elif field.kind == "bool":
                widget = QCheckBox(); widget.setChecked(bool(field.default)); widget.toggled.connect(self._changed)
            else:
                widget = QLineEdit()
                widget.setText(str(field.default) if field.kind == "text" else "" if field.default is None else format(float(field.default)*field.scale, ".15g"))
                if field.optional:
                    widget.setPlaceholderText("留空 = 未知 / 不使用")
                widget.textChanged.connect(self._changed)
            widget.setObjectName("toolbox_"+tool_id+"_"+field.key)
            self.widgets[field.key] = widget
            self._form.addRow(field.label, widget)
        self._update_visibility()

    def _changed(self, *_):
        if self._loading:
            return
        sender = self.sender()
        if sender is not None:
            prefix = "toolbox_" + self.tool_id + "_"
            name = sender.objectName()
            if name.startswith(prefix):
                self._exact_values.pop(name[len(prefix):], None)
        self._update_visibility()
        self.changed.emit()

    def inactive_keys(self):
        """Branch-local editing state is retained, but is not a model input."""
        if self.tool_id == "X01" and not self.widgets["compare"].isChecked():
            return {key for key in self.widgets if key.startswith("b_")}
        if self.tool_id == "X02" and self.widgets["control"].currentData() == "CMC":
            return {"inductance_h"}
        if self.tool_id == "X03" and self.widgets["branch_count"].currentData() == 2:
            return {"c3", "esr3", "esl3"}
        if self.tool_id == "X07":
            return ({"high_ohm", "low_ohm"} if self.widgets["operation"].currentData() == "design"
                    else {"known_arm", "known_resistance_ohm", "series", "parallel"})
        if self.tool_id == "X12" and self.widgets["configuration"].currentData() == "lc":
            return {"filter_capacitance_f", "filter_esr_ohm", "filter_esl_h"}
        return set()

    def _update_visibility(self):
        inactive = self.inactive_keys()
        for key, widget in self.widgets.items():
            self._form.setRowVisible(widget, key not in inactive)
        if self.tool_id == "X10":
            self._sync_units()

    def _sync_units(self):
        units = list(UNIT_CATEGORIES[self.widgets["category"].currentData()])
        for key in ("from_unit", "to_unit"):
            combo = self.widgets[key]
            current = [combo.itemData(i) for i in range(combo.count())]
            if current != units:
                previous = combo.currentData()
                combo.blockSignals(True)
                combo.clear()
                for unit in units:
                    combo.addItem(choice_label(unit), unit)
                combo.setCurrentIndex(max(0, combo.findData(previous)))
                combo.blockSignals(False)

    def load_device(self, prefix, device):
        if device is None:
            return
        # The snapshot includes exactly what the library supplies. Missing
        # charge/Miller fields overwrite demo values with blank, never linger.
        temperature_text = self.widgets[prefix+"_temperature_c"].text()
        try:
            temperature = float(temperature_text)
            parameters = fet_parameters_from_device(device, temperature)
            self.set_values({prefix+"_"+key: value for key, value in asdict(parameters).items()})
        except (ValueError, TypeError, ArithmeticError) as exc:
            self.validation_failed.emit(prefix.upper() + " · 结温或器件参数无效，未载入器件：" + str(exc))
            return
        self.changed.emit()

    def values(self, *, active_only=False):
        values = {}
        inactive = self.inactive_keys() if active_only else set()
        for field in self.fields:
            if field.key in inactive:
                continue
            widget = self.widgets[field.key]
            if field.kind == "choice":
                values[field.key] = widget.currentData()
            elif field.kind == "bool":
                values[field.key] = widget.isChecked()
            elif field.kind == "text":
                values[field.key] = widget.text()
            else:
                text = widget.text().strip()
                if not text and field.optional:
                    values[field.key] = None
                else:
                    try:
                        values[field.key] = self._exact_values[field.key] if field.key in self._exact_values else float(text)/field.scale
                    except (ValueError, OverflowError) as exc:
                        raise ModelDomainError(field.label + "：请输入有限数字") from exc
                    if not math.isfinite(values[field.key]):
                        raise ModelDomainError(field.label + "：请输入有限数字")
        return values

    def snapshot(self):
        """Typed SI values plus lossless incomplete numeric editor text."""
        values, raw = {}, {}
        for field in self.fields:
            widget = self.widgets[field.key]
            if field.kind == "choice":
                values[field.key] = widget.currentData()
            elif field.kind == "bool":
                values[field.key] = widget.isChecked()
            elif field.kind == "text":
                values[field.key] = widget.text()
            else:
                text = widget.text().strip()
                try:
                    value = (None if not text and field.optional else
                             self._exact_values[field.key] if field.key in self._exact_values else float(text)/field.scale)
                    if value is not None and not math.isfinite(value):
                        raise ValueError("nonfinite")
                    values[field.key] = value
                except (ValueError, OverflowError):
                    raw[field.key] = widget.text()
        return {"values": values, **({"raw_inputs": raw} if raw else {})}

    def restore_snapshot(self, snapshot):
        values, raw = snapshot["values"], snapshot.get("raw_inputs", {})
        fields = {field.key: field for field in self.fields}
        if (not isinstance(values, dict) or not isinstance(raw, dict)
                or set(values) & set(raw) or set(values) | set(raw) != set(fields)):
            raise ValueError("Invalid toolbox input fields")
        for key, text in raw.items():
            if (fields[key].kind != "number" or not isinstance(text, str)
                    or len(text.encode("utf-16-le")) // 2 > self.widgets[key].maxLength()):
                raise ValueError("Invalid raw toolbox input")
        self.set_values(values)
        self._loading = True
        try:
            for key, text in raw.items():
                self._exact_values.pop(key, None)
                self.widgets[key].setText(text)
        finally:
            self._loading = False
        self._update_visibility()

    def _validated_text(self, values):
        """Validate the entire update before changing any live editor state."""
        if not isinstance(values, dict) or any(not isinstance(key, str) for key in values):
            raise ValueError("Toolbox inputs must be a field-value object")
        unknown = set(values)-set(self.widgets)
        if unknown:
            raise ValueError("Unknown toolbox inputs: " + ", ".join(sorted(unknown)))
        category = (values.get("category", self.widgets["category"].currentData())
                    if self.tool_id == "X10" else None)
        text = {}
        for field in self.fields:
            if field.key not in values:
                continue
            widget, value = self.widgets[field.key], values[field.key]
            if field.kind == "choice":
                # X10 units must be checked against the proposed category,
                # without first replacing the current category/unit widgets.
                options = (UNIT_CATEGORIES[category] if self.tool_id == "X10" and field.key in ("from_unit", "to_unit")
                           else field.options)
                if not any(type(option) is type(value) and option == value for option in options):
                    raise ValueError("Unsupported value/type for " + field.key)
            elif field.kind == "bool":
                if not isinstance(value, bool):
                    raise ValueError(field.key + " requires boolean")
            elif field.kind == "text":
                if not isinstance(value, str):
                    raise ValueError(field.label + "：必须为文字")
                if len(value.encode("utf-16-le")) // 2 > widget.maxLength():
                    raise ValueError(field.label + "：文字超出长度上限")
                text[field.key] = value
            else:
                if value is None:
                    if not field.optional:
                        raise ValueError(field.label + "：不能为空")
                elif type(value) not in (int, float):
                    raise ValueError(field.label + "：请输入有限数字")
                else:
                    try:
                        finite = math.isfinite(value * field.scale)
                    except OverflowError:
                        finite = False
                    if not finite:
                        raise ValueError(field.label + "：数字超出可表示范围")
                text[field.key] = "" if value is None else format(float(value)*field.scale, ".15g")
        return text

    def set_values(self, values):
        text = self._validated_text(values)
        self._loading = True
        try:
            # Every choice, type and rendered number was checked above.
            if self.tool_id == "X10" and "category" in values:
                widget = self.widgets["category"]
                widget.setCurrentIndex(widget.findData(values["category"]))
                self._sync_units()
            for field in self.fields:
                if field.key not in values:
                    continue
                widget, value = self.widgets[field.key], values[field.key]
                if field.kind == "choice":
                    widget.setCurrentIndex(widget.findData(value))
                elif field.kind == "bool":
                    widget.setChecked(value)
                else:
                    if field.kind == "number":
                        self._exact_values[field.key] = value
                    widget.setText(text[field.key])
        finally:
            self._loading = False
        self._update_visibility()


class ToolBoxWidget(QWidget):
    """Public integration surface for the main PSD window.

    `set_stage_result(None)` invalidates stage-derived output immediately. Pure
    independent results remain valid when unrelated stage settings change.
    `export_to` rejects stale/invalid results. `state` is reproducible calculator
    inputs, not historical outputs; `restore_state` validates before changing UI.
    """
    result_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._generation = 0
        self._closing = False
        self._workers = []
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._pending = False
        self._restore_pending = False
        self.latest_result: CalculatorResult | None = None
        self._stage_result = None
        self._stage_imports = {}
        self._edited_pages = set()
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.setInterval(AUTO_UPDATE_DELAY_MS)
        self._auto_timer.timeout.connect(self.calculate)
        self.database = DeviceDatabase()
        root = QVBoxLayout(self)
        top = QHBoxLayout(); root.addLayout(top)
        self.tool_combo = QComboBox(); self.tool_combo.setObjectName("psd_toolbox_selector")
        for key, (title, *_rest) in TOOLS.items():
            self.tool_combo.addItem(title, key)
        top.addWidget(self.tool_combo, 1)
        self.import_button = QPushButton("导入当前功率级相关参数"); top.addWidget(self.import_button)
        self.calculate_button = QPushButton("计算 / 更新"); top.addWidget(self.calculate_button)
        self.export_button = QPushButton("导出 TXT / CSV / JSON"); top.addWidget(self.export_button)
        self.cancel_button = QPushButton("取消结果"); self.cancel_button.setEnabled(False); top.addWidget(self.cancel_button)
        files = QHBoxLayout(); root.addLayout(files)
        self.save_inputs_button = QPushButton("保存计算器输入")
        self.open_inputs_button = QPushButton("打开计算器输入")
        files.addWidget(self.save_inputs_button); files.addWidget(self.open_inputs_button); files.addStretch(1)
        self.status = QLabel("独立计算器；环路计算请前往模拟环路页。所有字段均标注单位。")
        self.status.setWordWrap(True); root.addWidget(self.status)
        self.description = QLabel(); self.description.setWordWrap(True); root.addWidget(self.description)
        splitter = QSplitter(); root.addWidget(splitter, 1)
        self.pages = {}; self.stack = QStackedWidget()
        for key in TOOLS:
            page = _InputPage(key, self.database)
            page.changed.connect(lambda k=key: self._edited(k))
            page.validation_failed.connect(lambda message, k=key: self._input_rejected(k, message))
            self.pages[key] = page
            scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(page)
            self.stack.addWidget(scroll)
        splitter.addWidget(self.stack)
        result_panel = QWidget(); result_layout = QVBoxLayout(result_panel)
        self.details_toggle = QCheckBox("显示详细依据 / 原始数据")
        self.details_toggle.setObjectName("psd_toolbox_details")
        self.details_toggle.toggled.connect(self._render_result)
        result_layout.addWidget(self.details_toggle)
        self.output = QTextBrowser(); self.output.setObjectName("psd_toolbox_output"); result_layout.addWidget(self.output, 2)
        self.response_plot = pg.PlotWidget(title="频响 / 档位映射")
        self.response_plot.showGrid(x=True, y=True, alpha=.2); self.response_plot.addLegend(); result_layout.addWidget(self.response_plot, 1)
        self.phase_plot = pg.PlotWidget(title="滤波器传递相位")
        self.phase_plot.showGrid(x=True, y=True, alpha=.2); result_layout.addWidget(self.phase_plot, 1)
        self.impedance_plot = pg.PlotWidget(title="滤波器输出阻抗")
        self.impedance_plot.showGrid(x=True, y=True, alpha=.2); self.impedance_plot.addLegend(); result_layout.addWidget(self.impedance_plot, 1)
        splitter.addWidget(result_panel); splitter.setSizes([440, 600])
        self.tool_combo.currentIndexChanged.connect(self._select_tool)
        self.calculate_button.clicked.connect(self.calculate)
        self.import_button.clicked.connect(self._import_clicked)
        self.export_button.clicked.connect(self._export_dialog)
        self.cancel_button.clicked.connect(self.cancel_pending)
        self.save_inputs_button.clicked.connect(self._save_inputs_dialog)
        self.open_inputs_button.clicked.connect(self._open_inputs_dialog)
        self._select_tool()

    @property
    def current_tool_id(self):
        return self.tool_combo.currentData()

    @property
    def input_widgets(self):
        return self.pages[self.current_tool_id].widgets

    def select_tool(self, tool_id):
        index = self.tool_combo.findData(tool_id)
        if index < 0:
            raise ValueError("Unknown toolbox tool")
        self.tool_combo.setCurrentIndex(index)

    def _select_tool(self, *_):
        self.stack.setCurrentIndex(self.tool_combo.currentIndex())
        self.description.setText(TOOLS[self.current_tool_id][1])
        self._clear_result("参数准备就绪，请计算。空白器件参数代表未知。")
        if self.current_tool_id in self._edited_pages:
            self._schedule_update()
        self._update_import_button()

    def _clear_result(self, message):
        self._auto_timer.stop()
        self._generation += 1
        for worker in self._workers[:]:
            if self._pool.tryTake(worker):
                self._workers.remove(worker)
        self._pending = False
        self._restore_pending = False
        self.cancel_button.setEnabled(False)
        self.save_inputs_button.setEnabled(False)
        self.latest_result = None
        self.output.clear(); self.response_plot.clear(); self.phase_plot.clear(); self.impedance_plot.clear()
        self.response_plot.hide(); self.phase_plot.hide(); self.impedance_plot.hide()
        self.export_button.setEnabled(False)
        self.status.setText(message)
        self.result_changed.emit(None)

    def _schedule_update(self):
        if not self._closing and not getattr(self.window(), "_closing", False):
            self._auto_timer.start()

    def _edited(self, tool_id):
        if self._restore_pending:
            self._cancel_restore("输入已改变，未采用正在校验的文件。")
        self._edited_pages.add(tool_id)
        # Only an explicit edit of this page relinquishes its stage binding.
        self._stage_imports.pop(tool_id, None)
        if tool_id == self.current_tool_id:
            self._clear_result("输入正在编辑，旧结果已失效；有效输入将自动更新。")
            self._schedule_update()
        self._update_import_button()

    def _input_rejected(self, tool_id, message):
        if tool_id == self.current_tool_id:
            self._clear_result("无有效结果：" + message)

    def set_values(self, values):
        self.pages[self.current_tool_id].set_values(values)
        self._edited(self.current_tool_id)

    def set_stage_result(self, result_or_none):
        previous = self._stage_result
        self._stage_result = result_or_none
        changed = result_or_none is None or previous is None or result_or_none.design_id != previous.design_id
        if changed and self._restore_pending:
            self._cancel_restore("功率级已改变，未采用正在校验的文件。")
        # Keep the provenance marker so recalculation cannot turn stale
        # imported numbers into apparently current stage-derived output.
        if changed and self.current_tool_id in self._stage_imports:
            self._clear_result("功率级已改变，导入参数已过期。请重新导入或手动编辑独立计算。")
        self._update_import_button()

    def _update_import_button(self):
        self.import_button.setEnabled(self._stage_result is not None)

    def import_stage(self):
        if self._stage_result is None:
            raise ModelDomainError("没有当前有效功率级结果")
        widgets = self.pages[self.current_tool_id].widgets
        mapped = stage_import_values(self.current_tool_id, self._stage_result,
                                     control=widgets["control"].currentData() if "control" in widgets else "VMC",
                                     role=widgets["role"].currentData() if "role" in widgets else "main")
        self.pages[self.current_tool_id].set_values(mapped)
        self._stage_imports[self.current_tool_id] = {
            "design_id": self._stage_result.design_id, "mapped_inputs": mapped.copy()}
        self._edited_pages.add(self.current_tool_id)
        labels = {field.key: field.label for field in self.pages[self.current_tool_id].fields}
        self._clear_result("已导入 " + "、".join(labels[key] for key in mapped) + "；其余输入保留，需自行核对。")
        self._schedule_update()
        return mapped

    def _import_clicked(self):
        try:
            self.import_stage()
        except (ValueError, TypeError, ArithmeticError) as exc:
            self._clear_result("无法映射：" + str(exc))

    def calculate(self):
        if self._closing or getattr(self.window(), "_closing", False):
            return None
        self._clear_result("正在计算…")
        generation = self._generation
        tool_id = self.current_tool_id
        try:
            imported = self._stage_imports.get(tool_id)
            if (imported and (self._stage_result is None or self._stage_result.design_id != imported["design_id"])):
                raise ModelDomainError("导入参数已过期 (stale)；请重新导入，或明确手动编辑为独立计算")
            values = self.pages[tool_id].values(active_only=True)
            if imported:
                imported = self._check_import(tool_id, self._page_state(tool_id), require_current=True)
        except (ValueError, TypeError, ArithmeticError) as exc:
            self.status.setText("无有效结果：" + str(exc))
            return None
        imported = ({"design_id": imported["design_id"], "mapped_inputs": imported["mapped_inputs"].copy()}
                    if imported else None)
        def compute():
            result = TOOLS[tool_id][3](values.copy())
            return replace(result, inputs={**result.inputs, "stage_import": imported}) if imported else result
        # Large explicit maps run through the established reusable worker.
        # Cancellation discards the result; a bounded CPU calculation may finish
        # in the background, but can never update a newer UI generation.
        if self._heavy_inputs(tool_id, values):
            self._pending = True
            self.cancel_button.setEnabled(True)
            self.status.setText("正在后台计算完整档位/频率数据；可编辑、切换或取消采用结果。")
            def job():
                try:
                    return generation, compute(), None
                except Exception as exc:  # noqa: BLE001 - worker boundary preserves generation even for unexpected failures
                    return generation, None, f"{type(exc).__name__}: {exc}"
            worker = FunctionWorker(job)
            self._workers.append(worker)
            worker.signals.result.connect(self._async_ready)
            worker.signals.finished.connect(lambda w=worker: self._workers.remove(w) if w in self._workers else None)
            self._pool.start(worker)
            return None
        try:
            return self._apply_result(compute())
        except (ValueError, TypeError, ArithmeticError) as exc:
            self.status.setText("无有效结果：" + str(exc))
            return None

    def _async_ready(self, payload):
        generation, result, error = payload
        if generation != self._generation or self._closing:
            return
        self._pending = False
        self.cancel_button.setEnabled(False)
        if error:
            self.status.setText("无有效结果：" + error)
            return
        self._apply_result(result)

    @staticmethod
    def _heavy_inputs(tool_id, values):
        return (tool_id == "X09" and values["bits"] > 12) or (tool_id == "X12" and values["points"] > 1001)

    def cancel_pending(self):
        if self._restore_pending:
            self._cancel_restore("已取消打开文件；当前输入与结果保持不变。")
        else:
            self._clear_result("已取消采用结果；后台短计算结束后也不会覆盖当前输入。")

    def _cancel_restore(self, message):
        self._auto_timer.stop()
        self._generation += 1
        self._pending = self._restore_pending = False
        self.cancel_button.setEnabled(False)
        for worker in self._workers[:]:
            if self._pool.tryTake(worker):
                self._workers.remove(worker)
        self.status.setText(message)

    def _apply_result(self, result):
        self.latest_result = result
        self.export_button.setEnabled(True)
        self.save_inputs_button.setEnabled(True)
        self.status.setText(status_label(result.status) + "；计算模型结果，不代表硬件验证。")
        self._render_result()
        self._plot(result)
        self.result_changed.emit(result)
        return result

    def _render_result(self, *_):
        if self.latest_result is not None:
            render = details_text if self.details_toggle.isChecked() else result_text
            self.output.setPlainText(render(self.latest_result))

    def _plot(self, result):
        self.response_plot.setLogMode(x=False, y=False)
        self.impedance_plot.setLogMode(x=True, y=True)
        if result.tool_id == "X12":
            curves = result.outputs["curves"]
            self.response_plot.show(); self.phase_plot.show(); self.impedance_plot.show()
            self.response_plot.setTitle("差模滤波器传递增益")
            self.phase_plot.setLogMode(x=True, y=False)
            self.phase_plot.setLabel("bottom", "频率", units="Hz")
            self.phase_plot.setLabel("left", "传递相位", units="deg")
            self.response_plot.setLogMode(x=True, y=False)
            self.response_plot.setLabel("bottom", "频率", units="Hz")
            self.response_plot.setLabel("left", "传递增益", units="dB")
            self.impedance_plot.setLabel("bottom", "频率", units="Hz")
            self.impedance_plot.setLabel("left", "输出阻抗", units="Ω")
            for key, color in (("noise_to_other", "#009e73"), ("other_to_noise_undamped", "#0072b2"), ("other_to_noise_damped", "#d55e00")):
                label = {"noise_to_other": "GF 噪声→源/负载", "other_to_noise_undamped": "源→变换器 无阻尼", "other_to_noise_damped": "GD 源→变换器 有阻尼"}[key]
                self.response_plot.plot(curves["frequency_hz"], curves[key+"_db"], pen=pg.mkPen(color, width=2), name=label)
                self.phase_plot.plot(curves["frequency_hz"], curves[key+"_phase_deg"], pen=pg.mkPen(color, width=2))
            for key, color in (("output_impedance_undamped", "#0072b2"), ("output_impedance_damped", "#d55e00")):
                self.impedance_plot.plot(curves["frequency_hz"], curves[key+"_magnitude"], pen=pg.mkPen(color, width=2), name="无阻尼" if key.endswith("undamped") else "有阻尼")
        elif result.tool_id in ("X08", "X09"):
            self.response_plot.show()
            self.response_plot.setTitle("实际电阻网络输出设定映射")
            mapping = result.outputs["mapping"]
            key = "code" if result.tool_id == "X09" else "adjust_v"
            self.response_plot.setLabel("bottom", "数字档位" if key == "code" else "调整电压 / V")
            self.response_plot.setLabel("left", "输出设定", units="V")
            self.response_plot.plot([row[key] for row in mapping], [row["output_v"] for row in mapping], pen=pg.mkPen("#0072b2", width=2))

    def export_to(self, path):
        if self.latest_result is None:
            raise ValueError("No current toolbox result to export")
        return self.latest_result.write(path)

    def _modal_revision(self):
        if self._closing or getattr(self.window(), "_closing", False):
            return None
        return self._generation, id(self.latest_result)

    def _export_dialog(self):
        token = self._modal_revision()
        if token is None or self.latest_result is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出当前工具结果", self.current_tool_id+".json", "JSON (*.json);;CSV (*.csv);;Text (*.txt)")
        if path and token == self._modal_revision():
            try:
                self.export_to(path)
            except (ValueError, OSError) as exc:
                self.status.setText("导出失败："+str(exc))

    @staticmethod
    def _result_provenance(result):
        return {"tool_id": result.tool_id, "model_version": result.model_version,
                "result_id": result.result_id, "status": result.status, "source": result.source}

    def save_inputs_to(self, path):
        if self.latest_result is None:
            raise ValueError("请先计算当前有效输入，再保存")
        document = {"schema": INPUT_SCHEMA, "toolkit_version": __version__,
                    "model_version": MODEL_VERSION, "state": self.state(),
                    "input_provenance": self._result_provenance(self.latest_result)}
        if len(json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")) + 1 > MAX_INPUT_BYTES:
            raise ValueError("计算器输入超过 256 kB，请缩短参数来源等文字说明")
        # Reuse the project writer's temporary-file/atomic-replace contract.
        return write_json(document, path)

    def open_inputs_from(self, path, *, background=False):
        if self._restore_pending:
            self._cancel_restore("正在检查新文件；先前打开请求已失效。")
        path = Path(path)
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError("计算器输入文件超过 256 kB；结果导出文件不能作为输入会话")
        document = strict_json_loads(path.read_bytes())
        if not isinstance(document, dict):
            raise ValueError("无效的计算器输入文件")  # noqa: TRY004 - session schema errors use ValueError
        # Versionless schema-1 has no reliable model identity. Do not silently
        # bless its cached identity or reinterpret its inputs under a new model.
        if document.get("schema") == "psd-toolbox-inputs-1":
            raise ValueError("旧版输入文件缺少模型版本，请核对原始参数后手动输入并重新保存")
        if set(document) != {"schema", "toolkit_version", "model_version", "state", "input_provenance"}:
            raise ValueError("计算器输入文件字段不完整或包含未知字段")
        if document["schema"] != INPUT_SCHEMA:
            raise ValueError("不支持此计算器输入文件版本")
        # Deliberately conservative until a specific migration is verified.
        if document["model_version"] != MODEL_VERSION or document["toolkit_version"] != __version__:
            raise ValueError("计算器模型或工具包版本不兼容，请核对参数后手动迁移")
        provenance = document["input_provenance"]
        if (not isinstance(provenance, dict)
                or set(provenance) != {"tool_id", "model_version", "result_id", "status", "source"}
                or any(not isinstance(value, str) or not value for value in provenance.values())):
            raise ValueError("计算器输入来源记录不完整或无效")
        self.restore_state(document["state"], expected_provenance=provenance, background=background)

    def _save_inputs_dialog(self):
        token = self._modal_revision()
        if token is None or self.latest_result is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存当前计算器输入", self.current_tool_id+"-inputs.json", "JSON (*.json)")
        if path and token == self._modal_revision():
            try:
                self.save_inputs_to(path)
            except (ValueError, OSError) as exc:
                self.status.setText("保存失败："+str(exc))

    def _open_inputs_dialog(self):
        token = self._modal_revision()
        if token is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "打开计算器输入并重算", "", "JSON (*.json)")
        if path and token == self._modal_revision():
            try:
                self.open_inputs_from(path, background=True)
            except (ValueError, OSError) as exc:
                self.status.setText("打开失败："+str(exc))

    def _page_state(self, tool_id):
        imported = self._stage_imports.get(tool_id)
        return {**self.pages[tool_id].snapshot(), "stage_import": None if imported is None else
                {"design_id": imported["design_id"], "mapped_inputs": imported["mapped_inputs"].copy()}}

    def state(self):
        state = {"tool_id": self.current_tool_id, **self._page_state(self.current_tool_id)}
        others = {key: self._page_state(key) for key in sorted(self._edited_pages | set(self._stage_imports))
                  if key != self.current_tool_id}
        if others:
            state["page_states"] = others
        return state

    def _check_import(self, tool_id, snapshot, *, require_current):
        imported = snapshot["stage_import"]
        if imported is None:
            return None
        if not isinstance(imported, dict) or set(imported) != {"design_id", "mapped_inputs"}:
            raise ValueError("Invalid imported-stage snapshot")
        mapped = imported["mapped_inputs"]
        try:
            finite_mapping = (isinstance(mapped, dict) and bool(mapped)
                              and all(type(value) in (int, float) and math.isfinite(value) for value in mapped.values()))
        except OverflowError:
            finite_mapping = False
        if (not isinstance(imported["design_id"], str) or not imported["design_id"]
                or not finite_mapping
                or any(key not in snapshot["values"] or snapshot["values"][key] != value for key, value in mapped.items())):
            raise ValueError("Invalid imported-stage provenance")
        current = self._stage_result is not None and imported["design_id"] == self._stage_result.design_id
        if require_current and not current:
            raise ValueError("Toolbox import does not match the current stage")
        if current:
            values = snapshot["values"]
            expected = stage_import_values(tool_id, self._stage_result,
                                          control=values.get("control", "VMC"), role=values.get("role", "main"))
            if mapped != expected:
                raise ValueError("Toolbox mapped values do not match the current stage")
        # A nonselected stale origin is kept only as an input binding. It cannot
        # authorize output and will be blocked when that page is revisited.
        return {"design_id": imported["design_id"], "mapped_inputs": mapped.copy()}

    def _prepare_restore(self, state):
        required = {"tool_id", "values", "stage_import"}
        if (not isinstance(state, dict) or not required <= set(state)
                or set(state) - required - {"raw_inputs", "page_states"}):
            raise ValueError("Invalid toolbox state fields")
        tool_id = state["tool_id"]
        if not isinstance(tool_id, str) or tool_id not in TOOLS:
            raise ValueError("Invalid toolbox tool")
        others = state.get("page_states", {})
        if (not isinstance(others, dict) or tool_id in others
                or any(key not in TOOLS for key in others)):
            raise ValueError("Invalid toolbox page states")
        candidates = {**others, tool_id: {key: state[key] for key in ("values", "stage_import", "raw_inputs") if key in state}}
        snapshots, imports, legacy_values = {}, {}, None
        # Prevalidate every field on the GUI thread. No QObject enters the
        # worker; other pages are retained editing state, never cached results.
        for key, snapshot in candidates.items():
            if (not isinstance(snapshot, dict) or not {"values", "stage_import"} <= set(snapshot)
                    or set(snapshot) - {"values", "stage_import", "raw_inputs"}):
                raise ValueError("Invalid toolbox page state fields")
            temporary = _InputPage(key, self.database)
            try:
                temporary.restore_snapshot(snapshot)
                imports[key] = self._check_import(key, snapshot, require_current=key == tool_id)
                snapshots[key] = {**temporary.snapshot(), "stage_import": imports[key]}
                if key == tool_id:
                    checked = temporary.values(active_only=True)
                    if key == "X12" and set(state) == required and checked["configuration"] == "lc":
                        legacy_values = temporary.values()
            finally:
                temporary.deleteLater()
        return tool_id, snapshots, imports, checked, legacy_values

    def _compute_restore(self, prepared, expected_provenance):
        tool_id, _snapshots, imports, checked, legacy_values = prepared
        result = TOOLS[tool_id][3](checked.copy())
        imported = imports[tool_id]
        if imported:
            result = replace(result, inputs={**result.inputs, "stage_import": imported})
        migrated_lc = False
        if expected_provenance is not None and expected_provenance != self._result_provenance(result):
            legacy_result = None
            if legacy_values is not None:
                # Old schema-2 included unused CF values in the input identity.
                # Freshly verify that identity, then publish absent-CF inputs.
                legacy_result = TOOLS[tool_id][3](legacy_values.copy())
                if imported:
                    legacy_result = replace(legacy_result, inputs={**legacy_result.inputs, "stage_import": imported})
            if legacy_result is None or expected_provenance != self._result_provenance(legacy_result):
                raise ValueError("输入来源与当前模型重算结果不一致；未采用历史结果")
            migrated_lc = True
        return result, migrated_lc

    def _adopt_restore(self, prepared, computed):
        tool_id, snapshots, imports, _checked, _legacy = prepared
        result, migrated_lc = computed
        self.select_tool(tool_id)
        for key, snapshot in snapshots.items():
            self.pages[key].restore_snapshot(snapshot)
            if imports[key] is None:
                self._stage_imports.pop(key, None)
            else:
                self._stage_imports[key] = imports[key]
        self._edited_pages.update(snapshots)
        self._clear_result("输入已恢复，已用当前模型重新计算。")
        self._apply_result(result)
        if migrated_lc:
            self.status.setText("已核对旧版 LC 输入并重算；未接入的 CF 保留在编辑器，结果中记为不使用。")

    def restore_state(self, state, *, expected_provenance=None, background=False):
        """Transactional recomputation; file-open UI requests heavy background work.

        The default remains synchronous for existing programmatic callers.
        """
        prepared = self._prepare_restore(state)
        if not background or not self._heavy_inputs(prepared[0], prepared[3]):
            self._adopt_restore(prepared, self._compute_restore(prepared, expected_provenance))
            return
        self._cancel_restore("正在后台校验并重算文件；成功前保留当前输入与结果，可取消。")
        generation = self._generation
        self._pending = self._restore_pending = True
        self.cancel_button.setEnabled(True)

        def job():
            try:
                return generation, prepared, self._compute_restore(prepared, expected_provenance), None
            except Exception as exc:  # noqa: BLE001 - bounded worker preserves transactional failure and generation
                return generation, prepared, None, f"{type(exc).__name__}: {exc}"

        worker = FunctionWorker(job)
        self._workers.append(worker)
        worker.signals.result.connect(self._restore_ready)
        worker.signals.finished.connect(lambda w=worker: self._workers.remove(w) if w in self._workers else None)
        self._pool.start(worker)

    def _restore_ready(self, payload):
        generation, prepared, computed, error = payload
        if (generation != self._generation or self._closing
                or getattr(self.window(), "_closing", False)):
            return
        self._pending = self._restore_pending = False
        self.cancel_button.setEnabled(False)
        if error:
            self.status.setText("打开失败，当前输入与结果保持不变：" + error)
            return
        self._adopt_restore(prepared, computed)

    def stop_background_work(self, timeout_ms=2000):
        """Invalidate/drain this tab's jobs without hiding a reusable tab page."""
        self._auto_timer.stop()
        self._generation += 1
        was_pending = self._pending
        self._pending = self._restore_pending = False
        self.cancel_button.setEnabled(False)
        for worker in self._workers[:]:
            if self._pool.tryTake(worker):
                self._workers.remove(worker)
        # Dedicated pool contains at most one bounded 16-bit/4001-point job.
        # A bounded wait keeps signal QObjects alive until emission ends and
        # never waits on other workspaces' global-pool tasks.
        finished = self._pool.waitForDone(timeout_ms)
        if was_pending:
            self.status.setText("后台计算已停止采用结果。" if finished else "正在结束后台计算，请稍后再次关闭。")
        return finished

    def showEvent(self, event):
        self._closing = False
        super().showEvent(event)

    def closeEvent(self, event):
        if not self.stop_background_work():
            event.ignore()
            return
        self._closing = True
        # Closing a tab/window may only hide it. Preserve plot items for a
        # later show(); Qt parent destruction owns their eventual cleanup.
        super().closeEvent(event)
