# Power Design Toolkit V9.2.0

Integrated engineering design and control-analysis toolkit for:

- **LLC resonant converter**: resonant-tank design, multi-load Q/gain maps, theoretical + engineering ZVS maps, operating-point trajectory, switching/dynamic waveforms, magnetics/loss analysis and digital voltage-loop design.
- **Single-phase TTPL PFC**: firmware-shaped dual-loop control, three sensing chains, open-loop Bode analysis, full settled AC-cycle solver, zero-crossing analyzer, workpoint-derived switching waveforms, PF/THD/harmonics.
- **Three-phase Vienna PFC**: DC-voltage outer loop + three ABC stationary-frame current loops, split-bus midpoint balance, three-phase sensing, common-mode/third-harmonic modulation support, full line-cycle solver, sector analysis, workpoint-derived three-level switching waveforms and per-phase PF/THD.

V9 consolidates the Toolkit around its native engineering and digital-control chain. GeckoCIRCUITS integration experiments have been removed from the formal baseline. The V9 focus is Control Tools → exact H(z) linkage → LLC/PFC small-signal plants → sampler/ADC → modulator/PWM → complete closed-loop stability analysis → single-file C99 controller export.

## 开发时间线 / Development Timeline

功能演进与缺陷修复同步记录，向所有使用者公开。
Legend: 🚀 Feature · 🐛 Bugfix · 🎨 GUI/UX · 🧪 Test/CI/Build

