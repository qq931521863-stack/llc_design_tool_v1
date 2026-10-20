"""Host C99 contract tests: preloaded state, recurrence, and task phase only.

The oracle derives G0 from nominal power and line voltage and uses sample-number
modulo scheduling, not the generated divider counters. No production waveform
or controller-step helper supplies expected values. The C is compiled and run;
this does not compare sensor chains, plant integration, PWM timing, or hardware.
"""
from dataclasses import replace
import ctypes
import json
import shutil
import subprocess
import sys

import numpy as np
import pytest

from llc_design.control.digital_loop import (
    PIControllerConfig, PIFControllerConfig, TwoP2ZControllerConfig,
)
from pfc_design.vienna import illustrative_vienna_config, build_vienna_control_lab_analysis
from power_codegen import generate_vienna_control_code


F = np.float32
CTRL_FIELDS = (
    "kind", "kp", "ki2", "alpha", "out_min", "out_max", "error_prev",
    "i_state", "out_prev", "b0", "b1", "b2", "a1", "a2", "x1", "x2", "y1", "y2",
)
STATE_FIELDS = (
    "reference_div", "voltage_div", "balance_div", "gcmd", "balance",
    "ia_ref", "ib_ref", "ic_ref", "ia_ref_prev", "ib_ref_prev", "ic_ref_prev",
    "ff_a", "ff_b", "ff_c", "pi_a", "pi_b", "pi_c",
)
NSTATE = 5 * len(CTRL_FIELDS) + len(STATE_FIELDS)
NOUTPUT = 16


def _nominal_conductance(cfg):
    p = cfg.power_stage
    return p.output_power_w / (p.efficiency * p.line_line_rms_v**2)


def _alpha(c):
    # Derive the backward-Euler LPF coefficient, independently of c.alpha.
    return 1.0 if c.lpf_cutoff_hz <= 0 else c.sample_time_s / (
        c.sample_time_s + 1.0 / (2.0 * np.pi * c.lpf_cutoff_hz))


def _controller_state(c, initial=0.0):
    state = dict.fromkeys(CTRL_FIELDS, F(0))
    state.update(out_min=F(c.output_min), out_max=F(c.output_max),
                 out_prev=F(initial), y1=F(initial), y2=F(initial))
    if isinstance(c, TwoP2ZControllerConfig):
        state["kind"] = F(2)
        state.update({name: F(getattr(c, name)) for name in ("a1", "a2", "b0", "b1", "b2")})
    else:
        state.update(kind=F(isinstance(c, PIFControllerConfig)), kp=F(c.kp),
                     ki2=F(c.sample_time_s / (2 * c.ti_s)),
                     alpha=F(_alpha(c) if isinstance(c, PIFControllerConfig) else 1),
                     i_state=F(initial / max(c.kp, 1e-12)))
    return state


def _step(s, error):
    """Scalar float32 difference equations, with conditional integration."""
    e = F(error)
    if s["kind"] == 2:
        terms = [-F(s["a1"] * s["y1"]), -F(s["a2"] * s["y2"]),
                 F(s["b0"] * e), F(s["b1"] * s["x1"]), F(s["b2"] * s["x2"])]
        raw = terms[0]
        for term in terms[1:]:
            raw = F(raw + term)
        y = F(np.clip(raw, s["out_min"], s["out_max"]))
        s["x2"], s["x1"] = s["x1"], e
        s["y2"], s["y1"] = s["y1"], y
    else:
        trial = F(s["i_state"] + F(s["ki2"] * F(e + s["error_prev"])))
        raw = F(s["kp"] * F(e + trial))
        limited = F(np.clip(raw, s["out_min"], s["out_max"]))
        drives_further_out = (raw > s["out_max"] and e > 0) or (raw < s["out_min"] and e < 0)
        if not drives_further_out:
            s["i_state"] = trial
        y = limited
        if s["kind"] == 1:
            y = F(F(F(1 - s["alpha"]) * s["out_prev"]) + F(s["alpha"] * limited))
            s["out_prev"] = y
        s["error_prev"] = e
    return y


