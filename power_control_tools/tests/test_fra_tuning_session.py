"""Adversarial tests for measured-only FRA PI experiment sessions.

The synthetic plant and PI response below are independent of the implementation:
passing a de-embed/re-embed test with the same evaluator on both sides would hide
coefficient, sample-time, and discretization mistakes.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import replace

import numpy as np
import pytest

from power_control_tools.fra.analysis import digital_frequency_response
from power_control_tools.fra.models import FRAMeasurement, FRASourceFormat
from power_control_tools.fra.tuning_session import (
    OperatingPoint,
    PIParameters,
    Scan,
    Session,
    Targets,
    export_scan_csv,
    import_csv,
    metrics,
    predict,
    score,
    suggestions,
)


FREQUENCIES = np.geomspace(10.0, 10_000.0, 181)
OPERATING_POINT = OperatingPoint(400.0, 1000.0, "CCM", "bench-1")


def independent_pi(controller, frequencies):
    q = np.exp(-2j * np.pi * np.asarray(frequencies) * controller.sample_period_s)
    if controller.method == "tustin":
        integral = controller.ki * controller.sample_period_s * (1 + q) / (2 * (1 - q))
    else:
        integral = controller.ki * controller.sample_period_s / (1 - q)
    return controller.kp + integral


def known_plant(frequencies):
    f = np.asarray(frequencies)
    return 8.0 * np.exp(-2j * np.pi * f * 8e-6) / ((1 + 1j * f / 300) * (1 + 1j * f / 6000))


def make_scan(name="baseline", controller=None, frequencies=None, response=None, **kwargs):
    controller = controller or PIParameters(0.2, 0.002, 1e-5)
    f = FREQUENCIES if frequencies is None else np.asarray(frequencies, dtype=float)
    h = known_plant(f) * independent_pi(controller, f) if response is None else np.asarray(response)
    measurement = FRAMeasurement(
        f.copy(), 20 * np.log10(np.abs(h)), np.angle(h, deg=True),
        FRASourceFormat.GENERIC, "fixture.csv", metadata={"source": "independent synthetic plant"},
    )
    defaults = {"measurement_type": "signed_loop_gain", "injection_convention": "negative_feedback_L"}
    defaults.update(kwargs)
    return Scan(name, measurement, controller, defaults.pop("operating_point", OPERATING_POINT), **defaults)


def session_document():
    session = Session()
    session.add(make_scan())
    return json.loads(session.to_json())


@pytest.mark.parametrize("method", ["tustin", "backward_euler"])
def test_exact_discrete_pi_coefficients_and_known_plant_prediction(method):
    original = PIParameters(0.2, 0.002, 1e-5, method)
    candidate = replace(original, kp=0.27, ti_s=0.0015)
    scan = make_scan(controller=original)
    expected_old = independent_pi(original, FREQUENCIES)
    actual_old = digital_frequency_response(original.digital(), FREQUENCIES)
    np.testing.assert_allclose(actual_old, expected_old, rtol=2e-13, atol=2e-13)
    np.testing.assert_allclose(scan.response() / actual_old, known_plant(FREQUENCIES), rtol=2e-13, atol=2e-13)
    np.testing.assert_allclose(predict(scan, original), scan.response(), rtol=2e-13, atol=2e-13)
    np.testing.assert_allclose(
        predict(scan, candidate), known_plant(FREQUENCIES) * independent_pi(candidate, FREQUENCIES),
        rtol=2e-13, atol=2e-13,
    )
    assert original.digital().a == (1.0, -1.0)
    assert original.digital().sample_rate_hz == pytest.approx(100_000)
    expected_b = (0.2005, -0.1995) if method == "tustin" else (0.201, -0.2)
    assert original.digital().b == pytest.approx(expected_b)


@pytest.mark.parametrize("field", ["kp", "ti_s", "sample_period_s"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), -float("inf"), True])
def test_pi_rejects_invalid_numeric_inputs(field, value):
    with pytest.raises((ValueError, TypeError)):
        replace(PIParameters(0.2, 0.002, 1e-5), **{field: value})


@pytest.mark.parametrize("kwargs", [
    {"kp": 1e308, "ti_s": 1e-308, "sample_period_s": 1e-5},
    {"kp": 0.2, "ti_s": 0.002, "sample_period_s": 1e-320},
])
def test_pi_rejects_nonfinite_derived_coefficients_or_rate(kwargs):
    with pytest.raises(ValueError):
        PIParameters(**kwargs).digital()


@pytest.mark.parametrize("kwargs", [{"method": "forward_euler"}, {"method": "pid"}, {"input_unit": " "}, {"output_unit": ""}])
def test_pi_requires_supported_discretization_and_units(kwargs):
    with pytest.raises(ValueError):
        replace(PIParameters(0.2, 0.002, 1e-5), **kwargs)


@pytest.mark.parametrize("kwargs", [
    {"sample_period_s": 2e-5}, {"method": "backward_euler"},
    {"input_unit": "ADC count"}, {"output_unit": "PWM count"},
])
def test_prediction_cannot_silently_change_controller_contract(kwargs):
    scan = make_scan()
    with pytest.raises(ValueError):
        predict(scan, replace(scan.controller, **kwargs))


def test_csv_mapping_converts_explicit_units_and_preserves_original_order(tmp_path):
    path = tmp_path / "mapped.csv"
    path.write_text("note;phase;gain;freq\nC;0.5;2;3\nA;-1;0.5;1\nB;1.5;1;2\n", encoding="utf-8")
    m = import_csv(path, columns=(3, 2, 1), frequency_unit="kHz", magnitude_unit="linear", phase_unit="rad", delimiter=";")
    np.testing.assert_array_equal(m.frequency_hz, [1000, 2000, 3000])
    np.testing.assert_allclose(m.magnitude_db, 20 * np.log10([0.5, 1, 2]))
    np.testing.assert_allclose(m.raw_phase_deg, np.rad2deg([-1, 1.5, 0.5]))
    assert m.metadata["raw_rows"] == [[3.0, 2.0, 0.5], [1.0, 0.5, -1.0], [2.0, 1.0, 1.5]]
    assert m.metadata["columns"] == [3, 2, 1]
    assert m.metadata["frequency_unit"] == "kHz"
    assert m.metadata["magnitude_unit"] == "linear"
    assert m.metadata["phase_unit"] == "rad"
    assert m.metadata["delimiter"] == ";"
    assert m.metadata["raw_text"] == path.read_text(encoding="utf-8")
    assert m.metadata["sorted"] is True


def test_csv_radians_per_second_bom_and_headerless_input(tmp_path):
    path = tmp_path / "angular.csv"
    path.write_text("\ufeff" + "\n".join(f"{2*np.pi*f},0,-90" for f in [1, 2, 3]), encoding="utf-8")
    m = import_csv(path, frequency_unit="rad/s", skip_rows=0)
    np.testing.assert_allclose(m.frequency_hz, [1, 2, 3])
    assert m.metadata["skip_rows"] == 0


@pytest.mark.parametrize("rows", [
    "1,0,0\n1,1,1\n3,2,2", "0,0,0\n2,0,0\n3,0,0",
    "-1,0,0\n2,0,0\n3,0,0", "1,nan,0\n2,0,0\n3,0,0",
    "1,0,inf\n2,0,0\n3,0,0", "nan,0,0\n2,0,0\n3,0,0",
    "1,0,0\n2,nope,0\n3,0,0", "1,0,0\n2,0\n3,0,0", "1,0,0\n2,0,0",
])
def test_csv_does_not_silently_discard_invalid_numeric_evidence(tmp_path, rows):
    path = tmp_path / "bad.csv"
    path.write_text("f,g,p\n" + rows, encoding="utf-8")
    with pytest.raises(ValueError):
        import_csv(path)


@pytest.mark.parametrize("kwargs", [
    {"frequency_unit": "rpm"}, {"magnitude_unit": "dBm"}, {"phase_unit": "turn"},
    {"columns": (0, 0, 1)}, {"columns": (0, 1)}, {"columns": (-1, 1, 2)},
    {"columns": (True, 1, 2)}, {"skip_rows": -1}, {"skip_rows": True},
    {"magnitude_unit": "linear"},
])
def test_csv_rejects_invalid_mapping_units_and_nonpositive_linear_gain(tmp_path, kwargs):
    path = tmp_path / "scan.csv"
    path.write_text("f,g,p\n1,0,-90\n2,1,-90\n3,1,-90\n", encoding="utf-8")
    with pytest.raises(ValueError):
        import_csv(path, **kwargs)


@pytest.mark.parametrize("kwargs", [
    {"measurement_type": "unknown"}, {"measurement_type": "plant"},
    {"injection_convention": "unknown"}, {"injection_convention": "positive_feedback"},
    {"quality_flags": ["saturated"]},
])
def test_unresolved_semantics_or_quality_never_produces_predictions(kwargs):
    scan = make_scan(**kwargs)
    assert scan.prediction_blocker()
    assert metrics(scan) is None
    assert suggestions(scan, Targets()) == []
    with pytest.raises(ValueError):
        predict(scan, scan.controller)


def test_nyquist_and_explicit_noise_exclusion_do_not_bridge_unknown_points():
    scan = make_scan(frequencies=[10, 100, 1000, 4999, 5000, 7000],
                     controller=PIParameters(0.2, 0.002, 1e-4), excluded_indices=[1])
    np.testing.assert_array_equal(scan.usable_mask(), [True, False, True, True, False, False])
    predicted = predict(scan, replace(scan.controller, kp=0.22))
    assert np.all(np.isfinite(predicted[scan.usable_mask()]))
    assert np.all(np.isnan(predicted[~scan.usable_mask()]))
    assert metrics(scan) is None
    assert suggestions(scan, Targets()) == []
    rows = list(csv.DictReader(io.StringIO(export_scan_csv(scan))))
    assert [int(row["excluded"]) for row in rows] == [0, 1, 0, 0, 1, 1]


def test_too_few_usable_points_is_blocked():
    scan = make_scan(frequencies=[10, 100, 1000], excluded_indices=[1])
    assert scan.prediction_blocker()
    assert suggestions(scan, Targets()) == []


@pytest.mark.parametrize("indices", [[-1], [9999], [1.5], [True]])
def test_invalid_exclusion_indices_are_rejected(indices):
    with pytest.raises(ValueError):
        make_scan(excluded_indices=indices)


def test_phase_offsets_and_wraps_preserve_raw_measurement():
    scan = make_scan()
    raw = scan.measurement.raw_phase_deg.copy()
    shifted_raw = raw + 180 + 720
    shifted_m = replace(scan.measurement, raw_phase_deg=shifted_raw)
    shifted = replace(scan, measurement=shifted_m, phase_offset_deg=-180)
    np.testing.assert_allclose(shifted.response(), scan.response(), rtol=1e-12, atol=1e-12)
    assert metrics(shifted).worst_phase_margin_deg == pytest.approx(metrics(scan).worst_phase_margin_deg)
    np.testing.assert_array_equal(shifted.measurement.raw_phase_deg, shifted_raw)
    rows = list(csv.DictReader(io.StringIO(export_scan_csv(shifted))))
    np.testing.assert_allclose([float(row["raw_phase_deg"]) for row in rows], raw + 900)


def test_no_observed_gain_margin_is_unknown_not_pass():
    f = np.geomspace(10, 1000, 51)
    h = (100 / f) * np.exp(-1j * np.pi / 2)
    scan = make_scan(frequencies=f, response=h)
    result = metrics(scan)
    assert result.main_crossover_hz == pytest.approx(100)
    assert result.worst_gain_margin_db is None
    assert result.status != "PASS"
    assert score(result, Targets(100, 50, 6)) > 0


def test_no_zero_db_crossing_cannot_be_ranked_as_success():
    scan = make_scan(response=np.full(FREQUENCIES.shape, 0.01 - 0.01j))
    result = metrics(scan)
    assert result.main_crossover_hz is None
    assert result.worst_phase_margin_deg is None
    assert result.status == "NO_0DB_CROSSING"
    assert np.isinf(score(result, Targets()))
    assert suggestions(scan, Targets()) == []
    assert Session(scans=[scan], current_index=0).best() is None


def test_local_suggestions_do_not_become_measured_scans_or_claim_hardware_safety():
    scan = make_scan()
    session = Session()
    session.add(scan)
    before = session.to_json()
    candidates = suggestions(scan, Targets(300, 50, 6))
    assert candidates
    assert len(candidates) <= 8
    assert session.to_json() == before
    for candidate in candidates:
        assert any(candidate.controller.kp / scan.controller.kp == pytest.approx(v) for v in (0.8, 1.0, 1.25))
        assert any(candidate.controller.ti_s / scan.controller.ti_s == pytest.approx(v) for v in (0.8, 1.0, 1.25))
        assert candidate.controller != scan.controller
        assert "Low confidence" in candidate.reason
        assert "new scan" in candidate.reason
        np.testing.assert_allclose(candidate.response, predict(scan, candidate.controller))
    assert [p.score for p in candidates] == sorted(p.score for p in candidates)


@pytest.mark.parametrize("change", [
    {"operating_point": replace(OPERATING_POINT, vin_v=230)},
    {"operating_point": replace(OPERATING_POINT, load_w=500)},
    {"operating_point": replace(OPERATING_POINT, mode="DCM")},
    {"operating_point": replace(OPERATING_POINT, label="different-hardware")},
    {"controller": PIParameters(0.2, 0.002, 2e-5)},
    {"controller": PIParameters(0.2, 0.002, 1e-5, "backward_euler")},
    {"controller": PIParameters(0.2, 0.002, 1e-5, input_unit="ADC count")},
    {"controller": PIParameters(0.2, 0.002, 1e-5, output_unit="PWM count")},
    {"measurement_type": "plant"}, {"injection_convention": "unknown"},
])
def test_operating_point_and_controller_contract_isolation(change):
    first = make_scan("first")
    second = make_scan("other", **change)
    session = Session()
    session.add(first)
    session.add(second)
    assert session.group() == [second]
    assert session.mismatch() is None
    assert session.best() in (None, second)
    session.current_index = 0
    assert session.group() == [first]


def test_held_out_same_plant_matches_and_changed_plant_is_flagged():
    old = make_scan()
    new_controller = replace(old.controller, kp=0.23, ti_s=0.0018)
    candidate_scan = make_scan("held out", controller=new_controller)
    session = Session()
    session.add(old)
    session.add(candidate_scan)
    result = session.mismatch()
    assert result["status"] == "consistent_in_band"
    assert result["magnitude_error_db_max"] < 1e-10
    assert result["phase_error_deg_max"] < 1e-10
    altered = make_scan("changed plant", controller=new_controller, response=candidate_scan.response() * 2 * np.exp(1j * np.deg2rad(25)))
    session.add(altered)
    result = session.mismatch()
    assert result["status"] == "mismatch"
    assert result["magnitude_error_db_max"] == pytest.approx(20 * np.log10(2))
    assert result["phase_error_deg_max"] == pytest.approx(25)


def test_history_selection_compares_current_against_previous_same_group_only():
    baseline = make_scan("baseline")
    good = make_scan("second", controller=replace(baseline.controller, kp=0.23))
    bad = make_scan("third", response=3 * baseline.response())
    session = Session()
    for scan in (baseline, good, bad):
        session.add(scan)
    assert session.mismatch()["status"] == "mismatch"
    session.current_index = 1
    assert session.mismatch()["status"] == "consistent_in_band"
    session.current_index = 0
    assert session.mismatch() is None


def test_mismatch_requires_three_overlapping_measured_points():
    session = Session()
    session.add(make_scan(frequencies=[10, 20, 30]))
    session.add(make_scan("next", frequencies=[29, 30, 40]))
    assert session.mismatch()["status"] == "unavailable"


def test_json_roundtrip_preserves_all_measurement_and_session_state():
    scan = make_scan(excluded_indices=[4], quality_flags=["noise"], phase_offset_deg=-180)
    scan = replace(scan, measurement=replace(scan.measurement,
        source_format=FRASourceFormat.BODE100, default_phase_offset_deg=-180,
        metadata={"columns": [2, 0, 4], "raw_rows": [[1.0, 3.0, 7.0]], "frequency_unit": "kHz"}))
    session = Session(targets=Targets(700, 65, 10))
    session.add(scan)
    session.add(make_scan("other", operating_point=replace(OPERATING_POINT, load_w=500)))
    session.current_index = 0
    loaded = Session.from_json(session.to_json())
    assert loaded.targets == session.targets
    assert loaded.current_index == 0
    assert len(loaded.scans) == 2
    restored = loaded.current
    assert restored.controller == scan.controller
    assert restored.operating_point == scan.operating_point
    assert restored.measurement_type == scan.measurement_type
    assert restored.injection_convention == scan.injection_convention
    assert restored.phase_offset_deg == scan.phase_offset_deg
    assert restored.excluded_indices == [4]
    assert restored.quality_flags == ["noise"]
    assert restored.measurement.source_format == FRASourceFormat.BODE100
    assert restored.measurement.default_phase_offset_deg == -180
    assert restored.measurement.source_path == scan.measurement.source_path
    assert restored.measurement.metadata == scan.measurement.metadata
    for attr in ("frequency_hz", "magnitude_db", "raw_phase_deg"):
        np.testing.assert_array_equal(getattr(restored.measurement, attr), getattr(scan.measurement, attr))
    assert json.loads(loaded.to_json()) == json.loads(session.to_json())
    assert Session.from_json(Session().to_json()).current is None


@pytest.mark.parametrize("text", ["[]", "null", "1", '"unexpected"', "{}", "{bad"])
def test_json_wrong_root_or_schema_is_cleanly_rejected(text):
    with pytest.raises(ValueError):
        Session.from_json(text)


@pytest.mark.parametrize("field", ["method", "input_unit", "output_unit", "sample_period_s"])
def test_json_missing_controller_contract_is_not_silently_defaulted(field):
    doc = session_document()
    del doc["scans"][0]["controller"][field]
    with pytest.raises((ValueError, KeyError, TypeError)):
        Session.from_json(json.dumps(doc))


@pytest.mark.parametrize("field", ["measurement_type", "injection_convention", "phase_offset_deg", "excluded_indices", "quality_flags"])
def test_json_missing_measurement_contract_is_not_silently_defaulted(field):
    doc = session_document()
    del doc["scans"][0][field]
    with pytest.raises((ValueError, KeyError, TypeError)):
        Session.from_json(json.dumps(doc))


@pytest.mark.parametrize("coefficients", [
    {"b": [1, 2], "a": [1, -1]}, {"b": [float("nan"), 2], "a": [1, -1]},
    {"b": [float("inf"), 2], "a": [1, -1]}, {"b": [], "a": [1, -1]},
    {"b": [0.2005, -0.1995], "a": [1, -0.9]},
    {"b": [0.2005, -0.1995, 0.0], "a": [1, -1]},
    {"b": [[0.2005, -0.1995]], "a": [1, -1]},
    {"b": ["0.2005", -0.1995], "a": [1, -1]},
    {"b": [0.2005, -0.1995], "a": [True, -1]},
    {"b": [0.2005, -0.1995], "a": [1, "-1"]},
    {"b": None, "a": [1, -1]},
    {"b": [0.2005, -0.1995], "a": [1]},
])
def test_json_rejects_non_pi_or_inconsistent_coefficients(coefficients):
    doc = session_document()
    doc["scans"][0]["coefficients"] = coefficients
    with pytest.raises((ValueError, TypeError)):
        Session.from_json(json.dumps(doc))


@pytest.mark.parametrize("index", [-1, 1, True, 0.0, "0", None])
def test_json_invalid_current_index_is_rejected(index):
    doc = session_document()
    doc["current_index"] = index
    with pytest.raises(ValueError):
        Session.from_json(json.dumps(doc))


@pytest.mark.parametrize("field,values", [
    ("frequency_hz", [1, 1, 2]), ("frequency_hz", [3, 2, 1]),
    ("frequency_hz", [0, 1, 2]), ("frequency_hz", [1, float("inf"), 3]),
    ("magnitude_db", [1, float("nan"), 3]), ("raw_phase_deg", [1, float("inf"), 3]),
])
def test_json_rejects_invalid_measurement_arrays(field, values):
    doc = session_document()
    measurement = doc["scans"][0]["measurement"]
    for attr in ("frequency_hz", "magnitude_db", "raw_phase_deg"):
        measurement[attr] = [1, 2, 3]
    measurement[field] = values
    with pytest.raises(ValueError):
        Session.from_json(json.dumps(doc))


def test_prediction_rejects_nonfinite_output_from_finite_candidate_parameters():
    scan = make_scan()
    candidate = PIParameters(1.5e308, 100, 1e-5)
    with pytest.raises(ValueError, match="overflow|underflow"):
        predict(scan, candidate)


def test_suggestion_search_skips_overflowed_grid_points_without_losing_session():
    controller = PIParameters(1.5e308, 100, 1e-5)
    scan = make_scan(controller=controller, response=known_plant(FREQUENCIES))
    candidates = suggestions(scan, Targets())
    for candidate in candidates:
        assert np.all(np.isfinite(candidate.response))
        assert np.isfinite(candidate.score)
        assert candidate.controller.kp <= np.finfo(float).max


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_json_rejects_nonfinite_values_inside_nested_metadata(token):
    doc = session_document()
    doc["scans"][0]["measurement"]["metadata"] = {"nested": ["NONFINITE_PLACEHOLDER"]}
    text = json.dumps(doc).replace('"NONFINITE_PLACEHOLDER"', token)
    with pytest.raises(ValueError):
        Session.from_json(text)


@pytest.mark.parametrize("field,values", [
    ("frequency_hz", [[1, 2, 3]]), ("magnitude_db", [True, 2, 3]),
    ("raw_phase_deg", ["1", 2, 3]), ("frequency_hz", None),
    ("metadata", []), ("source_path", 123), ("source_format", "invented"),
    ("default_phase_offset_deg", True),
])
def test_json_rejects_wrong_measurement_field_types(field, values):
    doc = session_document()
    doc["scans"][0]["measurement"][field] = values
    with pytest.raises(ValueError):
        Session.from_json(json.dumps(doc))


def test_margin_status_respects_selected_targets():
    f = np.geomspace(10, 10_000, 121)
    mag_db = -20 * np.log10(f / 100)
    phase_deg = -120 - 60 * np.log10(f / 100)
    scan = make_scan(frequencies=f, response=10 ** (mag_db / 20) * np.exp(1j * np.deg2rad(phase_deg)))
    assert metrics(scan).status == "PASS"
    assert metrics(scan, targets=Targets(100, 80, 6)).status == "REVIEW_PM"
    assert metrics(scan, targets=Targets(100, 50, 30)).status == "REVIEW_GM"