| 版本 / 日期 | 类型 | 内容 |
| --- | --- | --- |
| V4.1 | 🚀 | 可拖动 Bode 光标（功率级小信号页 + 完整数字环页），幅频/相频同步、对数频率插值 |
| V4.2 | 🐛 | Bode 光标中文标注方块修复：移除强制 monospace，新增 Win/macOS/Linux CJK 字体解析器 |
| V6 | 🚀 | LLC 与 PFC 拆分为独立顶层工作区；PFC Bode 默认仅开环、逐传递函数独立显隐；新增完整 AC 周期与开关周期波形 |
| 2026-08-02 | 🚀 | Windows/macOS 启动脚本（中文文件名、venv 兜底） |
| 2026-08-02 | 🧪 | PyInstaller 打包入口 + GitHub Actions 构建（macOS/Windows，tag 触发发布） |
| 2026-08-02 | 🐛 | CI 修复：bash 续行符、Windows pwsh `ls -la` 兼容 |
| 2026-08-03 | 🐛 | 修复 Windows 启动脚本：UTF-8/CRLF 解析损坏 + errorlevel 展开错误 |
| V7 | 🚀 | 结构性重构：LLC/PFC/Vienna 三工作区；新增三相 Vienna PFC 拓扑（ABC 电流环 + 中点平衡 + 三电平调制） |
| V7.1 | 🎨 | LLC GUI 信息架构整理：可停靠参数面板（F4）、专注模式（F9）、运行日志（F8）、Ctrl+R 按页运行 |
| V7.1.1 | 🎨 | “设计参数”统一为单一显隐切换；移除 dock 关闭按钮避免不一致 |
| V7.1.2 | 🎨 | 信号链框图可读性：放大块/标签/箭头，正交路由，角色配色与文字层级 |
| V7.1.3 | 🚀 | LLC 数字控制信号链改为可缩放/平移矢量画布；全屏检视；选中高亮 + 联动参数/Bode |
| V7.1.4 | 🚀 | LLC 变压器规格书驱动设计：TDK PQ35/35 预设、整数匝数搜索、Litz 选型、iGSE/分层铜损、一键回填主工程 |
| V7.1.5 | 🚀 | TTPL 电流环 PI 一键自动整定 + indu_comp 包络校验；Vienna 保守外环 PI（~53° 相位裕度） |
| V7.1.5 | 🐛 | 修复 TTPL 开关电流重构：跨 PWM 周期连续积分，不再每周期重置为三角纹波模板 |
| V7.1.5 | 🐛 | 修复 TTPL/Vienna 采样链延迟双重计数：ADC/SOC 延迟归采样块，计算/PWM 延迟归固件块 |
| V7.2 | 🚀 | C99 Float32 控制代码生成器（LLC / TTPL / Vienna），Init/Reset/ControlStep API + ISR 集成模板 + 稳定性报告 |
| V7.2 | 🐛 | 修复 Vienna GUI `self.fsw` spinbox 与 Figure 属性冲突（重命名为 `self.fsw_fig`） |
| V7.2.1 | 🎨 | PFC UI 密度优化：TTPL 紧凑控制头、可收起框图/参数、Bode 复选框默认折叠；Windows 11 样式统一 |
| V7.3 | 🚀 | PFC 电感设计页（Magnetics High Flux 254，DC-bias 跌落 + 铜损 + 一键回填）；Vienna UI 与 TTPL/LLC 统一 |
| V7.3 | 🐛 | 修复 LLC/PFC 共享模拟采样示意图在窄面板下被裁剪（高度自适应） |
| V7.4 | 🚀 | 工具栏“检查更新”：后台线程查 GitHub Releases API；启动 2.5s 静默自检；联系邮箱/微信 |
| 2026-08-17 | 🚀 | GUI 自动适配 Windows 11 深色/浅色主题（注册表检测 + Fusion 调色板 + 两套 token） |
| 2026-08-17 | 🐛 | 修复 Win11 深色主题下整屏不可见：浅色背景 + 跟随系统的浅色文字 → 统一从主题模块取色 |
| 2026-09-03 / V8.0 | 🚀 | LLC 分析架构重构：统一工作点请求、收敛状态、Golden Waveform、指标与模型比较数据结构；保留 V7 API |
| 2026-09-03 / V8.2 | 🚀 | 新增 DCM ON+/OPEN/ON− 互补整流、SR Qrr/第三象限/Timing/LUT/Loss、Golden 磁性损耗与寄生参数 |
| 2026-09-03 / V8.2 | ⚡ | 新增固定 90° 双相与固定 120° 三相 Interleaved LLC 引擎及 GUI 页面 |
| 2026-09-03 / V8.1 | 🚀 | 新增自洽非线性多谐波 HB（1/3/5/7 次自适应）、精确换向零点投影、FHA/HB/分段时域比较、CLI/GUI 与导出 |
| 2026-09-03 / V8.1 | 🧪 | 新增多谐波物理一致性、轻载分支降级、Golden 参考选择和导出回归测试；完成全仓库测试与 wheel 构建 |
| 2026-09-09 / V9.1.0 | 🚀 | LLC 公式 PDF 计算书：公式、数值代入、单位、结果与模型边界；支持自动设计和自定义谐振参数，GUI 一键导出 |
| 2026-09-09 / V9.1.0 | 🚀 | 版本化工程 JSON 保存，包含输入、计算摘要和工程参考数据来源；兼容旧参数文件 |
| 2026-09-09 / V9.1.0 | 🐛 | 统一更新检查版本号，正确比较 SemVer，记住已忽略版本；修复控制工具页签初始化异常 |
| 2026-09-09 / V9.1.0 | 🧪 | 补齐 Excel/API 测试依赖，增加公式报告、工程保存、数据来源和 GUI 启动回归；本机 315 项通过、2 项预期失败 |
| 2026-09-15 / V9.2.0 | 🚀 | 新增 **FRA Loop Designer** 独立工作区：导入 Bode100 / SIMPLIS / 通用频响，支持 Plant TS 与 Complete Loop TS（剥离当前控制器）两种语义，Quick Tune / New Structure 实时整定，导出最终 H(z) 的 C99 float32_t |
| 2026-09-15 / V9.2.0 | 🚀 | 新增目标 Fc/PM 的 **FRA Auto Design** 与**低阶有理模型辨识**（≤5 极点、实/复共轭稳定极点、可选纯延时）；Raw FRA 始终为稳定性判据，辨识结果仅为工程近似 |
| 2026-09-15 / V9.2.0 | 🚀 | **辨识被控对象模型 × 控制器打通**：Model ID 结果可直接回传为被控对象，与控制工具库中任意控制器组成开环 L(jω)，输出 Bode、PM/GM、S/T 与闭环 Step |
| 2026-09-15 / V9.2.0 | 🚀 | FRA Loop Designer 控制器类型与 Control Tools **完全配齐**（Integrator / PI / PIF / PID / PIDF / Type-II / Type-III / Modified PI / Lead / Lag / 1P1Z / 2P2Z / 3P3Z / General + R/C 输入），并新增 **Custom H(z)** 精确系数入口 |
| 2026-09-15 / V9.2.0 | 🐛 | 修复 Complete Loop TS 导入后 summary/details 全部变成 ERROR（PySide6 将 str-Enum 的 itemData 还原为 str 导致 `.value` 抛异常）；导出按钮在无有效计算时置灰 |
| 2026-09-15 / V9.2.0 | 🐛 | 修复 LEAD / LAG / 1P1Z / Modified PI 在 FRA 界面下 `fz_hz` / `fp_hz` 未映射而静默使用引擎默认值；补齐 Type-II/III 的 `fp0` |
| 2026-09-15 / V9.2.0 | 🧪 | **阶跃门控统一**：拟合环路与辨识被控对象两条 Step 路径共用同一带宽覆盖判据（`Fc/Fmin ≥ 10`、`Fmax/Fc ≥ 5`），置信度 LOW / 模型含右半平面极点 / 环路裕度 FAIL / 闭环不稳定时一律不给 Step 并说明原因 |
| 2026-09-15 / V9.2.0 | 🧪 | 新增 FRA 深度审计报告与回归：多圈相位与重复穿越裕度、Bode100 实测夹具列一致性、Auto Power 2P2Z 精确 H(z) 导出并与 float32 C99 数值对照、控制工具全类型可构造性 |
| 2026-09-15 / V9.2.1 | 🚀 | 新增**应用内帮助系统**：四个工作区工具栏各加「帮助」（F1），含快速上手、页面与参数说明、模型边界与已知限制、快捷键与文档链接；功能选择页新增「使用说明 / 帮助 (F1)」。此前应用内只有一段「关于」弹窗 |

