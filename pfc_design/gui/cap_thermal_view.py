"""TTPL DC-bus capacitor-bank and semiconductor thermal design page."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase

from pfc_design.engineering import (
    CapacitorBankDesignConfig,
    CapacitorBankResult,
    CapacitorUnitSpec,
    PFCDeviceDatabase,
    TTPLDesignResult,
    TTPLThermalConfig,
    TTPLThermalResult,
    design_bus_capacitor_bank,
    solve_ttpl_semiconductor_thermal,
)


_CAP_PRESETS: dict[str, CapacitorUnitSpec] = {
    "Generic 450 V / 470 uF": CapacitorUnitSpec(
        name="Generic 450 V / 470 uF electrolytic",
        capacitance_f=470e-6,
        rated_voltage_v=450.0,
        esr_ohm=0.120,
        rated_ripple_current_a=3.5,
        rated_temperature_c=105.0,
        rated_life_h=5000.0,
        thermal_resistance_k_per_w=18.0,
    ),
    "Generic 450 V / 680 uF": CapacitorUnitSpec(
        name="Generic 450 V / 680 uF electrolytic",
        capacitance_f=680e-6,
        rated_voltage_v=450.0,
        esr_ohm=0.090,
        rated_ripple_current_a=4.2,
        rated_temperature_c=105.0,
        rated_life_h=5000.0,
        thermal_resistance_k_per_w=16.0,
    ),
    "Generic 500 V / 470 uF": CapacitorUnitSpec(
        name="Generic 500 V / 470 uF electrolytic",
        capacitance_f=470e-6,
        rated_voltage_v=500.0,
        esr_ohm=0.130,
        rated_ripple_current_a=3.3,
        rated_temperature_c=105.0,
        rated_life_h=5000.0,
        thermal_resistance_k_per_w=18.0,
    ),
}


class TTPLCapacitorThermalView(QWidget):
    """Hardware-oriented bus-capacitor sizing and lumped thermal screening."""

    def __init__(
        self,
        design: TTPLDesignResult | None = None,
        database: PFCDeviceDatabase | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.design = design
        self.database = database or PFCDeviceDatabase()
        self.cap_result: CapacitorBankResult | None = None
        self.thermal_result: TTPLThermalResult | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)

        header = QHBoxLayout()
        title = QLabel("TTPL Bus Capacitor / Thermal Design")
        title.setStyleSheet("font-size:16px;font-weight:650;")
        header.addWidget(title)
        hint = QLabel("Cbus bank sizing · ESR / hot-spot / life · semiconductor loss↔temperature fixed point")
        hint.setStyleSheet("color:#667085;")
        header.addWidget(hint)
        header.addStretch(1)
        self.run_all_button = QPushButton("Run Capacitor + Thermal")
        header.addWidget(self.run_all_button)
        root.addLayout(header)

        note = QLabel(
            "Engineering screening only. Capacitor life uses a 10°C empirical rule and lumped ESR heating; "
            "semiconductor temperatures use lumped junction-to-ambient thermal resistance. Verify selected vendor data, "
            "heatsink/interface/airflow and hardware temperatures before release."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_capacitor_page(), "DC-Bus Capacitor Bank")
        self.tabs.addTab(self._build_thermal_page(), "Semiconductor Thermal")
        root.addWidget(self.tabs, 1)

        self.run_all_button.clicked.connect(self.run_all)
        self.cap_run_button.clicked.connect(self.run_capacitor)
        self.thermal_run_button.clicked.connect(self.run_thermal)
        self.cap_preset.currentIndexChanged.connect(self._apply_cap_preset)
        self._apply_cap_preset()
        self.refresh_devices()
        if self.design is not None:
            self.run_all()

    @staticmethod
    def _spin(lo: float, hi: float, decimals: int, value: float, suffix: str = "") -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(lo, hi)
        widget.setDecimals(decimals)
        widget.setValue(value)
        widget.setSuffix(suffix)
        widget.setKeyboardTracking(False)
        return widget

    def _build_capacitor_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(2, 2, 6, 2)

        unit_group = QGroupBox("Unit capacitor")
        unit = QFormLayout(unit_group)
        self.cap_preset = QComboBox()
        for name in _CAP_PRESETS:
            self.cap_preset.addItem(name)
        self.cap_c = self._spin(1.0, 100000.0, 2, 470.0, " uF")
        self.cap_v = self._spin(10.0, 2000.0, 1, 450.0, " V")
        self.cap_esr = self._spin(0.001, 10000.0, 3, 120.0, " mOhm")
        self.cap_ripple = self._spin(0.01, 1000.0, 3, 3.5, " A")
        self.cap_rated_temp = self._spin(40.0, 200.0, 1, 105.0, " °C")
        self.cap_life = self._spin(1.0, 200000.0, 0, 5000.0, " h")
        self.cap_rth = self._spin(0.1, 200.0, 2, 18.0, " K/W")
        for label, widget in (
            ("Preset", self.cap_preset),
            ("Capacitance", self.cap_c),
            ("Rated voltage", self.cap_v),
            ("ESR", self.cap_esr),
            ("Rated ripple", self.cap_ripple),
            ("Rated temperature", self.cap_rated_temp),
            ("Rated life", self.cap_life),
            ("Thermal resistance", self.cap_rth),
        ):
            unit.addRow(label, widget)
        left_layout.addWidget(unit_group)

        design_group = QGroupBox("Bank derating / environment")
        form = QFormLayout(design_group)
        self.cap_v_derating = self._spin(0.10, 1.0, 3, 0.90)
        self.cap_i_derating = self._spin(0.10, 1.0, 3, 0.80)
        self.cap_ambient = self._spin(-40.0, 150.0, 1, 45.0, " °C")
        form.addRow("Voltage derating", self.cap_v_derating)
        form.addRow("Ripple-current derating", self.cap_i_derating)
        form.addRow("Ambient", self.cap_ambient)
        left_layout.addWidget(design_group)
        self.cap_run_button = QPushButton("Auto Size Capacitor Bank")
        left_layout.addWidget(self.cap_run_button)
        left_layout.addStretch(1)

        self.cap_summary = QPlainTextEdit()
        self.cap_summary.setReadOnly(True)
        self.cap_summary.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.cap_summary.setPlainText("Run Power Stage / Sizing first.")

        splitter.addWidget(left)
        splitter.addWidget(self.cap_summary)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([390, 1050])
        layout.addWidget(splitter, 1)
        return page

    def _build_thermal_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(2, 2, 6, 2)

        device_group = QGroupBox("Semiconductor selection")
        form = QFormLayout(device_group)
        self.hf_device = QComboBox()
        self.slow_device = QComboBox()
        self.thermal_workpoint = QComboBox()
        self.thermal_workpoint.addItem("Low line", "low")
        self.thermal_workpoint.addItem("Nominal", "nominal")
        self.thermal_workpoint.addItem("High line", "high")
        form.addRow("HF half-bridge", self.hf_device)
        form.addRow("Line-frequency leg", self.slow_device)
        form.addRow("Workpoint", self.thermal_workpoint)
        left_layout.addWidget(device_group)

        thermal_group = QGroupBox("Lumped thermal network")
        form = QFormLayout(thermal_group)
        self.thermal_ambient = self._spin(-40.0, 150.0, 1, 45.0, " °C")
        self.hf_rth = self._spin(0.01, 100.0, 3, 2.0, " K/W")
        self.slow_rth = self._spin(0.01, 100.0, 3, 3.0, " K/W")
        self.max_tj = self._spin(50.0, 250.0, 1, 150.0, " °C")
        self.thermal_deadtime = self._spin(0.0, 2000.0, 1, 100.0, " ns")
        self.thermal_reverse_drop = self._spin(0.0, 10.0, 3, 2.0, " V")
        self.thermal_v_derating = self._spin(0.10, 1.0, 3, 0.80)
        for label, widget in (
            ("Ambient", self.thermal_ambient),
            ("HF Rtheta JA", self.hf_rth),
            ("Slow-leg Rtheta JA", self.slow_rth),
            ("Maximum Tj", self.max_tj),
            ("Deadtime", self.thermal_deadtime),
            ("Reverse drop", self.thermal_reverse_drop),
            ("VDS derating", self.thermal_v_derating),
        ):
            form.addRow(label, widget)
        left_layout.addWidget(thermal_group)
        self.thermal_run_button = QPushButton("Solve Loss / Temperature")
        left_layout.addWidget(self.thermal_run_button)
        left_layout.addStretch(1)

        self.thermal_summary = QPlainTextEdit()
        self.thermal_summary.setReadOnly(True)
        self.thermal_summary.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.thermal_summary.setPlainText("Run Power Stage / Sizing first.")

        splitter.addWidget(left)
        splitter.addWidget(self.thermal_summary)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([390, 1050])
        layout.addWidget(splitter, 1)
        return page

    def _apply_cap_preset(self, *_args) -> None:
        preset = _CAP_PRESETS[self.cap_preset.currentText()]
        self.cap_c.setValue(preset.capacitance_f * 1e6)
        self.cap_v.setValue(preset.rated_voltage_v)
        self.cap_esr.setValue(preset.esr_ohm * 1e3)
        self.cap_ripple.setValue(preset.rated_ripple_current_a)
        self.cap_rated_temp.setValue(preset.rated_temperature_c)
        self.cap_life.setValue(preset.rated_life_h)
        self.cap_rth.setValue(preset.thermal_resistance_k_per_w)

    def refresh_devices(self) -> None:
        self.database.refresh()
        hf_current = self.hf_device.currentData()
        slow_current = self.slow_device.currentData()
        self.hf_device.clear()
        self.slow_device.clear()
        for device in self.database.all:
            label = f"{device.part_number} · {device.technology} · {device.rds_on_25c*1e3:.1f} mOhm"
            self.hf_device.addItem(label, device.part_number)
            self.slow_device.addItem(label, device.part_number)
        self._restore_combo(self.hf_device, hf_current, preferred="GS66516T")
        self._restore_combo(self.slow_device, slow_current, preferred="IPW65R032M8")

    @staticmethod
    def _restore_combo(combo: QComboBox, previous, *, preferred: str) -> None:
        target = previous or preferred
        for index in range(combo.count()):
            if str(combo.itemData(index)).casefold() == str(target).casefold():
                combo.setCurrentIndex(index)
                return
        if combo.count():
            combo.setCurrentIndex(0)

    def set_design_result(self, result: TTPLDesignResult) -> None:
        self.design = result
        self.run_all()

    def run_all(self) -> None:
        self.run_capacitor()
        self.run_thermal()

    def run_capacitor(self) -> None:
        if self.design is None:
            self.cap_summary.setPlainText("Run Power Stage / Sizing first.")
            return
        try:
            unit = CapacitorUnitSpec(
                name=self.cap_preset.currentText() + " / edited",
                capacitance_f=self.cap_c.value() * 1e-6,
                rated_voltage_v=self.cap_v.value(),
                esr_ohm=self.cap_esr.value() * 1e-3,
                rated_ripple_current_a=self.cap_ripple.value(),
                rated_temperature_c=self.cap_rated_temp.value(),
                rated_life_h=self.cap_life.value(),
                thermal_resistance_k_per_w=self.cap_rth.value(),
            )
            config = CapacitorBankDesignConfig(
                voltage_derating=self.cap_v_derating.value(),
                ripple_current_derating=self.cap_i_derating.value(),
                ambient_temperature_c=self.cap_ambient.value(),
            )
            result = design_bus_capacitor_bank(self.design, unit, config)
        except Exception as exc:
            self.cap_summary.setPlainText(f"Capacitor design failed: {exc}")
            return
        self.cap_result = result
        warnings = "\n".join(f"  - {item}" for item in result.warnings)
        self.cap_summary.setPlainText(
            "TTPL DC-BUS CAPACITOR BANK\n"
            + "=" * 74
            + "\n"
            + f"Required Cbus       : {result.design.recommended_bus_capacitance_uf:.1f} uF\n"
            + f"Bank topology       : {result.series_count}S x {result.parallel_count}P = {result.total_count} capacitors\n"
            + f"Actual Cbank        : {result.bank_capacitance_uf:.1f} uF\n"
            + f"Bank ESR            : {result.bank_esr_ohm*1e3:.3f} mOhm\n"
            + f"Bank ripple Irms    : {result.bank_ripple_current_rms_a:.3f} A\n"
            + f"Per-cap voltage     : {result.per_cap_voltage_v:.2f} V\n"
            + f"Per-cap ripple      : {result.per_cap_ripple_current_rms_a:.3f} A\n"
            + f"Per-cap ESR loss    : {result.per_cap_esr_loss_w:.3f} W\n"
            + f"Total ESR loss      : {result.bank_esr_loss_w:.3f} W\n"
            + f"Estimated hot-spot  : {result.estimated_hotspot_c:.2f} °C\n"
            + f"Estimated life      : {result.estimated_life_h:.0f} h / {result.estimated_life_years:.2f} years\n"
            + f"Predicted 2xline dV : {result.predicted_bus_ripple_pp_v:.3f} Vpp\n"
            + f"Capacitance check   : {'PASS' if result.capacitance_ok else 'FAIL'}\n"
            + f"Voltage derating    : {'PASS' if result.voltage_ok else 'FAIL'}\n"
            + f"Ripple derating     : {'PASS' if result.ripple_current_ok else 'FAIL'}\n\n"
            + "MODEL / VALIDATION WARNINGS\n"
            + warnings
        )

    def run_thermal(self) -> None:
        if self.design is None:
            self.thermal_summary.setPlainText("Run Power Stage / Sizing first.")
            return
        self.refresh_devices()
        if self.hf_device.count() == 0 or self.slow_device.count() == 0:
            self.thermal_summary.setPlainText("No MOSFET records available.")
            return
        try:
            hf = self.database.get(str(self.hf_device.currentData()))
            slow = self.database.get(str(self.slow_device.currentData()))
            config = TTPLThermalConfig(
                ambient_temperature_c=self.thermal_ambient.value(),
                hf_rth_ja_k_per_w=self.hf_rth.value(),
                slow_rth_ja_k_per_w=self.slow_rth.value(),
                maximum_junction_temperature_c=self.max_tj.value(),
                voltage_derating=self.thermal_v_derating.value(),
                deadtime_s=self.thermal_deadtime.value() * 1e-9,
                reverse_drop_v=self.thermal_reverse_drop.value(),
            )
            result = solve_ttpl_semiconductor_thermal(
                self.design,
                hf,
                slow,
                workpoint=str(self.thermal_workpoint.currentData()),
                config=config,
            )
        except Exception as exc:
            self.thermal_summary.setPlainText(f"Thermal solve failed: {exc}")
            return
        self.thermal_result = result
        warnings = "\n".join(f"  - {item}" for item in result.warnings)
        self.thermal_summary.setPlainText(
            "TTPL SEMICONDUCTOR THERMAL SCREEN\n"
            + "=" * 74
            + "\n"
            + f"Workpoint           : {result.workpoint} / {result.vin_rms_v:.2f} Vrms\n"
            + f"HF device           : {result.hf_device.part_number} ({result.hf_device.technology})\n"
            + f"Slow-leg device     : {result.slow_device.part_number} ({result.slow_device.technology})\n"
            + f"HF active loss      : {result.active_device_loss_w:.3f} W\n"
            + f"HF SR loss          : {result.sr_device_loss_w:.3f} W\n"
            + f"Slow loss / device  : {result.slow_device_loss_each_w:.3f} W\n"
            + f"Tj active           : {result.active_junction_temperature_c:.2f} °C\n"
            + f"Tj SR               : {result.sr_junction_temperature_c:.2f} °C\n"
            + f"Tj slow             : {result.slow_junction_temperature_c:.2f} °C\n"
            + f"Converged           : {'YES' if result.converged else 'NO'} in {result.iterations} iterations\n"
            + f"Thermal limit       : {'PASS' if result.thermal_ok else 'FAIL'}\n"
            + f"VDS / current       : {'PASS' if result.electrical_ok else 'FAIL'}\n"
            + f"HF switching model  : {result.hf_loss.switching_model}\n\n"
            + "MODEL / VALIDATION WARNINGS\n"
            + warnings
        )


__all__ = ["TTPLCapacitorThermalView"]
