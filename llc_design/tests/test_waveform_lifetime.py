"""Qt scene cleanup must work in a real process, including application shutdown."""
import os
import subprocess
import sys

import pytest


def test_repeated_waveform_parent_destruction_and_qapp_shutdown():
    pytest.importorskip('PySide6')
    program = '''
from PySide6.QtWidgets import QApplication
from shiboken6 import delete, isValid
from llc_design.gui.widgets.waveform_view import WaveformView
app = QApplication([])
for _ in range(4):
    view = WaveformView()
    graphics = view.graphics
    graphics.addPlot().plot([0, 1, 2], [0, 1, 0])
    delete(view)
    assert not isValid(view)
    assert not isValid(graphics)
    app.processEvents()
app.shutdown()
'''
    result = subprocess.run([sys.executable, '-c', program], text=True, capture_output=True,
                            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Traceback' not in result.stderr, result.stderr
