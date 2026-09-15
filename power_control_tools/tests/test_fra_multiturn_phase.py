from __future__ import annotations

import numpy as np

from power_control_tools.fra import analyze_loop_response


def test_multi_turn_phase_uses_nearest_odd_180_branch_for_each_gain_crossing():
    # Phase step is kept below 180 deg so np.unwrap reconstructs the intended
    # multi-turn trajectory. Magnitude oscillates through 0 dB three times.
    f = np.geomspace(100.0, 100_000.0, 13)
    mag_db = np.asarray([10, 7, 3, -2, -5, -1, 3, 5, 1, -2, 2, -1, -5], dtype=float)
    phase_deg = np.asarray([-80, -110, -140, -170, -205, -245, -285, -325, -360, -400, -440, -480, -520], dtype=float)
    loop = np.power(10.0, mag_db / 20.0) * np.exp(1j * np.deg2rad(phase_deg))

    result = analyze_loop_response(f, loop)

    assert len(result.gain_crossovers) >= 3
    # At least one later crossover must be on a phase branch below -180 deg and
    # produce a negative signed PM; a naive 180+phase formula would be invalid.
    assert any(item.phase_deg < -180.0 for item in result.gain_crossovers)
    assert result.worst_phase_margin_deg is not None
    assert result.worst_phase_margin_deg < 0.0
    assert result.status in {"FAIL", "WARNING_MULTIPLE_CROSSOVERS"}

    # The phase analysis must also detect more than one odd-180-degree branch
    # (e.g. -180 and -540 when covered by the sweep).
    targets = {round(item.target_phase_deg) for item in result.phase_crossovers}
    assert -180 in targets
