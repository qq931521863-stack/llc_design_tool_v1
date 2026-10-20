"""Reproducible Vienna loop sessions, candidate sources and file deliverables.

Inputs are authoritative. Stored candidate records are historical only; fresh
analysis and a fresh explicit Apply are required after restore.
"""
from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass, replace
from enum import Enum
import json
import math
from pathlib import Path
import tempfile

import numpy as np

from llc_design.control.candidate_evidence import exact_signature
from llc_design.control.design_session import strict_json_loads
from llc_design.control.digital_loop import PIControllerConfig, PIFControllerConfig, TwoP2ZControllerConfig
from pfc_design.control.config import ADCTimingConfig, DigitalFilterConfig, ExternalSenseConfig, LoadModel
from .config import ViennaControlLabConfig, ViennaFirmwareConfig, ViennaPowerStageConfig
from .analysis import build_vienna_control_lab_analysis

MODEL_REVISION = 'vienna-abc-principal-band-v1'
SCHEMA = 'power-design-toolkit/vienna-loop-session'
LOOPS = ('current', 'voltage', 'balance')
_MODELS = {cls.__name__: cls for cls in (ViennaControlLabConfig, ViennaFirmwareConfig,
    ViennaPowerStageConfig, PIControllerConfig, PIFControllerConfig, TwoP2ZControllerConfig,
    ADCTimingConfig, DigitalFilterConfig, ExternalSenseConfig)}


