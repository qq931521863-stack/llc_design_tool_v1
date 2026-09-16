"""Install the TTPL shared-ngspice closed-loop stage after Exact H(z)."""
from __future__ import annotations

from .ngspice_closed_loop_view import TTPLNgSpiceClosedLoopView


def install_ttpl_ngspice_closed_loop_stage(pfc_window) -> TTPLNgSpiceClosedLoopView:
    workbench = pfc_window.control_lab_view
    if hasattr(workbench, "ngspice_closed_loop_view"):
        return workbench.ngspice_closed_loop_view

    view = TTPLNgSpiceClosedLoopView(parent=workbench)
    workbench.ngspice_closed_loop_view = view
    workbench.tabs.addTab(view, "8. Closed-Loop Verification")
    view.run_requested.connect(pfc_window.run_ttpl_shared_ngspice)
    return view


__all__ = ["install_ttpl_ngspice_closed_loop_stage"]