def _dividers(cfg):
    fw = cfg.firmware
    return tuple(round(fw.current_loop_rate_hz / rate) for rate in (
        fw.reference_rate_hz, fw.voltage_loop_rate_hz, fw.balance_loop_rate_hz))


def _initial_snapshot(cfg):
    states = [_controller_state(cfg.current_controller) for _ in range(3)]
    states += [_controller_state(cfg.voltage_controller, _nominal_conductance(cfg)),
               _controller_state(cfg.balance_controller)]
    scalars = [n - 1 for n in _dividers(cfg)] + [_nominal_conductance(cfg)] + [0] * 13
    return np.array([s[key] for s in states for key in CTRL_FIELDS] + scalars, dtype=np.float32)


def _wrapper():
    # Serialize named members to avoid assuming any host C struct layout.
    controller_dump = "\n".join(f"    out[{i}] = (float)s->{name};" for i, name in enumerate(CTRL_FIELDS))
    state_dump = "\n".join(f"    out[{5 * len(CTRL_FIELDS) + i}] = (float)s->{name};"
                           for i, name in enumerate(STATE_FIELDS))
    return f'''#include "vienna_control.h"
#if defined(_WIN32)
#define PROBE_API __declspec(dllexport)
#else
#define PROBE_API
#endif

static void dump_controller(const CTRL_State_t *s, float *out)
{{
{controller_dump}
}}
static void dump_state(const Vienna_State_t *s, float *out)
{{
    dump_controller(&s->current_a, out);
    dump_controller(&s->current_b, out + {len(CTRL_FIELDS)});
    dump_controller(&s->current_c, out + {2 * len(CTRL_FIELDS)});
    dump_controller(&s->voltage_ctrl, out + {3 * len(CTRL_FIELDS)});
    dump_controller(&s->balance_ctrl, out + {4 * len(CTRL_FIELDS)});
{state_dump}
}}
PROBE_API void initial(float *out)
{{
    Vienna_State_t s;
    Vienna_Init(&s);
    dump_state(&s, out);
}}
PROBE_API void replay(const float *inputs, float *outputs, float *states, int n, float *reset)
{{
    Vienna_State_t s;
    int k;
    Vienna_Init(&s);
    for(k = 0; k < n; ++k)
    {{
        const float *x = inputs + 11 * k;
        float *y = outputs + {NOUTPUT} * k;
        Vienna_Input_t in = {{0}};
        Vienna_Output_t out = {{0}};
        in.va = x[0]; in.vb = x[1]; in.vc = x[2];
        in.ia = x[3]; in.ib = x[4]; in.ic = x[5];
        in.vdc_pos = x[6]; in.vdc_neg = x[7]; in.vdc_ref = x[8];
        in.enable = (x[9] > 0.5f) ? 1u : 0u;
        if(x[10] > 0.5f) {{ Vienna_Reset(&s); }}
        Vienna_ControlStep(&in, &out, &s);
        y[0] = out.duty_a; y[1] = out.duty_b; y[2] = out.duty_c;
        y[3] = out.modulation_a; y[4] = out.modulation_b; y[5] = out.modulation_c;
        y[6] = out.ia_ref; y[7] = out.ib_ref; y[8] = out.ic_ref;
        y[9] = out.pi_a; y[10] = out.pi_b; y[11] = out.pi_c;
        y[12] = out.gcmd; y[13] = out.balance_cmd;
        y[14] = out.third_harmonic; y[15] = (float)out.enable;
        dump_state(&s, states + {NSTATE} * k);
    }}
    Vienna_Reset(&s);
    dump_state(&s, reset);
}}
PROBE_API void recurrence(int loop, const float *errors, float *outputs, int n)
{{
    Vienna_State_t s;
    CTRL_State_t *controller;
    int k;
    Vienna_Init(&s);
    controller = (loop == 0) ? &s.current_a : ((loop == 1) ? &s.voltage_ctrl : &s.balance_ctrl);
    for(k = 0; k < n; ++k) {{ outputs[k] = CTRL_Step(controller, errors[k]); }}
}}
'''


