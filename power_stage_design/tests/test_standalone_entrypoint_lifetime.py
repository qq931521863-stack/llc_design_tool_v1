"""The real standalone Qt entry must release its own application trees."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("mode", ["quit", "linked_llc", "invalid_project"])
def test_standalone_main_finally_owns_every_created_workspace(mode, tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    script = r'''
import sys
from pathlib import Path
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow
from pyqtgraph import GraphicsView, PlotItem, ViewBox
from shiboken6 import isValid
from llc_design.gui.lifetime import dispose_widgets
from llc_design.gui import main_window as llc_module
from pfc_design.gui import main_window as pfc_module
from power_stage_design import __main__ as entry, gui
llc_module.check_for_updates = pfc_module.check_for_updates = lambda *a, **k: None
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
unrelated = QMainWindow()
roots, retained = [], []
def capture(window):
    roots.append(window)
    retained.extend([window, *window.findChildren(QObject)])
    for view in window.findChildren(GraphicsView):
        for item in view.scene().items():
            if isinstance(item, PlotItem):
                retained.extend([item.ctrlMenu, item.stateGroup])
            if isinstance(item, ViewBox) and item.menu is not None:
                retained.extend([item.menu, *item.menu.widgetGroups])
real_constructor = gui.PowerStageMainWindow
def construct():
    window = real_constructor()
    capture(window)
    return window
gui.PowerStageMainWindow = construct
mode, temporary = sys.argv[1:]
sys.argv = ['power-stage-design']
if mode == 'invalid_project':
    project = Path(temporary) / 'invalid.json'
    project.write_text('{ invalid JSON', encoding='utf-8')
    sys.argv.append(str(project))
    try:
        entry.main()
    except ValueError:
        pass
    else:
        raise AssertionError('Invalid project unexpectedly loaded')
else:
    def finish():
        if mode == 'linked_llc':
            window = roots[0]
            window.topology_combo.setCurrentIndex(window.topology_combo.findData('T21'))
            window.open_llc_workspace()
            controller = window._linked_application
            for owner in controller._windows():
                if owner is not window:
                    capture(owner)
            assert len(roots) == 6 and controller.llc_window.isVisible()
        app.quit()
    QTimer.singleShot(100, finish)
    assert entry.main() == 0
assert roots and retained and all(not isValid(obj) for obj in retained)
assert isValid(unrelated)
dispose_widgets([unrelated])
print('STANDALONE_OWNERSHIP_COMPLETE', mode)
'''
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", MPLBACKEND="Agg")
    result = subprocess.run([sys.executable, "-c", script, mode, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[2], env=environment,
                            capture_output=True, text=True, timeout=90, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "STANDALONE_OWNERSHIP_COMPLETE" in result.stdout
    assert "Traceback" not in result.stderr
