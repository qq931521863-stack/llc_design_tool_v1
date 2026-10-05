"""Completed-result export regression: no re-analysis, fits, or domain shifts."""
import json
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace

import numpy as np
import pytest

from llc_design.control.analysis import build_small_signal_analysis
from llc_design.control.digital_loop import (
    DigitalTransferFunction,
    PIControllerConfig,
    PIFControllerConfig,
    TwoP2ZControllerConfig,
    build_digital_loop_analysis,
)
from llc_design.control.linearize import ControlInputKind
from llc_design.core.spec import LLCDesignSpec
from pfc_design.control.analysis import build_pfc_control_lab_analysis
from pfc_design.control.config import LoadModel, PFCControlLabConfig
from power_control_tools.research_export import (
    ResearchExport,
    ResearchModel,
    build_llc_research_export,
    build_pfc_research_export,
    export_research_script,
    render_matlab,
    render_python,
)


@pytest.fixture(scope="module")
def small():
    return build_small_signal_analysis(LLCDesignSpec())


@pytest.fixture(scope="module")
def pfc():
    return build_pfc_control_lab_analysis(PFCControlLabConfig(frequency_points=101))


def _execute(bundle):
    namespace = {"__name__": "export_test"}
    exec(compile(render_python(bundle), "standalone_export.py", "exec"), namespace)  # noqa: S102 - generated-script integration test
    return namespace


def _models(bundle):
    return {m.name: m for m in bundle.models}


@pytest.mark.parametrize("config", [
    PIControllerConfig(kp=0.007, ti_s=0.003),
    PIFControllerConfig(kp=0.007, ti_s=0.003, lpf_cutoff_hz=2300),
    TwoP2ZControllerConfig(b0=0.3, b1=-0.2, b2=0.04, a1=-1.1, a2=0.2),
])
def test_llc_controllers_exact_and_mixed_boundary(small, config):
    f = np.geomspace(1.0, 10000.0, 31)
    analysis = build_digital_loop_analysis(small, controller_config=config, frequencies_hz=f)
    bundle = build_llc_research_export(analysis)
    exported = _models(bundle)
    namespace = _execute(bundle)
    assert exported["C_z"].numerator == tuple(analysis.controller.numerator)
    assert exported["C_z"].denominator == tuple(analysis.controller.denominator)
    assert exported["C_z"].sample_time_s == config.sample_time_s
    for name, key in (("L", "open_loop_nominal"), ("T", "closed_loop_nominal"), ("S", "sensitivity_nominal")):
        assert not exported[name].rational
        np.testing.assert_array_equal(exported[name].response, analysis.responses[key])
        with pytest.raises(ValueError, match="frequency samples only"):
            namespace["evaluate_rational"](name, [100.0])
    np.testing.assert_allclose(namespace["evaluate_rational"]("C_z", f), analysis.controller.frequency_response(f), rtol=1e-13)
    np.testing.assert_allclose(namespace["evaluate_rational"]("G_s", f), analysis.responses["power_stage"], rtol=1e-11)


def test_llc_external_controller_and_explicit_plant_delay(small):
    external = DigitalTransferFunction(np.asarray([0.0, 0.12, -0.025]), np.asarray([1.0, -0.7]), small.sample_time_s,
                                       name="Manual exact z^-1 controller")
    delayed = replace(small, discrete_plant=replace(small.discrete_plant, input_delay_samples=3))
    analysis = build_digital_loop_analysis(delayed, controller_transfer_function=external,
                                          controller_source="External/manual b/a", frequencies_hz=np.geomspace(1, 9000, 27))
    bundle = build_llc_research_export(analysis)
    models = _models(bundle)
    assert models["C_z"].numerator == tuple(external.numerator)
    assert models["G_z"].numerator[:3] == (0.0, 0.0, 0.0)
    namespace = _execute(bundle)
    np.testing.assert_allclose(namespace["evaluate_rational"]("G_z", analysis.frequencies_hz),
                               delayed.discrete_plant.frequency_response(analysis.frequencies_hz), rtol=1e-13)
    assert json.loads(bundle.metadata_json)["controller_source"] == "External/manual b/a"


