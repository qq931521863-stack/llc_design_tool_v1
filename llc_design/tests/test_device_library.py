from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from llc_design.models.devices import DeviceDatabase, USER_LIBRARY_SCHEMA


def test_builtin_device_library_has_useful_primary_and_sr_choices(tmp_path: Path):
    db = DeviceDatabase(user_path=tmp_path / "missing-user.json")
    assert len(db.primary) >= 6
    assert len(db.sr) >= 6
    assert db.get_primary("GEN_650V_SJ_20M").vds_max_v == pytest.approx(650.0)
    assert db.get_primary("GEN_650V_SIC_80M").rds_on_25_ohm == pytest.approx(0.080)
    assert db.get_sr("GEN_100V_SR_1P2M").vds_max_v == pytest.approx(100.0)
    assert db.get_sr("GEN_100V_SR_5P0M").rds_on_25_ohm == pytest.approx(0.005)


def test_user_device_round_trip_and_origin(tmp_path: Path):
    user_path = tmp_path / "devices.json"
    db = DeviceDatabase(user_path=user_path)
    custom = replace(
        db.get_primary("REF_650V_SIC_45M"),
        part_number="USER_TEST_650V_SIC",
        technology="Selected datasheet test device",
        rds_on_25_ohm=0.031,
        qoss_c=72e-9,
    )
    db.save_user_device("primary", custom)

    assert user_path.exists()
    reloaded = DeviceDatabase(user_path=user_path)
    got = reloaded.get_primary("USER_TEST_650V_SIC")
    assert got.rds_on_25_ohm == pytest.approx(0.031)
    assert got.qoss_c == pytest.approx(72e-9)
    assert reloaded.is_user("primary", got.part_number)
    assert not reloaded.is_user("primary", "REF_650V_SIC_45M")

    payload = json.loads(user_path.read_text(encoding="utf-8"))
    assert payload["metadata"]["schema"] == USER_LIBRARY_SCHEMA
    assert [entry["part_number"] for entry in payload["primary_mosfets"]] == ["USER_TEST_650V_SIC"]


def test_user_library_can_export_and_import(tmp_path: Path):
    first = DeviceDatabase(user_path=tmp_path / "first.json")
    primary = replace(first.get_primary("GEN_650V_SJ_70M"), part_number="USER_PRIMARY_A")
    sr = replace(first.get_sr("GEN_100V_SR_3P0M"), part_number="USER_SR_A")
    first.save_user_device("primary", primary)
    first.save_user_device("sr", sr)

    portable = first.export_user_library(tmp_path / "portable.json")
    second = DeviceDatabase(user_path=tmp_path / "second.json")
    counts = second.import_user_library(portable)

    assert counts == (1, 1)
    assert second.get_primary("USER_PRIMARY_A").rds_on_25_ohm == pytest.approx(primary.rds_on_25_ohm)
    assert second.get_sr("USER_SR_A").qrr_c == pytest.approx(sr.qrr_c)


def test_builtin_names_cannot_be_shadowed_by_user_library(tmp_path: Path):
    db = DeviceDatabase(user_path=tmp_path / "devices.json")
    builtin = db.get_primary("REF_650V_SIC_45M")
    with pytest.raises(ValueError, match="built-in reference name"):
        db.save_user_device("primary", builtin)


def test_user_device_delete(tmp_path: Path):
    db = DeviceDatabase(user_path=tmp_path / "devices.json")
    custom = replace(db.get_sr("REF_100V_SI_1P8M"), part_number="USER_DELETE_ME")
    db.save_user_device("sr", custom)
    assert db.is_user("sr", "USER_DELETE_ME")
    db.delete_user_device("sr", "USER_DELETE_ME")
    with pytest.raises(KeyError):
        db.get_sr("USER_DELETE_ME")


def test_colliding_record_in_hand_edited_user_file_is_ignored_with_warning(tmp_path: Path):
    path = tmp_path / "devices.json"
    base = DeviceDatabase(user_path=path)
    collision = base.get_primary("REF_650V_SIC_45M")
    payload = {
        "metadata": {"schema": USER_LIBRARY_SCHEMA},
        "primary_mosfets": [collision.__dict__],
        "sr_mosfets": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    db = DeviceDatabase(user_path=path)
    assert len([d for d in db.primary if d.part_number == collision.part_number]) == 1
    assert db.warnings
