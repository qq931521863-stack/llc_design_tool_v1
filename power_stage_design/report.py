"""Traceable HTML calculation book and CSV signals from the current result only."""
from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import numpy as np

from llc_design import __version__
from llc_design.report.export import _jsonable

from . import MODEL_REVISION
from .schematic import svg_schematic


def _table(values: dict) -> str:
    return '<table>' + ''.join(f'<tr><th>{escape(str(k))}</th><td>{escape(str(v))}</td></tr>' for k,v in values.items()) + '</table>'


def _curve_svg(t, y, title: str, unit: str, *, logx=False) -> str:
    t, y = np.asarray(t), np.asarray(y)
    x = np.log10(t) if logx else t
    lo, hi = float(np.min(y)), float(np.max(y))
    span = hi - lo or max(abs(hi), 1.0)
    xx = 55 + 640*(x-x[0])/(x[-1]-x[0])
    yy = 160 - 115*(y-lo)/span
    pts = ' '.join(f'{a:.2f},{b:.2f}' for a,b in zip(xx,yy))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 740 195" role="img" aria-label="{escape(title)}"><rect width="740" height="195" fill="#f8fafc"/><text x="55" y="22" font-size="15">{escape(title)}</text><path d="M55 35V160H700" fill="none" stroke="#94a3b8"/><polyline points="{pts}" fill="none" stroke="#0284c7" stroke-width="1.6"/><text x="5" y="46" font-size="12">{hi:.5g}</text><text x="5" y="162" font-size="12">{lo:.5g}</text><text x="55" y="184" font-size="12">{t[0]:.5g}</text><text x="610" y="184" font-size="12">{t[-1]:.5g} {escape(unit)}</text></svg>'''