本版使用说明与已知限制见 [V9.2.0 发布说明](DC/release_v9.2.0.md)。

## Install

Core/CLI:

```bash
python -m pip install -e .
```

GUI:

```bash
python -m pip install -e ".[gui]"
```

Development tests:

```bash
python -m pip install -e ".[dev]"
pytest
```

## Web edition (server-side Python)

A FastAPI browser edition is included under `webapp/`. The browser only handles input and plotting; the original Python LLC engineering kernels execute on the server.

```bash
python -m pip install -e ".[web]"
power-design-web
```

Open `http://127.0.0.1:8000`. See `WEB_DEPLOY.md` for GitHub Codespaces and container deployment.

## Start the GUI

```bash
python -m llc_design gui
```

or:

```bash
power-design-gui
```

The launcher first asks whether to enter **LLC Design**, **PFC Design**, **Control Tools** or **FRA Loop Designer**. All top-level windows preserve their state while switching.

## Control Tools — controller catalogue

`power_control_tools` is the single source of truth for the controller structures shared by every workspace (`CONTROLLER_LABELS`, `controller_parameter_keys`):

| Structure | Parameters |
| --- | --- |
| Integrator | `gain` |
| PI | `Kp`, `Ti` |
| PIF | `Kp`, `Ti`, LPF pole |
| PID | `Kp`, `Ti`, `Td` |
| PIDF | `Kp`, `Ti`, `Td`, LPF pole |
| Analog Type-II | `fp0`, `fz1`, `fp1` — or `R1/R2/C1/C2` |
| Analog Type-III | `fp0`, `fz1`, `fz2`, `fp1`, `fp2` — or `R1/R2/R3/C1/C2/C3` |
| Modified PI | `gain`, `fz1`, `fp1` |
| Lead / Lag / 1P1Z | `gain`, `fz1`, `fp1` |
| 2P2Z / 3P3Z | equal-order finite pole/zero form |
| General H(s) | explicit numerator / denominator |

