"""Install the TTPL device/loss stage without coupling it to the legacy control lab."""
from __future__ import annotations

from pfc_design.engineering import PFCDeviceDatabase
from .pfc_device_view import TTPLDeviceLossView


def install_ttpl_device_loss_stage(pfc_window) -> TTPLDeviceLossView:
    """Insert `Devices / Loss` between sizing and the existing control stage.

    The installer pattern keeps the engineering workflow modular, matching the
    LLC closed-loop/device-stage installers while the monolithic historical PFC
    Control Lab is gradually split into dedicated pages.
    """

    workbench = pfc_window.control_lab_view
    if hasattr(workbench, "device_loss_view"):
        return workbench.device_loss_view

    database = PFCDeviceDatabase()
    design = workbench.power_stage_view.result
    view = TTPLDeviceLossView(design=design, database=database, parent=workbench)
    workbench.device_database = database
    workbench.device_loss_view = view

    workbench.tabs.insertTab(1, view, "2. Devices / Loss")
    control_index = workbench.tabs.indexOf(workbench.control_lab)
    if control_index >= 0:
        workbench.tabs.setTabText(control_index, "3. Control / Sensing / AC / Switching")

    def refresh_if_needed(index: int) -> None:
        if workbench.tabs.widget(index) is view:
            latest = workbench.power_stage_view.result
            if latest is not None:
                view.set_design_result(latest)

    workbench.tabs.currentChanged.connect(refresh_if_needed)
    workbench.power_stage_view.apply_requested.connect(view.set_design_result)
    return view


__all__ = ["install_ttpl_device_loss_stage"]
