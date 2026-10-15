import json
from pathlib import Path

import numpy as np
import pytest

from llc_design.magnetics.catalog import load_magnetic_catalog
from llc_design.magnetics.material import MaterialDatabase
from llc_design.magnetics.transformer_designer import load_transformer_core_presets
from llc_design.magnetics.validated_surrogate import ValidatedMagneticSurrogate
from llc_design.magnetics.validation import compare_measurements


def test_raw_catalog_retains_flags_and_verified_bobbin_area_is_separate():
    records = load_magnetic_catalog()["records"]
    assert len(records) == 137
    assert sum(bool(r["issues"]) for r in records) == 3
    presets = load_transformer_core_presets()
    assert len(presets) == 46
    pq = next(p for p in presets.values() if p.part_number == "PC44PQ35/35Z-12")
    assert pq.ae_mm2 == 196
    assert pq.ve_mm3 == 17200
    assert pq.winding_area_mm2 == 154.2
    assert "p.43" in pq.source
    assert len(pq.source_notes) >= 3


def test_measurement_comparison_keeps_signed_error_and_rejects_invalid_rows(tmp_path):
    path = tmp_path / "measured.csv"
    header = "material_key,frequency_hz,temperature_c,b_peak_t,measured_pv_w_m3\n"
    pv = (
        MaterialDatabase().get("TDK_PC40_2002").steinmetz_density_w_m3(100000, 0.2, 100)
    )
    path.write_text(header + f"TDK_PC40_2002,100000,100,.2,{pv / 1.1}\n")
    report = compare_measurements(path)
    assert report["mean_signed_error_percent"] == pytest.approx(10)
    path.write_text(header + "TDK_PC40_2002,100000,100,.2,0\n")
    with pytest.raises(ValueError, match="row 2"):
        compare_measurements(path)


def test_validated_surrogate_is_optional_and_bounded():
    model = ValidatedMagneticSurrogate()
    b = 0.1 * np.sin(2 * np.pi * np.arange(1024) / 1024)
    pv = model.predict(b, 100000, 70, material_grade="3C90")
    assert pv.shape == (1,) and pv[0] > 0
    for values in [
        (b, 100000, 70, "PC40"),
        (b, 1e6, 70, "3C90"),
        (b + 0.1, 100000, 70, "3C90"),
    ]:
        with pytest.raises(ValueError):
            model.predict(values[0], values[1], values[2], material_grade=values[3])
    with pytest.raises(ValueError, match="1024"):
        model.predict(b[:512], 100000, 70, material_grade="3C90")
    path = Path(__file__).parents[1] / "data/magnetic_surrogate_3c90.json"
    assert json.loads(path.read_text())["validation_gate_passed"]
