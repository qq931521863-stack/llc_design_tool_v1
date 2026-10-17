"""Control-session persistence is exact for inputs and distrustful of snapshots."""
from dataclasses import replace
import copy
import json

import numpy as np
import pytest

from llc_design.control.analysis import build_small_signal_analysis
from llc_design.control.design_session import (
    MAX_ARRAY_ITEMS, MAX_DEPTH, MAX_FILE_BYTES, MODEL_REVISION, SESSION_SCHEMA,
    SessionFormatError, decode_model, encode_model, llc_inputs, make_session,
    read_session, replay_llc, replay_ttpl, response_signature, strict_json_loads,
    ttpl_inputs, validate_session, write_session,
)
from llc_design.control.digital_loop import (
    ADCSamplingConfig, AnalogSenseConfig, CommandTimingConfig,
    DigitalTransferFunction, FMLUTMode, FrequencyModulatorLUT,
    PIControllerConfig, PIFControllerConfig, TwoP2ZControllerConfig,
    build_digital_loop_analysis,
)
from llc_design.control.linearize import ControlInputKind
from llc_design.core.config import load_spec, save_project, save_spec
from llc_design.core.spec import LLCDesignSpec, PrimaryTopology
from llc_design.models.system import LLCSystemAnalyzer
from pfc_design.control.analysis import build_pfc_control_lab_analysis
from pfc_design.control.config import PFCControlLabConfig


@pytest.fixture(scope="module")
def small_signal():
    return build_small_signal_analysis(
        LLCDesignSpec(), vbus_v=380.0, load_fraction=0.85,
        series_resistance_ohm=0.123456789012345, input_delay_samples=2,
    )


@pytest.mark.parametrize("controller", [
    PIControllerConfig(kp=0.012345678901234567, ti_s=0.0012345678901234),
    PIFControllerConfig(kp=0.012345678901234567, lpf_cutoff_hz=3456.123456789),
    TwoP2ZControllerConfig(b0=0.017, b1=-0.011, b2=0.001, a1=-1.2, a2=0.2),
    DigitalTransferFunction(np.array([0.05, -0.025, 0.0025]), np.array([1.0, -0.7, 0.1]), 20e-6),
])
def test_llc_exact_round_trip_and_replay(tmp_path, small_signal, controller):
    arguments = ({"controller_transfer_function": controller} if isinstance(controller, DigitalTransferFunction)
                 else {"controller_config": controller})
    result = build_digital_loop_analysis(
        small_signal, **arguments, controller_source="precise saved controller",
        analog_sense=AnalogSenseConfig(calibration_gain=73.123456789, opamp_bandwidth_hz=234567.123456789),
        adc_sampling=ADCSamplingConfig(soc_count=2, soc_sample_offsets_s=(0.000000123456789, 0.000000987654321)),
        command_timing=CommandTimingConfig(computation_delay_s=1.123456789e-6, pwm_update_delay_s=2.345678901e-6),
        fm_lut=FrequencyModulatorLUT(np.array([0., 0.432123456789, 1.]),
                                   np.array([250000.123456789, 123456.789012345, 65000.123456789]),
                                   mode=FMLUTMode.PCMD_TO_FREQUENCY),
        frequencies_hz=np.geomspace(0.1, 10000.0, 301),
    )
    inputs = llc_inputs(result)
    path = write_session(make_session("llc", inputs),
                         tmp_path / "session.json")
    restored = decode_model(read_session(path)["inputs"])
    assert encode_model(restored) == encode_model(inputs)
    rebuilt = replay_llc(restored)
    assert np.array_equal(result.frequencies_hz, rebuilt.frequencies_hz)
    for key in result.responses:
        assert np.array_equal(result.responses[key], rebuilt.responses[key]), key
    assert result.small_signal.discrete_plant.input_delay_samples == rebuilt.small_signal.discrete_plant.input_delay_samples
    assert result.small_signal.control_input_kind == rebuilt.small_signal.control_input_kind
    assert response_signature(result.frequencies_hz, result.nominal_open_loop) == response_signature(
        rebuilt.frequencies_hz, rebuilt.nominal_open_loop)


def test_llc_system_derived_damping_and_geometry_replay():
    spec = LLCDesignSpec()
    system = LLCSystemAnalyzer().analyze(spec)
    small = build_small_signal_analysis(spec, system_analysis=system)
    result = build_digital_loop_analysis(small, controller_config=PIControllerConfig())
    rebuilt = replay_llc(encode_model(llc_inputs(result)))
    assert result.small_signal.parameters == rebuilt.small_signal.parameters
    for key in result.responses:
        assert np.array_equal(result.responses[key], rebuilt.responses[key]), key


