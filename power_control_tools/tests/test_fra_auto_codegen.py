from __future__ import annotations

import numpy as np

from power_control_tools.codegen import export_c99_filter, verify_c99_filter
from power_control_tools.fra import auto_design_controller, digital_frequency_response
from power_control_tools.models import ControllerKind


def test_auto_power_2p2z_exact_hz_exports_and_matches_float32_c99(tmp_path):
    f = np.geomspace(20.0, 20_000.0, 700)
    s = 1j * 2.0 * np.pi * f
    plant = 1.5 / (
        (1.0 + s / (2.0 * np.pi * 350.0))
        * (1.0 + s / (2.0 * np.pi * 2_500.0))
        * (1.0 + s / (2.0 * np.pi * 12_000.0))
    )

    result = auto_design_controller(
        f,
        plant,
        controller_kind=ControllerKind.TWO_P_TWO_Z,
        sample_rate_hz=50_000.0,
        target_crossover_hz=3_000.0,
        target_phase_margin_deg=60.0,
    )
    assert result.selected is not None
    controller = result.selected.controller.normalized()
    assert controller.implementable
    assert np.all(np.isfinite(digital_frequency_response(controller, f[f < 20_000.0])))

    exported = export_c99_filter(controller, tmp_path / "fra_auto_2p2z.h", prefix="FRA_AUTO_2P2Z")
    verified = verify_c99_filter(controller, exported, samples=512)
    assert verified.available
    assert verified.passed, verified.message
    assert verified.impulse_max_abs_error < 2e-4
    assert verified.step_max_abs_error < 2e-4
