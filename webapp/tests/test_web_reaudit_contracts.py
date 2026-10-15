import pytest
import json
import shutil
import subprocess
from pathlib import Path
from fastapi.testclient import TestClient

from llc_design.models.devices import DeviceDatabase
from webapp.app import app
from webapp.service import analyze_llc

client = TestClient(app)


@pytest.mark.parametrize("path", ["/api/llc/report", "/api/llc/report.xlsx"])
def test_bad_report_input_is_422_not_internal_error(path):
    assert client.post(path, json={"spec": {"pout_w": -1}}).status_code == 422
    assert client.post(path, json={"spec": {"primary_topology": "HALF_BRIDGE"}}).status_code == 422


def test_chinese_report_metadata_is_supported_or_actionable(monkeypatch):
    response = client.post("/api/llc/report", json={"spec": {}, "project": "测试", "engineer": "杨帅"})
    assert response.status_code in (200, 422), response.text
    if response.status_code == 200:
        assert response.content.startswith(b"%PDF")
    else:
        assert "Unicode font" in response.json()["detail"]
    from matplotlib import font_manager
    monkeypatch.setattr(font_manager, "findSystemFonts", lambda: [])
    response = client.post("/api/llc/report", json={"spec": {}, "project": "测试", "engineer": "杨帅"})
    assert response.status_code == 422
    assert "Unicode font" in response.json()["detail"]


def test_control_report_consumes_control_context_not_default_llc(monkeypatch):
    from webapp.reporting import _ReportPdf
    rows = []
    original = _ReportPdf.kv_table

    def capture(self, values, *args, **kwargs):
        rows.extend(values)
        return original(self, values, *args, **kwargs)

    monkeypatch.setattr(_ReportPdf, "kv_table", capture)
    response = client.post("/api/control/report", json={
        "plant_context": {"plant": dict(lr_h=100e-6, cr_f=25e-9, lm_h=500e-6,
                turns_ratio=7.6, vbus_v=480, vout_v=48, pout_w=1200), "sample_time_s": 20e-6},
        "controller": {"kind": "pi", "kp": 0.001, "ti_s": 0.001},
        "project": "Control report", "engineer": "Yang Shuai"})
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"%PDF")
    actual = dict(rows)
    assert actual["Input bus"] == "480 V"
    assert actual["Output target"] == "48 V"
    assert actual["Rated output power"] == "1200 W"
    assert actual["Turns ratio"] == "7.6"
    assert actual["Sample rate"] == "50000 Hz"


@pytest.mark.parametrize("topology,positions", [("FULL_BRIDGE", 4), ("HALF_BRIDGE", 2)])
def test_device_cost_includes_all_switch_positions(topology, positions):
    result = analyze_llc({"primary_topology": topology,
                          "primary_turns": 15 if topology == "HALF_BRIDGE" else 30})
    spec = result["spec"]
    db = DeviceDatabase()
    expected = positions * db.get_primary(spec["primary_device"]).price_usd * spec["primary_parallel_devices"]
    expected += 4 * db.get_sr(spec["sr_device"]).price_usd * spec["sr_parallel_devices_per_position"]
    assert result["summary"]["device_cost_usd"] == pytest.approx(expected)


def test_shipped_frontend_boot_reset_and_report_event_chain():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js required to execute the shipped JavaScript client')
    root = Path(__file__).parents[1]
    fixture = {'defaults': client.get('/api/llc/defaults').json(),
               'analysis': analyze_llc({})}
    result = subprocess.run([node, str(root / 'tests/frontend_reaudit.cjs'), str(root / 'static/app.js')],
                            input=json.dumps(fixture), text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
