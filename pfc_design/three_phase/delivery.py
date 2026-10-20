"""Frozen-source dq research/report and canonical DF2T C99 delivery."""

import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np

from .control import build_three_phase_loops, request_identity
from .exact_control import DF2T, ExactController, controller_for
from .workspace import atomic_json, save_session, scan_operating_points


def render_c99(config, *, scalar="double"):
    if scalar not in ("double", "float"):
        raise ValueError("C99 scalar must be double or float")
    identity = request_identity(config)
    lines = [
        "/* dq exact controller reference, source " + identity + " */",
        f"/* SOFTWARE_MODEL_ONLY. {scalar} C99 DF2T; no PLL, PWM, sensing, protection or BSP. */",
        "/* Advance tracking correction enters state[0]; disabled freezes all states. */",
        "/* Call Peek(error), compute external saturation/tracking, then Advance once. */",
        "/* Native voltage Reset initial value = P/(1.5*Vd); imported controllers start at zero. */",
        "#ifndef DQ_EXACT_CONTROLLERS_H",
        "#define DQ_EXACT_CONTROLLERS_H",
        "#include <stddef.h>",
    ]
    if scalar == "float":
        lines += [
            "/* Float products and differences round separately to match the float32 recurrence. */",
            "/* Volatile intermediates prevent FMA contraction across these rounding boundaries. */",
        ]
    for loop in ("current", "voltage"):
        c = controller_for(config, loop)
        prefix = "DQ_" + loop.upper()
        n = c.order
        fmt = lambda x: (
            format(float(x), ".17g")
            + (
                ".0"
                if float(x).is_integer() and "e" not in format(float(x), ".17g")
                else ""
            )
        )
        lines += [
            "/* "
            + json.dumps(c.source, ensure_ascii=True).replace("*/", "* /")
            + " */",
            f"#define {prefix}_ORDER {n}",
            f"#define {prefix}_FS_HZ ({fmt(c.sample_rate_hz)})",
            f"typedef struct {{ double z[{max(n, 1)}]; }} {prefix}_STATE;",
            f"static inline void {prefix}_Reset({prefix}_STATE *s, double initial) {{",
            f"  size_t j; for(j=0;j<{max(n, 1)};j++) s->z[j]=0.0;",
            "  s->z[0]=initial;" if n else "  (void)initial;",
            "}",
            f"static inline double {prefix}_Peek(const {prefix}_STATE *s, double x) {{",
        ]
        if scalar == "float":
            lines += [
                f"  const volatile double product=({fmt(c.b[0])})*x;",
                "  return product" + (" + s->z[0];" if n else ";"),
            ]
        else:
            lines += [
                f"  return ({fmt(c.b[0])})*x" + (" + s->z[0];" if n else ";"),
            ]
        lines += [
            "}",
            f"static inline double {prefix}_Advance({prefix}_STATE *s, double x, double correction, int enabled) {{",
            f"  const double y={prefix}_Peek(s,x);",
            "  if(enabled) {",
        ]
        for j in range(n):
            bj = c.b[j + 1] if j + 1 < len(c.b) else 0.0
            aj = c.a[j + 1] if j + 1 < len(c.a) else 0.0
            if scalar == "float":
                lines += [
                    "    {",
                    f"      const volatile double bx=({fmt(bj)})*x;",
                    f"      const volatile double ay=({fmt(aj)})*y;",
                    "      const volatile double difference=bx-ay;",
                    f"      s->z[{j}]=difference"
                    + (f"+s->z[{j + 1}];" if j + 1 < n else ";"),
                    "    }",
                ]
            else:
                lines.append(
                    f"    s->z[{j}]=({fmt(bj)})*x-({fmt(aj)})*y"
                    + (f"+s->z[{j + 1}];" if j + 1 < n else ";")
                )
        lines += [
            "    s->z[0]+=correction;" if n else "    (void)correction;",
            "  }",
            "  return y;",
            "}",
        ]
    lines += ["#endif", ""]
    rendered = "\n".join(lines)
    if scalar == "float":
        import re

        rendered = rendered.replace("double", "float")
        # Constants use the float32 rounding of the unchanged canonical coefficients.
        rendered = re.sub(
            r"(?<![A-Za-z0-9_])(-?(?:\d+\.\d*|\d*\.\d+)(?:[eE][+-]?\d+)?)(?![A-Za-z0-9_])",
            lambda m: m.group(0) + "F",
            rendered,
        )
    return rendered


