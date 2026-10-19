"""Reusable TTPL/Vienna PFC boost-inductor design widgets."""
from __future__ import annotations

from dataclasses import replace, fields
from typing import Callable

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pfc_design.magnetics import (
    HIGH_FLUX_254,
    HIGH_FLUX_254_MATERIALS,
    HighFluxCoreGeometry,
    PFCInductorDesignRequest,
    PFCInductorDesignResult,
    design_pfc_inductor,
    FerriteInductorRequest,
    FerriteInductorResult,
    design_ferrite_pfc_inductor,
)
from pfc_design.magnetics.core_database import CoreDatabase
from pfc_design.magnetics.core_entry import CoreSpec
from pfc_design.magnetics.steinmetz import SteinmetzMaterial
from power_control_tools.part_validation import (
    brand_display_names,
    format_missing,
    load_brand_taxonomy,
    missing_ferrite_steinmetz_parameters,
)
from llc_design.gui import theme
from llc_design.user_messages import show_operation_issue
from llc_design.i18n import t


def apply_stage_inductor_values(l_widget, r_widget, inductance_uh, dcr_mohm):
    """Check both target controls before writing either; never silently clamp."""
    import math
    for name, widget, value in (("L [µH]", l_widget, inductance_uh), ("DCR [mΩ]", r_widget, dcr_mohm)):
        if not math.isfinite(value) or not widget.minimum() <= value <= widget.maximum():
            raise ValueError(f"Calculated {name}={value:g} is outside the stage editor range [{widget.minimum():g}, {widget.maximum():g}]; stage unchanged")
    l_widget.setValue(inductance_uh)
    r_widget.setValue(dcr_mohm)


