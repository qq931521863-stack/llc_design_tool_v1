import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog
from power_control_tools.gui.fra_tuning_map import FRATuningMapWindow, ScanImportDialog
from power_control_tools.fra.tuning_session import PIParameters, OperatingPoint, Scan
from power_control_tools.fra.models import FRAMeasurement, FRASourceFormat
from power_control_tools.fra.analysis import digital_frequency_response


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def sample(name="baseline", kp=.3):
    f = np.geomspace(10, 10000, 100)
    c = PIParameters(kp, .001, 1e-5)
    h = 10 / (1 + 1j*f/1000)**2 * digital_frequency_response(c.digital(), f)
    m = FRAMeasurement(f, 20*np.log10(abs(h)), np.angle(h, deg=True), FRASourceFormat.GENERIC)
    return Scan(name, m, c, OperatingPoint(400, 1000, "CCM", "bench"), "signed_loop_gain", "negative_feedback_L")


def test_map_sparse_history_and_repeated_actions(app, monkeypatch, tmp_path):
    w = FRATuningMapWindow()
    w.add_scan(sample())
    assert len(w.predictions) == 8
    assert len(w.figure.axes[0].images) == 0  # No invented filled surface.
    w.add_scan(sample("round2", .24))
    assert "round2" in w.summary.text()
    assert "consistent_in_band" in w.details.toPlainText()
    w.scan_selector.setCurrentIndex(0)
    assert "复测与上一轮" not in w.details.toPlainText()
    w.scan_selector.setCurrentIndex(1)
    path = tmp_path / "session.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    w.save_session(); w.save_session()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    w.open_session(); w.open_session()
    assert len(w.session.scans) == 2
    assert w.scan_selector.currentIndex() == 1
    before = w.session.to_json()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    w.import_scan(); w.open_session(); w.save_session(); w.export_current()
    assert w.session.to_json() == before
    w.show(); app.processEvents()
    assert not w.grab().isNull()
    w.close()


def test_import_validation_and_cancel(app, tmp_path):
    path = tmp_path / "scan.csv"
    path.write_text("f,m,p\n10,20,0\n100,0,-120\n1000,-20,-170\n")
    d = ScanImportDialog(str(path))
    d.cols[2].setValue(2)
    d.validate()
    assert d.scan is None and "different" in d.error.text()
    d.cols[2].setValue(3)
    d.confirm.setChecked(True)
    d.validate()
    assert d.result() == QDialog.DialogCode.Accepted
    assert d.scan.measurement.metadata["frequency_unit"] == "Hz"
    d2 = ScanImportDialog(str(path))
    d2.reject()
    assert d2.scan is None
    d.close(); d2.close()


def test_existing_workspace_reuses_window(app):
    from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow
    w = FRALoopDesignerWindow()
    w.open_tuning_map()
    first = w._tuning_map_window
    first.close()
    w.open_tuning_map()
    assert w._tuning_map_window is first
    first.close(); w.close()


def test_malformed_open_preserves_state_and_mismatch_suppresses(app, monkeypatch, tmp_path):
    import json
    from PySide6.QtWidgets import QMessageBox
    w = FRATuningMapWindow(); w.add_scan(sample())
    before = w.session.to_json()
    path = tmp_path / "bad.json"; path.write_text("null")
    warnings = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a))
    w.open_session()
    assert warnings and w.session.to_json() == before
    noisy = sample("drift", .24)
    m = noisy.measurement
    noisy.measurement = FRAMeasurement(m.frequency_hz, m.magnitude_db + 6, m.raw_phase_deg, m.source_format)
    w.add_scan(noisy)
    assert not w.predictions
    assert "Prediction disagreement" in w.details.toPlainText()
    w.add_scan(sample("different"))
    # Changing group must stop cross-operating-point comparisons.
    w.session.current.operating_point = OperatingPoint(380, 500, "DCM", "other")
    w.refresh()
    assert len(w.session.group()) == 1
    assert "复测与上一轮" not in w.details.toPlainText()
    assert "different" in w.summary.text()
    w.close()


def test_unconfirmed_and_excluded_points_never_rank(app):
    w = FRATuningMapWindow()
    s = sample(); s.measurement_type = "unknown"
    w.add_scan(s)
    assert not w.predictions and "Unknown" in w.details.toPlainText()
    s.measurement_type = "signed_loop_gain"; s.excluded_indices = [20]
    w.refresh()
    assert not w.predictions and "Unknown" in w.details.toPlainText()
    w.close()