Auto Design uses **power-compensator** templates for the 2P2Z/3P3Z cases (integrator pole at `s=0`: `K(s+wz1)(s+wz2)/[s(s+wp1)]`), which is deliberately different from the legacy equal-order manual sliders. Those results are therefore transferred as **exact H(z)** coefficients rather than silently remapped into the sliders.

Every controller or filter can be exported as one header-only C99 `float32_t` file (DF2T / SOS) and verified by compiling and stepping the generated C against the Python reference.

## FRA Loop Designer (V9.2)

A separate workspace for measured-frequency-response-based controller work.

**Inputs** — Bode100 CSV, SIMPLIS TXT, generic frequency / gain / phase. Two semantics, never guessed:

* **Plant TS** — the record already excludes the controller: `L_new = G_plant · C_new`.
* **Complete Loop TS** — the record is the loop gain / return ratio around the closed loop and does contain the current controller. Only that controller is divided out: `G_eq = H_scan / C_old`. PWM, ADC, sensing and real delays stay inside `G_eq` because they are already in the measurement. The tool immediately rebuilds `H_scan` algebraically and reports the reconstruction error; that check proves the arithmetic, **not** that the entered `C_old` is what produced the hardware sweep.

**Controller handling**

* *Quick Tune* — scale loop gain and per-coefficient `b0x…a3x` of the existing controller (exact B/A input, canonical or firmware `+A` convention), or scale `Kp`/`Ti` when the current controller is a PI. Unity scales reproduce the measured loop exactly.
* *New Structure* — redesign with the full Control Tools catalogue above, including the Type-II/III R/C input mode and `Custom H(z)` exact coefficients.

**Outputs** — open-loop Bode, main and all 0-dB crossovers, PM, GM, worst PM/GM, `S`, `T`, `Ms`, `Mt`, and single-file C99 export. A finite sweep that never reaches an odd-180° phase crossing reports GM as **not proven** instead of silently passing it. Analysis is limited to `0.49·Fs`.

**Auto Design** — target `Fc`/`PM`/minimum `GM`/maximum `Ms` synthesis on the raw measured plant, retrying at lower crossover when the requested point cannot satisfy every measured constraint. Raw FRA points remain the acceptance authority; no rational fit is required.

**Model Identification** — optional stable low-order rational approximation (≤5 poles, real or complex-conjugate, optional pure delay) from ~10 Hz to 100 kHz class sweeps. This is explicitly *not* Vector Fitting and not physical component identification.

**Identified model × controller link (V9.2)** — a fitted **plant** model can be handed to the designer as the plant source and connected to any controller from the catalogue:

```text
L(jw) = G_fit(jw) · H_ctrl(e^{jwT})      margins / S / T via the same engine as raw FRA
T(z)  = L(z) / (1 + L(z))                closed-loop step, exact discrete controller
```

The step is a **model-derived prediction** and is withheld — with a stated reason — when the fit residual confidence is LOW, the identified model contains right-half-plane poles, the linked loop fails its margin check, the closed loop is unstable, or the analysed band does not satisfy the step bandwidth-coverage requirement (`Fc/Fmin ≥ 10` and `Fmax/Fc ≥ 5`). A narrow local fit may be useful for `Fc`/`PM` interpretation but never authorises a time-domain prediction.