def test_untrimmed_and_period_request_preserved():
    small = build_small_signal_analysis(LLCDesignSpec(), trim_frequency_to_output=False,
                                        control_input_kind=ControlInputKind.TIMER_COUNTS, timer_clock_hz=120e6)
    result = build_digital_loop_analysis(small, controller_config=PIControllerConfig())
    restored = replay_llc(llc_inputs(result))
    assert not restored.small_signal.steady_state.frequency_trimmed
    assert restored.small_signal.control_input_kind is ControlInputKind.TIMER_COUNTS
    assert np.array_equal(result.nominal_open_loop, restored.nominal_open_loop)


def test_ttpl_hidden_fields_and_both_controllers_exact(tmp_path):
    base = PFCControlLabConfig()
    config = replace(
        base, frequency_points=333, switching_samples_per_cycle=803,
        current_controller=PIFControllerConfig(kp=0.010123456789, ti_s=73.123456789e-6),
        voltage_controller=TwoP2ZControllerConfig(b0=0.05, b1=-0.02, a1=-1.0, sample_time_s=100e-6),
        current_sense=replace(base.current_sense, second_resistance_ohm=17.123456789,
                              second_capacitance_f=6.123456789e-9,
                              timing=replace(base.current_sense.timing, soc_count=2, soc_spacing_s=1.123456789e-6)),
        firmware=replace(base.firmware, indu_comp_gain=0.087123456789, vff_bypass=True),
    )
    result = build_pfc_control_lab_analysis(config)
    path = write_session(make_session("ttpl", ttpl_inputs(result)), tmp_path / "ttpl.json")
    document = read_session(path)
    rebuilt = replay_ttpl(document["inputs"])
    assert rebuilt.config == config
    for name in ("current_loop", "voltage_loop"):
        for key, values in getattr(result, name).responses.items():
            assert np.array_equal(values, getattr(rebuilt, name).responses[key]), key


@pytest.mark.parametrize("dtype", ["<f8", ">f8", "<f4", "<c16", ">c8", "<i2", "<u8", "?"])
def test_arrays_preserve_dtype_shape_and_exact_bits(dtype):
    dt = np.dtype(dtype)
    if dt.kind == "c":
        values = np.array([complex(-0.0, 1.123456789), complex(0.123456789, -0.0)], dtype=dt)
    elif dt.kind == "b":
        values = np.array([True, False], dtype=dt)
    elif dt.kind == "u":
        values = np.array([0, np.iinfo(dt).max], dtype=dt)
    else:
        values = np.array([-0.0, 1.123456789], dtype=dt)
    values = values.reshape((1, 2))
    restored = decode_model(strict_json_loads(json.dumps(encode_model(values))))
    assert restored.dtype == values.dtype
    assert restored.shape == values.shape
    assert restored.tobytes() == values.tobytes()


def test_tuple_enum_and_copy_preservation(small_signal):
    result = build_digital_loop_analysis(small_signal, controller_config=PIControllerConfig())
    snapshot = llc_inputs(result)
    assert type(snapshot["spec"].transformer_core_families) is tuple
    assert snapshot["spec"].primary_topology is PrimaryTopology.FULL_BRIDGE
    assert snapshot["spec"] is not result.small_signal.spec
    snapshot["digital_loop"]["frequencies_hz"][0] = 99.0
    assert result.frequencies_hz[0] != 99.0


@pytest.mark.parametrize("text", [
    '{"a": 1, "a": 2}', '{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}',
    '{"a": 1e309}', '{"a":}', '[1,]', 'null garbage',
    '[' * (MAX_DEPTH + 1) + '0' + ']' * (MAX_DEPTH + 1),
])
def test_malformed_and_nonfinite_json_rejected(text):
    with pytest.raises(SessionFormatError):
        strict_json_loads(text)


def test_size_limit_precedes_parsing(tmp_path):
    path = tmp_path / "large.json"
    with path.open("wb") as stream:
        stream.truncate(MAX_FILE_BYTES + 1)
    with pytest.raises(SessionFormatError, match="size"):
        read_session(path)


