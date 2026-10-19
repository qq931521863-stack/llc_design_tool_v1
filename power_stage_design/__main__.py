"""PSD desktop entry and reproducible headless project/report calculation."""
from __future__ import annotations

import argparse


def main():
    parser=argparse.ArgumentParser(description="Power Stage Designer")
    parser.add_argument("project",nargs="?",help="Saved PSD project JSON")
    parser.add_argument("--report",help="Recompute project and write HTML without Qt")
    args=parser.parse_args()
    if args.report:
        if not args.project:parser.error("--report requires a project JSON")
        from .project import read_project
        from .report import write_report
        from .topologies import calculate_stage
        inputs,document=read_project(args.project)
        loop=None;settings=document.get("loop_settings")
        if settings:
            from .loop import Compensator, LoopPlant, analyze_loop
            loop=analyze_loop(LoopPlant(**settings["plant"]),Compensator(**settings["compensator"]))
        sweep=None
        if document.get("corner_request"):
            from .corners import CornerRequest, analyze_corners
            from .project import _validate_device
            selected=document.get("selected_device")
            sweep=analyze_corners(inputs,CornerRequest(**document["corner_request"]),loop_settings=settings,
                selected_device=_validate_device(selected) if selected else None)
        output=write_report(calculate_stage(inputs),args.report,loop_result=loop,loop_settings=settings,
            selected_device=document.get("selected_device"),corner_sweep=sweep,
            parameter_suggestion=document.get("parameter_suggestion"),loop_request=document.get("loop_request"))
        print(output);return 0
    import sys

    from PySide6.QtWidgets import QApplication

    from llc_design.gui import theme
    from llc_design.gui.lifetime import dispose_widgets

    from .gui import PowerStageMainWindow
    app=QApplication.instance() or QApplication(sys.argv)
    theme.apply_app_theme(app)
    window=PowerStageMainWindow()
    try:
        if args.project:window.open_from(args.project)
        window.showMaximized()
        return app.exec()
    finally:
        # A standalone PSD session may create the linked six-workspace owner.
        # Its aboutToQuit handler can already have disposed it; final cleanup
        # is idempotent and also runs if startup/open raises before app.exec().
        linked=getattr(window,"_linked_application",None)
        if linked is not None:linked.dispose()
        else:dispose_widgets([window])


if __name__ == "__main__":
    raise SystemExit(main())