@pytest.mark.parametrize("loop", ["current", "voltage", "both"])
def test_pfc_loop_selection_and_distinct_sample_times(pfc, loop):
    bundle = build_pfc_research_export(pfc, loop)
    models = _models(bundle)
    namespace = _execute(bundle)
    assert len(bundle.loops) == (2 if loop == "both" else 1)
    if loop != "voltage":
        assert models["Ci_z"].sample_time_s == 20e-6
        np.testing.assert_array_equal(models["Li"].response, pfc.current_loop.responses["open_current"])
        np.testing.assert_allclose(namespace["evaluate_rational"]("Gi_s", pfc.frequencies_hz), pfc.current_loop.responses["plant_gid"], rtol=1e-13)
    if loop != "current":
        assert models["Cv_z"].sample_time_s == 100e-6
        assert not models["Gv"].rational
        np.testing.assert_array_equal(models["Lv"].response, pfc.voltage_loop.responses["open_voltage"])
        np.testing.assert_allclose(np.asarray(models["Gv_base_s"].response) * pfc.current_loop.responses["closed_current_actual"], models["Gv"].response, rtol=1e-13)
    assert json.loads(bundle.metadata_json)["samples_above_controller_nyquist"]["voltage"] > 0
    assert any("not clipped" in note for note in bundle.notes)


@pytest.mark.parametrize("load", list(LoadModel))
def test_pfc_bus_factor_and_raw_sensing(load):
    cfg = PFCControlLabConfig(frequency_points=101)
    cfg = replace(cfg, power_stage=replace(cfg.power_stage, load_model=load),
                  current_sense=replace(cfg.current_sense, normalize_to_engineering_units=False),
                  vbus_sense=replace(cfg.vbus_sense, normalize_to_engineering_units=False))
    result = build_pfc_control_lab_analysis(cfg)
    bundle = build_pfc_research_export(result)
    models = _models(bundle)
    assert models["Ci_z"].input_unit == "ADC input V"
    assert models["Hi"].output_unit == "ADC input V"
    assert models["Ti"].input_unit == models["Ti"].output_unit == "ADC input V"
    assert models["Cv_z"].input_unit == "ADC input V"
    assert models["Gv_base_s"].denominator[-1] == (0.0 if load == LoadModel.CONSTANT_POWER else 16.5)
    _execute(bundle)


def test_snapshot_is_detached_and_immutable(pfc):
    # Work on private source arrays so the fixture itself remains untouched.
    source = replace(pfc, frequencies_hz=pfc.frequencies_hz.copy(),
                     current_loop=replace(pfc.current_loop, responses={k: v.copy() for k, v in pfc.current_loop.responses.items()}))
    bundle = build_pfc_research_export(source)
    before = render_python(bundle)
    source.frequencies_hz[0] = 999.0
    source.current_loop.responses["open_current"][:] = 0
    assert render_python(bundle) == before
    with pytest.raises(FrozenInstanceError):
        bundle.title = "new title"
    with pytest.raises(TypeError):
        bundle.models[0].response[0] = 0


@pytest.mark.parametrize("numerator,denominator", [
    ([1], [1, -0.3, 0.04]), ([0, 0.4], [1]),
    ([1, 0.2, -0.1], [1, -0.3]), ([0, 0, 1], [1, -0.8]),
    ([0, 0, 0], [1, -0.2]), ([1e-16, -7e-17], [1, -0.5]),
])
def test_standalone_scipy_preserves_z_inverse_orientation_and_origin_roots(numerator, denominator):
    f = np.geomspace(1, 10000, 21)
    tf = DigitalTransferFunction(np.asarray(numerator), np.asarray(denominator), 20e-6)
    model = ResearchModel("C_z", "orientation test", "discrete", "1", "1", 20e-6,
                          tuple(tf.numerator), tuple(tf.denominator), tuple(tf.frequency_response(f)), "test")
    bundle = ResearchExport("test", tuple(f), (model,), (), "{}", ())
    namespace = _execute(bundle)
    scipy_tf = namespace["models"]["C_z"]
    from scipy import signal
    _, actual = signal.dfreqresp(scipy_tf, w=2*np.pi*f*20e-6)
    scale = max(float(np.max(np.abs(tf.frequency_response(f)))), np.finfo(float).tiny)
    np.testing.assert_allclose(actual / scale, tf.frequency_response(f) / scale, atol=1e-14, rtol=1e-12)
    common_order = max(len(numerator), len(denominator))
    expected_poles = np.roots(np.pad(denominator, (0, common_order-len(denominator))))
    np.testing.assert_allclose(np.sort_complex(namespace["poles"]["C_z"]), np.sort_complex(expected_poles), atol=1e-14)


