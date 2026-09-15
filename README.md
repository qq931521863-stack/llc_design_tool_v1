# Power Design Toolkit

> **Power Electronics CAE + Digital Control Engineering Platform**  
> LLC · Totem-Pole PFC · Vienna PFC · Digital Control · FRA · C99 · ngspice · Web API

[![Build & Test](https://github.com/yangshuai2022-star/llc_design_tool_v1/actions/workflows/build-release.yml/badge.svg)](https://github.com/yangshuai2022-star/llc_design_tool_v1/actions/workflows/build-release.yml)
[![ngspice Smoke](https://github.com/yangshuai2022-star/llc_design_tool_v1/actions/workflows/ngspice-smoke.yml/badge.svg)](https://github.com/yangshuai2022-star/llc_design_tool_v1/actions/workflows/ngspice-smoke.yml)
![Python](https://img.shields.io/badge/Python-%3E%3D3.10-blue)
![Version](https://img.shields.io/badge/version-9.2.2-informational)
![License](https://img.shields.io/badge/license-GPL--3.0-green)

Power Design Toolkit is an engineering-oriented power-electronics design and digital-control platform. It is not limited to a single LLC calculator: the repository connects **power-stage design, multi-fidelity models, measured FRA data, exact discrete controller H(z), stability analysis, C99 code generation, switching-circuit verification and a browser API** in one codebase.

The project is built around three rules:

1. **One engineering model, multiple front ends.** Desktop GUI, CLI, web API and code generation reuse the same Python kernels instead of re-implementing equations in each UI.
2. **Exact digital control semantics.** The discrete transfer function designed by Control Tools is passed as its real `b/a` coefficients; it is not silently re-fit into another PI/PID form.
3. **Model boundaries are explicit.** FHA, HB, switched time-domain, measured FRA, small-signal Bode and ngspice each answer different questions. A software regression is not presented as hardware validation.

---

## 1. Four engineering workspaces

The desktop launcher opens four independent workspaces while preserving their state during switching.

| Workspace | Main purpose | Typical outputs |
| --- | --- | --- |
| **LLC Design** | Resonant-tank design, operating region, Q/ZVS, magnetics, SR, interleaving, waveforms, small signal and digital voltage loop | Lr/Cr/Lm, gain map, stress/loss data, FHA/HB/TD comparison, Gvf(s/z), PM/GM, closed-loop verification |
| **PFC Design** | Single-phase Totem-Pole PFC and three-phase Vienna PFC | Current/voltage-loop Bode, sensing-chain response, AC-cycle waveforms, switching waveforms, PF/THD, inductor design |
| **Control Tools** | General digital controller/filter design | H(s), exact H(z), Bode, step/impulse, poles/zeros, SOS/DF2T, single-file C99 `float32_t` |
| **FRA Loop Designer** | Controller design from measured frequency response | Bode100/SIMPLIS import, controller de-embedding, Equivalent Plant, Fc/PM/GM/Ms/Mt, Auto Design, model ID, C99 |

### End-to-end engineering chain

```text
Power-stage design / measured FRA
            │
            ├── theoretical plant G(s)/G(z)
            └── measured complex response G(jω)
                         │
                         ▼
                  Control Tools
                 exact controller H(z)
                         │
            ┌────────────┴────────────┐
            ▼                         ▼
   Small-signal loop            C99 float32_t
 sensing + ADC + PWM            DF2T / SOS export
 modulator + delays                    │
            │                          ▼
            ▼                    firmware integration
      PM / GM / S / T
            │
            ▼
 optional shared-ngspice
 switching closed-loop verification
```

---

## 2. LLC capability

### Electrical design and multi-fidelity analysis

The LLC workspace keeps several model levels instead of forcing one solver to do every job:

- **FHA** — fast fundamental-harmonic design baseline for tank synthesis, gain and operating-point work.
- **Nonlinear multi-harmonic HB** — self-consistent selected odd harmonics with rectifier commutation/polarity coupling.
- **Internal switched time-domain solver** — piecewise nonlinear switching model with periodic steady-state solution.
- **shared-ngspice closed-loop verification** — optional circuit-level switching correlation driven by the toolkit's discrete controller runtime.

The analysis also includes multi-load Q/gain maps, theoretical and engineering ZVS regions, operating trajectories, waveform reconstruction, transformer/resonant-inductor design, synchronous-rectifier timing/loss analysis, and fixed 2-phase/3-phase interleaved LLC analysis.

### Digital voltage-loop chain

The LLC digital loop explicitly models:

```text
Vref
 -> C(z)
 -> PCMD clamp
 -> PCMD/Frequency or PCMD/TBPRD FM block
 -> PWM / ZOH / timing delay
 -> Gvf
 -> Vout
 -> analog sensing
 -> ADC / multi-SOC / recursive averaging
 -> feedback
```

It reports all detected gain/phase crossovers, phase/gain margins, sensitivity/complementary sensitivity, delay envelopes, discrete closed-loop poles, command headroom and polarity diagnostics.

### ngspice boundary

The current ngspice layer is a **switching-correlation model**, not a vendor semiconductor sign-off model. The implemented LLC circuit contains controlled full-bridge switches, Lr/Cr, coupled transformer, rectifier, Cout and load with small physical damping for numerical conditioning. It does **not** claim release-accurate ringing, nonlinear Coss commutation, Qrr, switching loss or SR device stress.

See [ngspice closed-loop architecture](docs/NGSPICE_CLOSED_LOOP.md).

---

## 3. PFC capability

### Single-phase Totem-Pole PFC

The single-phase workspace includes:

- current inner loop and DC-bus voltage outer loop;
- explicit voltage/current sensing chains and ADC/digital delay;
- open-loop Bode and phase-budget inspection;
- full settled AC-line-cycle analysis;
- zero-crossing state analysis;
- selected-workpoint switching waveforms;
- PF/THD/integer-harmonic calculation;
- inductor design with DC-bias and loss checks.

### Three-phase Vienna PFC

The Vienna workspace includes:

- DC-voltage outer loop plus three stationary-frame ABC current loops;
- split-bus midpoint balance loop;
- three-phase voltage/current and split-bus sensing;
- common-mode / third-harmonic modulation support;
- full three-phase line-cycle solution and sector analysis;
- three-level switching waveforms;
- per-phase PF/THD and power analysis.

The Bode models are local linear models. Saturation, minimum pulse, zero-crossing state transitions, switching-state decisions and protection logic remain time-domain/nonlinear concerns and are not hidden inside one misleading LTI transfer function.

---

## 4. Control Tools

`power_control_tools` is the common controller/filter engine used across the project. Current controller structures include:

`Integrator · PI · PIF · PID · PIDF · Type-II · Type-III · Modified PI · Lead · Lag · 1P1Z · 2P2Z · 3P3Z · General`

Supported workflows include:

- analog controller/filter definition;
- Tustin, prewarped Tustin and backward-Euler discretization;
- exact discrete frequency response;
- Bode, step/impulse and pole-zero analysis;
- DF2T/SOS coefficient representation;
- single-file C99 `float32_t` export;
- generated-C verification against the Python reference when a C compiler is available.

Canonical coefficient convention:

```text
H(z) = (b0 + b1 z^-1 + ...)/(1 + a1 z^-1 + ...)

y[k] = Σ bi·x[k-i] - Σ aj·y[k-j]
```

See [Digital control architecture](docs/DIGITAL_CONTROL_ARCHITECTURE.md).

---

## 5. FRA Loop Designer

The FRA workspace is the measured-frequency-response path for controller redesign and verification.

### Import formats

- **Bode100 CSV**
- **SIMPLIS TXT** (`frequency / gain / phase`)
- **Generic frequency/gain/phase** text or CSV

### Measurement semantics are explicit

The GUI does not infer the physical meaning of a file merely from its columns. The user chooses whether the measurement is:

- **Plant TS** — the scan is already the plant/equivalent response; or
- **Complete Loop TS** — the scan includes the existing controller and requires de-embedding.

For a measured complete loop,

```text
L_meas(jω) = C_old(jω) · G_rest(jω)
G_rest(jω) = L_meas(jω) / C_old(jω)
L_new(jω)  = C_new(jω) · G_rest(jω)
```

Raw measured complex points remain the authority for crossover and stability metrics. Low-order rational identification is optional and is used for interpretation/model-derived time response, not to overwrite the measurement.

The analyzer detects **all** 0-dB and phase crossovers and reports critical/worst margins instead of assuming one crossover.

See [FRA Loop Designer engineering contract](docs/FRA_LOOP_DESIGNER.md).

---

## 6. Install and run

### Requirements

- Python **3.10+**
- Windows / macOS / Linux
- PySide6 only for the desktop GUI
- ngspice/libngspice only for the optional SPICE verification path

### Core / CLI

```bash
python -m pip install -e .
```

### Desktop GUI

```bash
python -m pip install -e ".[gui]"
power-design-gui
```

Equivalent source-tree entry:

```bash
python -m llc_design gui
```

### Web edition

```bash
python -m pip install -e ".[web]"
power-design-web
```

Then open `http://127.0.0.1:8000`.

### Development environment

```bash
python -m pip install -e ".[dev,gui,web]"
pytest
```

### Installed CLI entry points

```text
llc-design
pfc-design
pfc-control-lab
vienna-control-lab
power-control-tools
power-control-codegen
power-design-gui
power-design-web
```

Use `--help` on the individual CLI commands for subcommands and options.

---

## 7. Web/API edition

The browser UI does not duplicate the engineering equations. FastAPI calls the same Python analysis kernels used by the desktop/CLI path.

Current LLC endpoints include:

```text
GET  /api/health
GET  /api/llc/defaults
POST /api/llc/analyze
GET  /api/llc/cores
POST /api/llc/optimize
POST /api/llc/report
POST /api/llc/report.xlsx
GET  /docs
```

Control API routes are mounted from `backend.api.control` into the same application.

Deployment notes: [docs/WEB_DEPLOYMENT.md](docs/WEB_DEPLOYMENT.md).

---

## 8. Repository layout

```text
llc_design/            LLC engineering, models, dynamics, control, GUI, reports
pfc_design/            Totem-Pole PFC + Vienna analysis and GUI
power_control_tools/   Generic controller/filter/FRA engine
power_codegen/         Portable C99 float32_t control-code generation
power_sim/             Backend-neutral digital runtime + SPICE integration
backend/               Shared backend/control API services
webapp/                FastAPI application + browser front end
examples/              Reproducible usage/code-generation examples
release_validation/    Version-controlled numerical regression baselines
docs/                  Maintained engineering documentation
.github/workflows/      CI, release and real-ngspice smoke validation
```

Generated wheels, one-off migration patches and historical test snapshots are intentionally **not** kept as source documentation. Git history and GitHub Releases/Actions are the archive for those artifacts.

---

## 9. Validation and evidence policy

The repository contains unit/regression coverage for LLC, PFC/Vienna, Control Tools, FRA, code generation, web/API and `power_sim`.

Two CI layers are especially important:

- **build-release** — project regression suite and packaging checks;
- **ngspice-smoke** — installs real `ngspice` + `libngspice` on CI and executes batch/shared-library switching tests rather than mocking the simulator.

For the bundled LLC baseline, a passing software regression means the current implementation reproduces repository-controlled numerical results within tolerance. It does **not** by itself verify magnetic loss, thermal performance, semiconductor stress, safety margin or hardware release readiness.

See [Engineering validation policy](docs/ENGINEERING_VALIDATION.md).

---

## 10. Documentation

Start here instead of reading historical version notes:

- [Documentation index](docs/README.md)
- [Digital control architecture](docs/DIGITAL_CONTROL_ARCHITECTURE.md)
- [LLC model hierarchy and boundaries](docs/LLC_MODELING.md)
- [FRA Loop Designer engineering contract](docs/FRA_LOOP_DESIGNER.md)
- [ngspice closed-loop architecture](docs/NGSPICE_CLOSED_LOOP.md)
- [Engineering validation policy](docs/ENGINEERING_VALIDATION.md)
- [Web deployment](docs/WEB_DEPLOYMENT.md)
- [Changelog](CHANGELOG.md)

The application also contains an offline **Help / F1** system in all four workspaces, including implementation notes, model boundaries and known limitations.

---

## 11. Language support

Desktop UI/help currently supports:

- 简体中文
- English
- 日本語
- 한국어

Engineering identifiers such as `Bode`, `H(z)`, `C99`, `Kp`, `PM`, `GM`, `FRA` and component/model names intentionally remain stable across languages.

---

## 12. Contact

**Yang Shuai / 杨帅**  
Power Electronics · Digital Power · Embedded Control · Control Algorithms

- GitHub: `yangshuai2022-star`
- Email: `maileyang@qq.com`
- WeChat: `maileyang`
- Technical blog / WeChat official account: **开关电源仿真与实用设计**

For bug reports, include the toolkit version, topology/workspace, input parameters, expected result, actual result and relevant screenshot/export whenever possible.

---

## 13. License

GNU GPL v3.0 — see [LICENSE](LICENSE).
