"""Actual parent-worker delivery must retain Vienna request/source identity."""
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace

import pytest


def _wait(app, predicate, seconds=15):
    stop = monotonic() + seconds
    while not predicate() and monotonic() < stop:
        app.processEvents()
        sleep(.005)
    assert predicate()


def _host(monkeypatch):
    from pfc_design.gui import main_window
    monkeypatch.setattr(main_window, 'check_for_updates', lambda *_a, **_k: None)
    host = main_window.PFCMainWindow()
    view = host.vienna_view
    monkeypatch.setattr(view.performance, 'set_vienna', lambda *args: None)
    for name in ('_plot_ac', '_plot_bus', '_plot_switch', '_plot_sector', '_plot_harm', '_summary'):
        monkeypatch.setattr(view, name, lambda *args: None)
    return main_window, host, view


@pytest.mark.parametrize('restore_same_value', [False, True])
def test_real_worker_changed_or_aba_request_cannot_publish(desktop_qapp, monkeypatch, restore_same_value):
    module, host, view = _host(monkeypatch)
    entered, release = Event(), Event()
    line = SimpleNamespace(metrics=SimpleNamespace(overall_power_factor=.99))

    def delayed_line(config, *, cancelled=None):
        entered.set()
        assert release.wait(10)
        return line

    monkeypatch.setattr(module, 'simulate_vienna_line_cycle', delayed_line)
    monkeypatch.setattr(module, 'validate_vienna_line_cycle', lambda _: None)
    monkeypatch.setattr(module, 'build_vienna_switching_waveforms', lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(module, 'validate_vienna_switching', lambda _: None)
    try:
        config = view._config()
        host.run_vienna_analysis(config)
        _wait(desktop_qapp, entered.is_set)
        revision = host._vienna_input_revision
        old_kp = view.cc['kp'].value()
        view.cc['kp'].setValue(old_kp * 1.1)
        if restore_same_value:
            view.cc['kp'].setValue(old_kp)
            assert view._config() == config
        assert host._vienna_input_revision > revision
        release.set()
        _wait(desktop_qapp, lambda: not host._active_workers)
        assert host.result is None and view.result is None
        assert not view.codegen_button.isEnabled()
        # A new request really publishes the newly captured source.
        host.run_vienna_analysis(view._config())
        _wait(desktop_qapp, lambda: not host._active_workers)
        assert host.result is view.result and view.has_current_result()
        assert view.codegen_button.isEnabled()
    finally:
        release.set()
        _wait(desktop_qapp, lambda: not host._active_workers)
        host.close()


def test_late_old_payload_cannot_erase_new_accepted_result(desktop_qapp, monkeypatch):
    from pfc_design.vienna import build_vienna_control_lab_analysis
    module, host, view = _host(monkeypatch)
    try:
        before = view._config()
        old = (build_vienna_control_lab_analysis(before), SimpleNamespace(metrics=SimpleNamespace(overall_power_factor=.99)), SimpleNamespace())
        old_request = module._ViennaCalculation(old, host._vienna_input_revision, before)
        view.cc['kp'].setValue(view.cc['kp'].value() * 1.1)
        new = (build_vienna_control_lab_analysis(view._config()), old[1], old[2])
        assert host._vienna_ready(new)
        assert view.has_current_result()
        assert host._vienna_ready(old_request) is False
        assert host._vienna_ready(old) is False
        assert host.result is new and view.result is new
        assert view.codegen_button.isEnabled()
    finally:
        host.close()


@pytest.mark.parametrize('action', ['cancel','close'])
def test_actual_parent_cancel_releases_worker_without_failure_dialog(desktop_qapp,monkeypatch,action):
    module,host,view=_host(monkeypatch)
    from pfc_design.vienna import ViennaAnalysisCancelled
    entered=Event();errors=[];observed=[]
    def cooperative_line(config,*,cancelled=None):
        entered.set()
        deadline=monotonic()+10
        while not cancelled() and monotonic()<deadline:
            sleep(.002)
        observed.append(cancelled())
        if cancelled():raise ViennaAnalysisCancelled('cancelled')
        raise RuntimeError('test timeout waiting for actual parent Event')
    monkeypatch.setattr(module,'simulate_vienna_line_cycle',cooperative_line)
    monkeypatch.setattr(host,'_worker_error',lambda error:errors.append(error))
    try:
        host.show()
        host.run_vienna_analysis(view._config())
        _wait(desktop_qapp,entered.is_set)
        assert view.cancel_button.isEnabled()
        if action=='cancel':view.cancel_button.click()
        else:
            assert not host.close()  # Busy close cooperatively cancels; never destroys a live worker.
        _wait(desktop_qapp,lambda:not host._active_workers)
        assert observed==[True] and errors==[]
        assert host.result is None and view.result is None
        assert host._vienna_cancel_event is None
        assert view.run_button.isEnabled() and not view.codegen_button.isEnabled()
        assert not view.cancel_button.isEnabled()
        assert host.close()
    finally:
        if host._vienna_cancel_event:host._vienna_cancel_event.set()
        _wait(desktop_qapp,lambda:not host._active_workers)
        host.close()


def test_vienna_real_worker_failure_still_reports_error(desktop_qapp,monkeypatch):
    module,host,view=_host(monkeypatch);errors=[]
    def broken_line(config,*,cancelled=None):raise ArithmeticError('physical divergence marker')
    monkeypatch.setattr(module,'simulate_vienna_line_cycle',broken_line)
    monkeypatch.setattr(host,'_worker_error',lambda error:errors.append(error))
    try:
        host.run_vienna_analysis(view._config())
        _wait(desktop_qapp,lambda:not host._active_workers)
        assert len(errors)==1 and 'physical divergence marker' in errors[0]
        assert host.result is None and view.result is None
        assert view.run_button.isEnabled() and not view.codegen_button.isEnabled()
    finally:host.close()


def test_parent_close_keeps_actual_vienna_map_worker_alive_until_finished(desktop_qapp,monkeypatch):
    from pfc_design.vienna import illustrative_vienna_config,build_vienna_control_lab_analysis
    module,host,view=_host(monkeypatch)
    try:
        view.set_config(illustrative_vienna_config())
        payload=(build_vienna_control_lab_analysis(view._config()),
                 SimpleNamespace(metrics=SimpleNamespace(overall_power_factor=.99)),SimpleNamespace())
        assert host._vienna_ready(payload)
        maps=view.solution_map_view
        maps.fc_min.setValue(500);maps.fc_max.setValue(2000)
        maps.fc_points.setValue(5);maps.pm_points.setValue(5)
        host.show();maps.run_button.click()
        assert maps.scan_running and not host._active_workers
        assert not host.close()
        assert maps._scan_cancel.is_set()
        _wait(desktop_qapp,lambda:not maps.scan_running)
        assert maps.result is None and not maps.apply_button.isEnabled()
        assert host.close()
    finally:
        view.solution_map_view.cancel_scan()
        _wait(desktop_qapp,lambda:not view.solution_map_view.scan_running)
        host.close()


def test_hidden_ttpl_minimum_does_not_force_vienna_parent_scroll(desktop_qapp,monkeypatch):
    module,host,view=_host(monkeypatch)
    try:
        host.resize(1280,720);host.show();desktop_qapp.processEvents()
        host.subtabs.setCurrentIndex(0);desktop_qapp.processEvents()
        ttpl_min=host.subtabs.minimumSizeHint()
        host.subtabs.setCurrentWidget(view);desktop_qapp.processEvents()
        assert host.size().width()==1280 and host.size().height()==720
        assert host.subtabs.minimumSizeHint().width()<ttpl_min.width()
        assert host.workspace_scroll.horizontalScrollBar().maximum()==0
        assert host.workspace_scroll.verticalScrollBar().maximum()==0
        assert view.width()<=host.workspace_scroll.viewport().width()
        assert view.run_button.isVisible() and view.codegen_button.isVisible()
        host.subtabs.setCurrentIndex(0);desktop_qapp.processEvents()
        assert host.subtabs.minimumSizeHint()==ttpl_min
        assert host.workspace_scroll.horizontalScrollBar().maximum()>0
    finally:host.close()