def _prompt_user_core(parent) -> tuple[CoreSpec, SteinmetzMaterial] | None:
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit

    dialog = QDialog(parent)
    dialog.setWindowTitle("Add user magnetic core")
    dialog.resize(480, 520)
    root = QVBoxLayout(dialog)
    note = QLabel(
        "Enter geometry and AL from one manufacturer datasheet revision. "
        "Brand placeholders such as 铂科 / 东睦科达 stay non-calculable until these fields and Steinmetz data exist. "
        "Do not invent coefficients."
    )
    note.setWordWrap(True)
    root.addWidget(note)
    form = QFormLayout()
    brand = QComboBox()
    for name in brand_display_names():
        brand.addItem(name)
    part = QLineEdit("USER_CORE_NEW")
    material = QLineEdit("Ferrite custom")
    material_class = QComboBox()
    material_class.addItems(["Ferrite", "HighFlux", "Sendust", "MPP", "Amorphous", "Nanocrystalline"])
    source = QLineEdit("")
    od = QDoubleSpinBox(); od.setRange(1, 500); od.setValue(50); od.setSuffix(" mm")
    id_ = QDoubleSpinBox(); id_.setRange(0.1, 499); id_.setValue(30); id_.setSuffix(" mm")
    ht = QDoubleSpinBox(); ht.setRange(0.1, 200); ht.setValue(20); ht.setSuffix(" mm")
    ae = QDoubleSpinBox(); ae.setRange(0.01, 100); ae.setDecimals(4); ae.setValue(1.5); ae.setSuffix(" cm²")
    le = QDoubleSpinBox(); le.setRange(0.1, 100); le.setDecimals(3); le.setValue(12.0); le.setSuffix(" cm")
    ve = QDoubleSpinBox(); ve.setRange(0.01, 1000); ve.setDecimals(3); ve.setValue(18.0); ve.setSuffix(" cm³")
    al = QDoubleSpinBox(); al.setRange(0.01, 10000); al.setDecimals(3); al.setValue(100.0); al.setSuffix(" nH/T²")
    ui = QDoubleSpinBox(); ui.setRange(1, 20000); ui.setValue(2000)
    bs = QDoubleSpinBox(); bs.setRange(0.05, 3); bs.setDecimals(3); bs.setValue(0.45); bs.setSuffix(" T")
    sk = QDoubleSpinBox(); sk.setRange(1e-6, 1e3); sk.setDecimals(6); sk.setValue(0.3)
    sa = QDoubleSpinBox(); sa.setRange(0.1, 5); sa.setDecimals(3); sa.setValue(1.25)
    sb = QDoubleSpinBox(); sb.setRange(0.1, 5); sb.setDecimals(3); sb.setValue(2.50)
    for label, w in (
        ("Brand / manufacturer", brand), ("Part number", part), ("Material name", material),
        ("Material class", material_class), ("Source / revision URL", source),
        ("OD", od), ("ID", id_), ("HT", ht), ("Ae", ae), ("Le", le), ("Ve", ve),
        ("AL", al), ("μi", ui), ("Bs", bs),
        ("Steinmetz k", sk), ("Steinmetz α", sa), ("Steinmetz β", sb),
    ):
        form.addRow(label, w)
    root.addLayout(form)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    root.addWidget(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    if not part.text().strip():
        QMessageBox.warning(parent, "User core", "Part number required.")
        return None
    core = CoreSpec(
        manufacturer=brand.currentText().strip(),
        part_number=part.text().strip(),
        material=material.text().strip() or "User material",
        material_class=material_class.currentText(),
        od_mm=od.value(),
        id_mm=id_.value(),
        ht_mm=ht.value(),
        ae_cm2=ae.value(),
        le_cm=le.value(),
        ve_cm3=ve.value(),
        al_nH_per_t2=al.value(),
        ui=ui.value(),
        bs_T=bs.value(),
        source_url=source.text().strip() or None,
        dc_bias_coeffs=[100.0, 0.0, 0.0, 0.0, 0.0],
    )
    steinmetz = SteinmetzMaterial(
        name=core.material, k=sk.value(), alpha=sa.value(), beta=sb.value(),
    )
    return core, steinmetz


class PFCInductorDesignEditor(QWidget):
    """Narrow parameter editor suitable for the PFC left inspector."""

    design_completed = Signal(object)
    design_invalidated = Signal()
    reference_completed = Signal(object)

    def __init__(
        self,
        topology: str,
        context_provider: Callable[[], dict[str, float]],
        apply_callback: Callable[[PFCInductorDesignResult], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._saved_context = None
        self._source_request_extras = {}
        self._magnetic_generation = 0
        self.topology = topology
        self.context_provider = context_provider
        self.apply_callback = apply_callback
        self.last_result: PFCInductorDesignResult | FerriteInductorResult | None = None
        self.core_db = CoreDatabase()
        self._taxonomy = load_brand_taxonomy()
        self._rebuild_ferrite_cores()

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(7)

        intro = QLabel(
            "PFC 升压电感：High Flux 原有模型、铁氧体气隙模型、CSC 来源数据与参考计算。"
            "缺参不会借用其他材料。"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{theme.active_theme().text_muted};padding:4px 2px;")
        root.addWidget(intro)

        path_group = QGroupBox("磁芯路径")
        path_form = QFormLayout(path_group)
        self.material_path = QComboBox()
        self.material_path.addItem("Powder — Magnetics High Flux", "high_flux")
        self.material_path.addItem("Ferrite — gapped / Steinmetz", "ferrite")
        self.material_path.addItem("CSC PDF 来源磁环 / 参考模型", "source_catalog")
        self.material_path.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.material_path.setMinimumContentsLength(12)
        path_form.addRow("Model path", self.material_path)
        root.addWidget(path_group)
        self.material_path.currentIndexChanged.connect(self._path_changed)

        op_group = QGroupBox("工作点 / 目标")
        op = QFormLayout(op_group)
        self.context_label = QLabel("尚未同步")
        self.context_label.setWordWrap(True)
        self.target_l = self._spin(1.0, 20000.0, 2, 220.0, " µH")
        self.eff = self._spin(0.80, 1.0, 4, 0.97)
        self.n_cores = QSpinBox(); self.n_cores.setRange(1, 16)
        self.n_cores.setValue(2 if topology == "ttpl" else 5)
        op.addRow("功率级", self.context_label)
        op.addRow("满载目标 L", self.target_l)
        op.addRow("估算效率 η", self.eff)
        op.addRow("叠加磁芯数", self.n_cores)
        root.addWidget(op_group)

        core_group = QGroupBox("磁芯 — Magnetics High Flux 254")
        self.high_flux_group = core_group
        core = QFormLayout(core_group)
        self.perm = QComboBox()
        for mu in sorted(HIGH_FLUX_254_MATERIALS):
            self.perm.addItem(f"{mu} µ", mu)
        self.perm.setCurrentIndex(self.perm.findData(60))
        self.al = self._spin(0.01, 5000.0, 3, 81.0, " nH/T²")
        self.le = self._spin(1.0, 1000.0, 2, HIGH_FLUX_254.le_mm, " mm")
        self.ae = self._spin(0.1, 10000.0, 2, HIGH_FLUX_254.ae_mm2, " mm²")
        self.ve = self._spin(1.0, 1e7, 1, HIGH_FLUX_254.ve_mm3, " mm³")
        self.od = self._spin(1.0, 500.0, 2, HIGH_FLUX_254.od_mm, " mm")
        self.id_ = self._spin(0.1, 499.0, 2, HIGH_FLUX_254.id_mm, " mm")
        self.ht = self._spin(0.1, 200.0, 2, HIGH_FLUX_254.ht_mm, " mm")
        self.bsat = self._spin(0.1, 3.0, 3, HIGH_FLUX_254.bs_t, " T")
        for label, widget in (
            ("材料磁导率", self.perm), ("AL", self.al), ("Le", self.le),
            ("Ae", self.ae), ("Ve", self.ve), ("OD", self.od),
            ("ID", self.id_), ("HT", self.ht), ("Bsat", self.bsat),
        ):
            core.addRow(label, widget)
        root.addWidget(core_group)
        self.perm.currentIndexChanged.connect(self._material_changed)

        ferrite_group = QGroupBox("磁芯 — Ferrite（气隙 + Steinmetz）")
        self.ferrite_group = ferrite_group
        ferrite = QFormLayout(ferrite_group)
        self.brand_filter = QComboBox()
        self.brand_filter.addItem("All brands", "")
        for item in self._taxonomy["brands"]:
            self.brand_filter.addItem(item["display_name"], item["id"])
        self.ferrite_core = QComboBox()
        self.gap_mm = self._spin(0.01, 10.0, 3, 0.50, " mm")
        self.b_limit = self._spin(0.05, 0.80, 3, 0.30, " T")
        self.brand_status = QLabel("")
        self.brand_status.setWordWrap(True)
        add_user = QPushButton("Add user core…")
        ferrite.addRow("Brand filter", self.brand_filter)
        ferrite.addRow("Ferrite core", self.ferrite_core)
        ferrite.addRow("Air gap", self.gap_mm)
        ferrite.addRow("Bpeak limit", self.b_limit)
        ferrite.addRow(self.brand_status)
        ferrite.addRow(add_user)
        ferrite_note = QLabel(
            "Built-in Ferrite records come from pfc_design/data/cores.json. "
            "粉末偏磁多项式不会用于本路径。缺少 Steinmetz 系数时会提示，不会借用其他品牌拟合。"
            " 铂科 / 东睦科达 are brand placeholders until datasheet-backed user cores exist."
        )
        ferrite_note.setWordWrap(True)
        ferrite.addRow(ferrite_note)
        root.addWidget(ferrite_group)
        self.ferrite_group.setVisible(False)
        self.brand_filter.currentIndexChanged.connect(self._rebuild_ferrite_combo)
        add_user.clicked.connect(self._add_user_core)
        self._rebuild_ferrite_combo()

        from .source_core_selector import SourceCoreSelector
        self.source_selector = SourceCoreSelector(self.core_db.source_catalog, self)
        root.addWidget(self.source_selector)
        self.source_selector.hide()

        wire_group = QGroupBox("绕组 — 漆包圆铜线")
        wire = QFormLayout(wire_group)
        self.wire_d = self._spin(0.10, 5.0, 3, 1.00, " mm")
        self.enamel = self._spin(0.0, 0.5, 3, 0.05, " mm/side")
        self.j_target = self._spin(0.5, 20.0, 2, 5.0, " A/mm²")
        self.cu_temp = self._spin(20.0, 180.0, 1, 100.0, " °C")
        self.fill = self._spin(0.10, 0.80, 3, 0.45)
        for label, widget in (
            ("单根铜径", self.wire_d), ("漆膜单边厚度", self.enamel),
            ("目标电流密度", self.j_target), ("铜温", self.cu_temp),
            ("窗口填充上限", self.fill),
        ):
            wire.addRow(label, widget)
        root.addWidget(wire_group)

        buttons = QHBoxLayout()
        self.sync_button = QPushButton("从功率级同步")
        self.run_button = QPushButton("计算电感")
        self.run_button.setDefault(True)
        buttons.addWidget(self.sync_button)
        buttons.addWidget(self.run_button)
        root.addLayout(buttons)
        self.apply_button = QPushButton("应用 L / DCR 到功率级")
        self.apply_button.setEnabled(False)
        root.addWidget(self.apply_button)

        magnetic_actions = QHBoxLayout()
        self.save_magnetic_button = QPushButton("保存磁件工程", self)
        self.open_magnetic_button = QPushButton("打开磁件工程", self)
        self.report_magnetic_button = QPushButton("磁件报告", self)
        for button in (self.save_magnetic_button, self.open_magnetic_button, self.report_magnetic_button):
            magnetic_actions.addWidget(button)
        root.addLayout(magnetic_actions)
        self.save_magnetic_button.clicked.connect(lambda: self._magnetic_file_action("save"))
        self.open_magnetic_button.clicked.connect(lambda: self._magnetic_file_action("open"))
        self.report_magnetic_button.clicked.connect(lambda: self._magnetic_file_action("report"))
        self.status = QLabel("等待计算")
        self.status.setWordWrap(True)
        _t = theme.active_theme()
        self.status.setStyleSheet(
            f"padding:6px;border:1px solid {_t.border_card};border-radius:5px;background:{_t.card_bg};color:{_t.text};"
        )
        root.addWidget(self.status)
        root.addStretch(1)

        self.sync_button.clicked.connect(self.sync_from_stage)
        self.run_button.clicked.connect(self.calculate)
        self.apply_button.clicked.connect(self.apply_to_stage)
        self.sync_from_stage()
        self.source_selector.selection_changed.connect(self._invalidate_design)
        self.source_selector.reference_completed.connect(self.reference_completed)
        for widget in self.findChildren(QDoubleSpinBox) + self.findChildren(QSpinBox):
            widget.valueChanged.connect(self._invalidate_design)
        for widget in (self.perm, self.ferrite_core):
            widget.currentIndexChanged.connect(self._invalidate_design)
        self._path_changed()

    @staticmethod
    def _spin(lo: float, hi: float, dec: int, value: float, suffix: str = "") -> QDoubleSpinBox:
        w = QDoubleSpinBox()
        w.setRange(lo, hi); w.setDecimals(dec); w.setValue(value); w.setSuffix(suffix)
        w.setKeyboardTracking(False)
        return w

    def _material_changed(self) -> None:
        mu = int(self.perm.currentData())
        self.al.setValue(HIGH_FLUX_254_MATERIALS[mu].al_nh_per_t2)

    def _path_changed(self) -> None:
        path = self.material_path.currentData()
        self.high_flux_group.setVisible(path == "high_flux")
        self.ferrite_group.setVisible(path == "ferrite")
        self.source_selector.setVisible(path == "source_catalog")
        self._invalidate_design()

    def _rebuild_ferrite_cores(self) -> None:
        self.core_db.refresh_user()
        self._ferrite_cores = [
            c for c in self.core_db.cores if c.material_class.casefold() == "ferrite"
        ]

    def _rebuild_ferrite_combo(self) -> None:
        brand_id = self.brand_filter.currentData() if hasattr(self, "brand_filter") else ""
        brand_entry = None
        if brand_id:
            brand_entry = next((b for b in self._taxonomy["brands"] if b["id"] == brand_id), None)
        aliases = set()
        if brand_entry:
            aliases = {brand_entry["display_name"].casefold(), *(a.casefold() for a in brand_entry.get("aliases", []))}
        self.ferrite_core.blockSignals(True)
        self.ferrite_core.clear()
        for core_spec in self._ferrite_cores:
            if aliases and core_spec.manufacturer.casefold() not in aliases and not any(
                a in core_spec.manufacturer.casefold() for a in aliases
            ):
                continue
            origin = "User" if self.core_db.is_user(core_spec.part_number) else "Built-in"
            label = f"[{origin}] {core_spec.manufacturer} {core_spec.part_number} ({core_spec.material})"
            self.ferrite_core.addItem(label, core_spec.part_number)
        self.ferrite_core.blockSignals(False)
        if brand_entry and brand_entry.get("status") == "brand-placeholder" and self.ferrite_core.count() == 0:
            self.brand_status.setText(
                brand_entry.get("note")
                or "No calculable built-in parts for this brand. Add a user core with datasheet evidence."
            )
        else:
            self.brand_status.setText(
                f"{self.ferrite_core.count()} ferrite core(s) listed for this filter."
            )

    def _add_user_core(self) -> None:
        result = _prompt_user_core(self)
        if result is None:
            return
        core, steinmetz = result
        try:
            lib = self.core_db.user_library
            if lib is None:
                raise RuntimeError("user core library unavailable")
            lib.save_steinmetz(steinmetz)
            lib.save_core(core)
            self._rebuild_ferrite_cores()
            self._rebuild_ferrite_combo()
            idx = self.ferrite_core.findData(core.part_number)
            if idx >= 0:
                self.ferrite_core.setCurrentIndex(idx)
        except Exception as exc:
            show_operation_issue(self, "User core save failed", str(exc), critical=False)

    def sync_from_stage(self) -> None:
        self._saved_context = None
        context = self.context_provider()
        self.target_l.setValue(float(context["target_inductance_uh"]))
        if "recommended_core_count" in context:
            self.n_cores.setValue(int(context["recommended_core_count"]))
        label = (
            f"Vin={context['input_rms_v']:.4g} V · Vbus={context['bus_voltage_v']:.4g} V\n"
            f"P={context['output_power_w']:.5g} W · fs={context['switching_frequency_hz']/1e3:.4g} kHz"
        )
        self.context_label.setText(label)

    def _request(self) -> PFCInductorDesignRequest:
        context = self._saved_context or self.context_provider()
        snapshot = declaration = None
        if self.material_path.currentData() == "source_catalog":
            from pfc_design.magnetics.manufacturer_catalog import source_pfc_components
            selected = self.source_selector.selection
            if selected is None:
                raise ValueError("未选择来源磁芯")
            declaration = self.source_selector.declaration
            core, material = source_pfc_components(selected, declaration)
            snapshot = selected.snapshot
        else:
            mu = int(self.perm.currentData())
            material = replace(HIGH_FLUX_254_MATERIALS[mu], al_nh_per_t2=self.al.value())
            core = HighFluxCoreGeometry(
                core_data="Magnetics High Flux 254/custom",
                le_mm=self.le.value(), ae_mm2=self.ae.value(), ve_mm3=self.ve.value(),
                od_mm=self.od.value(), id_mm=self.id_.value(), ht_mm=self.ht.value(),
                bs_t=self.bsat.value(),
            )
        return PFCInductorDesignRequest(
            **(self._source_request_extras if snapshot is not None else {}),
            source_snapshot=snapshot, model_declaration=declaration,
            topology=self.topology,
            input_rms_v=float(context["input_rms_v"]),
            bus_voltage_v=float(context["bus_voltage_v"]),
            output_power_w=float(context["output_power_w"]),
            switching_frequency_hz=float(context["switching_frequency_hz"]),
            target_inductance_uh=self.target_l.value(),
            efficiency=self.eff.value(),
            core=core,
            material=material,
            n_cores=self.n_cores.value(),
            wire_copper_diameter_mm=self.wire_d.value(),
            enamel_build_mm=self.enamel.value(),
            target_current_density_a_mm2=self.j_target.value(),
            copper_temperature_c=self.cu_temp.value(),
            max_fill_factor=self.fill.value(),
        )

    def _ferrite_request(self) -> FerriteInductorRequest:
        context = self.context_provider()
        part = str(self.ferrite_core.currentData())
        core = next(c for c in self._ferrite_cores if c.part_number == part)
        steinmetz = self.core_db.get_steinmetz(core.material)
        if steinmetz is None:
            steinmetz = self.core_db.get_steinmetz(core.material_class)
        missing = missing_ferrite_steinmetz_parameters(core.material, steinmetz)
        if missing:
            raise ValueError("Ferrite Steinmetz data missing:\n" + format_missing(missing))
        return FerriteInductorRequest(
            topology=self.topology,
            input_rms_v=float(context["input_rms_v"]),
            bus_voltage_v=float(context["bus_voltage_v"]),
            output_power_w=float(context["output_power_w"]),
            switching_frequency_hz=float(context["switching_frequency_hz"]),
            target_inductance_h=self.target_l.value() * 1e-6,
            efficiency=self.eff.value(),
            core=core,
            steinmetz=steinmetz,
            gap_m=self.gap_mm.value() * 1e-3,
            n_cores=self.n_cores.value(),
            wire_copper_diameter_mm=self.wire_d.value(),
            enamel_build_mm=self.enamel.value(),
            target_current_density_a_mm2=self.j_target.value(),
            copper_temperature_c=self.cu_temp.value(),
            max_fill_factor=self.fill.value(),
            b_peak_limit_t=self.b_limit.value(),
        )

    def calculate(self) -> None:
        self._invalidate_design()
        try:
            if self.material_path.currentData() == "ferrite":
                result = design_ferrite_pfc_inductor(self._ferrite_request())
            else:
                result = design_pfc_inductor(self._request())
        except Exception as exc:
            show_operation_issue(self, t("PFC 电感设计失败"), str(exc), critical=False)
            return
        self.last_result = result
        self.apply_button.setEnabled(self._context_matches_stage())
        if isinstance(result, FerriteInductorResult):
            state = "PASS" if result.b_peak_total_t <= result.request.b_peak_limit_t and result.fill_factor <= result.request.max_fill_factor else "CHECK"
            self.status.setText(
                f"{state} · Ferrite N={result.turns}T · L={result.inductance_h*1e6:.3f}µH · "
                f"Bpk={result.b_peak_total_t:.3f}T · Pind={result.total_inductor_loss_w:.3f}W"
            )
        else:
            state = ("REFERENCE" if result.request.source_snapshot else "PASS") if result.inductance_target_met and result.window_ok else "CHECK"
            self.status.setText(
                f"{state} · N={result.turns}T · {result.parallel_wires}×{result.request.wire_copper_diameter_mm:.3g}mm · "
                f"L(full)={result.l_full_load_peak_uh:.3f}µH · PΣ={result.total_loss_w:.3f}W"
            )
        self.status.setStyleSheet(
            "padding:6px;border:1px solid #75e0a7;border-radius:5px;background:#ecfdf3;color:#067647;"
            if state == "PASS" else
            "padding:6px;border:1px solid #fec84b;border-radius:5px;background:#fffaeb;color:#b54708;"
        )
        self.design_completed.emit(result)

    def _context_matches_stage(self):
        if self._saved_context is None:
            return True
        import math
        current = self.context_provider()
        return all(math.isclose(float(value), float(current[key]), rel_tol=1e-12, abs_tol=1e-12)
                   for key, value in self._saved_context.items())

    def stage_context_changed(self, *_):
        self._invalidate_design()
        if not self._context_matches_stage():
            self.status.setText("保存的磁件工况与当前功率级不同；请同步并重新计算后再应用")

    def _invalidate_design(self, *_):
        self._magnetic_generation += 1
        self.last_result = None
        if not hasattr(self, "run_button"):
            return
        self.apply_button.setEnabled(False)
        catalog = self.material_path.currentData() == "source_catalog"
        blocker = self.source_selector.pfc_blocker() if catalog else ""
        if catalog and not blocker:
            try:
                self._request().validate()
            except ValueError as exc:
                blocker = str(exc)
        self.run_button.setEnabled(not blocker)
        self.run_button.setToolTip(blocker)
        self.save_magnetic_button.setEnabled(catalog and self.source_selector.selection is not None)
        self.report_magnetic_button.setEnabled(catalog and self.source_selector.selection is not None)
        self.status.setText(blocker or "参数已更改，请重新计算")
        self.design_invalidated.emit()

    def magnetic_document(self):
        from pfc_design.magnetics.source_project import project_document
        if self.material_path.currentData() != "source_catalog" or self.source_selector.selection is None:
            raise ValueError("磁件工程保存需要选择 PDF 来源记录")
        pfc_inputs = None
        if self.last_result is not None and self.last_result.request.source_snapshot:
            req = self.last_result.request
            pfc_inputs = {field.name: getattr(req, field.name) for field in fields(req)
                          if field.name not in {"core", "material", "source_snapshot", "model_declaration"}}
        reference_inputs = self.source_selector.reference_inputs() if self.source_selector.reference_result is not None else None
        return project_document(self.source_selector.selection, reference_inputs=reference_inputs,
                                pfc_inputs=pfc_inputs, declaration=self.source_selector.declaration)

    def save_magnetic_to(self, path):
        from pfc_design.magnetics.source_project import save_project
        return save_project(self.magnetic_document(), path)

    def report_magnetic_to(self, path):
        from pfc_design.magnetics.source_project import write_report
        return write_report(self.magnetic_document(), path)

    def open_magnetic_from(self, path):
        from pfc_design.magnetics.source_project import read_project, recompute_project
        document = read_project(path)
        selection, reference, result = recompute_project(document)
        if result is not None and result.request.topology != self.topology:
            raise ValueError("磁件工程拓扑与当前 PFC 工作区不一致")
        restore_fields = []
        reference_inputs = document["reference_inputs"] or {}
        for key, widget in (("turns", self.source_selector.turns), ("n_cores", self.source_selector.stack)):
            if key in reference_inputs:
                restore_fields.append((key, widget, reference_inputs[key]))
        if result is not None:
            req = result.request
            restore_fields.extend((name, widget, getattr(req, name)) for name, widget in (
                ("target_inductance_uh", self.target_l), ("efficiency", self.eff),
                ("n_cores", self.n_cores), ("wire_copper_diameter_mm", self.wire_d),
                ("enamel_build_mm", self.enamel), ("target_current_density_a_mm2", self.j_target),
                ("copper_temperature_c", self.cu_temp), ("max_fill_factor", self.fill)))
        for name, widget, value in restore_fields:
            if not widget.minimum() <= value <= widget.maximum():
                raise ValueError(f"Saved {name} is outside the editor range; current project preserved")
            if isinstance(widget, QDoubleSpinBox) and round(float(value), widget.decimals()) != float(value):
                raise ValueError(f"Saved {name} exceeds editor precision; current project preserved")
        # All parsing, recomputation and representability checks precede mutation.
        self.material_path.setCurrentIndex(self.material_path.findData("source_catalog"))
        self.source_selector.set_snapshot(selection.snapshot, document["user_model_declaration"])
        self.source_selector.set_reference_inputs(document["reference_inputs"])
        if result is not None:
            req = result.request
            for name, widget in (("target_inductance_uh", self.target_l), ("efficiency", self.eff),
                                 ("n_cores", self.n_cores), ("wire_copper_diameter_mm", self.wire_d),
                                 ("enamel_build_mm", self.enamel), ("target_current_density_a_mm2", self.j_target),
                                 ("copper_temperature_c", self.cu_temp), ("max_fill_factor", self.fill)):
                widget.setValue(getattr(req, name))
            self._source_request_extras = {name: getattr(req, name) for name in ("winding_length_factor", "curve_points")}
            self._saved_context = {name: getattr(req, name) for name in ("input_rms_v", "bus_voltage_v", "output_power_w", "switching_frequency_hz")}
            self.context_label.setText("已恢复磁件工程工作点（点击同步将切回当前功率级）\n" + str(self._saved_context) + "\n其他保存参数：" + str(self._source_request_extras))
        else:
            self._saved_context = None
            self._source_request_extras = {}
        self.source_selector.reference_result = reference
        if reference is not None:
            import json
            self.source_selector.reference_output.setPlainText(json.dumps(reference, ensure_ascii=False, indent=2))
            self.reference_completed.emit(reference)
        self.last_result = result
        if result is not None:
            self.apply_button.setEnabled(self._context_matches_stage())
            self.status.setText("已从保存的来源与用户声明重新计算 PFC 参考估算" if self._context_matches_stage() else "已恢复保存工况；与当前功率级不同，请同步并重新计算后再应用")
            self.design_completed.emit(result)

    def _magnetic_file_action(self, action):
        captured_generation = self._magnetic_generation
        captured_document = None
        if action != "open":
            try:
                captured_document = self.magnetic_document()
            except Exception as exc:
                show_operation_issue(self, "磁件工程操作失败", str(exc), critical=False)
                return
        if action == "open":
            path, _ = QFileDialog.getOpenFileName(self, "打开磁件工程", "", "Magnetic project (*.json)")
        else:
            extension = "html" if action == "report" else "json"
            path, _ = QFileDialog.getSaveFileName(self, "磁件工程", f"magnetic_project.{extension}", f"*.{extension}")
        if not path:
            return
        try:
            if action == "open":
                self.open_magnetic_from(path)
            else:
                if captured_generation != self._magnetic_generation:
                    raise ValueError("选择文件期间磁件输入已变化，请重新保存/导出")
                from pfc_design.magnetics.source_project import save_project, write_report
                (save_project if action == "save" else write_report)(captured_document, path)
        except Exception as exc:
            show_operation_issue(self, "磁件工程操作失败", str(exc), critical=False)

    def apply_to_stage(self) -> None:
        if self.last_result is None:
            return
        if not self._context_matches_stage():
            self.apply_button.setEnabled(False)
            self.status.setText("保存工况与当前功率级不同，请同步并重新计算后再应用")
            return
        try:
            self.apply_callback(self.last_result)
        except ValueError as exc:
            self.status.setText(str(exc))
            show_operation_issue(self, "磁件参数未应用", str(exc), critical=False)


class PFCInductorDesignResultView(QWidget):
    """Summary + L(I), line ripple/core-loss and loss-breakdown plots."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.title = title
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(5)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(190)
        self.summary.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.summary.setPlainText("尚未计算电感。左侧进入“电感设计”，然后点击“计算电感”。")
        root.addWidget(self.summary)
        self.impedance_dialog = None
        self.impedance_fit_button = QPushButton("实测复阻抗拟合 / SPICE 模型")
        self.impedance_fit_button.clicked.connect(self.open_impedance_fit)
        root.addWidget(self.impedance_fit_button)
        self.figure = Figure(figsize=(11, 7))
        self.canvas = FigureCanvasQTAgg(self.figure)
        root.addWidget(self.canvas, 1)

    def open_impedance_fit(self):
        from shiboken6 import isValid

        from llc_design.gui.widgets.impedance_fit_view import ImpedanceFitDialog

        dialog = self.impedance_dialog
        if dialog is None or not isValid(dialog):
            dialog = ImpedanceFitDialog(self)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.impedance_dialog = dialog
            # Repeated clicks reuse a live dialog. Closing discards that fit;
            # a late destroyed signal must not clear a newly opened dialog.
            dialog.finished.connect(lambda _result: self._release_impedance_dialog(dialog))
            dialog.destroyed.connect(lambda: self._release_impedance_dialog(dialog))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _release_impedance_dialog(self, dialog):
        if self.impedance_dialog is dialog:
            self.impedance_dialog = None

    def set_reference_result(self, result):
        import json
        self.figure.clear()
        self.summary.setPlainText("来源磁件参考计算（不是满载资格验证）\n" + json.dumps(result, ensure_ascii=False, indent=2))
        self.canvas.draw_idle()

    def clear_result(self):
        self.summary.setPlainText("磁件参数或选择已改变，请重新计算。")
        self.figure.clear()
        self.canvas.draw_idle()

    def set_result(self, result) -> None:
        if isinstance(result, FerriteInductorResult):
            req = result.request
            warn = "\n".join(f"  - {w}" for w in result.warnings)
            self.summary.setPlainText(
                f"{self.title}\n"
                f"Path      : Ferrite gapped + Steinmetz (powder dc_bias NOT used)\n"
                f"Core      : {req.core.manufacturer} {req.core.part_number} ({req.core.material}), cores={req.n_cores}\n"
                f"Gap / AL  : lg={req.gap_m*1e3:.3f} mm, AL≈{result.al_nH_per_t2:.3f} nH/T², Leff_gap={result.effective_gap_m*1e3:.3f} mm\n"
                f"Winding   : N={result.turns} T, {result.parallel_wires} × Ø{req.wire_copper_diameter_mm:.3f} mm, fill={100*result.fill_factor:.2f}%\n"
                f"Inductance: L={result.inductance_h*1e6:.3f} µH (target {req.target_inductance_h*1e6:.3f} µH)\n"
                f"Current   : Irms={result.phase_current_rms_a:.3f} A, Ipk={result.phase_current_peak_a:.3f} A, ΔIpp={result.ripple_pp_a:.3f} A\n"
                f"Flux      : Bac={result.bac_peak_t:.4f} T, Bdc={result.bdc_t:.4f} T, Bpeak={result.b_peak_total_t:.4f} T (limit {req.b_peak_limit_t:.3f} T)\n"
                f"Inductor loss (component only): Pcore={result.core_loss_w:.3f} W, Pcu={result.copper_loss_w:.3f} W, PΣ={result.total_inductor_loss_w:.3f} W\n"
                f"Note      : inductor-self loss above is NOT the converter system loss\n"
                f"Warnings:\n{warn}"
            )
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            labels = ["Core", "Copper", "Inductor Σ"]
            values = [result.core_loss_w, result.copper_loss_w, result.total_inductor_loss_w]
            ax.bar(labels, values)
            ax.set_title("Ferrite inductor-self loss (not system loss)")
            ax.set_ylabel("Loss (W)")
            ax.grid(True, axis="y", alpha=0.3)
            self.figure.suptitle(self.title)
            self.figure.tight_layout()
            self.canvas.draw_idle()
            return

        req = result.request
        warn = "\n".join(f"  - {w}" for w in result.warnings)
        self.summary.setPlainText(
            f"{self.title}\n"
            f"Core      : {req.core.core_data}, μ={req.material.permeability}, AL={req.material.al_nh_per_t2:.3f} nH/T², cores={req.n_cores}\n"
            f"Geometry  : Le={req.core.le_mm:.3f} mm, Ae={req.core.ae_mm2:.3f} mm², Ve={req.core.ve_mm3:.1f} mm³, OD/ID/HT={req.core.od_mm:.2f}/{req.core.id_mm:.2f}/{req.core.ht_mm:.2f} mm\n"
            f"Winding   : N={result.turns} T, {result.parallel_wires} × Ø{req.wire_copper_diameter_mm:.3f} mm enamel Cu, Cu area={result.copper_area_mm2:.3f} mm², fill={100*result.fill_factor:.2f}%\n"
            f"Inductance: L0={result.l_no_bias_uh:.3f} µH, L@full-peak={result.l_full_load_peak_uh:.3f} µH, drop={result.l_drop_percent:.2f}%\n"
            f"Current   : Iphase,rms={result.phase_current_rms_a:.3f} A, Irms+ripple={result.total_current_rms_with_ripple_a:.3f} A, Ipk={result.full_load_peak_with_ripple_a:.3f} A\n"
            f"Bias      : Hpk={result.h_peak_oe:.3f} Oe, μ/μi={result.permeability_at_peak_percent:.2f}%, Bdc≈{result.b_dc_approx_peak_t:.4f} T, Bac,max={result.b_ac_line_max_t:.4f} T\n"
            f"Copper    : R20={1e3*result.rdc_20_ohm:.3f} mΩ, Rhot={1e3*result.rdc_hot_ohm:.3f} mΩ, J={result.current_density_a_mm2:.3f} A/mm², Pcu={result.copper_loss_w:.3f} W\n"
            f"Core loss : {result.core_loss_w:.3f} W ({'source-specific user-declared reference' if req.source_snapshot else 'Magnetics High Flux fit'}, line-cycle average)\n"
            f"Source    : {req.source_snapshot['record']['record_id'] if req.source_snapshot else 'Magnetics Core Data 254'}\n"
            f"Inductor-self total: {result.total_loss_w:.3f} W (not converter system loss)\n"
            f"Warnings:\n{warn}"
        )

        self.figure.clear()
        ax_l = self.figure.add_subplot(221)
        ax_ripple = self.figure.add_subplot(222)
        ax_core = self.figure.add_subplot(223)
        ax_loss = self.figure.add_subplot(224)

        ax_l.plot(result.current_a, result.inductance_uh, linewidth=2.0)
        ax_l.axhline(req.target_inductance_uh, linestyle="--", linewidth=1.0, label="Target L")
        ax_l.scatter([result.full_load_peak_with_ripple_a], [result.l_full_load_peak_uh], s=35, zorder=5, label="Full-load peak")
        ax_l.set_title("Inductance droop vs current")
        ax_l.set_xlabel("Current (A)"); ax_l.set_ylabel("L (µH)"); ax_l.grid(True, alpha=.3); ax_l.legend(fontsize=8)

        ax_ripple.plot(result.line_angle_deg, result.line_ripple_pp_a)
        ax_ripple.set_title("Switching ripple over line cycle")
        ax_ripple.set_xlabel("Line angle (deg)"); ax_ripple.set_ylabel("ΔIpp (A)"); ax_ripple.grid(True, alpha=.3)

        ax_core.plot(result.line_angle_deg, result.line_core_loss_w, label="Core loss")
        ax_core2 = ax_core.twinx()
        ax_core2.plot(result.line_angle_deg, result.line_b_ac_t, linestyle="--", label="Bac")
        ax_core.set_title("Core loss / AC flux over line cycle")
        ax_core.set_xlabel("Line angle (deg)"); ax_core.set_ylabel("Core loss (W)"); ax_core2.set_ylabel("Bac (T)")
        ax_core.grid(True, alpha=.3)

        labels = ["Core", "Copper", "Inductor Σ"]
        values = [result.core_loss_w, result.copper_loss_w, result.total_loss_w]
        ax_loss.bar(labels, values)
        ax_loss.set_title("Inductor-self loss breakdown")
        ax_loss.set_ylabel("Loss (W)"); ax_loss.grid(True, axis="y", alpha=.3)
        for idx, value in enumerate(values):
            ax_loss.text(idx, value, f"{value:.2f}", ha="center", va="bottom", fontsize=8)

        self.figure.suptitle(self.title)
        self.figure.tight_layout()
        self.canvas.draw_idle()


__all__ = ["PFCInductorDesignEditor", "PFCInductorDesignResultView"]
