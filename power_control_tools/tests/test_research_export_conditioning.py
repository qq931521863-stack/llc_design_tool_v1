"""Actual export boundary types and numerical pole conditioning, without fitting."""
import json
from dataclasses import replace

import numpy as np
import pytest

from llc_design.control.analysis import build_small_signal_analysis
from llc_design.control.digital_loop import (
    PIControllerConfig,
    build_digital_loop_analysis,
)
from llc_design.core.spec import LLCDesignSpec
from power_control_tools.research_export import (
    ResearchExport,
    ResearchModel,
    build_llc_research_export,
    render_matlab,
    render_python,
)


def _execute(bundle):
    namespace={'__name__':'research_conditioning_test'}
    exec(compile(render_python(bundle),'research_conditioning.py','exec'),namespace)  # noqa: S102
    return namespace


@pytest.fixture(scope='module')
def llc():
    return build_digital_loop_analysis(build_small_signal_analysis(LLCDesignSpec()), controller_config=PIControllerConfig(kp=.007, ti_s=.003))


def test_actual_gui_session_string_kind_matches_enum_and_invalid_kind_rejects(llc):
    original=build_llc_research_export(llc)
    copied=replace(llc,small_signal=replace(llc.small_signal,control_input_kind='frequency_hz'))
    actual=build_llc_research_export(copied)
    assert actual.models==original.models and actual.notes==original.notes
    assert json.loads(actual.metadata_json)['control_input_kind']=='frequency_hz'
    _execute(actual)
    with pytest.raises(ValueError):
        build_llc_research_export(replace(llc,small_signal=replace(llc.small_signal,control_input_kind='bad-input-unit')))


def _singular_bundle(frequencies):
    b=(.1025,-.114925,.016575000000000003);a=(1.,-1.23,.23);ts=.0001
    f=np.asarray(frequencies);z=np.exp(2j*np.pi*f*ts)
    response=sum(v*z**(-k) for k,v in enumerate(b))/sum(v*z**(-k) for k,v in enumerate(a))
    model=ResearchModel('Cv_z','Actual 2P2Z pole case','discrete','V','A/V',ts,b,a,tuple(response),'actual PFC coefficients')
    return ResearchExport('Aliased integrator pole',tuple(f),(model,),(),'{}',())


def test_pole_sample_retained_but_not_used_as_cross_library_numerical_oracle(capsys):
    bundle=_singular_bundle(np.geomspace(1.,20000.,103))
    namespace=_execute(bundle)
    item=namespace['model_info']['Cv_z']
    assert item['numerator']==list(bundle.models[0].numerator)
    assert item['denominator']==list(bundle.models[0].denominator)
    np.testing.assert_array_equal(namespace['responses']['Cv_z'],bundle.models[0].response)
    assert item['ill_conditioned_indices']==[102]
    assert 102 not in item['coefficient_reference_indices']
    assert namespace['coefficient_verification']['Cv_z']=='PASS_AT_WELL_CONDITIONED_SAMPLES'
    assert 'original samples and coefficients retained' in capsys.readouterr().out
    matlab=render_matlab(bundle)
    assert 'coefficient_indices' in matlab and '<=1e-8' in matlab
    assert format(bundle.models[0].response[-1].real,'.17g') in matlab


def test_all_pole_samples_report_not_run_without_inventing_reference_values(capsys):
    bundle=_singular_bundle([10000.,20000.])
    namespace=_execute(bundle)
    assert namespace['coefficient_verification']['Cv_z']=='NOT_RUN'
    np.testing.assert_array_equal(namespace['responses']['Cv_z'],bundle.models[0].response)
    assert 'NOT_RUN' in capsys.readouterr().out


def test_wrong_well_conditioned_coefficients_still_fail_unchanged_error_budget():
    original=_singular_bundle(np.geomspace(1.,20000.,103))
    changed=replace(original.models[0],numerator=(.11,-.114925,.016575000000000003))
    with pytest.raises(AssertionError,match='coefficient/reference mismatch'):
        _execute(replace(original,models=(changed,)))