See [`DC/FRA_LOOP_DESIGNER_V1.md`](DC/FRA_LOOP_DESIGNER_V1.md), [`DC/FRA_LOOP_DESIGNER_V1_5_V2.md`](DC/FRA_LOOP_DESIGNER_V1_5_V2.md) and the [FRA deep audit](DC/FRA_DEEP_AUDIT_2026-09-15.md) for the frozen contracts, margins policy and the list of assumptions that frequency-domain margins alone cannot prove.

## LLC V8.1 multi-fidelity analysis

V8.1 introduces a common electrical-analysis contract while retaining the existing V7 modules:

- **FHA** — fast, backward-compatible analytical design baseline.
- **Nonlinear multi-harmonic balance (HB)** — solves the selected odd harmonics together with the rectifier polarity/commutation state; it is not a linear sum of independent FHA harmonic circuits.
- **Switched periodic time-domain model (TD)** — adapts the existing piecewise switched steady-state solver as the current ideal-topology reference.

All three layers return a common waveform/metric schema. The model-comparison view reports convergence, switching frequency, gain, RMS/peak currents, resonant-capacitor stress and errors relative to the highest converged fidelity.

The default HB sequence is adaptive `H1 -> H1/H3/H5 -> H1/H3/H5/H7`. At continuous-clamp operating points, rectifier commutation angles are found from the reconstructed trigonometric current and the sign-function Fourier coefficients are integrated exactly between zero crossings. Near light-load/discontinuous topology transitions, V8.1 can explicitly return a regularized projection with a warning instead of silently reporting an exact solution.

CLI examples:

```bash
# Generate a nonlinear multi-harmonic waveform
python -m llc_design waveforms --mode hb --samples 1024 --output output/llc_hb

# Compare FHA, HB and switched-periodic TD at one work point
python -m llc_design model-compare --vbus 400 --load 1.0 \
    --max-harmonic 7 --hb-samples 1024 --td-samples 1024 \
    --output output/llc_v8_compare
```

The comparison export contains CSV, JSON and Markdown summaries plus optional per-model waveform files. See [`V8_IMPLEMENTATION_NOTES.md`](V8_IMPLEMENTATION_NOTES.md) for equations, numerical strategy, validation and known boundaries.

## LLC V7 foundation retained in V8.1

### Q / gain / ZVS map

The LLC page calculates Q at multiple load fractions (default 10/25/50/75/100/120%), overlays gain curves, and shows real bus/load workpoints. The ZVS view separates:

1. **Theoretical inductive region**: positive tank input phase / `Im{Zin} > 0`.
2. **Engineering ZVS margin**: commutation current and deadtime versus device Qoss/Coss charge and energy demand.

The map therefore distinguishes capacitive operation, inductive-but-insufficient commutation, warning margin and safe ZVS margin.

### Digital control diagram

The LLC digital-control page has a clickable signal-chain diagram:

`Vref -> controller -> PCMD clamp -> PCMD/TBPRD FM LUT -> PWM/ZOH/delay -> Gvf -> Vout -> divider/op-amp/RC/ADC -> feedback`

Selecting a block moves to the corresponding parameter group and focuses the associated Bode trace. The Bode cursor reports gain/phase and the phase-budget view decomposes the total response by controller, FM, plant, sensing and delay.

## Single-phase TTPL PFC V7

The main control structure remains **two feedback loops**:

- 50 kHz inductor-current inner loop.
- 10 kHz DC-bus voltage outer loop.

The 25 kHz AMC layer is **not** a third feedback loop; it generates the current reference and feed-forward terms.

Firmware-shaped relations used by the model include:

```text
i_ref      = gcmd * |Vac_meas|
duty_ff    = 1 - |Vac_meas| / Vbus_set
indu_comp  = clamp(0.085 * i_ref, 0.7, 1.0)
duty_total = clamp(duty_ff + duty_pi * indu_comp)
```

Three external sensing paths are modeled independently:

- inductor current `iL`;
- AC input voltage `Vac`;
- DC bus voltage `Vbus`.

