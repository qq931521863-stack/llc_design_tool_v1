"""Install the TTPL capacitor/thermal stage into the engineering workbench."""
from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from .cap_thermal_view import TTPLCapacitorThermalView


def install_ttpl_capacitor_thermal_stage(pfc_window) -> TTPLCapacitorThermalView:
    workbench = pfc_window.control_lab_view
    if hasattr(workbench, "cap_thermal_view"):
        return workbench.cap_thermal_view

    database = getattr(workbench, "device_database", None)
    design = workbench.power_stage_view.result
    view = TTPLCapacitorThermalView(
        design=design,
        database=database,
        parent=workbench,
    )
    workbench.cap_thermal_view = view

    device_view = getattr(workbench, "device_loss_view", None)
    if device_view is not None:
        device_index = workbench.tabs.indexOf(device_view)
        insert_index = device_index + 1 if device_index >= 0 else 2
    else:
        insert_index = 1
    workbench.tabs.insertTab(insert_index, view, "3. Capacitor / Thermal")

    control_index = workbench.tabs.indexOf(workbench.control_lab)
    if control_index >= 0:
        workbench.tabs.setTabText(control_index, "4. Control / Sensing / AC / Switching")

    # Hardware component selection must feed the plant that is later used for
    # Bode/AC/switching analysis.  Otherwise the engineering page could show a
    # real 5x470-uF bank while Control Lab silently kept the Phase-1 minimum C.
    # Add an explicit Apply button instead of auto-mutating the control model.
    view.cap_apply_button = QPushButton("Apply Actual Cbank / ESR to Control")
    cap_left = view.cap_run_button.parentWidget()
    cap_layout = cap_left.layout() if cap_left is not None else None
    if cap_layout is not None:
        cap_layout.insertWidget(max(cap_layout.count() - 1, 0), view.cap_apply_button)

    def apply_capacitor_bank() -> None:
        if view.cap_result is None:
            view.run_capacitor()
        result = view.cap_result
        if result is None:
            return
        workbench.control_lab.cbus.setValue(result.bank_capacitance_uf)
        workbench.control_lab.cbus_esr.setValue(result.bank_esr_ohm * 1e3)
        workbench.tabs.setCurrentWidget(workbench.control_lab)

    view.cap_apply_button.clicked.connect(apply_capacitor_bank)

    def refresh_if_needed(index: int) -> None:
        if workbench.tabs.widget(index) is view:
            latest = workbench.power_stage_view.result
            if latest is not None:
                view.design = latest
            view.refresh_devices()
            view.run_all()

    workbench.tabs.currentChanged.connect(refresh_if_needed)
    workbench.power_stage_view.apply_requested.connect(view.set_design_result)
    return view


__all__ = ["install_ttpl_capacitor_thermal_stage"]