def test_generated_python_runs_as_standalone_file_outside_repository(pfc, tmp_path):
    bundle = build_pfc_research_export(pfc)
    path = export_research_script(bundle, tmp_path / "loop.py", "python")
    env = dict(os.environ, MPLBACKEND="Agg", MPLCONFIGDIR=str(tmp_path / "mpl"), PYTHONPATH="")
    result = subprocess.run([sys.executable, str(path)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=90, check=False)
    assert result.returncode == 0, result.stderr
    assert "Reference and feedback identity checks passed." in result.stdout
    assert "from llc_design" not in path.read_text()
    assert "python-control" not in path.read_text()


def test_matlab_uses_r2014b_compatible_constructs_and_exact_controller_coefficients(pfc):
    bundle = build_pfc_research_export(pfc)
    script = render_matlab(bundle)
    assert "MATLAB R2014b+" in script
    assert "'Variable', 'z^-1'" in script
    assert "Gi_s = tf(" in script and "Ci_z = tf(" in script
    assert "Lv = frd(" in script and "Gv = frd(" in script
    assert "Gv_base_s = tf(" in script
    assert "models.Lv = Lv" in script
    assert "freqresp(Ci_z, omega_rad_s(reference_indices))" in script
    assert "Ts=0 as a container convention" in script
    assert "jsondecode(" not in script
    assert "tiledlayout(" not in script
    assert "function " not in script
    assert "eval(" not in script
    for coefficient in pfc.current_loop.controller.numerator:
        assert format(coefficient, ".17g") in script


def test_untrusted_provenance_is_literal_not_executable(pfc):
    bundle = replace(build_pfc_research_export(pfc, "current"),
                     metadata_json=json.dumps({"label": "x'); error('bad'); %\n''' python"}))
    namespace = _execute(bundle)
    assert namespace["metadata"]["label"] == "x'); error('bad'); %\n''' python"
    matlab = render_matlab(bundle)
    assert "x''); error(''bad''); % '''''' python" in matlab


def test_invalid_selection_and_nonfinite_source_fail_clearly(pfc):
    with pytest.raises(ValueError, match="loop must"):
        build_pfc_research_export(pfc, "unknown")
    bad = replace(pfc, frequencies_hz=np.zeros_like(pfc.frequencies_hz))
    with pytest.raises(ValueError, match="strictly increasing"):
        build_pfc_research_export(bad, "current")
    responses = dict(pfc.current_loop.responses)
    responses["controller_ci"] = np.full_like(responses["controller_ci"], np.nan)
    with pytest.raises(ValueError, match="finite"):
        build_pfc_research_export(replace(pfc, current_loop=replace(pfc.current_loop, responses=responses)), "current")


def test_non_hz_llc_source_units_and_caveat(small):
    converted = small.continuous_transfer.scaled(1000, input_name="frequency_khz", input_unit="kHz")
    source = replace(small, continuous_transfer=converted, control_input_kind=ControlInputKind.FREQUENCY_KHZ)
    result = build_digital_loop_analysis(source, controller_config=PIControllerConfig(), frequencies_hz=np.geomspace(1, 1000, 17))
    bundle = build_llc_research_export(result)
    assert _models(bundle)["G_s"].input_unit == "kHz"
    assert any("SOURCE MODEL CAVEAT" in note for note in bundle.notes)
    _execute(bundle)