Each path can include front-end gain/divider, op-amp bandwidth, external RC, ADC aperture/sample timing, digital filtering and delay.

### PFC Bode policy

The current-loop and voltage-loop pages default to **open loop only**. Individual plant/controller/sensing/PWM/closed-loop traces can be shown with checkboxes. Clicking a control-diagram block focuses the relevant trace. The cursor provides gain/phase and phase-budget data.

### PFC waveforms

The line-cycle solver is multi-rate (50/25/10 kHz control updates) and uses a bus-capacitor energy state. It explicitly distinguishes:

- non-negative boost-inductor current magnitude;
- signed AC input current;
- real bus-voltage dynamics.

The local switching waveform is reconstructed from a selected point in the **final settled AC cycle**, rather than from an unrelated independent workpoint.

A dedicated zero-crossing analyzer shows the firmware-like half-cycle/ZC states, current reference/error, duty FF/PI/total, minimum-pulse state, LF bridge intent and PI reset events.

PF/THD is calculated on a complete settled line period with explicit integer harmonics.

## Three-phase Vienna PFC V7

Vienna reuses the PFC-common controller/sensing/Bode infrastructure, but has topology-specific plant and modulation models.

### Main control

The V7 baseline uses stationary-frame ABC control:

```text
DC Vbus voltage outer loop
        -> Gcmd
        -> ia*=Gcmd*va, ib*=Gcmd*vb, ic*=Gcmd*vc
        -> three phase-current controllers
        -> Vienna modulator
        -> Vienna power stage
```

The split DC bus adds an **auxiliary midpoint-balance controller** based on `Vdc+ - Vdc-`; it is intentionally presented separately from the two main PFC feedback loops.

### Vienna averaged model

Dynamic states include:

```text
ia, ib, ic, Vdc+, Vdc-
```

The model includes:

- three-wire floating-neutral current dynamics;
- optional common-mode/third-harmonic injection;
- optional `R*i_ref + L*di_ref/dt` inductor-voltage-drop feed-forward;
- Vienna signed active-state modulation;
- center-switch zero-state duty `D0 = 1 - |m|`;
- minimum-pulse handling;
- split-bus capacitor energy dynamics;
- averaged midpoint current from zero-state occupancy.

### Vienna sensing

Core sensing channels are:

- `Ia/Ib/Ic`;
- `Va/Vb/Vc`;
- `Vdc+/Vdc-`.

The GUI can lock phase channels to common hardware values or unlock gain/offset mismatch diagnostics.

### Vienna Bode and waveforms

Three return-ratio views are kept separate and clean:

- Phase-A current open loop (ABC loops are symmetric in the nominal model).
- Total DC-voltage open loop.
- Midpoint-balance open loop.

All default to open loop only; component traces are opt-in. A separate sampling Bode page can focus current, phase-voltage or split-bus sensing.

The final settled 3-phase AC-cycle view includes Vabc, Iabc and references, modulation/zero-state duty, split-bus voltages, midpoint current, control outputs and power. The sector analyzer tracks sector, phase polarity, modulation and midpoint current. Switching waveforms are derived from the selected AC phase and include center-switch gates, three-level converter voltage, phase-current ripple, upper/lower diode current and split-bus/midpoint currents.

## CLI

Single-phase PFC:

```bash
pfc-control-lab --output output/ttpl
```

Vienna:

```bash
vienna-control-lab --vll 400 --vdc 700 --power 10000 --output output/vienna
```

LLC CLI remains available through:

```bash
llc-design --help
python -m llc_design model-compare --help
```

## 帮助 / HELP

每个工作区工具栏右侧都有 **帮助** 按钮，**F1** 随时打开；功能选择页也有「使用说明 / 帮助 (F1)」。
帮助内容随程序分发，不依赖浏览器或 PDF，包含：