KINDS = ("PI", "PIF", "2P2Z-integrator", "2P2Z-general")
RATES = ((1, 1, 1), (2, 5, 5), (3, 4, 5))  # reference, voltage, balance


@pytest.fixture(scope="module", params=[(k, d) for k in KINDS for d in RATES],
                ids=lambda case: f"{case[0]}-div{'-'.join(map(str, case[1]))}")
def runtime(request, tmp_path_factory):
    compiler = shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        pytest.skip("compiled Vienna runtime tests require a host C compiler")
    kind, (dr, dv, db) = request.param
    cfg = illustrative_vienna_config()
    fw = replace(cfg.firmware, reference_rate_hz=cfg.firmware.current_loop_rate_hz / dr,
                 voltage_loop_rate_hz=cfg.firmware.current_loop_rate_hz / dv,
                 balance_loop_rate_hz=cfg.firmware.current_loop_rate_hz / db)
    voltage = replace(cfg.voltage_controller, sample_time_s=1 / fw.voltage_loop_rate_hz)
    if kind == "PIF":
        voltage = PIFControllerConfig(voltage.kp, voltage.ti_s, 230.0, voltage.sample_time_s,
                                      voltage.output_min, voltage.output_max)
    elif kind.startswith("2P2Z"):
        # Explicit Tustin PI coefficients; do not read a generated transfer function.
        k = voltage.sample_time_s / (2 * voltage.ti_s)
        a1, a2 = (-1.0, 0.0) if kind == "2P2Z-integrator" else (-0.7, 0.1)
        voltage = TwoP2ZControllerConfig(voltage.kp * (1 + k), voltage.kp * (-1 + k),
                                         0.0, a1, a2, voltage.sample_time_s,
                                         voltage.output_min, voltage.output_max)
    cfg = replace(cfg, firmware=fw, voltage_controller=voltage,
                  balance_controller=replace(cfg.balance_controller, sample_time_s=1 / fw.balance_loop_rate_hz),
                  frequency_points=200)
    folder = tmp_path_factory.mktemp("vienna-startup")
    # Some deliberate arbitrary poles/rates fail the model gate; explicitly draft.
    result = generate_vienna_control_code(build_vienna_control_lab_analysis(cfg), folder, require_stable=False)
    (folder / "probe.c").write_text(_wrapper(), encoding="utf-8")
    if sys.platform == "win32":
        library_name, shared_flags = "probe.dll", ["-shared"]
    elif sys.platform == "darwin":
        library_name, shared_flags = "probe.dylib", ["-dynamiclib", "-fPIC"]
    else:
        library_name, shared_flags = "probe.so", ["-shared", "-fPIC"]
    subprocess.run([compiler, "-std=c99", "-pedantic", "-Wall", "-Wextra", "-Werror",
                    "-Wdouble-promotion", "-O0", "-ffp-contract=off", *shared_flags,
                    "control_runtime.c", "vienna_control.c", "isr_template.c", "probe.c",
                    "-lm", "-o", library_name], cwd=folder, check=True, capture_output=True, text=True)
    lib = ctypes.CDLL(str(folder / library_name))
    pointer = ctypes.POINTER(ctypes.c_float)
    lib.initial.argtypes = [pointer]
    lib.replay.argtypes = [pointer, pointer, pointer, ctypes.c_int, pointer]
    lib.recurrence.argtypes = [ctypes.c_int, pointer, pointer, ctypes.c_int]
    for func in (lib.initial, lib.replay, lib.recurrence):
        func.restype = None
    return cfg, lib, result


def _ptr(array):
    return array.ctypes.data_as(ctypes.POINTER(ctypes.c_float))


def _replay(lib, inputs):
    inputs = np.ascontiguousarray(inputs, dtype=np.float32)
    outputs = np.empty((len(inputs), NOUTPUT), dtype=np.float32)
    states = np.empty((len(inputs), NSTATE), dtype=np.float32)
    reset = np.empty(NSTATE, dtype=np.float32)
    lib.replay(_ptr(inputs), _ptr(outputs), _ptr(states), len(inputs), _ptr(reset))
    return outputs, states, reset


