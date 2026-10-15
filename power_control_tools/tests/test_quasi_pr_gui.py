import os

import numpy as np
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.fixture(scope='module')
def app():
    qt = pytest.importorskip('PySide6.QtWidgets')
    instance = qt.QApplication.instance() or qt.QApplication([])
    yield instance
    instance.processEvents()


def test_pr_page_export_routing_invalid_design_and_p_only(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from power_control_tools.gui.main_window import ControlToolsMainWindow
    window = ControlToolsMainWindow()
    page = window.pr_view
    window.workspace_pages.setCurrentWidget(page)
    for box, _, _ in page.branch_widgets.values():
        box.setChecked(True)
    window.recalculate()
    assert len(page.config.branches) == 6
    target = tmp_path/'all.h'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(target), ''))
    window.export_c99()
    assert 'MULTI_PR_pr_ctrl_ys_func' in target.read_text()
    page.fs.setValue(100)
    page.recalculate()
    assert page.config is None and not page.export_button.isEnabled()
    assert not page.code.toPlainText()
    page.fs.setValue(40000)
    for box, _, _ in page.branch_widgets.values():
        box.setChecked(False)
    page.recalculate()
    assert page.config.branches == ()
    assert page.export_button.isEnabled()
    page.timer.stop()
    window.close()


def test_pfc_magnetic_fit_entry_and_export(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from llc_design.magnetics.impedance_fit import ChokeModel
    from pfc_design.gui.inductor_design import PFCInductorDesignResultView
    view = PFCInductorDesignResultView('test')
    view.impedance_fit_button.click()
    dialog = view.impedance_dialog
    f = np.geomspace(20, 1e7, 100)
    z = ChokeModel(.02,120e-6,25000,45e-12).impedance(f)
    source = tmp_path/'measured.csv'
    np.savetxt(source, np.c_[f,z.real,z.imag], delimiter=',',
               header='frequency_hz,real_ohm,imag_ohm', comments='')
    dialog.fit_file(source)
    assert dialog.result is not None and dialog.export.isEnabled()
    target = tmp_path/'fit.json'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(target), ''))
    dialog.export_model()
    assert target.exists() and target.with_suffix('.cir').exists()
    source.write_text('bad,input\n1,2\n')
    dialog.fit_file(source)
    assert dialog.result is None and not dialog.export.isEnabled()
    dialog.close()
    view.close()


def test_pr_toolbar_recovers_after_automatic_recalculation(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QFileDialog
    from power_control_tools.gui.main_window import ControlToolsMainWindow

    window = ControlToolsMainWindow()
    page = window.pr_view
    window.workspace_pages.setCurrentWidget(page)
    page.fs.setValue(100)
    window.recalculate()
    assert page.config is None
    assert not window.export_action.isEnabled()
    page.fs.setValue(40000)
    loop = QEventLoop()
    QTimer.singleShot(350, loop.quit)
    loop.exec()
    assert page.config is not None
    assert page.export_button.isEnabled()
    assert window.export_action.isEnabled()
    target = tmp_path / 'recovered.h'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(target), ''))
    window.export_action.trigger()
    assert 'MULTI_PR_pr_ctrl_ys_func' in target.read_text()
    page.timer.stop()
    window.close()
