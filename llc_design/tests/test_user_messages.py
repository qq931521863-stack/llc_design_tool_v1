"""Presentation changes must preserve constraints and diagnostic evidence."""

import os

import pytest

from llc_design.user_messages import design_reason, design_status, show_operation_issue
from llc_design.i18n import current_language, set_language, t


@pytest.fixture(autouse=True)
def chinese_messages():
    previous = current_language()
    set_language("zh-Hans", notify=False)
    yield
    set_language(previous, notify=False)


def test_screenshot_diagnostics_keep_values_and_explain_next_step():
    gap = design_reason("transformer: total gap 5.79 mm exceeds 4.00 mm")
    assert "变压器" in gap and "5.79 mm" in gap and "4.00 mm" in gap
    assert "核对" in gap
    hold = design_reason("installed bus capacitance does not meet requested hold-up time")
    assert "未达到设定要求" in hold and "母线电容" in hold
    assert "低于" in design_reason("bus voltage falls below LLC hold-up endpoint")


def test_unknown_diagnostic_is_not_hidden_or_marked_successful():
    reason = "new solver diagnostic: operating point unavailable"
    assert reason in design_reason(reason)
    assert "复核" in design_reason(reason)
    assert "未满足项" in design_status(False)
    set_language("en", notify=False)
    assert design_reason(reason) == reason
    assert "unmet constraints" in design_status(False)


def test_real_unmet_hold_time_stays_infeasible():
    from llc_design.core.spec import LLCDesignSpec
    from llc_design.models.system import LLCSystemAnalyzer

    result = LLCSystemAnalyzer().analyze(LLCDesignSpec().clone(bus_capacitance_f=100e-6))
    assert not result.feasible
    assert "installed bus capacitance does not meet requested hold-up time" in result.feasibility_reasons
    assert "计算完成" in design_status(result.feasible)


def test_dialog_keeps_full_details_and_actual_error_severity(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import Qt

    app = qt.QApplication.instance() or qt.QApplication([])
    captured = []
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda box: captured.append(box))
    details = "Traceback\nValueError: <invalid input>"
    show_operation_issue(None, t("计算失败"), details, critical=True)
    box = captured[0]
    assert box.detailedText() == details
    assert box.icon() == qt.QMessageBox.Icon.Critical
    assert box.textFormat() == Qt.TextFormat.PlainText
    assert "详细信息" in box.informativeText()
    assert box.text() == "本次计算尚未完成"
    app.processEvents()
