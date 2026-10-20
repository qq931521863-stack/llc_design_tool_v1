"""Vienna-owned bindings to the shared controller solution-map workbench."""
from __future__ import annotations

from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import re
from threading import Event

import numpy as np

from PySide6.QtCore import QSignalBlocker, QThreadPool
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,
    QFileDialog,QInputDialog,QMessageBox)

from llc_design.control.candidate_evidence import exact_signature
from llc_design.control.digital_loop import PIControllerConfig
from llc_design.control.solution_map import SolutionMapResult, SolutionStatus
from llc_design.control.smart_control import evaluate_common_solution_point
from llc_design.gui.workers import FunctionWorker
from shiboken6 import isValid
from llc_design.gui.widgets.solution_map_view import SolutionMapView
from .analysis import build_vienna_control_lab_analysis
from .config import illustrative_vienna_config
from .workspace import (LOOPS,encode_config,decode_config,validate_config,read_session,write_session,
    loop_sources,import_exact_2p2z,export_report)

MAP_CONTROLS=('fc_min','fc_max','fc_points','pm_min','pm_max','pm_points','gm_min',
              'ms_max','switch_max','fc_tol','pm_tol','live_fc','live_pm')


class ViennaSolutionMapView(SolutionMapView):
    def __init__(self,*args,**kwargs):
        self._scan_workers=[]
        self._scan_generation=0
        self._scan_cancel=None
        super().__init__(*args,**kwargs)
        heading=self.layout().itemAt(0).layout().itemAt(0).widget()
        heading.setText('Vienna Fc / PM')
        self.generate_button.setText('Generate PI')
        self.run_button.setText('Scan Fc × PM')
        self.apply_button.setText('Apply PI')

    @property
    def scan_running(self):
        return bool(self._scan_workers)

    def cancel_scan(self):
        self._scan_generation+=1
        if self._scan_cancel is not None:self._scan_cancel.set()

    def cancel_preview(self):
        self.cancel_scan()
        super().cancel_preview()

    def build_map(self):
        self.cancel_preview();self.result=None
        source=self._source()
        if source is None:return
        problem=self._source_problem(source) or self._pending_snapshot_problem()
        if problem:self.details.setPlainText(problem);return
        if self.fc_max.value()<=self.fc_min.value() or self.pm_max.value()<=self.pm_min.value():
            self.details.setPlainText('Fc/PM maximum must exceed minimum.');return
        # Freeze every numerical input before leaving the GUI thread.
        corners=self._active_corners();constraints=self._constraints()
        fc=np.geomspace(self.fc_min.value(),self.fc_max.value(),self.fc_points.value())
        pm=np.linspace(self.pm_min.value(),self.pm_max.value(),self.pm_points.value())
        label=f'{source.label} ({len(corners)} operating points)'
        cancel=Event();self._scan_cancel=cancel;generation=self._scan_generation
        def calculate():
            rows=[];codes=np.empty((len(pm),len(fc)),dtype=np.int16)
            for iy,target_pm in enumerate(pm):
                row=[]
                for ix,target_fc in enumerate(fc):
                    if cancel.is_set():return None
                    point=evaluate_common_solution_point(corners,target_crossover_hz=float(target_fc),
                        target_phase_margin_deg=float(target_pm),constraints=constraints)
                    row.append(point);codes[iy,ix]=int(point.status)
                rows.append(tuple(row))
            if cancel.is_set():return None
            return SolutionMapResult(corners[0].frequencies_hz,fc,pm,tuple(rows),codes,
                codes==int(SolutionStatus.FEASIBLE),corners[0].sample_rate_hz,
                corners[0].switching_frequency_hz,label,constraints)
        worker=FunctionWorker(calculate);self._scan_workers.append(worker)
        def current():return isValid(self) and worker in self._scan_workers and generation==self._scan_generation
        def completed(result):
            if current() and result is not None:
                self.result=result;self.selected_point=None;self.apply_button.setEnabled(False);self._plot_result()
        def failed(error):
            if current():self.details.setPlainText('Vienna map failed: '+error)
        def finished():
            if worker in self._scan_workers:self._scan_workers.remove(worker)
            worker.signals.result.disconnect();worker.signals.error.disconnect();worker.signals.finished.disconnect()
            worker.signals.deleteLater()
            if isValid(self) and not self._scan_workers:
                valid=self._source() is not None and not self._source_problem(self._source())
                self.run_button.setEnabled(valid)
        worker.signals.result.connect(completed);worker.signals.error.connect(failed);worker.signals.finished.connect(finished)
        self.run_button.setEnabled(False)
        self.details.setPlainText('Scanning exact PI candidates. Cancel preview, edits, or a new source discard this scan.')
        worker.start(QThreadPool.globalInstance())

    @staticmethod
    def _source_problem(source):
        if 'ADC_OVERRANGE' in source.provenance:
            return 'ADC_OVERRANGE: fix the split-bus gain before candidate synthesis/Apply. The linear model omits clipping.'
        return SolutionMapView._source_problem(source)


