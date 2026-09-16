"""Install first-class AC/PF/THD and switching/zero-crossing TTPL stages."""
from __future__ import annotations

from .ttpl_waveform_views import TTPLACPerformanceView, TTPLSwitchingValidationView


_LEGACY_WAVEFORM_CANVASES = (
    "ac_overview_canvas",
    "ac_control_canvas",
    "switch_canvas",
    "zero_canvas",
    "harmonic_canvas",
)


def _detach_legacy_waveform_result_tabs(control_lab) -> list[object]:
    """Remove old embedded waveform result pages while keeping their objects alive.

    ``PFCControlLabView.set_result`` still owns the legacy rendering calls in
    this migration tranche.  The detached widgets are therefore retained so
    older code paths remain valid, but users no longer see duplicate AC and
    switching pages inside the control workspace.  A later cleanup can remove
    those render calls once the new views have accumulated enough regression
    history.
    """

    detached: list[object] = []
    for attribute in _LEGACY_WAVEFORM_CANVASES:
        canvas = getattr(control_lab, attribute, None)
        page = canvas.parentWidget() if canvas is not None else None
        if page is None:
            continue
        index = control_lab.tabs.indexOf(page)
        if index >= 0:
            control_lab.tabs.removeTab(index)
            detached.append(page)
    control_lab._detached_legacy_waveform_pages = detached
    return detached


def install_ttpl_ac_switching_stages(pfc_window):
    workbench = pfc_window.control_lab_view
    if hasattr(workbench, "ac_performance_view"):
        return workbench.ac_performance_view, workbench.switching_validation_view

    config_provider = workbench.control_lab._config
    ac_view = TTPLACPerformanceView(config_provider, parent=workbench)
    switching_view = TTPLSwitchingValidationView(config_provider, parent=workbench)
    workbench.ac_performance_view = ac_view
    workbench.switching_validation_view = switching_view

    cap_view = getattr(workbench, "cap_thermal_view", None)
    if cap_view is not None:
        cap_index = workbench.tabs.indexOf(cap_view)
        insert_index = cap_index + 1 if cap_index >= 0 else 3
    else:
        insert_index = max(workbench.tabs.indexOf(workbench.control_lab), 1)

    workbench.tabs.insertTab(insert_index, ac_view, "4. AC Line / PF / THD")
    workbench.tabs.insertTab(insert_index + 1, switching_view, "5. Switching / Zero Crossing")
    control_index = workbench.tabs.indexOf(workbench.control_lab)
    if control_index >= 0:
        workbench.tabs.setTabText(control_index, "6. Control / Sensing / Bode")

    _detach_legacy_waveform_result_tabs(workbench.control_lab)
    return ac_view, switching_view


__all__ = ["install_ttpl_ac_switching_stages"]
