"""Result-kernel and project-framework revisions are independent provenance."""
import json
from dataclasses import replace
from html import unescape

from power_stage_design.models import MODEL_VERSION, DesignInputs
from power_stage_design.report import write_report
from power_stage_design.topologies import calculate_stage


def test_report_preserves_actual_result_model_revision_not_global_default(tmp_path):
    # A lightweight record-boundary counterexample; physical extended circuits
    # are exercised independently in test_advanced_model_desktop.
    base=calculate_stage(DesignInputs('T01',12,5,3,100e3,47e-6))
    result=replace(base,model_version='independently-versioned-result')
    output=write_report(result,tmp_path/'report.html')
    text=output.read_text(encoding='utf-8')
    snapshot=json.loads(unescape(text.split('<h2>可复现数据</h2><pre>',1)[1].split('</pre>',1)[0]))
    assert snapshot['model_revision']==result.model_version
    assert snapshot['framework_model_revision']==MODEL_VERSION
    assert 'independently-versioned-result' in text.split('<h2>设计输入')[0]


def test_ideal_qr_impulse_statistics_cannot_look_like_complete_stress(tmp_path):
    from power_stage_design.devices import assess_component
    from power_stage_design.qr_dynamics import QROptions
    result=calculate_stage(DesignInputs('T11',48,12,1,180e3,200e-6,
        turns_ratio_np_ns=8,resonant_capacitance_f=500e-12,
        qr_options=QROptions(valley_policy='selected',valley_index=2)))
    for key in ('Q1','Cnode'):
        assessment=assess_component(result,key)
        assert assessment['statistics_scope']=='excludes_ideal_turn_on_impulse'
        assert '不能作为完整' in assessment['assessment']
    text=write_report(result,tmp_path/'qr.html').read_text(encoding='utf-8')
    assert 'excludes_ideal_turn_on_impulse' in text