def install_workspace(view):
    page=QWidget(view); layout=QVBoxLayout(page)
    layout.setContentsMargins(4, 4, 4, 4)
    bar=QHBoxLayout()
    actions=(('Save session',lambda:save_dialog(view)),
             ('Open session',lambda:load_dialog(view)),
             ('New range-sane example',lambda:new_example(view)),
             ('Apply exact H(z) ≤2',lambda:exact_dialog(view)),
             ('Report / research',lambda:report_dialog(view)))
    view._workspace_buttons=[]
    for text,callback in actions:
        button=QPushButton(text); button.clicked.connect(callback); bar.addWidget(button)
        view._workspace_buttons.append(button)
    layout.addLayout(bar)
    notice=QLabel('ABC feedback-loop model. Exact import supports order ≤2, z⁻¹ coefficients, unchanged firmware clock. '
                 'Midpoint is an auxiliary gain approximation; no dq or hardware-stability claim. '
                 'Legacy overrange inputs remain unchanged; use the explicit new example to start a range-sane split divider.')
    notice.setWordWrap(True); layout.addWidget(notice)
    view.solution_map_view=ViennaSolutionMapView('Vienna exact PI candidates / retained operating points')
    layout.addWidget(view.solution_map_view)
    view.tabs.addTab(page,'Loop workflow / Session')
    view.solution_map_view.controller_selected.connect(lambda key,point:apply_candidate(view,key,point))
    view.solution_map_view.evidence_context_changed.connect(lambda:clear_evidence(view))
    view._candidate_evidence=None; view._historical_candidate=None


def clear_evidence(view):
    view._candidate_evidence=None


def apply_candidate(view,key,point):
    maps=view.solution_map_view
    if view._busy or not view.has_current_result() or key not in maps.sources or not maps.can_apply(point):
        return
    source=maps.sources[key]
    if maps._source_problem(source):
        return
    config=view._config()
    from .adc_range import operating_adc_range_warnings
    if operating_adc_range_warnings(config) or source.model_inputs != encode_config(config):
        maps.details.setPlainText('Candidate source no longer matches the current range-checked model. Regenerate.')
        return
    fresh=evaluate_common_solution_point(maps._active_corners(),
        target_crossover_hz=point.target_crossover_hz,
        target_phase_margin_deg=point.target_phase_margin_deg,constraints=maps._constraints())
    if fresh != point:
        maps.details.setPlainText('Candidate assessment changed; regenerate before Apply.')
        return
    old=getattr(config,key+'_controller')
    controller=PIControllerConfig(float(point.kp),float(point.ti_s),1/source.sample_rate_hz,old.output_min,old.output_max)
    candidate=replace(config,**{key+'_controller':controller})
    evidence={'loop':key,'source_sha256':exact_signature(config),
        'applied_config_sha256':exact_signature(candidate),'controller':encode_config(controller),
        'assessment':_history(asdict(point)),'constraints':asdict(maps._constraints()),
        'operating_points':[corner.corner_id for corner in maps._active_corners()],
        'scope':'Finite-band candidate only; NOT_PROVEN; explicit conditional adoption where selected.'}
    view.set_config(candidate)
    view._candidate_evidence=evidence
    view._request()