@pytest.mark.parametrize("value", [np.array([np.nan]), np.array([np.inf]), float("nan"), complex(1, float("inf"))])
def test_nonfinite_models_rejected(value):
    with pytest.raises(SessionFormatError):
        encode_model(value)


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(__model__="subprocess.Popen"),
    lambda data: data["fields"].update(kp=True),
    lambda data: data["fields"].update(kp="0.01"),
    lambda data: data["fields"].update(kp=-1.0),
    lambda data: data["fields"].update(extra=1),
    lambda data: data["fields"].pop("ti_s"),
    lambda data: data["fields"].update(output_min=2.0),
])
def test_model_allowlist_and_strict_field_types(mutation):
    data = encode_model(PIControllerConfig())
    mutation(data)
    with pytest.raises(SessionFormatError):
        decode_model(data)


@pytest.mark.parametrize("payload", [
    {"__ndarray__": "|O", "shape": [1], "data": ["malicious"]},
    {"__ndarray__": "<f8", "shape": [1], "data": [True]},
    {"__ndarray__": "<f8", "shape": [True], "data": [1.0]},
    {"__ndarray__": "<f8", "shape": [MAX_ARRAY_ITEMS + 1], "data": []},
    {"__ndarray__": "<f8", "shape": [1, 1, 1, 1, 1], "data": [1.0]},
    {"__ndarray__": "|u1", "shape": [1], "data": [256]},
    {"__ndarray__": "<f4", "shape": [1], "data": [1e100]},
    {"__ndarray__": "<c16", "shape": [1], "data": [[True, 0.0]]},
])
def test_malformed_arrays_rejected(payload):
    with pytest.raises(SessionFormatError):
        decode_model(payload)


def test_controller_order_and_adc_resource_limits():
    data = encode_model(PIControllerConfig().transfer_function())
    data["fields"]["numerator"] = encode_model(np.ones(100))
    with pytest.raises(SessionFormatError, match="polynomial"):
        decode_model(data)
    data = encode_model(ADCSamplingConfig())
    data["fields"]["soc_count"] = 1_000_000
    with pytest.raises(SessionFormatError, match="SOC"):
        decode_model(data)
    data = encode_model(PFCControlLabConfig())
    data["fields"]["waveform_integration_rate_hz"] = 1e20
    with pytest.raises(SessionFormatError, match="waveform"):
        decode_model(data)


def test_session_header_type_revision_and_input_validation():
    base = make_session("ttpl", PFCControlLabConfig())
    assert base["schema"] == SESSION_SCHEMA and base["model_revision"] == MODEL_REVISION
    for key, value in [("schema_version", True), ("schema_version", 999),
                       ("schema", "pickle"), ("workspace", "llc"),
                       ("model_revision", ""), ("candidate_evidence", []),
                       ("unexpected", 1)]:
        document = copy.deepcopy(base)
        document[key] = value
        with pytest.raises(SessionFormatError):
            validate_session(document)


def test_changed_historical_responses_cannot_change_replay(small_signal):
    result = build_digital_loop_analysis(small_signal, controller_config=PIControllerConfig())
    document = make_session("llc", llc_inputs(result), solution_map={
        "saved_response": [0.0],
        "historical_assessment": {"status": "FEASIBLE", "hardware_release_ready": True},
    })
    restored = validate_session(document)
    rebuilt = replay_llc(restored["inputs"])
    assert np.array_equal(result.nominal_open_loop, rebuilt.nominal_open_loop)
    # Metadata is intentionally only data; no candidate is applied by decoding.
    assert rebuilt.controller_config == result.controller_config


def test_legacy_plain_spec_and_project_remain_readable(tmp_path):
    spec = LLCDesignSpec(primary_turns=28)
    for saver in (save_spec, save_project):
        path = saver(spec, tmp_path / f"{saver.__name__}.json")
        assert load_spec(path) == spec


def test_old_model_revision_is_historical_and_requires_fresh_replay():
    document = make_session("ttpl", PFCControlLabConfig())
    document["model_revision"] = "previous-model-revision"
    assert validate_session(document)["model_revision"] != MODEL_REVISION
    assert isinstance(replay_ttpl(document["inputs"]).config, PFCControlLabConfig)


def test_unrun_llc_inputs_accept_only_documented_defaults(small_signal):
    result = build_digital_loop_analysis(small_signal, controller_config=PIControllerConfig())
    request = llc_inputs(result)
    request.pop("small_signal_metadata")
    request["small_signal"].pop("series_resistance_ohm")
    request["small_signal"].pop("trim_frequency_to_output")
    request["digital_loop"].pop("frequencies_hz")
    request["digital_loop"]["command_pu"] = None
    document = make_session("llc", request)
    rebuilt = replay_llc(document["inputs"])
    assert rebuilt.controller_config == result.controller_config
    assert rebuilt.small_signal.steady_state.frequency_trimmed
    request["small_signal"]["system_analysis"] = None
    with pytest.raises(SessionFormatError):
        make_session("llc", request)


