from __future__ import annotations

from dataclasses import replace
import os

import pytest


def test_device_library_installs_sr_selector_and_updates_llc_spec(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")

    from llc_design.gui.device_library_install import install_device_library
    from llc_design.gui.main_window import LLCMainWindow

    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    window = LLCMainWindow()
    database = install_device_library(window, user_path=tmp_path / "devices.json")

    assert window.device_database is database
    assert window.primary_device_combo.count() >= 6
    assert window.sr_device_combo.count() >= 6
    assert window.device_library_button.text() == "Device Library…"
    assert window.device_compare_button.text() == "Device Compare…"

    primary_index = window.primary_device_combo.findData("GEN_650V_SJ_70M")
    sr_index = window.sr_device_combo.findData("GEN_100V_SR_3P0M")
    assert primary_index >= 0 and sr_index >= 0
    window.primary_device_combo.setCurrentIndex(primary_index)
    window.sr_device_combo.setCurrentIndex(sr_index)
    spec = window._spec_from_widgets()
    assert spec.primary_device == "GEN_650V_SJ_70M"
    assert spec.sr_device == "GEN_100V_SR_3P0M"

    custom = replace(
        database.get_primary("REF_650V_SIC_45M"),
        part_number="USER_GUI_PRIMARY",
    )
    database.save_user_device("primary", custom)
    window.refresh_device_library("USER_GUI_PRIMARY", spec.sr_device)
    assert window.primary_device_combo.currentData() == "USER_GUI_PRIMARY"
    assert "User library" in window.primary_device_details.text()

    window.close()
    app.processEvents()
