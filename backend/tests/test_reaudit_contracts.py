import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.schemas.control_schema import PlantContext
from backend.services.control.plant import build_spec
from llc_design.control.digital_loop import DigitalTransferFunction, export_controller_c99
from llc_design.core.tank import design_tank
from webapp.app import app

client = TestClient(app)
PLANT = dict(lr_h=23.8e-6, cr_f=106.5e-9, lm_h=119e-6, turns_ratio=7.6,
             vbus_v=400, vout_v=53, pout_w=3000)


def test_manual_tank_and_ratio_are_not_resynthesized():
    spec = build_spec(PlantContext(plant=PLANT))
    tank = design_tank(spec)
    assert spec.turns_ratio == pytest.approx(7.6, abs=1e-12)
    assert (tank.lr_h, tank.cr_f, tank.lm_h) == pytest.approx((23.8e-6, 106.5e-9, 119e-6), rel=1e-12)
    with pytest.raises(ValueError, match="no silent rounding"):
        build_spec(PlantContext(plant={**PLANT, "secondary_turns": 4}))


def test_unreachable_manual_work_point_is_actionable_not_silently_redesigned():
    response = client.post('/api/control/plant', json={
        'plant': {**PLANT, 'vbus_v': 480, 'vout_v': 48, 'pout_w': 1200}})
    assert response.status_code == 422
    assert 'outside available range' in response.json()['detail']


def test_stability_cannot_hide_failed_kernel_poles():
    body = {"plant_context": {"spec": {}},
            "controller": {"kind": "pi", "kp": 0.008376776400682925, "ti_s": 0.001}}
    result = client.post("/api/control/loop_gain", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["margins"]["stable"] is False
    assert result.json()["margins_min_delay"]["stable"] is None
    assert result.json()["margins_max_delay"]["stable"] is None


def test_json_coefficients_roundtrip_without_sign_change():
    controller = dict(kind="2p2z", b0=0.1, b1=0.2, b2=0.3, a1=-0.9, a2=0.1)
    request = {"plant_context": {"spec": {}}, "controller": controller}
    response = client.post("/api/control/digital_controller", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["coefficients"] == {k: v for k, v in controller.items() if k != "kind"}
    again = client.post("/api/control/digital_controller", json={
        **request, "controller": {"kind": "2p2z", **response.json()["coefficients"]}})
    assert again.json()["coefficients"] == response.json()["coefficients"]


@pytest.mark.parametrize("field,value", [("b0", "NaN"), ("a1", "Infinity"),
                                        ("b1", "-Infinity")])
def test_nonfinite_controller_is_rejected(field, value):
    controller = dict(kind="2p2z", b0=0.1, b1=0.2, b2=0.3, a1=-0.9, a2=0.1)
    controller[field] = value
    assert client.post("/api/control/digital_controller", json={
        "plant_context": {"spec": {}}, "controller": controller}).status_code == 422


def test_reversed_output_limits_and_float32_overflow_do_not_export(tmp_path):
    assert client.post("/api/control/digital_controller", json={
        "plant_context": {"spec": {}},
        "controller": {"kind": "pi", "kp": 0.1, "ti_s": 0.01,
                       "output_min": 1, "output_max": 0}}).status_code == 422
    for value in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="finite"):
            DigitalTransferFunction(np.array([value]), np.array([1]), 0.001)
    model = DigitalTransferFunction(np.array([1e40]), np.array([1]), 0.001)
    with pytest.raises(ValueError, match="float32"):
        export_controller_c99(model, tmp_path / "invalid.c")
    assert not (tmp_path / "invalid.c").exists()


def test_requested_frequency_grid_is_preserved_and_checked():
    request = {"plant_context": {"spec": {}},
               "controller": {"kind": "pi", "kp": 0.05, "ti_s": 0.0002},
               "frequencies_hz": [100, 1000, 2000]}
    response = client.post("/api/control/bode", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["frequencies_hz"] == [100, 1000, 2000]
    for grid in ([], [100, 100], [-1, 100], [100, 25000], [100, "NaN"]):
        assert client.post("/api/control/bode", json={**request, "frequencies_hz": grid}).status_code == 422