def test_qt_string_enum_is_canonicalized_only_when_valid():
    base = PFCControlLabConfig()
    config = replace(base, power_stage=replace(base.power_stage, load_model="resistive"))
    restored = decode_model(encode_model(config))
    from pfc_design.control.config import LoadModel
    assert restored.power_stage.load_model is LoadModel.RESISTIVE
    with pytest.raises(SessionFormatError):
        encode_model(replace(base.power_stage, load_model="run-python"))
    encoded = encode_model(base.power_stage)
    encoded["fields"]["load_model"] = "resistive"
    with pytest.raises(SessionFormatError):
        decode_model(encoded)


def test_sample_time_and_huge_pwm_delay_rejected_before_replay(small_signal):
    result = build_digital_loop_analysis(small_signal, controller_config=PIControllerConfig())
    request = llc_inputs(result)
    request["digital_loop"]["controller_config"] = PIControllerConfig(sample_time_s=21e-6)
    with pytest.raises(SessionFormatError, match="sample times"):
        make_session("llc", request)
    request["digital_loop"]["controller_config"] = PIControllerConfig()
    request["digital_loop"]["command_timing"] = CommandTimingConfig(pwm_update_delay_s=100)
    with pytest.raises(SessionFormatError, match="delay"):
        make_session("llc", request)
    request["digital_loop"]["command_timing"] = CommandTimingConfig()
    request["small_signal_metadata"] = None
    with pytest.raises(SessionFormatError, match="metadata"):
        make_session("llc", request)


def test_failed_atomic_save_preserves_existing_session(tmp_path, monkeypatch):
    from pathlib import Path
    path = tmp_path / "session.json"
    original = make_session("ttpl", PFCControlLabConfig())
    write_session(original, path)
    before = path.read_bytes()
    def fail_replace(self, destination):
        raise OSError("simulated storage failure")
    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="storage failure"):
        write_session(original, path)
    assert path.read_bytes() == before
    assert list(tmp_path.glob(".*.tmp")) == []


@pytest.mark.parametrize("workspace,payload", [
    ("ttpl", {"current": [1]}), ("ttpl", {"other": {}}),
    ("llc", {"status": "FEASIBLE"}),
])
def test_malformed_nested_evidence_rejected_before_ui_restore(workspace, payload, small_signal):
    inputs = (PFCControlLabConfig() if workspace == "ttpl" else llc_inputs(
        build_digital_loop_analysis(small_signal, controller_config=PIControllerConfig())))
    with pytest.raises(ValueError):
        make_session(workspace, inputs, candidate_evidence=payload)


@pytest.mark.parametrize("field,value", [
    ("magnetic_thermal_max_iterations", 0),
    ("magnetic_thermal_max_iterations", -1),
    ("magnetic_thermal_max_iterations", 101),
    ("magnetic_thermal_max_iterations", 2**62),
    ("magnetic_thermal_max_iterations", True),
    ("magnetic_thermal_tolerance_c", 0.0),
    ("magnetic_thermal_tolerance_c", -0.25),
    ("magnetic_thermal_tolerance_c", True),
    ("magnetic_candidate_full_evaluation_limit", -1),
    ("magnetic_candidate_full_evaluation_limit", 1025),
    ("magnetic_candidate_full_evaluation_limit", 2**62),
    ("magnetic_candidate_full_evaluation_limit", True),
])
def test_magnetic_workload_bounds_reject_untrusted_requests(field, value):
    encoded = encode_model(LLCDesignSpec())
    encoded["fields"][field] = value
    with pytest.raises(SessionFormatError):
        decode_model(encoded)
    with pytest.raises(SessionFormatError):
        encode_model(replace(LLCDesignSpec(), **{field: value}))


@pytest.mark.parametrize("iterations,candidates", [(1, 0), (100, 1024), (12, 32)])
def test_magnetic_workload_boundaries_and_legacy_zero_semantics(iterations, candidates):
    spec = replace(LLCDesignSpec(), magnetic_thermal_max_iterations=iterations,
                   magnetic_candidate_full_evaluation_limit=candidates,
                   magnetic_thermal_tolerance_c=0.001)
    assert decode_model(encode_model(spec)) == spec