* **快速上手** — 该工作区的典型操作顺序，以及每个页面/结果页在做什么；
* **页面与参数说明** — 控制器类型与参数对照、采样链、离散化方法、导出含义；
* **模型边界与已知限制** — 每个工作区声明的假设与不能用来声称的结论；
* **快捷键与操作**；
* **打开文档** — 本地 Markdown 存在时直接打开，打包版本自动回退到 GitHub 上的同名文档。

模型边界是帮助的重点内容，例如：FHA 不能用来声称 ZVS 裕度；PM/GM/Ms/Mt 不是与拓扑无关的
闭环稳定性证明；采样 `Ms`/`Mt` 不能当作数学上界；有理拟合无法辨识不稳定的开环被控对象；
辨识模型的 Step 在被扣留时仍然给出 `Fc`/`PM`，但不得当作时域结论。

## Validation status

The source tree includes unit/regression tests covering LLC tank/magnetics/digital control/Q-ZVS, TTPL sensing/control/waveforms/PF-THD, Vienna nested-loop/midpoint/switching behavior, the Control Tools controller/filter/codegen chain, the FRA Loop Designer (import, de-embedding, margins, Auto Design, rational identification, identified-model × controller link) and the FastAPI web edition.

Current full-suite result (Linux, gcc available, `pip install -e ".[dev,web,gui]"`, `QT_QPA_PLATFORM=offscreen`):

```text
376 passed, 2 xfailed
```

The five `verify_c99_filter` regressions compile and step the generated C against the Python reference, so they only run where a C compiler is on `PATH`; without one those cases fail with `C compiler not found` rather than being skipped.
GUI tests really construct the windows and render offscreen figures when PySide6 is installed, and are skipped by `pytest.importorskip` when it is not.

GUI source is import/compile checked by the project; a real window run still requires PySide6 on the target machine.

## Modeling boundary

Bode pages are local linear models. Saturation, minimum pulse, TTPL zero-crossing state transitions, Vienna switching-state decisions and protection behavior are assessed in the time-domain solvers rather than being folded into a misleading single LTI transfer function.

## LLC transformer design (V7.1.4)

The LLC GUI now includes a dedicated **变压器** page.  Enter ferrite/core/bobbin datasheet values (Ae/Amin/le/Ve/AL/µe/AN/lN etc.) or load the bundled TDK PQ35/35 example, then run automatic turns and Litz synthesis.  The default conductor is 0.10 mm Litz and strand count is rounded in multiples of 50.  The page reports winding feasibility, gap/AL, Bpk, DCR, harmonic copper loss, iGSE core loss, hotspot estimate and per-workpoint loss.

CLI example:

```bash
python -m llc_design transformer-design --preset TDK_PQ35_35_B65881A_N87 --output output/transformer_design
```

### PFC stability tools (V7.1.5)

The single-phase TTPL control page includes **一键稳定整定并应用**.  It designs a conservative
50 kHz current-loop PI from the current L/R, current-sense filter, ADC/ZOH and computation/PWM
delay, then checks the scheduled `indu_comp` gain over a line/load/phase envelope.  The legacy
firmware PI remains available as an explicit comparison preset.

The Vienna page now validates the Bode, three-phase AC-cycle and local switching stages
independently.  If a calculation or plot fails, the GUI reports the exact failing stage and exposes
the complete traceback through the error dialog's detailed-information section.

## V7.2 C99 / Float32 control-code generator

The GUI and CLI can generate portable real-time control cores for:

- LLC frequency control;
- single-phase TTPL PFC double-loop control;
- three-phase Vienna PFC ABC current control + DC-voltage loop + midpoint balance.

The generated code deliberately stops before the BSP boundary. ADC/PWM/GPIO/interrupt-controller setup is not emitted. The BSP supplies engineering-unit inputs and consumes semantic duty/frequency outputs.

CLI examples:

```bash
python -m power_codegen ttpl --output output/generated_ttpl
python -m power_codegen vienna --output output/generated_vienna
python -m power_codegen llc --output output/generated_llc
```