def verify_c99(config, header, compiler="gcc", *, scalar="double"):
    compiler = shutil.which(compiler)
    if compiler is None:
        return {"status": "NOT_RUN", "reason": "C99 host compiler unavailable"}
    rng = np.random.default_rng(12591)
    # Independent test signals include impulse, step, random, clipping/recovery and freeze.
    values = np.r_[
        1.0,
        np.zeros(31),
        np.ones(64),
        rng.normal(size=256),
        np.ones(48) * 20,
        np.zeros(48),
    ]
    lines = ["#include <stdio.h>", '#include "controllers.h"', "int main(void) {"]
    expected = []
    for loop in ("current", "voltage"):
        c = controller_for(config, loop)
        reference = DF2T(c)
        if scalar == "float":
            reference.state = reference.state.astype(np.float32)
            b32, a32 = (
                np.asarray(c.b, dtype=np.float32),
                np.asarray(c.a, dtype=np.float32),
            )
        prefix = "DQ_" + loop.upper()
        lines += ["{", f"{prefix}_STATE s; {prefix}_Reset(&s,0.0);"]
        for index, value in enumerate(values):
            if scalar == "float":
                value = np.float32(value)
                y = np.float32(
                    b32[0] * value + (reference.state[0] if c.order else np.float32(0))
                )
            else:
                y = reference.output(value)
            correction = (
                float(np.clip(y, -2.0, 2.0) - y) * 0.1 if 352 <= index < 420 else 0.0
            )
            enabled = not 380 <= index < 390
            if scalar == "float":
                correction = np.float32(correction)
                expected.append(float(y))
                if enabled:
                    for j in range(c.order):
                        bj = b32[j + 1] if j + 1 < len(b32) else np.float32(0)
                        aj = a32[j + 1] if j + 1 < len(a32) else np.float32(0)
                        reference.state[j] = np.float32(
                            np.float32(bj * value - aj * y)
                            + (
                                reference.state[j + 1]
                                if j + 1 < c.order
                                else np.float32(0)
                            )
                        )
                    if c.order:
                        reference.state[0] = np.float32(reference.state[0] + correction)
            else:
                expected.append(reference.advance(value, correction, enabled=enabled))
            lines.append(
                f'printf("%.17g\\n", {prefix}_Advance(&s,{value:.17g},{correction:.17g},{int(enabled)}));'
            )
        lines.append("}")
    lines += ["return 0;", "}"]
    with tempfile.TemporaryDirectory(prefix="dq-c99-") as root:
        root = Path(root)
        (root / "controllers.h").write_text(header, encoding="utf-8")
        (root / "verify.c").write_text("\n".join(lines), encoding="utf-8")
        compiled = subprocess.run(
            [
                compiler,
                "-std=c99",
                "-Wall",
                "-Wextra",
                "-pedantic",
                "-O0",
                str(root / "verify.c"),
                "-o",
                str(root / "verify"),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if compiled.returncode:
            return {"status": "FAIL", "reason": compiled.stderr}
        run = subprocess.run(
            [str(root / "verify")],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if run.returncode:
            return {"status": "FAIL", "reason": run.stderr}
        actual = np.asarray([float(v) for v in run.stdout.splitlines()])
        expected = np.asarray(expected)
        error = (
            float(np.max(abs(actual - expected)))
            if actual.shape == expected.shape
            else float("inf")
        )
        passed = actual.shape == expected.shape and np.allclose(
            actual,
            expected,
            rtol=(2e-6 if scalar == "float" else 2e-13),
            atol=(2e-6 if scalar == "float" else 2e-12),
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "compiler": compiler,
            "samples_per_controller": len(values),
            "maximum_absolute_error": error,
            "scope": f"{scalar} canonical DF2T impulse/step/random/external tracking/freeze; no target ABI or hardware acceptance",
        }


PYTHON_RESEARCH = '''"""Standalone dq frozen-source response replay; requires only Python + NumPy."""
import json
from pathlib import Path
import numpy as np
root = Path(__file__).resolve().parent
data = json.loads((root / 'research.json').read_text(encoding='utf-8'))
def tf(b,a,q):
    return sum(v*q**j for j,v in enumerate(b))/sum(v*q**j for j,v in enumerate(a))
for name, loop in data['loops'].items():
    f=np.array(loop['frequency_hz']); ts=loop['sample_time_s']; q=np.exp(-2j*np.pi*f*ts)
    a=np.array(loop['plant_A']); b=np.array(loop['plant_B']); c=np.array(loop['plant_C'])
    g=np.array([c@np.linalg.solve((1/qi)*np.eye(len(a))-a,b) for qi in q])
    controller=tf(loop['controller_b'],loop['controller_a'],q)
    sense=tf(loop['sense_b'],loop['sense_a'],q)
    forward=g*controller*q**loop['command_delay_ticks']; result=forward*sense
    reference=np.array(loop['open_real'])+1j*np.array(loop['open_imag'])
    np.testing.assert_allclose(result,reference,rtol=2e-11,atol=2e-11)
    np.testing.assert_allclose(result/(1+result)+1/(1+result),1,rtol=2e-13,atol=2e-13)
    poles=np.linalg.eigvals(np.array(loop['closed_A']))
    assert bool(np.all(abs(poles)<1))==loop['stable']
    assert f[-1]<.5/ts
    print(name, 'PASS', 'max relative response error', np.max(abs(result-reference)/np.maximum(abs(reference),1e-30)))
print('source',data['source_identity'])
'''

MATLAB_RESEARCH = """% Frozen dq sampled-state replay. MATLAB/Octave execution is not implied.
root=fileparts(mfilename('fullpath')); data=jsondecode(fileread(fullfile(root,'research.json')));
names=fieldnames(data.loops);
for ni=1:numel(names)
 x=data.loops.(names{ni}); f=x.frequency_hz(:); q=exp(-2i*pi*f*x.sample_time_s);
 g=zeros(size(q));
 for k=1:numel(q), g(k)=x.plant_C(:).' * ((eye(size(x.plant_A))/q(k)-x.plant_A)\\x.plant_B(:)); end
 cb=x.controller_b(:); ca=x.controller_a(:); sb=x.sense_b(:); sa=x.sense_a(:);
 c=polyval(flipud(cb),q)./polyval(flipud(ca),q); s=polyval(flipud(sb),q)./polyval(flipud(sa),q);
 L=g.*c.*q.^x.command_delay_ticks.*s; ref=x.open_real(:)+1i*x.open_imag(:);
 assert(max(abs(L-ref)./max(abs(ref),1e-30))<2e-10);
 assert(max(abs(L./(1+L)+1./(1+L)-1))<2e-12);
 assert(all(f<0.5/x.sample_time_s));
 assert(all(abs(eig(x.closed_A))<1)==x.stable);
 fprintf('%s PASS\\n',names{ni});
end
disp(data.source_identity);
"""


def export_bundle(
    destination, config, analysis, *, retained=(), targets=None, nonlinear_result=None
):
    identity = request_identity(config)
    if analysis.get("source_identity") != identity:
        raise ValueError("stale analysis: export requires the current exact source")
    if (
        nonlinear_result is not None
        and nonlinear_result.metrics.get("source_identity") != identity
    ):
        raise ValueError("nonlinear result belongs to a different source")
    destination = Path(destination)
    if destination.exists():
        raise ValueError(
            "choose a new output directory to avoid mixing source identities"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=destination.parent, prefix=".dq-export-"
    ) as stage:
        stage = Path(stage)
        header = render_c99(config)
        verification = verify_c99(config, header)
        if verification["status"] == "FAIL":
            raise ValueError("C99 host verification failed: " + str(verification))
        (stage / "controllers.h").write_text(header, encoding="utf-8")
        float_header = render_c99(config, scalar="float")
        float_verification = verify_c99(config, float_header, scalar="float")
        if float_verification["status"] == "FAIL":
            raise ValueError(
                "float32 C99 host verification failed: " + str(float_verification)
            )
        (stage / "controllers_float32.h").write_text(float_header, encoding="utf-8")
        report = {
            key: value
            for key, value in analysis.items()
            if key not in ("current", "voltage")
        }
        report["loops"] = {
            key: analysis[key].summary() for key in ("current", "voltage")
        }
        report["c99_verification"] = verification
        report["c99_float32_verification"] = float_verification
        report["float32_controller_coefficients"] = {
            key: {
                "b": np.asarray(controller_for(config, key).b, dtype=np.float32)
                .astype(float)
                .tolist(),
                "a": np.asarray(controller_for(config, key).a, dtype=np.float32)
                .astype(float)
                .tolist(),
            }
            for key in ("current", "voltage")
        }
        quantized_config = replace(
            config,
            **{
                key + "_controller": ExactController(
                    tuple(
                        np.asarray(
                            controller_for(config, key).b, dtype=np.float32
                        ).astype(float)
                    ),
                    tuple(
                        np.asarray(
                            controller_for(config, key).a, dtype=np.float32
                        ).astype(float)
                    ),
                    controller_for(config, key).sample_rate_hz,
                    controller_for(config, key).source
                    + " / float32 coefficient rounding",
                )
                for key in ("current", "voltage")
            },
        )
        try:
            quantized_analysis = build_three_phase_loops(quantized_config)
            report["float32_local_reanalysis"] = {
                "status": "ANALYZED",
                "scope": "coefficient rounding only; real-time float32 state arithmetic and hardware effects are not linearized",
                "source_identity": quantized_analysis["source_identity"],
                "loops": {
                    key: {
                        "summary": quantized_analysis[key].summary(),
                        "maximum_relative_return_ratio_change": float(
                            np.max(
                                abs(
                                    quantized_analysis[key].open_response
                                    - analysis[key].open_response
                                )
                                / np.maximum(abs(analysis[key].open_response), 1e-30)
                            )
                        ),
                    }
                    for key in ("current", "voltage")
                },
            }
        except (ValueError, np.linalg.LinAlgError) as exc:
            report["float32_local_reanalysis"] = {
                "status": "INVALID",
                "reason": str(exc),
            }
        report["matlab_verification"] = "NOT_RUN"
        report["retained_operating_point_scan"] = scan_operating_points(
            config, retained
        )
        if nonlinear_result is not None:
            report["nonlinear_metrics"] = nonlinear_result.metrics
            report["nonlinear_warnings"] = nonlinear_result.warnings
        atomic_json(stage / "report.json", report)
        research = {"source_identity": identity, "loops": {}}
        for key in ("current", "voltage"):
            loop = analysis[key]
            sense = getattr(config, key + "_sense").controller(1 / loop.sample_time_s)
            delay = (
                config.pwm_delay_ticks
                if key == "current"
                else config.voltage_delay_ticks
            )
            research["loops"][key] = {
                "frequency_hz": loop.frequency_hz.tolist(),
                "sample_time_s": loop.sample_time_s,
                "plant_A": loop.plant_state_a.tolist(),
                "plant_B": loop.plant_state_b.tolist(),
                "plant_C": loop.plant_state_c.tolist(),
                "controller_b": loop.controller_b.tolist(),
                "controller_a": loop.controller_a.tolist(),
                "sense_b": sense.b,
                "sense_a": sense.a,
                "command_delay_ticks": delay,
                "open_real": loop.open_response.real.tolist(),
                "open_imag": loop.open_response.imag.tolist(),
                "closed_A": loop.closed_state_a.tolist(),
                "stable": loop.stable,
            }
            values = [loop.frequency_hz]
            labels = ["frequency_hz"]
            for name, response in (
                ("plant", loop.physical_plant_response),
                ("controller", loop.controller_response),
                ("sense", loop.sense_response),
                ("command", loop.command_response),
                ("forward", loop.forward_response),
                ("L", loop.open_response),
                ("T", loop.closed_response),
                ("S", loop.sensitivity),
            ):
                values += [response.real, response.imag]
                labels += [name + "_real", name + "_imag"]
            np.savetxt(
                stage / (key + ".csv"),
                np.column_stack(values),
                delimiter=",",
                header=",".join(labels),
                comments="",
            )
        atomic_json(stage / "research.json", research)
        (stage / "research.py").write_text(PYTHON_RESEARCH, encoding="utf-8")
        (stage / "research.m").write_text(MATLAB_RESEARCH, encoding="utf-8")
        save_session(stage / "session.json", config, retained, targets)
        lines = [
            "# Two-level dq control-workspace report",
            "",
            "Source identity: " + identity,
            "",
            "## Physical operating point and limits",
            "",
            analysis["scope"],
            "",
            analysis["equilibrium_scope"],
            "",
            f"Grid {config.line_line_rms_v:g} V line RMS; regulated physical bus {analysis['operating_bus_v']:.9g} V; load {analysis['operating_power_w']:.9g} W; Id {analysis['operating_id_a']:.9g} A.",
            "",
            "Sensing: " + analysis["sensing"],
            "",
            "ADC headroom: " + json.dumps(analysis["adc_headroom"], ensure_ascii=False),
            "",
        ]
        for key in ("current", "voltage"):
            summary = analysis[key].summary()
            controller = controller_for(config, key)
            lines += [
                "## " + key + " loop",
                "",
                "Controller source: " + controller.source,
                "",
                "b (ascending z^-1): " + repr(controller.b),
                "a (ascending z^-1): " + repr(controller.a),
                "",
                f"Fs = {controller.sample_rate_hz:g} Hz; requested frequency range {analysis['frequency_request_hz']}; actual range {summary['frequency_domain_hz']} Hz.",
                "",
                f"Declared-state stability: {summary['closed_loop_stability']}; maximum pole radius {summary['max_pole_radius']:.12g}.",
                "",
                "All gain crossovers / phase margins: "
                + repr(
                    list(
                        zip(summary["gain_crossovers_hz"], summary["phase_margins_deg"])
                    )
                ),
                "All phase crossovers / signed gain margins: "
                + repr(
                    list(
                        zip(
                            summary["phase_crossovers_hz"],
                            summary["signed_gain_margins_db"],
                        )
                    )
                ),
                "",
                f"Ms = {summary['ms']:.9g}; Mt = {summary['mt']:.9g}. Missing crossings remain UNKNOWN; finite-band margins do not prove nonlinear/PLL stability.",
                "",
            ]
        lines += [
            "## Delivery verification",
            "",
            "Binary64 C99: " + verification["status"],
            "Float32 C99: " + float_verification["status"],
            "Standalone Python: PASS (verified before directory publication)",
            "MATLAB/Octave: NOT_RUN",
            "",
            "C99 is the canonical controller recurrence with external tracking/freeze. It omits sensing, PLL, modulation, hardware protection and BSP. Float32 rounding is disclosed in report.json; host verification is not target/hardware qualification.",
            "",
            "Exact coefficients, physical state matrices, individual frequency grids, T/S identities, and source-bound sessions are included. Retained operating points: "
            + str(len(retained))
            + ".",
            "",
        ]
        (stage / "report.md").write_text("\n".join(lines), encoding="utf-8")
        # Test the actual standalone delivered script, before publishing the directory.
        import sys

        run = subprocess.run(
            [sys.executable, str(stage / "research.py")],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if run.returncode:
            raise ValueError("standalone research self-check failed: " + run.stderr)
        (stage / "python-verification.txt").write_text(run.stdout, encoding="utf-8")
        manifest = {
            "source_identity": identity,
            "sha256": {
                file.name: hashlib.sha256(file.read_bytes()).hexdigest()
                for file in sorted(stage.iterdir())
                if file.is_file()
            },
        }
        atomic_json(stage / "manifest.json", manifest)
        shutil.copytree(stage, destination)
    return {"path": str(destination), "source_identity": identity, "c99": verification}
