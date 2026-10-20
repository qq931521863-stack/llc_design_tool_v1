"""Visible native canvases must survive tab changes, not only hidden pytest views."""
import os
import subprocess
import sys


def test_actual_vienna_parent_pll_sidebar_run_paint_and_close():
    code=r'''
import time
from llc_design.tests.desktop_support import offline_update_checks
with offline_update_checks():
    from PySide6.QtWidgets import QApplication,QScrollArea
    from pfc_design.gui.main_window import PFCMainWindow
    app=QApplication([]);host=PFCMainWindow();host.resize(1280,720)
    try:
        view=host.vienna_view;host.subtabs.setCurrentWidget(view);host.show();app.processEvents()
        pll=view.pll_comparison
        assert view.tabs.indexOf(pll)>=0
        view.tabs.setCurrentWidget(pll);app.processEvents()
        # The control sidebar can scroll; the Matplotlib tabs are not inside that area.
        parent=pll.canvas.parentWidget()
        while parent is not None and parent is not pll:
            assert not isinstance(parent,QScrollArea)
            parent=parent.parentWidget()
        pll.inputs['sample_hz'].setValue(4000)
        pll.run_button.click();deadline=time.monotonic()+45
        while pll._worker is not None and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        assert pll._worker is None and pll.result is not None
        for kind in range(pll.kind.count()):
            pll.kind.setCurrentIndex(kind)
            for tab in range(pll.tabs.count()):
                pll.tabs.setCurrentIndex(tab)
                for _ in range(6):app.processEvents();time.sleep(.01)
            pll.tabs.setCurrentWidget(pll.canvas);pll.canvas.draw()
            assert not host.grab().isNull()
        for _ in range(12):app.processEvents();time.sleep(.01)
        assert host.size().width()==1280 and host.size().height()==720
        assert host.close()
        for _ in range(6):app.processEvents();time.sleep(.01)
    finally:
        from llc_design.gui.lifetime import dispose_widgets
        dispose_widgets([host])
print('PASS_REAL_VIENNA_PLL_PARENT',flush=True)
'''
    process=subprocess.run([sys.executable,'-X','faulthandler','-c',code],
        env={**os.environ,'QT_QPA_PLATFORM':'offscreen','OPENBLAS_NUM_THREADS':'1'},
        text=True,capture_output=True,timeout=70)
    assert process.returncode==0,process.stdout+'\n'+process.stderr
    assert 'PASS_REAL_VIENNA_PLL_PARENT' in process.stdout
