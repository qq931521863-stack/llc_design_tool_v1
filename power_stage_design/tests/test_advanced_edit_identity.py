"""Explicit advanced numeric edits release restored precision, not model policy.

These are real Qt editor -> options -> identity/JSON snapshot tests. They do not
claim physical solver differences for numbers below the display precision.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from power_stage_design.advanced_gui import AdvancedModelWidget
from power_stage_design.gui import PRESETS
from power_stage_design.models import DesignInputs
from power_stage_design.project import write_json
from power_stage_design.psfb_dynamics import PSFBOptions
from power_stage_design.qr_dynamics import QROptions
from power_stage_design.two_switch_dynamics import T05Options


@pytest.fixture
def editor():
    app = QApplication.instance() or QApplication([])
    widget = AdvancedModelWidget()
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def base_options(topology):
    if topology == "T05":
        return T05Options()
    if topology == "T11":
        return QROptions()
    return PSFBOptions(coss_f=300e-12, r_on_ohm=.15, body_diode_drop_v=.8,
        body_diode_resistance_ohm=.05, transformer_capacitance_f=100e-12,
        rectifier_capacitance_f=1e-9, rectifier_resistance_ohm=.02,
        parameter_source="Explicit UI identity test constants")


@pytest.mark.parametrize("topology,group,option_key", [
    ("T05", "t05", "two_switch_options"),
    ("T11", "qr", "qr_options"),
    ("T18", "psfb", "psfb_options"),
])
def test_every_advanced_field_explicit_edit_releases_only_its_exact_snapshot(
    editor, tmp_path, topology, group, option_key,
):
    boxes = getattr(editor, group + "_widgets")
    fields = getattr(editor, group + "_fields")
    assert set(boxes) == {field[0] for field in fields}
    changed = []
    editor.changed.connect(lambda: changed.append(True))
    for field in fields:
        key, _, scale = field[:3]
        # PSFB tolerance has a strict upper bound of 1e-6; use 0.1 ppm.
        text = ".1" if group == "psfb" and key == "relative_tolerance" else "1"
        shown = float(text)
        exact = (shown + 1e-13) / scale
        original = replace(base_options(topology), **{key: exact})
        source = replace(PRESETS[topology], **{option_key: original})
        changed.clear()
        editor.restore(source)
        assert not changed, (group, key, "restore emitted a user change")
        assert editor.options()[option_key] == original
        cache = dict(editor._exact)
        box = boxes[key]
        assert box.value() == shown
        values = []
        box.valueChanged.connect(values.append)
        box.setFocus()
        box.selectAll()
        QTest.keyClicks(box, text)
        box.interpretText()
        box.valueChanged.disconnect(values.append)
        assert not values, (group, key, "must exercise unchanged stored value")
        assert changed == [True], (group, key, "one notification for hidden precision")
        assert group + "." + key not in editor._exact
        expected_cache = {name: value for name, value in cache.items() if name != group + "." + key}
        assert editor._exact == expected_cache
        expected = replace(original, **{key: shown / scale})
        assert editor.options()[option_key] == expected
        current = replace(source, **editor.options())
        assert current.design_id != source.design_id
        path = write_json({"inputs": asdict(current), "identity": current.design_id},
                          tmp_path / (group + "-" + key + ".json"))
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        assert DesignInputs(**snapshot["inputs"]) == current
        assert snapshot["inputs"][option_key][key] == shown / scale
        assert snapshot["identity"] == current.design_id
        # Repeat the same edit: no cache remains and no extra event is needed.
        box.selectAll()
        QTest.keyClicks(box, text)
        box.interpretText()
        assert changed == [True]
        # A later load reinstates the original precision without an edit event.
        changed.clear()
        editor.restore(source)
        assert editor.options()[option_key] == original and not changed
