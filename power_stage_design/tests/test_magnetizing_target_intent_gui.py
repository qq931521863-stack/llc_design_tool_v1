"""Optional Lm sizing must express this design's explicit user intent."""
import json
import os
import time
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from power_stage_design.gui import PRESETS, PowerStageMainWindow
from power_stage_design.project import read_project, save_project
from power_stage_design.suggestions import MAGNETIZING_TOPOLOGIES
from power_stage_design.tests.test_psfb_magnetizing_sizing import example, reference


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    view = PowerStageMainWindow()
    view.show()
    app.processEvents()
    yield view
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def wait(view):
    deadline = time.monotonic() + 30
    app = QApplication.instance()
    while view._jobs and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert not view._jobs
    assert view._pool.waitForDone(30000)


@pytest.mark.parametrize("topology", sorted(MAGNETIZING_TOPOLOGIES))
def test_optional_magnetizing_target_defaults_off_and_stays_explicit_on_same_topology(window, topology):
    window.set_inputs(PRESETS[topology])
    wait(window)
    assert window.suggestion_magnetizing.value() == 0
    assert window._suggestion_targets().magnetizing_ripple_pp_a is None
    window.suggestion_magnetizing.setValue(.037)
    assert window._suggestion_targets().magnetizing_ripple_pp_a == .037
    source = window.current_inputs()
    window.set_inputs(replace(source, inductance_h=source.inductance_h * 1.05))
    wait(window)
    assert window._suggestion_targets().magnetizing_ripple_pp_a == .037
    window.input_widgets["inductance_h"].setValue(source.inductance_h * 1e6)
    assert window._suggestion_targets().magnetizing_ripple_pp_a == .037


def test_magnetizing_target_does_not_cross_topology_or_unrelated_project_boundary(window, tmp_path):
    window.set_inputs(PRESETS["T17"])
    wait(window)
    window.suggestion_magnetizing.setValue(.037)
    # Actual topology-selector change, including an intermediate hidden field.
    for topology in ("T01", "T18"):
        window.topology_combo.setCurrentIndex(window.topology_combo.findData(topology))
        assert window.suggestion_magnetizing.value() == 0
        assert window._suggestion_targets().magnetizing_ripple_pp_a is None
    window.set_inputs(example(.1))
    wait(window)
    window.suggestion_magnetizing.setValue(.041)
    plain_project = save_project(example(.1), tmp_path / "no-suggestion.json")
    window.open_from(plain_project)
    wait(window)
    assert window._applied_suggestion is None
    assert window._suggestion_targets().magnetizing_ripple_pp_a is None
    assert window.suggestion_magnetizing.value() == 0


@pytest.mark.parametrize("branch", ["CCM", "DCM"])
@pytest.mark.parametrize("target_lm", [False, True])
def test_real_psfb_optional_lm_target_preview_apply_reopen_report(window, tmp_path, branch, target_lm):
    expected = example(.1 if branch == "DCM" else 5.)
    # Independent MNA/IVP observer, not the production model's target values.
    ripple, magnetic, duty, _ = reference(expected, branch)
    source = replace(expected, inductance_h=expected.inductance_h * 1.4,
        turns_ratio_np_ns=3.5,
        magnetizing_inductance_h=expected.magnetizing_inductance_h * (1.5 if target_lm else 1))
    window.set_inputs(source)
    wait(window)
    window.tabs.setCurrentWidget(window.suggestion_tab)
    window.suggestion_ripple.setValue(ripple)
    window.suggestion_duty.setValue(duty)
    window.suggestion_ratio.setChecked(True)
    if target_lm:
        window.suggestion_magnetizing.setValue(magnetic)
    QTest.mouseClick(window.suggest_button, Qt.MouseButton.LeftButton)
    wait(window)
    proposal = window.parameter_proposal
    assert proposal is not None, window.suggestion_output.toPlainText()
    assert window.current_inputs() == source
    assert window.result.design_id == source.design_id
    assert proposal.result.mode == branch
    assert proposal.inputs.inductance_h == pytest.approx(expected.inductance_h, rel=5e-6)
    assert proposal.inputs.turns_ratio_np_ns == pytest.approx(expected.turns_ratio_np_ns, rel=5e-6)
    assert proposal.result.stress["L1"]["current_pp_a"] == pytest.approx(ripple, rel=3e-6)
    assert proposal.result.duty == pytest.approx(duty, rel=3e-6)
    changed = {"inductance_h", "turns_ratio_np_ns"}
    if target_lm:
        changed.add("magnetizing_inductance_h")
        assert proposal.inputs.magnetizing_inductance_h != source.magnetizing_inductance_h
        assert proposal.inputs.magnetizing_inductance_h == pytest.approx(expected.magnetizing_inductance_h, rel=5e-6)
        assert proposal.targets.magnetizing_ripple_pp_a == pytest.approx(magnetic, abs=5e-10)
        assert proposal.result.stress["Lm"]["current_pp_a"] == pytest.approx(magnetic, rel=3e-6)
    else:
        assert proposal.inputs.magnetizing_inductance_h == source.magnetizing_inductance_h
        assert proposal.targets.magnetizing_ripple_pp_a is None
    assert set(proposal.changed_fields) == changed
    QTest.mouseClick(window.apply_suggestion_button, Qt.MouseButton.LeftButton)
    wait(window)
    assert window.current_inputs() == proposal.inputs
    assert window.result.design_id == proposal.result.design_id
    path = window.save_to(tmp_path / "applied.json")
    saved, document = read_project(path)
    assert saved == proposal.inputs
    assert document["parameter_suggestion"] == proposal.record()
    if not target_lm:
        # Historical records may omit the optional target. The current reader
        # must restore canonical None, without inventing the old GUI default.
        historical = json.loads(path.read_text(encoding="utf-8"))
        historical["parameter_suggestion"]["targets"].pop("magnetizing_ripple_pp_a")
        path.write_text(json.dumps(historical), encoding="utf-8")
        assert read_project(path)[1]["parameter_suggestion"] == proposal.record()
    # An unrelated explicitly entered value must not override saved None or a
    # saved positive target, whether opening from this topology or another one.
    window.suggestion_magnetizing.setValue(.041)
    if target_lm:
        window.set_inputs(PRESETS["T17"])
        wait(window)
    window.open_from(path)
    wait(window)
    assert window.current_inputs() == proposal.inputs
    assert window.result.design_id == proposal.result.design_id
    assert window._applied_suggestion == proposal.record()
    assert window._suggestion_targets().magnetizing_ripple_pp_a == proposal.targets.magnetizing_ripple_pp_a
    restored = window.save_to(tmp_path / "restored.json")
    assert json.loads(restored.read_text(encoding="utf-8"))["parameter_suggestion"] == proposal.record()
    text = window.report_to(tmp_path / "report.html").read_text(encoding="utf-8")
    assert proposal.source_design_id in text
    assert proposal.result.design_id in text
    assert "magnetizing_ripple_pp_a" in text
