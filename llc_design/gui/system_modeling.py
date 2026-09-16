"""V9.3 guided system modeling and design workflow.

This is the product-entry layer that was missing from the existing expert
workspaces.  It asks the user to define the real closed-loop system in order:

    topology -> power stage -> sensing -> ADC/PWM/timing -> controller intent
    -> review/validation -> existing analysis engine

The wizard does not duplicate LLC/PFC equations.  It emits the canonical
``ControlSystemDefinition`` and adapters translate that definition into the
existing LLC and TTPL configuration objects.
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from llc_design.core.spec import LLCDesignSpec
from llc_design.gui import theme
from pfc_design.control import (
    ADCTimingConfig,
    DigitalFilterConfig,
    ExternalSenseConfig,
    PFCControlLabConfig,
    PFCFirmwareAlgorithmConfig,
    PFCPowerStageConfig,
)
from power_control_tools.system_definition import (
    ADCDefinition,
    ControlArchitecture,
    ControllerIntent,
    ControlSystemDefinition,
    LLCStageDefinition,
    ModulatorDefinition,
    PlantSource,
    SensorDefinition,
    SystemTopology,
    TTPLStageDefinition,
    TimingDefinition,
)


class SystemModelingDesignDialog(QDialog):
    """Guided V9.3 model-definition wizard for LLC and TTPL PFC."""

    PAGE_TOPOLOGY = 0
    PAGE_STAGE = 1
    PAGE_SENSING = 2
    PAGE_DIGITAL = 3
    PAGE_CONTROLLER = 4
    PAGE_REVIEW = 5

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.definition: ControlSystemDefinition | None = None
        self.setWindowTitle("系统建模与设计 / Guided System Design — V9.3")
        self.resize(1180, 820)
        self.setMinimumSize(1000, 700)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setStyleSheet(theme.workspace_stylesheet(theme.active_theme()))

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title = QLabel("系统建模与设计 / Guided System Design")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size:22px;font-weight:700;padding:5px;")
        root.addWidget(title)
        subtitle = QLabel(
            "先逐步定义功率级、采样、ADC、调制/PWM 与数字时序，再把同一个标准系统模型交给现有 LLC / TTPL 分析引擎。"
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color:#667085;padding-bottom:8px;")
        root.addWidget(subtitle)

        body = QHBoxLayout()
        self.progress = QVBoxLayout()
        self.progress_labels: list[QLabel] = []
        for text in (
            "1  拓扑 / 架构",
            "2  功率级",
            "3  采样与滤波",
            "4  ADC / PWM / 延时",
            "5  控制器意图",
            "6  系统复核",
        ):
            label = QLabel(text)
            label.setMinimumWidth(175)
            label.setStyleSheet("padding:10px 12px;border-radius:6px;")
            self.progress.addWidget(label)
            self.progress_labels.append(label)
        self.progress.addStretch(1)
        body.addLayout(self.progress)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._topology_page())
        self.pages.addWidget(self._power_stage_page())
        self.pages.addWidget(self._sensing_page())
        self.pages.addWidget(self._digital_page())
        self.pages.addWidget(self._controller_page())
        self.pages.addWidget(self._review_page())
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)

        footer = QHBoxLayout()
        self.cancel_button = QPushButton("取消")
        self.back_button = QPushButton("上一步")
        self.next_button = QPushButton("下一步")
        self.finish_button = QPushButton("创建系统并进入分析")
        self.finish_button.setEnabled(False)
        footer.addWidget(self.cancel_button)
        footer.addStretch(1)
        footer.addWidget(self.back_button)
        footer.addWidget(self.next_button)
        footer.addWidget(self.finish_button)
        root.addLayout(footer)

        self.cancel_button.clicked.connect(self.reject)
        self.back_button.clicked.connect(self._back)
        self.next_button.clicked.connect(self._next)
        self.finish_button.clicked.connect(self._finish)
        self.pages.currentChanged.connect(self._page_changed)
        self._page_changed(0)

    @staticmethod
    def _double(lo: float, hi: float, decimals: int, value: float, suffix: str = "") -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(lo, hi)
        widget.setDecimals(decimals)
        widget.setValue(value)
        widget.setSuffix(suffix)
        widget.setKeyboardTracking(False)
        return widget

    @staticmethod
    def _spin(lo: int, hi: int, value: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(lo, hi)
        widget.setValue(value)
        widget.setKeyboardTracking(False)
        return widget

    def _topology_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        intro = QLabel(
            "选择要定义的真实闭环系统。V9.3 第一阶段只接入已经成熟的 LLC 与单相 Totem-Pole PFC；其他拓扑先显示路线图占位，不伪装成已支持。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        box = QGroupBox("Topology / Control Architecture")
        form = QVBoxLayout(box)
        self.topology_group = QButtonGroup(self)
        self.llc_radio = QRadioButton("LLC — FM 数字电压环（V9.3 已接入）")
        self.ttpl_radio = QRadioButton("Single-Phase Totem-Pole PFC — 电流内环 + 母线电压外环（V9.3 已接入）")
        self.vienna_radio = QRadioButton("Three-Phase Vienna PFC — 占位，后续版本接入")
        self.dc_radio = QRadioButton("Buck / Boost / Buck-Boost / Flyback — 占位，后续版本接入")
        self.generic_radio = QRadioButton("Generic Plant / Generic Control System — 占位，后续版本接入")
        self.vienna_radio.setEnabled(False)
        self.dc_radio.setEnabled(False)
        self.generic_radio.setEnabled(False)
        for button in (self.llc_radio, self.ttpl_radio, self.vienna_radio, self.dc_radio, self.generic_radio):
            self.topology_group.addButton(button)
            form.addWidget(button)
        self.llc_radio.setChecked(True)
        layout.addWidget(box)

        plant = QGroupBox("Plant Source")
        plant_form = QFormLayout(plant)
        self.plant_source = QComboBox()
        self.plant_source.addItem("Analytical / topology model", PlantSource.ANALYTICAL)
        self.plant_source.addItem("Imported FRA (reserved in guided adapter)", PlantSource.IMPORTED_FRA)
        self.plant_source.addItem("Identified plant (reserved in guided adapter)", PlantSource.IDENTIFIED)
        self.plant_source.addItem("Custom G(s) (reserved in guided adapter)", PlantSource.CUSTOM_GS)
        self.plant_source.addItem("Custom G(z) (reserved in guided adapter)", PlantSource.CUSTOM_GZ)
        # First tranche keeps the adapter on the already-maintained analytical
        # LLC/TTPL path; FRA/custom entry remains available in Expert/FRA workspaces.
        for i in range(1, self.plant_source.count()):
            item = self.plant_source.model().item(i)
            if item is not None:
                item.setEnabled(False)
        plant_form.addRow("Source", self.plant_source)
        layout.addWidget(plant)
        layout.addStretch(1)
        self.llc_radio.toggled.connect(self._sync_topology_pages)
        self.ttpl_radio.toggled.connect(self._sync_topology_pages)
        return page

    def _power_stage_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        self.stage_stack = QStackedWidget()

        llc = QWidget(); form = QFormLayout(llc)
        self.llc_vmin = self._double(50, 1000, 2, 360, " V")
        self.llc_vnom = self._double(50, 1000, 2, 400, " V")
        self.llc_vmax = self._double(50, 1000, 2, 420, " V")
        self.llc_vout = self._double(1, 1000, 2, 53, " V")
        self.llc_pout = self._double(1, 200000, 1, 3000, " W")
        self.llc_fr = self._double(1, 1000, 2, 100, " kHz")
        self.llc_fmin = self._double(1, 1000, 2, 60, " kHz")
        self.llc_fmax = self._double(1, 1000, 2, 180, " kHz")
        self.llc_ln = self._double(1.001, 100, 4, 5.0)
        self.llc_q = self._double(0.001, 10, 4, 0.35)
        self.llc_np = self._spin(1, 1000, 30)
        self.llc_ns = self._spin(1, 1000, 4)
        for label, widget in (
            ("Bus min", self.llc_vmin), ("Bus nominal", self.llc_vnom), ("Bus max", self.llc_vmax),
            ("Output voltage", self.llc_vout), ("Output power", self.llc_pout),
            ("Resonant frequency", self.llc_fr), ("Minimum frequency", self.llc_fmin),
            ("Maximum frequency", self.llc_fmax), ("Ln = Lm/Lr", self.llc_ln),
            ("Q @ full load", self.llc_q), ("Primary turns", self.llc_np), ("Secondary turns", self.llc_ns),
        ):
            form.addRow(label, widget)
        self.stage_stack.addWidget(llc)

        ttpl = QWidget(); form = QFormLayout(ttpl)
        self.ttpl_vmin = self._double(20, 400, 2, 176, " Vrms")
        self.ttpl_vnom = self._double(20, 400, 2, 230, " Vrms")
        self.ttpl_vmax = self._double(20, 400, 2, 264, " Vrms")
        self.ttpl_line = self._double(40, 70, 2, 50, " Hz")
        self.ttpl_vbus = self._double(100, 1000, 2, 400, " V")
        self.ttpl_pout = self._double(10, 200000, 1, 3300, " W")
        self.ttpl_eff = self._double(0.7, 1.0, 4, 0.97)
        self.ttpl_fsw = self._double(5, 500, 2, 50, " kHz")
        self.ttpl_l = self._double(1, 10000, 2, 220, " µH")
        self.ttpl_dcr = self._double(0, 5000, 3, 55, " mΩ")
        self.ttpl_cbus = self._double(1, 100000, 1, 1320, " µF")
        self.ttpl_cesr = self._double(0, 1000, 3, 35, " mΩ")
        for label, widget in (
            ("Vin min", self.ttpl_vmin), ("Vin nominal", self.ttpl_vnom), ("Vin max", self.ttpl_vmax),
            ("Line frequency", self.ttpl_line), ("DC bus", self.ttpl_vbus), ("Output power", self.ttpl_pout),
            ("Efficiency estimate", self.ttpl_eff), ("Switching frequency", self.ttpl_fsw),
            ("Boost inductance", self.ttpl_l), ("Inductor DCR", self.ttpl_dcr),
            ("Bus capacitance", self.ttpl_cbus), ("Bus capacitor ESR", self.ttpl_cesr),
        ):
            form.addRow(label, widget)
        self.stage_stack.addWidget(ttpl)
        root.addWidget(self.stage_stack)
        root.addStretch(1)
        return page

    def _sensing_page(self) -> QWidget:
        page = QWidget(); root = QVBoxLayout(page)
        self.sense_stack = QStackedWidget()

        llc = QWidget(); form = QFormLayout(llc)
        self.llc_rup = self._double(0.001, 1e6, 3, 117.0, " kΩ")
        self.llc_rlow = self._double(0.001, 1e6, 3, 1.6, " kΩ")
        self.llc_cdiv = self._double(0, 1e6, 4, 1.0, " nF")
        self.llc_amp_gain = self._double(0.001, 1000, 5, 1.0)
        self.llc_amp_bw = self._double(0, 1e6, 2, 0.0, " kHz")
        self.llc_adc_r = self._double(0, 1e7, 2, 220, " Ω")
        self.llc_adc_c = self._double(0, 1e6, 4, 2.0, " nF")
        self.llc_sample = self._double(1, 1000, 3, 50, " kHz")
        for label, widget in (
            ("Divider Rup", self.llc_rup), ("Divider Rlow", self.llc_rlow), ("Rlow shunt C", self.llc_cdiv),
            ("OpAmp gain", self.llc_amp_gain), ("OpAmp bandwidth (0=ideal)", self.llc_amp_bw),
            ("ADC series R", self.llc_adc_r), ("ADC shunt C", self.llc_adc_c),
            ("Control sample rate", self.llc_sample),
        ):
            form.addRow(label, widget)
        self.sense_stack.addWidget(llc)

        ttpl = QWidget(); ttpl_layout = QVBoxLayout(ttpl)
        self.current_sense = self._ttpl_sensor_group(ttpl_layout, "Inductor current sense", 0.03, 2000, 0, 0, 220, 2, 50)
        self.vac_sense = self._ttpl_sensor_group(ttpl_layout, "AC voltage sense", 1/150, 1000, 2000, 1, 220, 2, 50)
        self.vbus_sense = self._ttpl_sensor_group(ttpl_layout, "Bus voltage sense", 1600/(117000+1600), 1000, 117000*1600/(117000+1600), 1, 220, 2, 10)
        ttpl_layout.addStretch(1)
        self.sense_stack.addWidget(ttpl)
        root.addWidget(self.sense_stack, 1)
        return page

    def _ttpl_sensor_group(self, parent: QVBoxLayout, title: str, gain: float, bw_khz: float,
                           source_r: float, source_c_nf: float, out_r: float, out_c_nf: float,
                           sample_khz: float) -> dict[str, QDoubleSpinBox]:
        box = QGroupBox(title); form = QFormLayout(box)
        fields = {
            "gain": self._double(1e-9, 1000, 9, gain, " V/unit"),
            "bw": self._double(0.1, 100000, 2, bw_khz, " kHz"),
            "source_r": self._double(0, 1e7, 3, source_r, " Ω"),
            "source_c": self._double(0, 1e6, 4, source_c_nf, " nF"),
            "out_r": self._double(0, 1e7, 3, out_r, " Ω"),
            "out_c": self._double(0, 1e6, 4, out_c_nf, " nF"),
            "sample": self._double(0.1, 1000, 3, sample_khz, " kHz"),
            "alpha": self._double(0.000001, 1, 6, 1.0),
        }
        for label, key in (
            ("Front-end gain", "gain"), ("Amplifier bandwidth", "bw"),
            ("Source R", "source_r"), ("Source C", "source_c"),
            ("ADC R", "out_r"), ("ADC C", "out_c"),
            ("Sample rate", "sample"), ("Digital LPF alpha", "alpha"),
        ):
            form.addRow(label, fields[key])
        parent.addWidget(box)
        return fields

    def _digital_page(self) -> QWidget:
        page = QWidget(); root = QVBoxLayout(page)
        adc = QGroupBox("ADC / Sampling")
        form = QFormLayout(adc)
        self.adc_vref = self._double(0.1, 20, 3, 3.3, " V")
        self.adc_bits = self._spin(1, 32, 12)
        self.adc_clock = self._double(1, 1000, 3, 60, " MHz")
        self.adc_acq = self._double(0, 100000, 2, 300, " ns")
        self.adc_cycles = self._double(0, 100, 3, 13, " cycles")
        self.adc_soc = self._spin(1, 16, 1)
        self.adc_soc_spacing = self._double(0, 1000, 3, 0, " µs")
        self.adc_prev_weight = self._double(0, 0.999, 6, 0.0)
        for label, widget in (
            ("Vref", self.adc_vref), ("Resolution", self.adc_bits), ("ADCCLK", self.adc_clock),
            ("Acquisition window", self.adc_acq), ("Conversion cycles", self.adc_cycles),
            ("SOC count", self.adc_soc), ("SOC spacing", self.adc_soc_spacing),
            ("Recursive previous weight", self.adc_prev_weight),
        ):
            form.addRow(label, widget)
        root.addWidget(adc)

        mod = QGroupBox("Modulator / PWM / FM")
        form = QFormLayout(mod)
        self.timer_clock = self._double(1, 1000, 3, 120, " MHz")
        self.count_mode = QComboBox(); self.count_mode.addItem("Up-Down", "up_down"); self.count_mode.addItem("Up", "up")
        self.duty_min = self._double(0, 0.9, 5, 0.01)
        self.duty_max = self._double(0.01, 1, 5, 0.98)
        self.min_pulse = self._double(0, 100, 4, 0.0, " µs")
        self.deadtime = self._double(0, 10000, 2, 100, " ns")
        form.addRow("Timer clock", self.timer_clock)
        form.addRow("Counter mode", self.count_mode)
        form.addRow("Duty min", self.duty_min)
        form.addRow("Duty max", self.duty_max)
        form.addRow("Minimum pulse", self.min_pulse)
        form.addRow("Deadtime", self.deadtime)
        root.addWidget(mod)

        timing = QGroupBox("Digital command timing")
        form = QFormLayout(timing)
        self.compute_delay = self._double(0, 1000, 4, 1.0, " µs")
        self.pwm_delay = self._double(0, 1000, 4, 10.0, " µs")
        self.include_zoh = QCheckBox("Include Zero-Order Hold")
        self.include_zoh.setChecked(True)
        form.addRow("Computation delay", self.compute_delay)
        form.addRow("PWM / shadow update delay", self.pwm_delay)
        form.addRow(self.include_zoh)
        root.addWidget(timing)
        root.addStretch(1)
        return page

    def _controller_page(self) -> QWidget:
        page = QWidget(); root = QVBoxLayout(page)
        note = QLabel(
            "V9.3 首个实现先把控制器“意图”纳入标准系统定义，并继续复用现有 LLC/PFC 的 PI/PIF/2P2Z、Auto Design、Exact H(z) 与 C99 引擎。Solution Map / Live Tuning 在后续 9.3 tranche 接到同一模型。"
        )
        note.setWordWrap(True); root.addWidget(note)
        box = QGroupBox("Controller intent")
        form = QFormLayout(box)
        self.controller_structure = QComboBox()
        for item in ("PI", "PIF", "2P2Z"):
            self.controller_structure.addItem(item, item)
        self.target_fc = self._double(0, 1e6, 2, 0, " Hz")
        self.target_fc.setSpecialValueText("Use existing / Auto Design")
        self.target_pm = self._double(0, 179, 2, 0, "°")
        self.target_pm.setSpecialValueText("Use existing / Auto Design")
        form.addRow("Structure", self.controller_structure)
        form.addRow("Target crossover", self.target_fc)
        form.addRow("Target phase margin", self.target_pm)
        root.addWidget(box)
        root.addStretch(1)
        return page

    def _review_page(self) -> QWidget:
        page = QWidget(); root = QVBoxLayout(page)
        label = QLabel("System Review — 只有标准系统定义通过验证后，才允许进入现有分析引擎。")
        label.setWordWrap(True); root.addWidget(label)
        self.review = QPlainTextEdit(); self.review.setReadOnly(True)
        root.addWidget(self.review, 1)
        self.review_status = QLabel("")
        self.review_status.setWordWrap(True)
        root.addWidget(self.review_status)
        return page

    def _is_llc(self) -> bool:
        return self.llc_radio.isChecked()

    def _sync_topology_pages(self) -> None:
        index = 0 if self._is_llc() else 1
        if hasattr(self, "stage_stack"):
            self.stage_stack.setCurrentIndex(index)
        if hasattr(self, "sense_stack"):
            self.sense_stack.setCurrentIndex(index)

    def _page_changed(self, index: int) -> None:
        for i, label in enumerate(self.progress_labels):
            if i == index:
                label.setStyleSheet("padding:10px 12px;border-radius:6px;background:#eff8ff;color:#175cd3;font-weight:700;")
            elif i < index:
                label.setStyleSheet("padding:10px 12px;border-radius:6px;background:#ecfdf3;color:#067647;")
            else:
                label.setStyleSheet("padding:10px 12px;border-radius:6px;color:#667085;")
        self.back_button.setEnabled(index > 0)
        self.next_button.setVisible(index < self.PAGE_REVIEW)
        self.finish_button.setVisible(index == self.PAGE_REVIEW)
        if index == self.PAGE_REVIEW:
            self._refresh_review()

    def _next(self) -> None:
        if self.pages.currentIndex() < self.PAGE_REVIEW:
            self.pages.setCurrentIndex(self.pages.currentIndex() + 1)

    def _back(self) -> None:
        if self.pages.currentIndex() > 0:
            self.pages.setCurrentIndex(self.pages.currentIndex() - 1)

    @staticmethod
    def _sensor_from_fields(name: str, fields: dict[str, QDoubleSpinBox]) -> SensorDefinition:
        return SensorDefinition(
            name=name,
            front_end_gain_v_per_unit=fields["gain"].value(),
            amplifier_bandwidth_hz=fields["bw"].value() * 1e3,
            source_resistance_ohm=fields["source_r"].value(),
            source_capacitance_f=fields["source_c"].value() * 1e-9,
            adc_series_resistance_ohm=fields["out_r"].value(),
            adc_shunt_capacitance_f=fields["out_c"].value() * 1e-9,
            digital_filter_alpha=fields["alpha"].value(),
            sample_rate_hz=fields["sample"].value() * 1e3,
        )

    def build_definition(self) -> ControlSystemDefinition:
        adc = ADCDefinition(
            vref_v=self.adc_vref.value(), bits=self.adc_bits.value(),
            clock_hz=self.adc_clock.value() * 1e6,
            acquisition_time_s=self.adc_acq.value() * 1e-9,
            conversion_cycles=self.adc_cycles.value(), soc_count=self.adc_soc.value(),
            soc_spacing_s=self.adc_soc_spacing.value() * 1e-6,
            recursive_previous_weight=self.adc_prev_weight.value(),
        )
        timing = TimingDefinition(
            computation_delay_s=self.compute_delay.value() * 1e-6,
            pwm_update_delay_s=self.pwm_delay.value() * 1e-6,
            include_zero_order_hold=self.include_zoh.isChecked(),
        )
        intent = ControllerIntent(
            structure=str(self.controller_structure.currentData()),
            target_crossover_hz=(self.target_fc.value() or None),
            target_phase_margin_deg=(self.target_pm.value() or None),
        )
        if self._is_llc():
            stage = LLCStageDefinition(
                self.llc_vmin.value(), self.llc_vnom.value(), self.llc_vmax.value(),
                self.llc_vout.value(), self.llc_pout.value(),
                self.llc_fr.value()*1e3, self.llc_fmin.value()*1e3, self.llc_fmax.value()*1e3,
                self.llc_ln.value(), self.llc_q.value(), self.llc_np.value(), self.llc_ns.value(),
            )
            rup = self.llc_rup.value() * 1e3
            rlow = self.llc_rlow.value() * 1e3
            sensor = SensorDefinition(
                name="LLC output voltage",
                front_end_gain_v_per_unit=rlow / (rup + rlow),
                amplifier_gain=self.llc_amp_gain.value(),
                amplifier_bandwidth_hz=self.llc_amp_bw.value()*1e3,
                source_resistance_ohm=(rup*rlow)/(rup+rlow),
                source_capacitance_f=self.llc_cdiv.value()*1e-9,
                adc_series_resistance_ohm=self.llc_adc_r.value(),
                adc_shunt_capacitance_f=self.llc_adc_c.value()*1e-9,
                sample_rate_hz=self.llc_sample.value()*1e3,
                divider_upper_ohm=rup, divider_lower_ohm=rlow,
            )
            return ControlSystemDefinition(
                topology=SystemTopology.LLC,
                plant_source=self.plant_source.currentData(),
                architecture=ControlArchitecture.LLC_FM_VOLTAGE,
                llc_stage=stage,
                sensors=(sensor,), adc=adc,
                modulator=ModulatorDefinition(
                    kind="FM/TBPRD", switching_frequency_hz=stage.resonant_frequency_hz,
                    timer_clock_hz=self.timer_clock.value()*1e6,
                    count_mode=str(self.count_mode.currentData()),
                    duty_min=self.duty_min.value(), duty_max=self.duty_max.value(),
                    minimum_pulse_s=self.min_pulse.value()*1e-6,
                    deadtime_s=self.deadtime.value()*1e-9,
                ),
                timing=timing, controller=intent,
            )

        stage = TTPLStageDefinition(
            self.ttpl_vmin.value(), self.ttpl_vnom.value(), self.ttpl_vmax.value(),
            self.ttpl_line.value(), self.ttpl_vbus.value(), self.ttpl_pout.value(),
            self.ttpl_eff.value(), self.ttpl_fsw.value()*1e3,
            self.ttpl_l.value()*1e-6, self.ttpl_dcr.value()*1e-3,
            self.ttpl_cbus.value()*1e-6, self.ttpl_cesr.value()*1e-3,
        )
        sensors = (
            self._sensor_from_fields("PFC inductor current", self.current_sense),
            self._sensor_from_fields("AC input voltage", self.vac_sense),
            self._sensor_from_fields("PFC bus voltage", self.vbus_sense),
        )
        return ControlSystemDefinition(
            topology=SystemTopology.TTPL_PFC,
            plant_source=self.plant_source.currentData(),
            architecture=ControlArchitecture.TTPL_DUAL_LOOP,
            ttpl_stage=stage, sensors=sensors, adc=adc,
            modulator=ModulatorDefinition(
                kind="TTPL PWM", switching_frequency_hz=stage.switching_frequency_hz,
                timer_clock_hz=self.timer_clock.value()*1e6,
                count_mode=str(self.count_mode.currentData()),
                duty_min=self.duty_min.value(), duty_max=self.duty_max.value(),
                minimum_pulse_s=self.min_pulse.value()*1e-6,
                deadtime_s=self.deadtime.value()*1e-9,
            ),
            timing=timing, controller=intent,
        )

    def _refresh_review(self) -> None:
        try:
            definition = self.build_definition()
            checks = definition.validation_checks()
            stage = definition.llc_stage if definition.topology == SystemTopology.LLC else definition.ttpl_stage
            sensor_text = "\n".join(
                f"  - {s.name}: gain={s.front_end_gain_v_per_unit:.7g} V/unit, "
                f"BW={s.amplifier_bandwidth_hz/1e3:.4g} kHz, Fs={s.sample_rate_hz/1e3:.4g} kHz, alpha={s.digital_filter_alpha:.5g}"
                for s in definition.sensors
            )
            self.review.setPlainText(
                "SYSTEM DEFINITION — V9.3\n" + "="*76 + "\n"
                f"Topology      : {definition.topology.value}\n"
                f"Architecture  : {definition.architecture.value}\n"
                f"Plant source  : {definition.plant_source.value}\n"
                f"Power stage   : {stage}\n\n"
                "SENSING\n" + sensor_text + "\n\n"
                f"ADC           : {definition.adc.bits}-bit, {definition.adc.vref_v:g} V, "
                f"ADCCLK={definition.adc.clock_hz/1e6:g} MHz, ready≈{definition.adc.acquisition_to_ready_s*1e6:.4g} µs\n"
                f"Modulator     : {definition.modulator.kind}, fsw={definition.modulator.switching_frequency_hz/1e3:g} kHz, "
                f"timer={definition.modulator.timer_clock_hz/1e6:g} MHz, {definition.modulator.count_mode}\n"
                f"Timing        : compute={definition.timing.computation_delay_s*1e6:g} µs, "
                f"PWM update={definition.timing.pwm_update_delay_s*1e6:g} µs, ZOH={definition.timing.include_zero_order_hold}\n"
                f"Controller    : {definition.controller.structure}, target Fc={definition.controller.target_crossover_hz}, "
                f"target PM={definition.controller.target_phase_margin_deg}\n\n"
                "VALIDATION\n" + "\n".join(f"  ✓ {item}" for item in checks) + "\n\n"
                "MODEL BOUNDARY\n"
                "  - Wizard only defines the system; LLC/PFC maintained kernels remain the numerical authority.\n"
                "  - ADC Vref/bits are preserved in the canonical definition. TTPL firmware-correlated runtime consumes them; "
                "LLC linear Bode currently uses ADC timing/averaging but not amplitude quantization.\n"
                "  - Solution Map / Live Tuning will consume this same definition in the next V9.3 tranche."
            )
            self.definition = definition
            self.finish_button.setEnabled(True)
            self.review_status.setText("定义有效：可以进入现有强分析引擎。")
            self.review_status.setStyleSheet("padding:7px;background:#ecfdf3;color:#067647;border:1px solid #75e0a7;")
        except Exception as exc:
            self.definition = None
            self.finish_button.setEnabled(False)
            self.review.setPlainText(f"SYSTEM DEFINITION INVALID\n\n{exc}")
            self.review_status.setText("请返回对应步骤修正参数。")
            self.review_status.setStyleSheet("padding:7px;background:#fef3f2;color:#b42318;border:1px solid #fda29b;")

    def _finish(self) -> None:
        self._refresh_review()
        if self.definition is not None:
            self.accept()


def llc_spec_from_system_definition(definition: ControlSystemDefinition, base: LLCDesignSpec) -> LLCDesignSpec:
    definition.validate()
    if definition.topology != SystemTopology.LLC or definition.llc_stage is None:
        raise ValueError("LLC adapter requires an LLC system definition")
    stage = definition.llc_stage
    spec = base.clone(
        vbus_min_normal_v=stage.vbus_min_v,
        vbus_nom_v=stage.vbus_nom_v,
        vbus_max_v=stage.vbus_max_v,
        vbus_hold_end_v=min(base.vbus_hold_end_v, stage.vbus_min_v),
        vout_v=stage.vout_v,
        pout_w=stage.pout_w,
        resonant_frequency_hz=stage.resonant_frequency_hz,
        minimum_frequency_hz=stage.minimum_frequency_hz,
        maximum_frequency_hz=stage.maximum_frequency_hz,
        ln_ratio=stage.ln_ratio,
        q_full_load=stage.q_full_load,
        primary_turns=stage.primary_turns,
        secondary_turns=stage.secondary_turns,
        primary_deadtime_s=definition.modulator.deadtime_s,
    )
    spec.validate()
    return spec


def _sense_from_definition(default: ExternalSenseConfig, sensor: SensorDefinition,
                           adc: ADCDefinition) -> ExternalSenseConfig:
    timing = ADCTimingConfig(
        sample_rate_hz=sensor.sample_rate_hz,
        adc_clock_hz=adc.clock_hz,
        acquisition_time_s=adc.acquisition_time_s,
        conversion_cycles=adc.conversion_cycles,
        soc_count=adc.soc_count,
        soc_spacing_s=adc.soc_spacing_s,
        recursive_previous_weight=adc.recursive_previous_weight,
        computation_delay_s=0.0,
        pwm_update_delay_s=0.0,
        include_zero_order_hold=default.timing.include_zero_order_hold,
        digital_filter=DigitalFilterConfig(sensor.digital_filter_alpha),
    )
    return replace(
        default,
        front_end_gain_v_per_unit=sensor.front_end_gain_v_per_unit,
        amplifier_gain=sensor.amplifier_gain,
        amplifier_bandwidth_hz=sensor.amplifier_bandwidth_hz,
        source_resistance_ohm=sensor.source_resistance_ohm,
        shunt_capacitance_f=sensor.source_capacitance_f,
        output_resistance_ohm=sensor.adc_series_resistance_ohm,
        adc_capacitance_f=sensor.adc_shunt_capacitance_f,
        adc_vref_v=adc.vref_v,
        adc_bits=adc.bits,
        timing=timing,
    )


def ttpl_config_from_system_definition(definition: ControlSystemDefinition,
                                       base: PFCControlLabConfig | None = None) -> PFCControlLabConfig:
    definition.validate()
    if definition.topology != SystemTopology.TTPL_PFC or definition.ttpl_stage is None:
        raise ValueError("TTPL adapter requires a TTPL system definition")
    base = base or PFCControlLabConfig()
    stage_in = definition.ttpl_stage
    stage = replace(
        base.power_stage,
        vin_rms_v=stage_in.vin_nom_rms_v,
        line_frequency_hz=stage_in.line_frequency_hz,
        bus_voltage_v=stage_in.bus_voltage_v,
        output_power_w=stage_in.output_power_w,
        switching_frequency_hz=stage_in.switching_frequency_hz,
        boost_inductance_h=stage_in.boost_inductance_h,
        equivalent_series_resistance_ohm=stage_in.inductor_dcr_ohm,
        bus_capacitance_f=stage_in.bus_capacitance_f,
        bus_cap_esr_ohm=stage_in.bus_cap_esr_ohm,
        efficiency=stage_in.efficiency,
        duty_min=definition.modulator.duty_min,
        duty_max=definition.modulator.duty_max,
        minimum_effective_pulse_s=definition.modulator.minimum_pulse_s,
        deadtime_s=definition.modulator.deadtime_s,
    )
    sensors = {sensor.name: sensor for sensor in definition.sensors}
    current = sensors["PFC inductor current"]
    vac = sensors["AC input voltage"]
    vbus = sensors["PFC bus voltage"]
    current_rate = current.sample_rate_hz
    voltage_rate = vbus.sample_rate_hz
    firmware = PFCFirmwareAlgorithmConfig(
        current_loop_rate_hz=current_rate,
        amc_rate_hz=min(base.firmware.amc_rate_hz, current_rate),
        voltage_loop_rate_hz=voltage_rate,
        vac_rms_lpf_alpha=base.firmware.vac_rms_lpf_alpha,
        vac_rms_feedforward_gain=base.firmware.vac_rms_feedforward_gain,
        gcmd_max_a_per_v=base.firmware.gcmd_max_a_per_v,
        indu_comp_gain=base.firmware.indu_comp_gain,
        indu_comp_min=base.firmware.indu_comp_min,
        indu_comp_max=base.firmware.indu_comp_max,
        vff_bypass=base.firmware.vff_bypass,
        current_computation_delay_s=definition.timing.computation_delay_s,
        current_pwm_update_delay_s=definition.timing.pwm_update_delay_s,
        amc_update_delay_s=base.firmware.amc_update_delay_s,
        voltage_computation_delay_s=definition.timing.computation_delay_s,
    )
    current_controller = replace(base.current_controller, sample_time_s=1.0/current_rate)
    voltage_controller = replace(base.voltage_controller, sample_time_s=1.0/voltage_rate)
    config = replace(
        base,
        power_stage=stage,
        firmware=firmware,
        current_controller=current_controller,
        voltage_controller=voltage_controller,
        current_sense=_sense_from_definition(base.current_sense, current, definition.adc),
        vac_sense=_sense_from_definition(base.vac_sense, vac, definition.adc),
        vbus_sense=_sense_from_definition(base.vbus_sense, vbus, definition.adc),
    )
    config.validate()
    return config


def apply_definition_to_llc_window(window, definition: ControlSystemDefinition) -> LLCDesignSpec:
    """Populate the existing LLC expert workspace from the canonical model."""
    spec = llc_spec_from_system_definition(definition, window.spec)
    window.spec = spec
    window._load_spec_to_widgets(spec)
    window.guided_system_definition = definition
    sensor = definition.sensors[0]
    loop = window.digital_loop_view
    loop.sample_us.setValue(1e6 / sensor.sample_rate_hz)
    loop.timer_mhz.setValue(definition.modulator.timer_clock_hz / 1e6)
    for index in range(loop.count_mode.count()):
        data = loop.count_mode.itemData(index)
        value = getattr(data, "value", str(data)).lower()
        if value == definition.modulator.count_mode:
            loop.count_mode.setCurrentIndex(index); break
    loop.computation_us.setValue(definition.timing.computation_delay_s * 1e6)
    loop.include_zoh.setChecked(definition.timing.include_zero_order_hold)
    if sensor.divider_upper_ohm > 0.0 and sensor.divider_lower_ohm > 0.0:
        loop.rup_k.setValue(sensor.divider_upper_ohm / 1e3)
        loop.rlow_k.setValue(sensor.divider_lower_ohm / 1e3)
    loop.cdiv_nf.setValue(sensor.source_capacitance_f * 1e9)
    loop.opamp_gain.setValue(sensor.amplifier_gain)
    loop.opamp_bw_khz.setValue(sensor.amplifier_bandwidth_hz / 1e3)
    loop.adc_r.setValue(sensor.adc_series_resistance_ohm)
    loop.adc_c_nf.setValue(sensor.adc_shunt_capacitance_f * 1e9)
    loop.adc_clock_mhz.setValue(definition.adc.clock_hz / 1e6)
    loop.acq_ns.setValue(definition.adc.acquisition_time_s * 1e9)
    loop.conversion_cycles.setValue(definition.adc.conversion_cycles)
    loop.soc_count.setValue(definition.adc.soc_count)
    loop.previous_weight.setValue(definition.adc.recursive_previous_weight)
    window.statusBar().showMessage("V9.3 Guided System Definition 已应用到 LLC；正在复用现有设计/控制引擎。", 8000)
    return spec


def apply_definition_to_ttpl_window(window, definition: ControlSystemDefinition) -> PFCControlLabConfig:
    """Populate the existing TTPL expert workspace and return the exact run config."""
    config = ttpl_config_from_system_definition(definition)
    window.guided_system_definition = definition
    window.guided_ttpl_config = config
    workbench = window.control_lab_view
    stage = definition.ttpl_stage
    assert stage is not None
    design = workbench.power_stage_view
    design.vin_min.setValue(stage.vin_min_rms_v)
    design.vin_nom.setValue(stage.vin_nom_rms_v)
    design.vin_max.setValue(stage.vin_max_rms_v)
    design.line_hz.setValue(stage.line_frequency_hz)
    design.vbus.setValue(stage.bus_voltage_v)
    design.pout.setValue(stage.output_power_w)
    design.efficiency.setValue(stage.efficiency)
    design.fsw.setValue(stage.switching_frequency_hz/1e3)

    control = workbench.control_lab
    control.engineering_efficiency = stage.efficiency
    control.vin_rms.setValue(stage.vin_nom_rms_v)
    control.line_hz.setValue(stage.line_frequency_hz)
    control.vbus.setValue(stage.bus_voltage_v)
    control.pout.setValue(stage.output_power_w)
    control.fsw.setValue(stage.switching_frequency_hz/1e3)
    control.inductance.setValue(stage.boost_inductance_h*1e6)
    control.dcr.setValue(stage.inductor_dcr_ohm*1e3)
    control.cbus.setValue(stage.bus_capacitance_f*1e6)
    control.cbus_esr.setValue(stage.bus_cap_esr_ohm*1e3)
    control.duty_min.setValue(definition.modulator.duty_min)
    control.duty_max.setValue(definition.modulator.duty_max)
    control.min_pulse_us.setValue(definition.modulator.minimum_pulse_s*1e6)
    control.deadtime_ns.setValue(definition.modulator.deadtime_s*1e9)
    control.current_delay_us.setValue(definition.timing.total_command_delay_s*1e6)
    control.voltage_delay_us.setValue(definition.timing.computation_delay_s*1e6)

    sensor_widgets = {
        "PFC inductor current": control.current_sense,
        "AC input voltage": control.vac_sense,
        "PFC bus voltage": control.vbus_sense,
    }
    for sensor in definition.sensors:
        fields = sensor_widgets[sensor.name]
        fields["gain"].setValue(sensor.front_end_gain_v_per_unit)
        fields["bw"].setValue(sensor.amplifier_bandwidth_hz/1e3)
        fields["source_r"].setValue(sensor.source_resistance_ohm)
        fields["source_c"].setValue(sensor.source_capacitance_f*1e9)
        fields["out_r"].setValue(sensor.adc_series_resistance_ohm)
        fields["out_c"].setValue(sensor.adc_shunt_capacitance_f*1e9)
        fields["sample"].setValue(sensor.sample_rate_hz/1e3)
        fields["alpha"].setValue(sensor.digital_filter_alpha)
    window.statusBar().showMessage("V9.3 Guided System Definition 已应用到 TTPL；即将运行同一维护分析链。", 8000)
    return config


__all__ = [
    "SystemModelingDesignDialog",
    "apply_definition_to_llc_window",
    "apply_definition_to_ttpl_window",
    "llc_spec_from_system_definition",
    "ttpl_config_from_system_definition",
]
