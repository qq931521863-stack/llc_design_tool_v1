"""Install the LLC user-device library into the existing design workspace.

The LLC main window historically exposed only the built-in primary-device combo.
This bridge keeps that window stable while adding a merged built-in/user library,
an SR selector, live device summaries, JSON library management and a nominal
work-point comparison table.
"""
from __future__ import annotations

from types import MethodType

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

from ..models.devices import DeviceDatabase
from .device_library import DeviceCompareDialog, DeviceLibraryDialog


def _device_label(device, user: bool) -> str:
    origin = "USER" if user else "REF"
    return (
        f"[{origin}] {device.part_number} | {device.vds_max_v:.0f} V | "
        f"Rds25={device.rds_on_25_ohm*1e3:.2f} mΩ"
    )


def _device_details(device, junction_temperature_c: float, user: bool) -> str:
    origin = "User library" if user else "Built-in generic reference"
    return (
        f"{origin} · {device.technology}\n"
        f"Rds@{junction_temperature_c:.0f}°C={device.rds_at(junction_temperature_c)*1e3:.3f} mΩ · "
        f"Qg={device.qg_c*1e9:.1f} nC · Qoss={device.qoss_c*1e9:.1f} nC · "
        f"Coss_eff={device.coss_er_f*1e12:.1f} pF"
    )


def install_device_library(host, *, user_path=None):
    """Attach device-library controls to an ``LLCMainWindow`` instance."""
    if hasattr(host, "device_database"):
        return host.device_database

    database = DeviceDatabase(user_path=user_path)
    host.device_database = database

    form = host.primary_device_combo.parentWidget().layout()
    if not isinstance(form, QFormLayout):
        raise TypeError("LLC primary device selector is expected to live in a QFormLayout")

    host.primary_device_details = QLabel()
    host.primary_device_details.setWordWrap(True)
    host.sr_device_combo = QComboBox()
    host.sr_device_details = QLabel()
    host.sr_device_details.setWordWrap(True)
    host.device_library_button = QPushButton("Device Library…")
    host.device_compare_button = QPushButton("Device Compare…")

    button_box = QWidget()
    buttons = QHBoxLayout(button_box)
    buttons.setContentsMargins(0, 0, 0, 0)
    buttons.addWidget(host.device_library_button)
    buttons.addWidget(host.device_compare_button)

    row, _role = form.getWidgetPosition(host.primary_device_combo)
    if row < 0:
        form.addRow("Primary details", host.primary_device_details)
        form.addRow("SR MOSFET", host.sr_device_combo)
        form.addRow("SR details", host.sr_device_details)
        form.addRow("Device tools", button_box)
    else:
        form.insertRow(row + 1, "Primary details", host.primary_device_details)
        form.insertRow(row + 2, "SR MOSFET", host.sr_device_combo)
        form.insertRow(row + 3, "SR details", host.sr_device_details)
        form.insertRow(row + 4, "Device tools", button_box)

    def selected_part(combo: QComboBox) -> str | None:
        value = combo.currentData()
        return str(value) if value is not None else None

    def refresh_combos(primary_part: str | None = None, sr_part: str | None = None) -> None:
        primary_part = primary_part or selected_part(host.primary_device_combo) or host.spec.primary_device
        sr_part = sr_part or selected_part(host.sr_device_combo) or host.spec.sr_device
        database.refresh()

        host.primary_device_combo.blockSignals(True)
        host.primary_device_combo.clear()
        for device in database.primary:
            host.primary_device_combo.addItem(
                _device_label(device, database.is_user("primary", device.part_number)),
                device.part_number,
            )
        index = host.primary_device_combo.findData(primary_part)
        host.primary_device_combo.setCurrentIndex(index if index >= 0 else 0)
        host.primary_device_combo.blockSignals(False)

        host.sr_device_combo.blockSignals(True)
        host.sr_device_combo.clear()
        for device in database.sr:
            host.sr_device_combo.addItem(
                _device_label(device, database.is_user("sr", device.part_number)),
                device.part_number,
            )
        index = host.sr_device_combo.findData(sr_part)
        host.sr_device_combo.setCurrentIndex(index if index >= 0 else 0)
        host.sr_device_combo.blockSignals(False)
        update_details()

    def update_details(*_args) -> None:
        try:
            primary = database.get_primary(str(host.primary_device_combo.currentData()))
            host.primary_device_details.setText(
                _device_details(
                    primary,
                    float(getattr(host.spec, "primary_junction_temperature_c", 100.0)),
                    database.is_user("primary", primary.part_number),
                )
            )
        except Exception as exc:
            host.primary_device_details.setText(str(exc))
        try:
            sr = database.get_sr(str(host.sr_device_combo.currentData()))
            host.sr_device_details.setText(
                _device_details(
                    sr,
                    float(getattr(host.spec, "sr_junction_temperature_c", 100.0)),
                    database.is_user("sr", sr.part_number),
                )
            )
        except Exception as exc:
            host.sr_device_details.setText(str(exc))

    host.primary_device_combo.currentIndexChanged.connect(update_details)
    host.sr_device_combo.currentIndexChanged.connect(update_details)

    original_spec_from_widgets = host._spec_from_widgets
    original_load_spec_to_widgets = host._load_spec_to_widgets

    def spec_from_widgets_with_sr(self):
        spec = original_spec_from_widgets()
        sr_part = self.sr_device_combo.currentData()
        if sr_part is None:
            raise ValueError("no SR MOSFET is selected")
        return spec.clone(sr_device=str(sr_part))

    def load_spec_with_devices(self, spec):
        refresh_combos(spec.primary_device, spec.sr_device)
        original_load_spec_to_widgets(spec)
        sr_index = self.sr_device_combo.findData(spec.sr_device)
        if sr_index >= 0:
            self.sr_device_combo.setCurrentIndex(sr_index)
        update_details()

    host._spec_from_widgets = MethodType(spec_from_widgets_with_sr, host)
    host._load_spec_to_widgets = MethodType(load_spec_with_devices, host)

    def open_library() -> None:
        primary = selected_part(host.primary_device_combo)
        sr = selected_part(host.sr_device_combo)
        dialog = DeviceLibraryDialog(database, host)
        dialog.exec()
        refresh_combos(primary, sr)
        if database.warnings:
            QMessageBox.warning(host, "Device Library", "\n".join(database.warnings))

    def compare_devices() -> None:
        try:
            spec = host._spec_from_widgets()
            dialog = DeviceCompareDialog(spec, database, host)
            dialog.exec()
        except Exception as exc:
            QMessageBox.warning(host, "Device Compare", str(exc))

    host.device_library_button.clicked.connect(open_library)
    host.device_compare_button.clicked.connect(compare_devices)
    host.refresh_device_library = refresh_combos
    host.open_device_library = open_library
    host.compare_devices = compare_devices

    refresh_combos(host.spec.primary_device, host.spec.sr_device)
    return database


__all__ = ["install_device_library"]
