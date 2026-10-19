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

    # The editor lives inside the control stage, while the device view is a
    # sibling. Route its actual selected-model result, never a test-only alias.
    editor = workbench.control_lab.inductor_editor
    def magnetic_result_ready(result):
        if not editor._context_matches_stage():
            view.set_inductor_self_loss(None)
            return
        from pfc_design.magnetics import FerriteInductorResult
        if isinstance(result, FerriteInductorResult):
            identity, loss = result.request.core.part_number, result.total_inductor_loss_w
        else:
            identity, loss = result.request.core.core_data, result.total_loss_w
            if result.request.source_snapshot:
                identity += "; user-declared reference model"
        req = result.request
        operating_point = (
            f"Vin={req.input_rms_v:g} V RMS; Vbus={req.bus_voltage_v:g} V; "
            f"Po={req.output_power_w:g} W; fs={req.switching_frequency_hz / 1000:g} kHz; "
            f"η={req.efficiency:g} (independent magnetic calculation point)"
        )
        view.set_inductor_self_loss(loss, model_identity=identity, operating_point=operating_point)
    editor.design_completed.connect(magnetic_result_ready)
    editor.design_invalidated.connect(lambda: view.set_inductor_self_loss(None))

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
