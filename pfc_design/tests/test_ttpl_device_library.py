from __future__ import annotations

from dataclasses import replace
import json

import pytest

from pfc_design.core.spec import MosfetSpec
from pfc_design.engineering import (
    PFCDeviceDatabase,
    TTPLDesignSpec,
    analyze_ttpl_design,
    compare_ttpl_devices,
    evaluate_hf_device,
)


def _user_device(part: str = "USER_650V_40M") -> MosfetSpec:
    return MosfetSpec(
        manufacturer="UnitTest",
        part_number=part,
        technology="SiC",
        vds_max=650.0,
        id_25c=80.0,
        id_100c=60.0,
        rds_on_25c=0.040,
        rds_on_150c=0.065,
        rds_alpha=0.004,
        qg_nc=55.0,
        coss_er_pF=90.0,
        tr_ns=15.0,
        tf_ns=9.0,
        vgs=15.0,
        package="TEST",
        price_usd=1.0,
        eon_ref_uj=60.0,
        eoff_ref_uj=35.0,
        e_ref_v=400.0,
        e_ref_i=20.0,
    )


def test_pfc_user_device_library_persists_without_shadowing_builtins(tmp_path):
    path = tmp_path / "pfc_devices.json"
    db = PFCDeviceDatabase(user_path=path)
    builtin_count = len(db.builtin)
    assert builtin_count > 5

    custom = _user_device()
    db.save_user_device(custom)
    assert path.exists()
    assert db.is_user(custom.part_number)
    assert len(db.all) == builtin_count + 1
    assert db.get(custom.part_number).rds_on_25c == pytest.approx(0.040)

    reloaded = PFCDeviceDatabase(user_path=path)
    assert reloaded.is_user(custom.part_number)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["metadata"]["schema"].startswith("power-design-toolkit-pfc")

    with pytest.raises(ValueError):
        reloaded.save_user_device(replace(custom, part_number=reloaded.builtin[0].part_number))


def test_pfc_user_library_export_import_roundtrip(tmp_path):
    source = PFCDeviceDatabase(user_path=tmp_path / "source.json")
    source.save_user_device(_user_device("USER_EXPORT_A"))
    exported = source.export_user_library(tmp_path / "portable.json")

    target = PFCDeviceDatabase(user_path=tmp_path / "target.json")
    count = target.import_user_library(exported)
    assert count == 1
    assert target.is_user("USER_EXPORT_A")


def test_ttpl_hf_loss_responds_to_rds_and_derating():
    design = analyze_ttpl_design(TTPLDesignSpec())
    base = _user_device("BASE")
    low_rds = replace(base, part_number="LOW_RDS", rds_on_25c=0.020, rds_on_150c=0.032)
    high_rds = replace(base, part_number="HIGH_RDS", rds_on_25c=0.080, rds_on_150c=0.130)

    low = evaluate_hf_device(design, low_rds, vin_rms_v=design.spec.vin_min_rms_v)
    high = evaluate_hf_device(design, high_rds, vin_rms_v=design.spec.vin_min_rms_v)
    assert low.active_conduction_w < high.active_conduction_w
    assert low.sr_conduction_w < high.sr_conduction_w
    assert low.total_w < high.total_w

    under_rated = evaluate_hf_device(
        design,
        replace(base, part_number="450V_TEST", vds_max=450.0),
        vin_rms_v=design.spec.vin_min_rms_v,
        voltage_derating=0.80,
    )
    assert under_rated.voltage_ok is False


def test_ttpl_device_compare_is_sorted_and_includes_user_records(tmp_path):
    db = PFCDeviceDatabase(user_path=tmp_path / "pfc_devices.json")
    db.save_user_device(_user_device("USER_COMPARE"))
    design = analyze_ttpl_design(TTPLDesignSpec())

    comparison = compare_ttpl_devices(design, db, workpoint="low")
    assert len(comparison.hf_devices) == len(db.all)
    assert len(comparison.slow_devices) == len(db.all)
    assert comparison.hf_devices == tuple(sorted(comparison.hf_devices, key=lambda item: item.total_w))
    assert comparison.slow_devices == tuple(sorted(comparison.slow_devices, key=lambda item: item.total_w))
    assert any(item.device.part_number == "USER_COMPARE" for item in comparison.hf_devices)
