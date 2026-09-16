"""Install the TTPL capacitor/thermal stage into the engineering workbench."""
from __future__ import annotations

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