Every generated folder contains C99/float32 sources, a compile-ready ISR integration template, `design_snapshot.json`, and `stability_report.txt`.

### V7.2.1 PFC workspace UI

The TTPL PFC page now mirrors the LLC digital-control workspace: a compact interactive overview, collapsible parameter inspector, collapsible transfer-function selector and a larger default Bode/waveform area. The PFC main window also uses the same cross-platform Qt stylesheet as LLC so Windows 11 no longer falls back to inconsistent native-looking tabs/buttons/spin boxes.


## V7.3 PFC inductor design

TTPL and Vienna workspaces include a dedicated **Inductor Design** page.

### Default core

Built-in default is the Magnetics **High Flux** powder-core **Core Data 254** (60 µ grade, AL = 81 nH/T²), with editable geometry/AL:

- Le = 98.4 mm, Ae = 110.6 mm², Ve = 10880 mm³
- OD = 40.77 mm, ID = 23.32 mm, HT = 15.37 mm

Only permeability grades with both a Core Data 254 AL value and complete published DC-bias/core-loss coefficients are selectable in the automatic model.

### Manufacturer fits

- DC-bias permeability droop: `%ui = 1 / (a + b·H^c)`, H in Oe.
- Core-loss density: `Pv = a·B^b·f^c`, B in T, f in kHz, Pv in mW/cm³.
- Full-load L(I) droop curve and line-cycle switching-ripple/core-loss calculation.

### Copper model

Default winding is enamelled round copper; the page sizes the wire from current density, computes hot DCR and DC I²R copper loss, and reports the full-load operating point. Skin/proximity losses are intentionally excluded in V7.3 per the design requirement.

### Apply back

One click applies the calculated full-load L and hot DCR back into the TTPL/Vienna power-stage parameters.

### V7.3 Vienna UI unification

The Vienna workspace now uses the same compact toolbar/diagram/hidden-parameter interaction model as TTPL/LLC (oversized title row and oversized diagram removed). The shared LLC/PFC analog-sense schematic wraps responsively in narrow inspectors instead of being clipped.

## V7.4 Update check and contact info

The GUI toolbar (top-right, LLC and PFC workspaces) shows the author's contact info and an **检查更新** button. The button queries the GitHub Releases API in a background thread: when a newer release exists it shows the release notes with a link to the download page. A silent auto-check runs 2.5 s after startup and only prompts when an update is available.

- 邮箱: maileyang@qq.com(点击可直接写信)
- 微信: maileyang

## License

GNU GPL v3 — see [LICENSE](LICENSE).



## V9 — Digital Control & Closed-Loop Baseline

V9 makes the Digital Control Tools a first-class controller source for the LLC loop analysis. The exact discrete transfer function designed in Control Tools is linked directly into the LLC small-signal closed-loop calculation; no PI/PID parameter re-fit is performed.

Canonical chain:

```text
Control Tools H(s)
        ↓ discretization
Control Tools H(z)
        ↓ exact coefficient linkage
LLC Gvf(s) / Gvf(z)
        ↓
Sampler / ADC / sensing
        ↓
FM LUT / TBPRD modulator / PWM delay
        ↓
L(z), T(z), S(z), PM, GM, Fc, closed-loop poles
        ↓
Single-file float32_t C99 controller export
```

The LLC **Digital Loop** page shows the active controller source. When a Control Tools controller is available, **Use current Control Tools H(z)** is enabled and selected by default. The loop sample time follows the Control Tools sample rate so the controller and discrete plant remain on the same z-domain clock.

The formal V9 baseline intentionally does not require Java, GeckoCIRCUITS or any external circuit simulator.

Developer runtime regression:

```bash
python -m llc_design closed-loop-selftest
```

See `V9_DIGITAL_CONTROL_BASELINE.md` for the numerical contract and validation scope.
