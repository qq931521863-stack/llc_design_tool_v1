"""Install the exact-H(z)/C99 stage after TTPL control design."""
from __future__ import annotations

from .exact_hz_view import TTPLExactHzView


def install_ttpl_exact_hz_stage(pfc_window) -> TTPLExactHzView:
    workbench = pfc_window.control_lab_view
    if hasattr(workbench, "exact_hz_view"):
        return workbench.exact_hz_view

    view = TTPLExactHzView(parent=workbench)
    workbench.exact_hz_view = view
    workbench.tabs.addTab(view, "7. Exact H(z) / C99")

    # The historical Control Lab button called codegen directly from controller
    # configuration.  Keep the implementation for backward compatibility but
    # remove the duplicate visible entry point: the new page freezes/analyzes
    # exact H(z) first and then performs audited generation.
    legacy_button = getattr(workbench.control_lab, "codegen_button", None)
    if legacy_button is not None:
        legacy_button.setVisible(False)
        legacy_button.setToolTip(
            "C99 generation moved to the Exact H(z) / C99 engineering stage."
        )
    return view


__all__ = ["install_ttpl_exact_hz_stage"]