def write_report(result, path: str | Path, *, loop_result=None, loop_settings=None,
                 selected_device=None, device_assessment=None, corner_sweep=None, parameter_suggestion=None,
                 junction_temperature_c: float | None = None, loop_request=None) -> Path:
    """No cached curves or statistics are accepted independently from result."""
    from .corners import CornerLoopIdentity
    from .devices import assess_component
    from .project import _validate_device
    if loop_request is not None:
        from .project import validate_loop_request
        loop_request=validate_loop_request(loop_request)
    if junction_temperature_c is None:
        junction_temperature_c = corner_sweep.junction_temperature_c if corner_sweep is not None else 100.0
    if (type(junction_temperature_c) not in (int, float)
            or not math.isfinite(junction_temperature_c) or not -40 <= junction_temperature_c <= 175):
        raise ValueError("Screening junction temperature must be within −40…175 °C")
    if corner_sweep is not None and (type(corner_sweep.junction_temperature_c) not in (int, float)
            or corner_sweep.junction_temperature_c != junction_temperature_c):
        raise ValueError("Corner scan belongs to a different junction temperature")
    device=_validate_device(selected_device) if selected_device is not None else None
    component_assessments = {key: assess_component(result, key, selected_main_device=device,
        temperature_c=junction_temperature_c) for key in result.waveforms}
    if device is not None and "Q1" in result.stress:
        fresh_assessment=component_assessments["Q1"]
        if device_assessment is not None and device_assessment!=fresh_assessment:
            raise ValueError("Device assessment belongs to different design or parameters")
        device_assessment=fresh_assessment
    current_loop_identity = None
    if loop_result is not None:
        if loop_settings is None:
            raise ValueError("Current loop settings are required")
        from .loop import Compensator, LoopPlant, analyze_loop
        from .project import _check_loop_identity
        plant = LoopPlant(**loop_settings["plant"])
        comp = Compensator(**loop_settings["compensator"])
        _check_loop_identity(result.inputs, plant)
        fresh = analyze_loop(plant, comp, loop_result.frequency_hz)
        for key in ("plant_response", "compensator_response", "amplifier_response", "loop_response"):
            if not np.array_equal(getattr(loop_result, key), getattr(fresh, key)):
                raise ValueError("Loop result is stale or belongs to different settings")
        loop_result = fresh
        current_loop_identity = CornerLoopIdentity(plant, comp)
    if corner_sweep is not None:
        if corner_sweep.base_design_id != result.design_id:
            raise ValueError("Corner scan belongs to a different base design")
        if corner_sweep.controller is not None and corner_sweep.loop_identity is None:
            raise ValueError("Corner scan lacks full loop identity; rerun the scan")
        if loop_settings is not None and current_loop_identity is None:
            raise ValueError("Corner scan requires a current loop result for supplied loop settings")
        if corner_sweep.loop_identity != current_loop_identity:
            raise ValueError("Corner scan belongs to different loop settings")
        current_comp = asdict(current_loop_identity.compensator) if current_loop_identity else None
        if corner_sweep.controller != current_comp or corner_sweep.selected_device != selected_device:
            raise ValueError("Corner scan belongs to different controller or device inputs")
    sections = [f'<h1>Power Stage Designer · {escape(result.topology_id)}</h1>',
        '<p>软件稳态模型结果；不表示硬件验证或完整效率预测。</p>',
        _table({'Design identity': result.design_id, 'Result model revision': result.model_version, 'PSD framework revision': MODEL_REVISION,
                'Toolkit': __version__, 'Generated UTC': datetime.now(timezone.utc).isoformat(),
                'Mode': result.mode, 'Duty': f'{result.duty:.12g}'}),
        '<h2>设计输入（SI）</h2>', _table(asdict(result.inputs)),
        svg_schematic(result.topology_id,device_keys=result.waveforms), '<h2>模型与来源</h2>',
        _table({'Assumptions': result.assumptions,'Warnings': result.warnings,'Provenance':result.provenance}),
        '<h2>工作点与物理约束</h2>', _table(result.metrics), _table(result.diagnostics)]
    if parameter_suggestion is not None:
        from .suggestions import validate_suggestion_record
        parameter_suggestion=validate_suggestion_record(parameter_suggestion, result)
        sections += ['<h2>参数建议：目标、来源与实际验算</h2>',
                     '<p>记录为用户应用的模型校核建议；历史数值与当前模型比较容差为 2 ppm 或 1e-9 SI，当前波形另行重算。不表示 TI 软件相关性、器件定型或硬件验证。</p>',
                     '<pre>'+escape(json.dumps(parameter_suggestion, ensure_ascii=False, indent=2, allow_nan=False))+'</pre>']
    if selected_device:
        sections += ['<h2>所选器件（用户需核对数据手册）</h2>',_table(selected_device),_table(device_assessment or {})]
    for key, wave in result.waveforms.items():
        sections += [f'<h2>{escape(wave.label)} · {escape(key)}</h2>',_table(result.stress[key]),
                     _table(component_assessments[key])]
        for signal, label in ((wave.current_a,'电流 / A'),(wave.voltage_v,'电压 / V')):
            t,y=signal.sample(count=1200,periods=2)
            sections += [_curve_svg(np.asarray(t)*1e6,y,label,'µs'), f'<p>参考方向：{escape(signal.reference)}</p>']
    if loop_result is not None:
        if loop_settings is None:
            raise ValueError('Loop settings are required for a reproducible report')
        sections += ['<h2>模拟环路：当前工况</h2>',_table(loop_settings),
                     _table({k:v for k,v in _jsonable(asdict(loop_result.stability)).items() if k not in {"sensitivity","complementary_sensitivity"}}),_table(loop_result.metrics),
                     _table({'Warnings':loop_result.warnings,'Sources':loop_result.sources})]
        for name, response in [('功率级',loop_result.plant_response),('补偿器',loop_result.compensator_response),
                               ('误差放大器开环',loop_result.amplifier_response),('返回比',loop_result.loop_response)]:
            sections += [_curve_svg(loop_result.frequency_hz,20*np.log10(np.maximum(abs(response),1e-300)),name+' / dB','Hz',logx=True),
                         _curve_svg(loop_result.frequency_hz,np.unwrap(np.angle(response))*180/np.pi,name+' / deg','Hz',logx=True)]
    else:
        sections += ['<h2>环路状态</h2><p>此报告没有当前工况的有效模拟环路结果。</p>']
    if loop_request is not None:
        sections += ['<h2>环路元件建议：用户目标</h2>',
                     '<p>保存的搜索意图，不表示当前元件已达到目标；实际交越与裕量以上述当前环路复算为准。元件取整后始终重新验算。</p>',
                     _table(loop_request)]
    if corner_sweep is not None:
        sections += ['<h2>逐指标多工况极值</h2>', '<p>'+escape(corner_sweep.evidence_scope)+'</p>',
                     _table(asdict(corner_sweep.request)),_table(corner_sweep.extrema),
                     '<p>point_id 标识完整工况，包括环路参数；design_id 只标识功率级。同一功率级可对应不同 C2/ESR/DCR 的环路工况。下列逐点记录保留实际 SI 参数，不把环路 C2 合并进理想功率级 C1。</p>',
                     '<pre>'+escape(json.dumps(asdict(corner_sweep),ensure_ascii=False,indent=2,allow_nan=False))+'</pre>']
    snapshot = {'inputs':asdict(result.inputs),'design_id':result.design_id,'model_revision':result.model_version,'framework_model_revision':MODEL_REVISION,
                'metrics':result.metrics,'stress':result.stress,'loop_settings':loop_settings,'loop_request':loop_request,
                'parameter_suggestion':parameter_suggestion, 'selected_device':selected_device,
                'junction_temperature_c':junction_temperature_c, 'device_assessment':device_assessment,
                'component_assessments':component_assessments}
    sections += ['<h2>可复现数据</h2><pre>'+escape(json.dumps(_jsonable(snapshot),ensure_ascii=False,indent=2,allow_nan=False))+'</pre>',
                 '<p>Reference: TI SLVUBB4B (2023-02). Models and declared domains are independently implemented; TI executable correlation and hardware evidence are separate.</p>']
    output=Path(path); output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Power Stage Design</title><style>body{font:15px system-ui;max-width:1000px;margin:32px auto;padding:0 24px;color:#163047}h1,h2{color:#075985}table{border-collapse:collapse;width:100%;margin:14px 0}th,td{text-align:left;border-bottom:1px solid #cbd5e1;padding:7px;overflow-wrap:anywhere}th{width:35%}svg{width:100%;max-width:740px}pre{white-space:pre-wrap;background:#f1f5f9;padding:18px}@media print{h2{break-after:avoid}svg,table{break-inside:avoid}}</style>'+''.join(sections)+'</html>',encoding='utf-8')
    return output


def write_waveform_csv(result, path: str | Path, *, samples: int = 2000) -> Path:
    output=Path(path); output.parent.mkdir(parents=True,exist_ok=True)
    t = np.linspace(0, next(iter(result.waveforms.values())).current_a.period_s, samples, endpoint=False)
    signals = {}
    for key, wave in result.waveforms.items():
        for suffix, signal in (("current_a",wave.current_a),("voltage_v",wave.voltage_v)):
            phase=t/signal.period_s
            values=np.empty_like(t)
            for segment in signal.segments:
                mask=(phase>=segment.start_fraction)&(phase<segment.end_fraction)
                u=(phase[mask]-segment.start_fraction)/segment.duration_fraction
                values[mask]=segment.evaluate(u)
            signals[key+"_"+suffix]=values
    with output.open("w",newline="",encoding="utf-8") as stream:
        writer=csv.writer(stream);writer.writerow(["design_id","time_s",*signals])
        for i,time in enumerate(t):
            writer.writerow([result.design_id,time,*[v[i] for v in signals.values()]])
    return output
