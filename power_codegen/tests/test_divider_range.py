"""Export preflight must match the generated uint16_t scheduler counters."""
from dataclasses import replace
import shutil
import subprocess

import pytest

from pfc_design.control import build_pfc_control_lab_analysis, illustrative_ttpl_config
from pfc_design.vienna import build_vienna_control_lab_analysis, illustrative_vienna_config
from power_codegen import generate_ttpl_control_code, generate_vienna_control_code
from power_codegen.generator import _divider


CASES = [
    ("ttpl", "amc_rate_hz", None, "TTPL_AMC_DIVIDER", "TTPL AMC"),
    ("ttpl", "voltage_loop_rate_hz", "voltage", "TTPL_VOLTAGE_DIVIDER", "TTPL voltage loop"),
    ("vienna", "reference_rate_hz", None, "VIENNA_REFERENCE_DIVIDER", "Vienna reference"),
    ("vienna", "voltage_loop_rate_hz", "voltage", "VIENNA_VOLTAGE_DIVIDER", "Vienna voltage loop"),
    ("vienna", "balance_loop_rate_hz", "balance", "VIENNA_BALANCE_DIVIDER", "Vienna balance loop"),
]


def _export_case(case, divider):
    topology, rate_name, controller_name, _, _ = case
    if topology == "ttpl":
        cfg = illustrative_ttpl_config()
        analyze, emit = build_pfc_control_lab_analysis, generate_ttpl_control_code
    else:
        cfg = illustrative_vienna_config()
        analyze, emit = build_vienna_control_lab_analysis, generate_vienna_control_code
    rate = cfg.firmware.current_loop_rate_hz / divider
    changes = {
        "firmware": replace(cfg.firmware, **{rate_name: rate}),
        # Keep requested frequency samples below the deliberately slow loop's
        # Nyquist limit, so rejection must come from the export's counter bound.
        "frequency_start_hz": 0.01,
        "frequency_points": 200,
    }
    if controller_name:
        field = controller_name + "_controller"
        changes[field] = replace(getattr(cfg, field), sample_time_s=1.0 / rate)
    cfg = replace(cfg, **changes)
    cfg.validate()
    return analyze(cfg), emit


@pytest.mark.parametrize("case", CASES, ids=lambda case: case[3])
@pytest.mark.parametrize("divider", [65536, 65537])
def test_out_of_range_divider_rejects_real_export_without_writes(case, divider, tmp_path):
    analysis, emit = _export_case(case, divider)
    missing = tmp_path / "missing"
    existing = tmp_path / "existing"
    existing.mkdir()
    sentinel = existing / "control_runtime.h"
    sentinel.write_text("preserve previous export\n", encoding="utf-8")

    for destination in (missing, existing):
        # Draft export bypasses only the model screen, never scheduler limits.
        with pytest.raises(ValueError, match=case[4] + r".*uint16_t.*65535"):
            emit(analysis, destination, require_stable=False)
        assert not missing.exists()
        assert list(existing.iterdir()) == [sentinel]
        assert sentinel.read_text(encoding="utf-8") == "preserve previous export\n"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case[3])
def test_largest_uint16_divider_exports_and_compiles(case, tmp_path):
    compiler = shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        pytest.skip("compiled divider boundary tests require a host C compiler")
    analysis, emit = _export_case(case, 65535)
    result = emit(analysis, tmp_path / case[0], require_stable=False)
    header = (result.directory / f"{case[0]}_control.h").read_text(encoding="utf-8")
    assert f"#define {case[3]} 65535u" in header
    for source in ("control_runtime.c", f"{case[0]}_control.c", "isr_template.c"):
        subprocess.run(
            [compiler, "-std=c99", "-pedantic", "-Wall", "-Wextra", "-Werror",
             "-Wdouble-promotion", "-c", source],
            cwd=result.directory, check=True, capture_output=True, text=True,
        )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case[3])
def test_noninteger_divider_still_rejects_before_writes(case, tmp_path):
    analysis, emit = _export_case(case, 2.5)
    destination = tmp_path / "rejected"
    with pytest.raises(ValueError, match="not an integer multi-rate divider"):
        emit(analysis, destination, require_stable=False)
    assert not destination.exists()


@pytest.mark.parametrize("base_hz,sub_hz", [(0, 1), (1, 0), (-1, 1), (1, -1)])
def test_divider_preserves_positive_rate_gate(base_hz, sub_hz):
    with pytest.raises(ValueError, match="rates must be positive"):
        _divider(base_hz, sub_hz, "test")


def test_divider_preserves_minimum_integer_gate():
    with pytest.raises(ValueError, match="not an integer multi-rate divider"):
        _divider(1.0, 2.0, "test")
    assert _divider(1.0, 1.0, "test") == 1