def _inputs(n):
    rng = np.random.default_rng(48024)
    phase = 2 * np.pi * 50 * np.arange(n)[:, None] / 50000 + [0, -2 * np.pi / 3, 2 * np.pi / 3]
    volts = 300 * np.sin(phase)
    current = 0.06 * volts + rng.normal(0, 0.1, (n, 3))
    bus = 350 + rng.normal(0, 0.4, (n, 2))
    return np.c_[volts, current, bus, np.full(n, 700), np.ones(n), np.zeros(n)].astype(np.float32)


def _independent_replay(cfg, inputs):
    """Apply the equations on t = k / f_current, reset the local epoch on restart."""
    p, fw = cfg.power_stage, cfg.firmware
    dr, dv, db = _dividers(cfg)
    def reset():
        return ([_controller_state(cfg.current_controller) for _ in range(3)],
                _controller_state(cfg.voltage_controller, _nominal_conductance(cfg)),
                _controller_state(cfg.balance_controller), F(_nominal_conductance(cfg)),
                F(0), np.zeros(3, dtype=np.float32), np.zeros(3, dtype=np.float32))
    current, voltage, balance, g, b, refs, ff = reset()
    epoch = 0
    outputs = []
    for index, x in enumerate(inputs):
        if x[10] or not x[9]:
            current, voltage, balance, g, b, refs, ff = reset()
            epoch = index if x[9] else index + 1
        if not x[9]:
            outputs.append(np.zeros(NOUTPUT, dtype=np.float32))
            continue
        tick = index - epoch
        if tick % dv == 0:
            g = max(_step(voltage, F(x[8] - F(x[6] + x[7]))), F(0))
        if tick % db == 0:
            b = F(np.clip(_step(balance, F(x[6] - x[7])), F(-fw.balance_injection_limit), F(fw.balance_injection_limit)))
        if tick % dr == 0:
            previous = refs
            refs = np.array([F(g * v) for v in x[:3]], dtype=np.float32)
            if fw.inductor_voltage_drop_feedforward_enabled:
                ff = np.array([F(F(F(p.phase_series_resistance_ohm) * refs[j]) +
                                F(F(p.boost_inductance_h) * F(F(refs[j] - previous[j]) / F(1 / fw.reference_rate_hz))))
                               for j in range(3)], dtype=np.float32)
        pi = np.array([_step(current[j], F(refs[j] - x[3 + j])) for j in range(3)], dtype=np.float32)
        half = max(F(F(0.5) * F(x[6] + x[7])), F(25))
        third = F(F(0.5) * F(max(x[:3]) + min(x[:3]))) if fw.third_harmonic_injection_enabled else F(0)
        mods = []
        active = []
        for j in range(3):
            feedforward = F(F(F(x[j] - third) - ff[j]) / half)
            m = F(np.clip(F(F(feedforward - pi[j]) - b), F(-p.modulation_limit), F(p.modulation_limit)))
            a = abs(m)
            minimum = F(p.minimum_effective_pulse_s * p.switching_frequency_hz)
            if 0 < a < minimum:
                a = F(0)
            elif 0 < F(1 - a) < minimum:
                a = F(1)
            active.append(a)
            mods.append(F(0) if a == 0 else a if m >= 0 else -a)
        outputs.append(np.r_[F(1) - np.array(active), mods, refs, pi, g, b, third, F(1)])
    return np.asarray(outputs, dtype=np.float32)