def _history(value):
    if isinstance(value,dict):return {k:_history(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [_history(v) for v in value]
    if isinstance(value,float) and not math.isfinite(value):return None
    return value


def capture_workspace(view):
    maps=view.solution_map_view
    return {'controls':{name:getattr(maps,name).value() for name in MAP_CONTROLS},
        'loop':maps.source_combo.currentData() or maps._last_source_key,
        'retained':[{'id':key,'checked':key in maps._checked_corner_ids,'loop':source.key,
                     'config':source.model_inputs} for key,source in maps._retained_sources.items()],
        'candidate_history':_history(view._candidate_evidence or view._historical_candidate)}


def prepare_workspace(view,state):
    # Validate and recompute every retained source before touching live widgets.
    if not isinstance(state,dict) or set(state)!={'controls','loop','retained','candidate_history'}:
        raise ValueError('Invalid Vienna workspace fields')
    controls=state['controls']; maps=view.solution_map_view
    if not isinstance(controls,dict) or set(controls)!=set(MAP_CONTROLS):
        raise ValueError('Invalid saved solution-map controls')
    for name,value in controls.items():
        widget=getattr(maps,name)
        if type(value) not in (int,float) or not math.isfinite(value) or not widget.minimum()<=value<=widget.maximum():
            raise ValueError('Invalid saved solution-map control: '+name)
        if name.endswith('points') and type(value) is not int:
            raise ValueError('Map point count must be integer')
    if controls['fc_max']<=controls['fc_min'] or controls['pm_max']<=controls['pm_min']:
        raise ValueError('Invalid saved sweep bounds')
    if state['loop'] not in (*LOOPS,None): raise ValueError('Unknown saved loop')
    if not isinstance(state['retained'],list) or len(state['retained'])>32:
        raise ValueError('At most 32 retained operating points are supported')
    if state['candidate_history'] is not None and not isinstance(state['candidate_history'],dict):
        raise ValueError('Invalid historical candidate')
    sources={}
    for item in state['retained']:
        if (not isinstance(item,dict) or set(item)!={'id','checked','loop','config'}
            or not isinstance(item['id'],str) or not re.fullmatch(r'OP[1-9][0-9]{0,5}',item['id'])
            or item['id'] in sources or type(item['checked']) is not bool or item['loop'] not in LOOPS):
            raise ValueError('Invalid retained operating point')
        config=validate_config(decode_config(item['config']))
        analysis=build_vienna_control_lab_analysis(config)
        sources[item['id']]=next(s for s in loop_sources(analysis) if s.key==item['loop'])
    return sources


def save_path(view,path):
    if view._busy or view._closed:raise ValueError('Wait for the active analysis before saving')
    return write_session(path,view._config(),capture_workspace(view))


def load_path(view,path):
    if view._busy or view._closed:raise ValueError('Wait for the active analysis before loading')
    config,state=read_session(path)
    assignments=view._config_assignments(config)  # preflight all widget ranges
    sources=prepare_workspace(view,state)
    view.set_config(config,assignments=assignments)
    maps=view.solution_map_view
    maps._retained_sources={key:maps._freeze_source(value) for key,value in sources.items()}
    maps._checked_corner_ids={item['id'] for item in state['retained'] if item['checked']}
    maps._corner_sequence=max((int(key[2:]) for key in sources),default=0)
    maps._last_source_key=state['loop']
    for name,value in state['controls'].items():
        with QSignalBlocker(getattr(maps,name)):getattr(maps,name).setValue(value)
    maps.allow_conditional.setChecked(False)
    maps._refresh_corners()
    view._historical_candidate=state['candidate_history']; view._candidate_evidence=None
    view.summary.setPlainText('Session restored exactly. Run analysis and generate a fresh candidate before Apply. Saved candidate evidence is historical only.')
    return config


def save_dialog(view):
    path,_=QFileDialog.getSaveFileName(view,'Save Vienna session','','JSON (*.json)')
    if path:
        try:save_path(view,path)
        except Exception as exc:QMessageBox.warning(view,'Session save failed',str(exc))


def load_dialog(view):
    path,_=QFileDialog.getOpenFileName(view,'Open Vienna session','','JSON (*.json)')
    if path:
        try:load_path(view,path)
        except Exception as exc:QMessageBox.warning(view,'Session load failed; current session unchanged',str(exc))


def new_example(view):
    if not view._busy and not view._closed:view.set_config(illustrative_vienna_config())


def exact_dialog(view):
    if view._busy or view._closed:return
    loop,ok=QInputDialog.getItem(view,'Exact H(z) input','Selected loop',list(LOOPS),0,False)
    if not ok:return
    config=view._config(); source=exact_signature(config)
    old=getattr(config,loop+'_controller').transfer_function()
    initial=json.dumps({'b':old.numerator.tolist(),'a':old.denominator.tolist(),'sample_time_s':old.sample_time_s},indent=2)
    text,ok=QInputDialog.getMultiLineText(view,'Import and apply exact H(z)','Ascending z^-1 coefficients; max order 2; no fit or clock change',initial)
    if not ok:return
    try:
        from llc_design.control.design_session import strict_json_loads
        if view._busy or view._closed or exact_signature(view._config())!=source:
            raise ValueError('Inputs changed during import; retry against current inputs')
        payload=strict_json_loads(text)
        if set(payload)!={'b','a','sample_time_s'}:raise ValueError('Expected b, a, sample_time_s only')
        candidate=import_exact_2p2z(config,loop,payload['b'],payload['a'],payload['sample_time_s'])
        view.set_config(candidate); view._request()
    except Exception as exc:QMessageBox.warning(view,'Exact controller import failed',str(exc))


def report_dialog(view):
    if view._busy or view._closed or not view.has_current_result():
        QMessageBox.information(view,'Report unavailable','Run the current inputs first.');return
    snapshot=view.result
    directory=QFileDialog.getExistingDirectory(view,'Vienna report/research output')
    if not directory:return
    try:
        if view._busy or view._closed or view.result is not snapshot or not view.has_current_result():
            raise ValueError('Inputs/results changed while selecting output. Run current inputs again.')
        paths=export_report(snapshot[0],Path(directory)/'vienna_loop_report')
        QMessageBox.information(view,'Vienna report exported',f'{len(paths)} files written to {paths[0].parent}')
    except Exception as exc:QMessageBox.warning(view,'Vienna report failed',str(exc))