def encode_config(value):
    if isinstance(value, Enum):
        return {'enum': type(value).__name__, 'value': value.value}
    if is_dataclass(value):
        return {'model': type(value).__name__, 'fields': {f.name: encode_config(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, tuple):
        return {'tuple': [encode_config(v) for v in value]}
    if value is None or type(value) in (bool, str, int, float):
        if type(value) is float and not math.isfinite(value):
            raise ValueError('Vienna session inputs must be finite')
        return value
    raise ValueError(f'Unsupported Vienna input type: {type(value).__name__}')


def decode_config(value):
    if type(value) in (bool, str, int, float) or value is None:
        return value
    if not isinstance(value, dict):
        raise ValueError('Invalid Vienna model payload')
    if set(value) == {'enum', 'value'} and value['enum'] == 'LoadModel':
        return LoadModel(value['value'])
    if set(value) == {'tuple'} and isinstance(value['tuple'], list):
        return tuple(decode_config(v) for v in value['tuple'])
    if set(value) != {'model', 'fields'} or value['model'] not in _MODELS:
        raise ValueError('Unsupported Vienna session model')
    cls = _MODELS[value['model']]
    payload = value['fields']
    if not isinstance(payload, dict) or set(payload) != {f.name for f in fields(cls)}:
        raise ValueError(f'Invalid fields for {cls.__name__}')
    result = cls(**{key: decode_config(item) for key, item in payload.items()})
    return result


def validate_config(config):
    if type(config) is not ViennaControlLabConfig:
        raise ValueError('Vienna session must contain ViennaControlLabConfig')
    if type(config.power_stage) is not ViennaPowerStageConfig or type(config.firmware) is not ViennaFirmwareConfig:
        raise ValueError('Invalid Vienna stage/firmware model')
    if not isinstance(config.power_stage.load_model, LoadModel):
        raise ValueError('Invalid Vienna load model')
    for key in LOOPS:
        if type(getattr(config, key+'_controller')) not in (PIControllerConfig, PIFControllerConfig, TwoP2ZControllerConfig):
            raise ValueError('Unsupported Vienna controller')
    for key in ('phase_current_sense', 'phase_voltage_sense', 'split_bus_sense'):
        sense = getattr(config, key)
        if type(sense) is not ExternalSenseConfig or type(sense.timing) is not ADCTimingConfig or type(sense.timing.digital_filter) is not DigitalFilterConfig:
            raise ValueError('Invalid Vienna sensing model')
    config.validate()
    if config.frequency_points > 20000 or config.waveform_line_cycles > 200 or config.switching_samples_per_cycle > 20000:
        raise ValueError('Vienna saved computation size exceeds supported bound')
    if config.waveform_line_cycles/config.power_stage.line_frequency_hz*config.waveform_integration_rate_hz > 2_000_000:
        raise ValueError('Vienna waveform request exceeds 2,000,000 integration steps')
    return config


def read_session(path):
    path = Path(path)
    if path.stat().st_size > 8*1024*1024:
        raise ValueError('Vienna session exceeds 8 MiB')
    document = strict_json_loads(path.read_text(encoding='utf-8'))
    if not isinstance(document, dict) or set(document) != {'schema','version','model_revision','config','workspace'}:
        raise ValueError('Invalid Vienna session fields')
    if document['schema'] != SCHEMA or document['version'] != 1 or document['model_revision'] != MODEL_REVISION:
        raise ValueError('Unsupported Vienna session schema/model revision')
    config = validate_config(decode_config(document['config']))
    if not isinstance(document['workspace'], dict):
        raise ValueError('Invalid Vienna workspace state')
    return config, document['workspace']


def write_session(path, config, workspace=None):
    validate_config(config)
    document = {'schema':SCHEMA,'version':1,'model_revision':MODEL_REVISION,
                'config':encode_config(config),'workspace':workspace or {}}
    text = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
    strict_json_loads(text)
    path=Path(path)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as handle:
        handle.write(text)
        temp=Path(handle.name)
    temp.replace(path)
    return path


def compatibility_key(config, loop):
    stage = asdict(config.power_stage)
    for key in ('line_line_rms_v','bus_voltage_v','output_power_w'):
        stage.pop(key)
    return exact_signature(loop, stage, config.firmware, config.phase_current_sense,
        config.phase_voltage_sense, config.split_bus_sense,
        config.current_controller if loop == 'voltage' else None,
        config.phase_current_gain_scale, config.phase_voltage_gain_scale, config.split_bus_gain_scale)


def loop_sources(analysis):
    from llc_design.gui.widgets.solution_map_view import LoopMapSource
    config=analysis.config
    op=(f'Vll={config.power_stage.line_line_rms_v:.9g} Vrms; Vdc={config.power_stage.bus_voltage_v:.9g} V; '
        f'Pout={config.power_stage.output_power_w:.9g} W')
    sources=[]
    factor_keys = {
        'current': ('pwm_zoh','plant_gid','sense_hi'),
        'voltage': ('voltage_command_hold','current_closed_for_outer','bus_plant_gvg','sense_hv'),
        'balance': ('balance_command_hold','balance_plant','sense_balance'),
    }
    for key in LOOPS:
        loop=getattr(analysis,key+'_loop')
        fixed=np.ones_like(loop.frequencies_hz,dtype=complex)
        for factor in factor_keys[key]:
            fixed*=loop.responses[factor]
        sources.append(LoopMapSource(key=key,label=f'Vienna {loop.name}',
            frequencies_hz=loop.frequencies_hz,fixed_loop_response=fixed,
            sample_rate_hz=1/loop.controller.sample_time_s,
            switching_frequency_hz=config.power_stage.switching_frequency_hz,
            current_loop_response=loop.responses['open_'+key],operating_point=op,
            compatibility_key=compatibility_key(config,key),model_inputs=encode_config(config),
            provenance=f'{MODEL_REVISION}; ABC, midpoint approximate; f < {loop.nyquist_hz:g} Hz; NOT_PROVEN. '+
                ' '.join(w for w in analysis.warnings if 'ADC_OVERRANGE' in w)))
    return sources


def import_exact_2p2z(config, loop, numerator, denominator, sample_time_s):
    """Exact z^-1 import without fitting or silently changing the clock/order."""
    from llc_design.control.digital_loop import DigitalTransferFunction
    if loop not in LOOPS:
        raise ValueError('Unknown Vienna loop')
    if not 1 <= len(numerator) <= 3 or not 1 <= len(denominator) <= 3:
        raise ValueError('Vienna runtime currently supports exact H(z) through order 2 only; no truncation is permitted')
    tf=DigitalTransferFunction(np.asarray(numerator),np.asarray(denominator),sample_time_s)
    old=getattr(config,loop+'_controller')
    if not math.isclose(sample_time_s,getattr(config.firmware,loop+'_loop_rate_hz')**-1,rel_tol=1e-12):
        raise ValueError('Imported sample time must match the selected firmware loop rate')
    b=np.pad(tf.numerator,(0,3-len(tf.numerator))); a=np.pad(tf.denominator,(0,3-len(tf.denominator)))
    ctrl=TwoP2ZControllerConfig(*map(float,(*b,*a[1:])),sample_time_s,old.output_min,old.output_max)
    return replace(config,**{loop+'_controller':ctrl})


def research_bundle(analysis, key):
    from power_control_tools.research_export import ResearchExport, ResearchLoop, ResearchModel
    if key not in LOOPS:
        raise ValueError('Unknown Vienna research loop')
    loop=getattr(analysis,key+'_loop'); f=loop.frequencies_hz; r=loop.responses
    source=next(item for item in loop_sources(analysis) if item.key==key)
    ctrl=loop.controller
    unit={'current':('A','pu modulation'), 'voltage':('V','A/V conductance'), 'balance':('V delta','pu balance')}[key]
    def model(name,response,input_unit='1',output_unit='1',tf=None):
        return ResearchModel(name,name,'discrete' if tf is not None else 'mixed',input_unit,output_unit,
            tf.sample_time_s if tf is not None else None,
            tuple(map(float,tf.numerator)) if tf is not None else (),
            tuple(map(float,tf.denominator)) if tf is not None else (),tuple(map(complex,response)),MODEL_REVISION)
    sense=r[{'current':'sense_hi','voltage':'sense_hv','balance':'sense_balance'}[key]]
    forward=r['forward_'+key]; open_loop=r['open_'+key]
    controller_response=ctrl.frequency_response(f)
    # Research T is measured output L/(1+L); actual output remains explicit.
    models=(model('G',source.fixed_loop_response,*unit[::-1]),model('C',controller_response,*unit,tf=ctrl),
            model('F',forward,*unit[:1]*2),model('H',sense),model('L',open_loop),
            model('T',open_loop/(1+open_loop)),model('S',1/(1+open_loop)),
            model('T_actual',forward/(1+open_loop)))
    return ResearchExport('Vienna '+key+' loop',tuple(map(float,f)),models,
        (ResearchLoop(key,'G','C','L','T','S','F','H'),),
        json.dumps({**analysis.summary_dict(), "inputs":encode_config(analysis.config), "source_sha256":exact_signature(analysis.config)},ensure_ascii=False),
        tuple(analysis.warnings)+(f'Controller units: {unit[0]} -> {unit[1]}.',
            'G is the fixed return path including sensing; F is forward path; L=C*G=F*H.',
            'T=L/(1+L) is measured output; T_actual=F/(1+L) is physical output.',))


def export_report(analysis, directory):
    """Human report + exact numerical samples + standalone research programs."""
    from power_control_tools.research_export import export_research_script
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    summary={**analysis.summary_dict(), "inputs":encode_config(analysis.config), "source_sha256":exact_signature(analysis.config)}
    paths=[directory/'vienna_report.json',directory/'vienna_report.md']
    paths[0].write_text(json.dumps(summary,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    rows=['# Vienna ABC loop report','',f'Model: {MODEL_REVISION}',
          'Finite-band evidence only. Closed-loop stability: NOT_PROVEN.','']
    for key in LOOPS:
        loop=getattr(analysis,key+'_loop')
        rows += [f'## {loop.name}',json.dumps(summary[key],indent=2,ensure_ascii=False),'']
        data={'frequency_hz':loop.frequencies_hz}
        for name,response in loop.responses.items():
            data[name+'_real']=response.real; data[name+'_imag']=response.imag
        path=directory/(key+'_responses.csv')
        np.savetxt(path,np.column_stack(list(data.values())),delimiter=',',header=','.join(data),comments='')
        paths.append(path)
        for language,suffix in (('python','.py'),('matlab','.m')):
            paths.append(export_research_script(research_bundle(analysis,key),directory/(key+'_research'+suffix),language))
    rows += ['## Declared limitations',*('- '+w for w in analysis.warnings)]
    paths[1].write_text('\n'.join(rows),encoding='utf-8')
    return tuple(paths)