def test_init_and_reset_restore_every_controller_and_runtime_member(runtime):
    cfg, lib, result = runtime
    initial = np.empty(NSTATE, dtype=np.float32)
    lib.initial(_ptr(initial))
    np.testing.assert_array_equal(initial.view(np.uint32), _initial_snapshot(cfg).view(np.uint32))
    inputs = _inputs(137)
    # Dirty every controller, reference history, feedforward, and task counter.
    inputs[20:45, 8] += 40000
    inputs[45:70, 8] -= 40000
    inputs[90:110, 6] += 300
    _, dirty, reset = _replay(lib, inputs)
    assert np.any(dirty[-1] != initial)
    np.testing.assert_array_equal(reset.view(np.uint32), initial.view(np.uint32))
    # Disable is also a reset, including preloaded voltage state and phase.
    inputs[-2:, 9] = 0
    outputs, states, _ = _replay(lib, inputs)
    np.testing.assert_array_equal(outputs[-2:], 0)
    for snapshot in states[-2:]:
        np.testing.assert_array_equal(snapshot.view(np.uint32), initial.view(np.uint32))
    metadata = json.loads(result.files["snapshot"].read_text(encoding="utf-8"))
    assert metadata["runtime_initialization"]["voltage_controller_output_preload_a_per_v"] == pytest.approx(_nominal_conductance(cfg), rel=1e-14)
    assert metadata["draft_override"] and not metadata["hardware_validated"] and not metadata["bsp_generated"]
    assert metadata["validation_scope"] == "FINITE_BAND_MODEL_SCREEN_ONLY"


def test_nominal_first_step_preload_is_state_not_persistent_feedforward(runtime):
    cfg, lib, _ = runtime
    inputs = np.tile(np.array([100, -50, -50, 0, 0, 0, 350, 350, 700, 1, 0], dtype=np.float32), (16, 1))
    outputs, _, _ = _replay(lib, inputs)
    c = cfg.voltage_controller
    g0 = _nominal_conductance(cfg)
    # At zero error the PI/PIF integral holds G0; arbitrary 2P2Z poles instead
    # produce y[0] = -(a1+a2)*G0. This excludes adding G0 on every update.
    first = -(c.a1 + c.a2) * g0 if isinstance(c, TwoP2ZControllerConfig) else g0
    assert outputs[0, 12] == pytest.approx(first, rel=2e-7)
    np.testing.assert_allclose(outputs[0, 6:9], first * inputs[0, :3], rtol=2e-7)
    np.testing.assert_allclose(outputs, _independent_replay(cfg, inputs), rtol=2e-6, atol=2e-6)


def test_first_step_multirate_reset_and_reenable_match_independent_oracle(runtime):
    cfg, lib, _ = runtime
    inputs = _inputs(180)
    inputs[31, 10] = 1  # Explicit Reset at a non-periodic tick.
    inputs[65:68, 9] = 0  # Repeated disable must not consume the t0 phase.
    inputs[95:112, 8] += 40000
    inputs[112:129, 8] -= 40000
    inputs[140:151, 6] += 300
    outputs, states, _ = _replay(lib, inputs)
    np.testing.assert_allclose(outputs, _independent_replay(cfg, inputs), rtol=2e-6, atol=2e-6)
    # Derive counter phase from sample indices, independently of C increments.
    epoch = 0
    offset = 5 * len(CTRL_FIELDS)
    for index, x in enumerate(inputs):
        if x[10] or not x[9]:
            epoch = index if x[9] else index + 1
        expected = [(index - epoch) % d if x[9] else d - 1 for d in _dividers(cfg)]
        np.testing.assert_array_equal(states[index, offset:offset + 3], expected)


@pytest.mark.parametrize("sequence", ("impulse", "step", "random", "saturation_recovery"))
def test_compiled_recurrence_uses_correct_loop_initial_state(runtime, sequence):
    cfg, lib, _ = runtime
    rng = np.random.default_rng(29419)
    errors = {
        "impulse": np.r_[1, np.zeros(127)],
        "step": np.ones(128),
        "random": rng.normal(0, 0.2, 256),
        "saturation_recovery": np.r_[np.full(80, 1e5), np.full(80, -1e5), np.zeros(80), rng.normal(0, 0.1, 80)],
    }[sequence].astype(np.float32)
    for loop, c in enumerate((cfg.current_controller, cfg.voltage_controller, cfg.balance_controller)):
        state = _controller_state(c, _nominal_conductance(cfg) if loop == 1 else 0)
        expected = np.array([_step(state, e) for e in errors], dtype=np.float32)
        actual = np.empty_like(errors)
        lib.recurrence(loop, _ptr(errors), _ptr(actual), len(errors))
        np.testing.assert_array_equal(actual, expected)
