from llc_design.core.spec import LLCDesignSpec
from webapp.service import analyze_llc, default_payload, spec_from_payload


def test_defaults_are_web_safe():
    payload = default_payload()
    assert payload["spec"]["vbus_nom_v"] == 400.0
    assert "FULL_BRIDGE" in payload["topologies"]
    assert payload["primary_devices"]
    spec_from_payload(payload["spec"])


def test_baseline_analysis_matches_engineering_kernel():
    result = analyze_llc({})
    assert result["status"] == "PASS"
    assert result["summary"]["lr_h"] > 0.0
    assert result["summary"]["cr_f"] > 0.0
    assert 0.90 < result["summary"]["nominal_efficiency"] < 1.0
    assert len(result["operating_points"]) == 7
    assert len(result["gain_map"]["curves"]) == 5


def test_chinese_design_notes_preserve_machine_readable_failure():
    from llc_design.i18n import current_language, set_language

    previous = current_language()
    try:
        set_language("en", notify=False)
        result = analyze_llc({"bus_capacitance_f": 100e-6})
        assert current_language() == "en"
    finally:
        set_language(previous, notify=False)
    assert result["status"] == "FAIL"
    assert result["feasible"] is False
    assert "installed bus capacitance does not meet requested hold-up time" in result["feasibility_reasons"]
    assert any("母线保持时间未达到设定要求" in note for note in result["design_notes_zh"])


def test_payload_updates_real_spec():
    spec = spec_from_payload({"pout_w": 2500.0, "primary_turns": 28})
    assert isinstance(spec, LLCDesignSpec)
    assert spec.pout_w == 2500.0
    assert spec.primary_turns == 28


def test_unknown_parameter_is_rejected():
    try:
        spec_from_payload({"magnetic_waveform_samples": 10_000_000})
    except ValueError as exc:
        assert "unsupported public web parameter" in str(exc)
    else:
        raise AssertionError("unsafe parameter should be rejected")
