"""English catalogue.

Policy: strings that are already English in the source have no entry here —
they are returned unchanged by :func:`llc_design.i18n.t`.
"""

CATALOGUE: dict[str, str] = {
    # ------------------------------------------------------------------ shell
    "电源设计工具箱 — 选择设计功能": "Power Design Toolkit — Choose a Workspace",
    "请选择进入的设计工作区": "Choose the workspace to enter",
    "LLC、PFC、数字控制工具与 FRA Loop Designer 使用独立工作区；": "LLC, PFC, Digital Control Tools and the FRA Loop Designer are separate workspaces;",
    "FRA 工作区支持控制器剥离、实时整定、目标 Fc/PM 自动设计与低阶模型辨识。": "the FRA workspace supports controller de-embedding, live tuning, Auto Design to a target Fc/PM and low-order model identification.",
    "进入 LLC 设计": "Enter LLC Design",
    "谐振腔、磁性器件、损耗、开关波形、小信号与数字电压环": "Resonant tank, magnetics, losses, switching waveforms, small-signal and the digital voltage loop",
    "进入 PFC 设计": "Enter PFC Design",
    "单相 TTPL + 三相 Vienna：控制、采样链、Bode、AC 周期、开关波形与 PF/THD": "Single-phase TTPL + three-phase Vienna: control, sensing chain, Bode, AC line cycle, switching waveforms and PF/THD",
    "进入 Control Tools": "Enter Control Tools",
    "S2Z、数字滤波器、Bode、Step/Impulse、P/Z、SOS 与 C99 float32_t 导出": "S2Z, digital filters, Bode, Step/Impulse, P/Z, SOS and single-file C99 float32_t export",
    "进入 FRA Loop Designer": "Enter FRA Loop Designer",
    "Bode100 / SIMPLIS / Generic：Equivalent Plant、Auto Design、Model ID、稳定性与 C99": "Bode100 / SIMPLIS / generic: Equivalent Plant, Auto Design, Model ID, margins and C99",
    "退出": "Quit",
    "使用说明 / 帮助 (F1)": "Help (F1)",
    "四个工作区分别做什么、如何选择、通用操作与快捷键": "What each workspace does, how to choose, general operation and shortcuts",
    # ---------------------------------------------------------------- toolbar
    "电源设计工具箱 — LLC Design / Waveform / Control": "Power Design Toolkit — LLC Design / Waveform / Control",
    "电源设计工具箱 — PFC Design: TTPL / Vienna": "Power Design Toolkit — PFC Design: TTPL / Vienna",
    "电源设计工具箱 — Digital Control Tools": "Power Design Toolkit — Digital Control Tools",
    "电源设计工具箱 — FRA Loop Designer": "Power Design Toolkit — FRA Loop Designer",
    "工程": "Project",
    "加载 JSON": "Load JSON",
    "保存工程 JSON": "Save project JSON",
    "导出公式 PDF": "Export formula PDF",
    "输出目录": "Output folder",
    "切换到 PFC": "Switch to PFC",
    "切换到 LLC": "Switch to LLC",
    "功能选择": "Workspace",
    "帮助": "Help",
    "语言 / Language": "Language / 语言",
    "检查更新": "Check for updates",
    "隐藏设计参数": "Hide design parameters",
    "显示设计参数": "Show design parameters",
    "显示/隐藏 LLC 全局设计参数（F4）": "Show or hide the global LLC design parameters (F4)",
    "运行日志": "Run log",
    "专注模式": "Focus mode",
    "运行当前": "Run current page",
    "运行当前页面对应的分析（Ctrl+R）": "Run the analysis for the current page (Ctrl+R)",
    "关于": "About",
    "关于 PFC": "About PFC",
    "PFC 工作区": "PFC workspace",
    "运行当前 PFC 分析": "Run current PFC analysis",
    "重新计算": "Recompute",
    "导出单文件 C99": "Export single-file C99",
    "导入 FRA": "Import FRA",
    "导出 C99": "Export C99",
    "PFC 工作区就绪": "PFC workspace ready",
    "就绪": "Ready",
    "专注模式：已隐藏全局参数与运行日志": "Focus mode: global parameters and run log hidden",
    # ----------------------------------------------------------------- updater
    "点击发送邮件：maileyang@qq.com\n": "Click to send an e-mail: maileyang@qq.com\n",
    "有不懂的地方、或结果与实测不符，欢迎直接发邮件讨论（帮助 F1 → 联系方式与支持）": "If anything is unclear, or a result disagrees with measurement, feel free to e-mail me (Help F1 → Contact & Support)",
    "微信: maileyang": "WeChat: maileyang",
    "微信号: maileyang\n": "WeChat ID: maileyang\n",
    "公众号 / 技术博客: 开关电源仿真与实用设计（帮助 F1 → 联系方式与支持 内有二维码）": "Official account / blog: 开关电源仿真与实用设计 (QR code under Help F1 → Contact & Support)",
    "(该版本未附带发布说明)": "(this release has no notes attached)",
    "发现新版本": "New version available",
    "打开下载页": "Open download page",
    "知道了": "Close",
    "当前已是最新版本 {APP_VERSION}。": "Already up to date ({APP_VERSION}).",
    "检查更新失败": "Update check failed",
    "无法连接到 GitHub,请检查网络后重试。\n": "Could not reach GitHub. Check your network and try again.\n",
    # -------------------------------------------------------------- help title
    "LLC Design 工作区 — 使用说明": "LLC Design workspace — User guide",
    "PFC Design 工作区 — 使用说明": "PFC Design workspace — User guide",
    "Digital Control Tools 工作区 — 使用说明": "Digital Control Tools workspace — User guide",
    "FRA Loop Designer 工作区 — 使用说明": "FRA Loop Designer workspace — User guide",
    "功能选择 — 该进哪个工作区": "Workspace selection — which one do I need?",
    "工具箱按工程任务分成四个独立工作区，各自保持状态，可用工具栏随时切换。": "The toolkit is split into four independent workspaces, each keeping its own state; the toolbar switches between them at any time.",
    # ------------------------------------------------------------ help sections
    "快速上手": "Quick start",
    "页面说明": "Page reference",
    "页面与参数说明": "Page and parameter reference",
    "模型边界与已知限制": "Model boundary and known limits",
    "快捷键与操作": "Shortcuts and operation",
    "联系方式与支持": "Contact and support",
    "实现说明 — FHA / HB / TD 三种模型怎么算的": "Implementation — how FHA / HB / TD are computed",
    "实现说明 — 数字环与延时怎么建模的": "Implementation — how the digital loop and delays are modelled",
    "实现说明 — 采样链与延迟拆分（最容易出错的地方）": "Implementation — sampling chain and delay split (the easiest place to get wrong)",
    "实现说明 — 控制结构与求解器": "Implementation — control structure and solvers",
    "电感设计": "Inductor design",
    "控制器类型一览": "Controller catalogue",
    "实现说明 — 系数约定与离散化": "Implementation — coefficient convention and discretization",
    "实现说明 — C99 导出结构": "Implementation — C99 export structure",
    "实现说明 — 稳定性判定": "Implementation — stability classification",
    "测量语义：TS 类型不能猜": "Measurement semantics: the TS type must not be guessed",
    "控制器输入与整定": "Controller input and tuning",
    "实现说明 — 频响求值与裕度判据": "Implementation — frequency response and margin criteria",
    "实现说明 — 剥离、重建与辨识算法": "Implementation — de-embedding, reconstruction and identification",
    "实现说明 — 辨识模型 × 控制器与 Step": "Implementation — identified model × controller and the step gate",
    "实现说明 — Auto Design 怎么综合的": "Implementation — how Auto Design synthesises",
    "假设与已知限制": "Assumptions and known limits",
    "四个工作区": "The four workspaces",
    "怎么选": "How to choose",
    "每个工作区的帮助里有什么": "What each workspace's help contains",
    "通用提示": "General notes",
    "此章节暂无当前语言的正文译文，以下显示原文。": "This section has no translation in the active language yet; the original text is shown below.",
    # ------------------------------------------------------------- help bodies
    "helpbody.common.shortcuts": """F1            Open help for the active workspace (any workspace)
F4            LLC: show/hide the design parameter panel
F8            LLC: show/hide the run log
F9            LLC: focus mode (hides parameters and run log)
Ctrl+R        LLC: run the analysis for the current page
Toolbar       Top of each workspace: switch workspace, run/recompute, export,
              help, contact, update check
""",
    "helpbody.common.contact": """E-mail    maileyang@qq.com
WeChat    maileyang
Official account / blog  开关电源仿真与实用设计 (scan the QR code below)

If something is unclear, if you are unsure about a trade-off, or if a result
disagrees with measurement or theory, please just e-mail me. Engineering
questions usually need the actual topology, operating point and waveforms to
answer, so a discussion is often faster than reading documentation — and I do
want to know where these tools get stuck on real projects.

When you write, it helps to include:

  1. workspace and version (Help -> About, or the version next to
     "Check for updates" in the toolbar)
  2. the key input parameters (a screenshot of the parameter panel is fine)
  3. the symptom: expected result vs actual result, ideally with a screenshot
     or the exported CSV/JSON
  4. for measurement-related questions (especially FRA): how you measured,
     where you injected, and whether the data already contains the current
     controller

Many "this looks wrong" cases are actually documented model boundaries rather
than defects — for example FHA far from resonance, sampled Ms/Mt falling between
sweep points, or a Complete Loop record without the real C_old. Each workspace's
"Model boundary and known limits" section covers these.
""",
    "helpbody.common.about": """Power Design Toolkit V{version}
Author: Yang Shuaiguo (杨帅锅)
E-mail    maileyang@qq.com
WeChat    maileyang
Official account / blog  开关电源仿真与实用设计

LLC / PFC / Vienna design, digital control tools and FRA loop design.
License: GNU GPL v3.

This is an engineering design aid. Every result rests on the models and
assumptions written down in each workspace's "Model boundary and known limits";
anything sent to hardware must still go through your project's measurement
verification. Questions are welcome by e-mail.
""",
    "helpbody.selector.workspaces": """LLC Design          Resonant tank design, multi-fidelity electrical analysis,
                    magnetics and losses, SR, interleaving, waveforms,
                    small-signal and the digital voltage loop. Start here for a
                    complete converter design from a specification.
PFC Design          Single-phase TTPL and three-phase Vienna: firmware-shaped
                    dual-loop control, sensing chain, Bode, full AC line cycle,
                    switching waveforms, PF/THD and inductor design.
Control Tools       Standalone digital controller/filter design: choose a
                    structure, watch H(s)/H(z), inspect Bode/Step/Impulse/
                    pole-zero/group delay, export single-file C99.
FRA Loop Designer   Controller tuning from measured frequency response
                    (Bode100/SIMPLIS/generic), Auto Design to a target Fc/PM,
                    low-order model identification, and closed-loop analysis of
                    an identified model with any controller.
""",
    "helpbody.selector.how_to_choose": """* Specification only, designing from scratch -> LLC Design or PFC Design.
* Power stage already exists, design or review controller coefficients ->
  Control Tools.
* You have a machine and a frequency sweep and want to know how much margin the
  current loop has and how to change the controller -> FRA Loop Designer; do
  Model ID first and hand the model back if you also want a time-domain
  prediction.
* Once FRA produced a controller you can re-check the coefficients and the
  export format in Control Tools, or export the C99 directly from the FRA
  workspace.
""",
    "helpbody.selector.what_help_contains": """Every workspace's help contains: quick start, page and parameter reference,
**implementation notes** (how the algorithms and coefficient conventions
actually work), model boundary and known limits, shortcuts, and contact
information.

If a step is unclear, or a result disagrees with measurement or theory, read the
implementation notes first and then check the model boundary to confirm whether
the conclusion is even inside what the model can claim.
""",
    "helpbody.selector.general_tips": """* Every workspace toolbar has 帮助 (F1), contact information and an update
  check on the right.
* The update check runs once silently after start-up and only prompts when a
  newer release exists; a version can be dismissed and remembered.
* "Workspace" returns to the selector at any time; switching workspaces does not
  lose the state of the other windows.
* Engineering questions are welcome by e-mail (see "Contact and support").
""",
    "helpbody.fra.ts_semantics": """Plant TS           The data is already the plant, without the controller:
                   L_new = G_plant * C_new
Complete Loop TS   The data is the closed-loop loop gain / return ratio and does
                   contain the current controller:
                   G_eq = H_scan / C_old

In Complete Loop mode, PWM, ADC, sensing/filtering and real delays are NOT added
again, because they are already inside the measured data. The tool immediately
reconstructs H_scan algebraically and reports the reconstruction error.

Important: the reconstruction check only verifies the complex division and
multiplication and its numerical implementation. If C_old is entered wrongly,
the wrong controller is divided out and multiplied back in, and the
reconstruction still "passes". Hardware controller provenance has to come from
the user.

If the imported file is the ordinary closed-loop reference-to-output transfer T
rather than a loop gain, it cannot be de-embedded directly; convert it first
with L = T/(1-T) (unity feedback case).
""",
    "helpbody.control.controller_catalogue": """Integrator            gain
PI                    Kp, Ti
PIF                   Kp, Ti, LPF pole
PID                   Kp, Ti, Td
PIDF                  Kp, Ti, Td, LPF pole
Analog Type-II        fp0, fz1, fp1   or R1/R2/C1/C2
Analog Type-III       fp0, fz1, fz2, fp1, fp2   or R1/R2/R3/C1/C2/C3
Modified PI           gain, fz1, fp1
Lead / Lag / 1P1Z     gain, fz1, fp1
2P2Z / 3P3Z           equal-order finite pole/zero (gain + N zeros + N poles)
General H(s)          explicit numerator / denominator coefficients

The Filter Designer tab additionally offers IIR (Butterworth / Bessel /
Chebyshev I / II / Elliptic), FIR window, moving average and a DC blocker.

Note: the manual 2P2Z / 3P3Z entry is the equal-order finite pole/zero form,
while FRA Auto Design uses power-compensator templates with an integrator pole.
The two are different, so Auto Design results are written back as exact H(z).
""",
    "helpbody.llc.limits": """* FHA keeps the fundamental only; it must not be used to claim ZVS margin.
* HB is a self-consistent solution of the selected odd harmonics together with
  the rectifier commutation state, not a linear superposition; near light load
  and the discontinuous topology transition it may only return a regularised
  projection with a warning.
* TD uses ideal switches and a piecewise-linear model: no parasitics, dead-time
  detail or device nonlinearity.
* The "engineering ZVS" boundary in the Q/ZVS view is an engineering criterion,
  not a device-level dead-time/junction-capacitance simulation.
* The small-signal and digital-loop models are averaged models: no switching
  ripple, sampling jitter or quantisation noise. FM LUT segment gain and
  comparator quantisation are not part of the linear model.
* Exported C99 contains the controller coefficients and the documented timing
  convention only; the actual sampling instant, update instant and
  saturation/anti-windup still have to be aligned on the project side.
""",
    "helpbody.control.limits": """* What is exported is the controller **mathematics**; sampling instant, update
  instant, saturation and anti-windup and the state reset policy have to be
  implemented in firmware according to the project convention.
* Filters are ideal-coefficient designs without a fixed-point quantisation error
  budget.
* Bode is the discrete-system response; the analog H(s) curve is a reference
  only, and the two must separate near Nyquist.
* Group delay is computed in samples and converted to seconds on the page.
""",
    "helpbody.fra.assumptions": """1. A measured file does not itself contain the firmware controller parameters;
   without a real C_old a Complete Loop record can only be used as a numerical
   stress case for import/margins/fitting, and cannot produce a physically valid
   plant.
2. PM / GM / Ms / Mt are frequency-domain robustness evidence that holds under
   the usual power-converter assumption that the plant contains no unaccounted
   right-half-plane poles. They are not a topology-independent proof of
   closed-loop stability; a frequency response cannot reveal the open-loop RHP
   pole count P.
3. Ms / Mt are low-confidence between sweep points: a resonance narrower than
   the sweep spacing can fall between samples, so the sampled value is not a
   mathematical upper bound.
4. The rational fitter is constrained to the stable half-plane, so it cannot
   identify a genuinely unstable open-loop plant.
5. Auto Design currently synthesises PI / PIF / PID / Power 2P2Z / Power 3P3Z;
   the other structures are available for manual design and through the exact
   H(z) entry.
""",
    # help dialog chrome
    '关闭': 'Close',
    '本地未包含 {name}（打包版本不携带 Markdown 源文件），已尝试在浏览器打开 GitHub 上的同名文档。': 'The packaged build does not ship {name} (Markdown sources are not bundled); tried to open the same document on GitHub instead.',
    # workspace chrome harvested from the built windows
    '设计总览': 'Overview',
    '增益 / 工作区': 'Gain / operating region',
    '变压器': 'Transformer',
    '波形': 'Waveforms',
    '小信号': 'Small-signal',
    '小信号 G(s) / G(z)': 'Small-signal G(s) / G(z)',
    '数字控制': 'Digital control',
    '多负载 Gain / Q': 'Multi-load Gain / Q',
    '工作点': 'Operating point',
    '工作点表': 'Operating point table',
    '损耗分解': 'Loss breakdown',
    '局部开关周期': 'Local switching period',
    '完整 AC 周期': 'Full AC line cycle',
    '详细开关波形': 'Detailed switching waveform',
    '详细分段波形': 'Detailed piecewise waveform',
    '快速波形': 'Quick waveform',
    '快速 EDF 波形': 'Quick EDF waveform',
    '多谐波 HB 波形': 'Multi-harmonic HB waveform',
    '横轴使用 Fn=Fsw/Fr': 'X axis uses Fn = Fsw/Fr',
    '适应': 'Fit',
    '全屏': 'Full screen',
    '全屏框图': 'Full-screen diagram',
    '全部关闭': 'Collapse all',
    '全部显示': 'Show all',
    '隐藏参数': 'Hide parameters',
    '隐藏框图': 'Hide block diagram',
    '隐藏环节参数': 'Hide stage parameters',
    '传递函数 ▸': 'Transfer functions ▸',
    '仅开环': 'Open loop only',
    'LLC 设计参数': 'LLC design parameters',
    '自动设计变压器': 'Auto-design transformer',
    '计算 SR Timing / Loss': 'Compute SR Timing / Loss',
    '计算 Interleaved LLC': 'Compute interleaved LLC',
    '计算电感': 'Compute inductor',
    '重新计算 Q / ZVS': 'Recompute Q / ZVS',
    '运行 LLC 完整计算': 'Run full LLC computation',
    '运行 FHA / HB / TD 对比': 'Run FHA / HB / TD comparison',
    '运行分段时域参考': 'Run piecewise time-domain reference',
    '应用匝数到 LLC 主设计': 'Apply turns to the LLC main design',
    '应用 L / DCR 到功率级': 'Apply L / DCR to the power stage',
    '导出设计结果': 'Export design result',
    '设计结果 / 绕组': 'Design result / windings',
    '复制 Auto Tank → User Defined': 'Copy Auto Tank → User Defined',
    '启用 R·I + L·dI/dt 电感压降前馈': 'Enable R·I + L·dI/dt inductor drop feed-forward',
    '建立 / 更新完整数字电压环': 'Build / update the full digital voltage loop',
    '建立小信号对象': 'Build the small-signal object',
    '使用 Control Tools 当前 H(z)': 'Use the current Control Tools H(z)',
    '由功率级工作频率反求 PCMD': 'Solve PCMD from the power-stage operating frequency',
    '生成 C99': 'Generate C99',
    '生成 C99 控制代码': 'Generate C99 control code',
    '运行分析': 'Run analysis',
    '分析摘要': 'Analysis summary',
    '功率级': 'Power stage',
    '功率级/波形': 'Power stage / waveform',
    '双环控制器': 'Dual-loop controller',
    '双环 / Balance': 'Dual loop / Balance',
    '外置滤波/ADC': 'External filter / ADC',
    '8 路采样链': '8-channel sensing chain',
    '电流环 Bode': 'Current loop Bode',
    '电压环 Bode': 'Voltage loop Bode',
    '采样链 Bode': 'Sensing chain Bode',
    'AC 控制细节': 'AC control details',
    'Bode / 稳定性': 'Bode / stability',
    '锁定 ABC 三相参数（硬件滤波共用；关闭后启用增益/偏置失配诊断）': 'Lock ABC three-phase parameters (shared hardware filter; disabling enables gain/offset mismatch diagnostics)',
    '启用 Third-Harmonic / common-mode injection': 'Enable Third-Harmonic / common-mode injection',
    '一键稳定整定并应用': 'One-click stable tuning and apply',
    '一键导出单文件 C99 float32_t / DF2T': 'One-click single-file C99 float32_t / DF2T export',
    '从功率级同步': 'Sync from power stage',
    '包含 Zero-Order Hold': 'Include Zero-Order Hold',
    '导出最终 H(z) — C99 float32_t': 'Export final H(z) — C99 float32_t',
    '当前 PI → New Structure': 'Current PI → New Structure',
    '恢复固件原始 PI': 'Restore firmware original PI',
    '整定参数复位': 'Reset tuning parameters',
    '计算闭环 Step': 'Compute closed-loop step',
    '清除辨识模型': 'Clear identified model',
    '选择并导入 FRA 文件': 'Choose and import an FRA file',
    '打开文档': 'Open document',
    '实现说明（算法与系数约定）': 'Implementation notes (algorithms and coefficient conventions)',
    'FRA Loop Designer V1 契约': 'FRA Loop Designer V1 contract',
    'V1.5 / V2 契约': 'V1.5 / V2 contract',
    'FRA 深度审计': 'FRA deep audit',
    '审计附加说明': 'Audit addendum',
    'V8 实现说明': 'V8 implementation notes',
    'V9 数字控制基线': 'V9 digital control baseline',
    'V9.2.1 发布说明': 'V9.2.1 release notes',
    'PFC 控制台变更': 'PFC control lab changelog',
    '详细结果 / 差分方程': 'Detailed result / difference equation',
    'helpbody.common.language': 'UI language   简体中文 / English / 日本語 / 한국어\nWhere         Right-hand end of any workspace toolbar: "语言 / Language".\n              The choice applies immediately, is remembered, and is reused at\n              the next start.\n\nNotes\n* Wording that is already English in the source (Bode, H(z), C99, Kp, PM, FRA,\n  Summary, ...) is deliberately left as written. Consistent terminology matters\n  more here than translating every word.\n* Help bodies are translated per section. A section that has no body\n  translation yet shows the original text and says so above the body.\n* Switching language changes interface text only; no result, coefficient or\n  exported file is affected.\n* The font stack follows the language (Meiryo / Yu Gothic for Japanese,\n  Malgun Gothic for Korean, Microsoft YaHei / Source Han for Chinese) so shared\n  Han characters are not rendered with Chinese glyph shapes.\n',
}
