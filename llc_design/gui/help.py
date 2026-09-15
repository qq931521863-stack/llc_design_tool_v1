"""In-application help for every workspace.

The toolkit spans four engineering workspaces with a large number of
parameters.  Before V9.2 the only in-app documentation was a one-paragraph
"关于" message box, which left users to read the README to find out what a page
computes, which inputs matter, and — most importantly — which assumptions the
numbers do *not* cover.

This module provides one shared help implementation:

* ``HelpTopic`` content per workspace (quick start, page/parameter reference,
  model boundary and known limits, shortcuts);
* ``install_help(window, topic)`` — a toolbar button with a section menu plus an
  application-wide F1 shortcut;
* ``show_help(parent, topic, section)`` — a section list + text browser dialog.

Content is kept in the application so it still works from a PyInstaller bundle;
"open document" entries fall back to the GitHub blob URL when the local file is
not part of the build.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QTextBrowser,
    QToolBar,
    QToolButton,
    QVBoxLayout,
)

from llc_design import __version__

REPO_SLUG = "yangshuai2022-star/llc_design_tool_v1"
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class HelpSection:
    title: str
    body: str


@dataclass(frozen=True)
class HelpTopic:
    key: str
    heading: str
    intro: str
    sections: tuple[HelpSection, ...]
    docs: tuple[tuple[str, str], ...] = field(default_factory=tuple)


_LLC = HelpTopic(
    key="llc",
    heading="LLC Design 工作区 — 使用说明",
    intro=(
        "LLC 谐振变换器的谐振腔设计、多精度电气分析、磁性器件与损耗、开关/SR 时序、"
        "双相·三相交错、波形、小信号与数字电压环设计。"
    ),
    sections=(
        HelpSection(
            "快速上手",
            """1. 左侧「设计参数」面板（F4 显示/隐藏）填写规格：输入母线、输出电压/电流、
   开关频率范围、谐振参数模式（自动/自定义 Lr-Cr-Lm）等；规格改动后按 Ctrl+R 或
   工具栏「运行当前」重算当前页面。
2. 「设计总览」给出工作点、增益需求与关键应力；确认「增益 / 工作区」中所需
   增益在整条频率范围内可达（灰色区域为不可达，轻载/重载曲线都必须覆盖）。
3. 「Q / ZVS」检查谐振腔 Q 值与理论/工程 ZVS 边界；
   开启工程 ZVS 图后，落在边界外的工作点应当在设计上避开。
4. 需要更精确的电气结果时进入「FHA / HB / TD」比较三种模型精度。
5. 磁件按「变压器」「SR Timing / Loss」逐页核算，或直接用变压器规格书驱动设计
   （TDK PQ 预设 + 整数匝数搜索 + Litz 选型）。
6. 环路设计进入「小信号」得到 Gvf(s)/Gvf(z)；再进入「数字控制」把控制器、
   采样/ADC、FM LUT/TBPRD 调制器与 PWM 延时串成完整 L(z) 并导出 C99。

工具栏：F4 设计参数，F8 运行日志，F9 专注模式，Ctrl+R 运行当前页面。
""",
        ),
        HelpSection(
            "页面说明",
            """设计总览        规格 → 工作点、增益需求、初级/次级电流与电容应力的第一屏汇总。
增益 / 工作区   归一化增益曲线随频率的族图，叠加真实工作点；用于确认目标增益可达。
Q / ZVS         谐振腔 Q 值扫描 + 理论/工程 ZVS 边界，判断全负载范围能否保持 ZVS。
FHA / HB / TD   同一份电气契约下的三种精度：FHA（解析基线）、非线性多谐波 HB、
                分段时域 TD（理想拓扑参考）；比较页报告收敛状态、频率、增益、
                RMS/峰值电流、谐振电容应力与相对最高精度的误差。
变压器          AP/窗口/绕组方案、匝数、电流密度、Dowell 交流铜损、铁损。
SR Timing/Loss  同步整流 Qrr、第三象限导通、Timing、LUT 与损耗核算。
2P / 3P Interleaved  固定 90° 双相与固定 120° 三相交错 LLC 引擎。
波形            工作点对应的开关周期波形与动态相量波形重建。
小信号          LLC 功率级小信号模型 Gvf(s) 与离散化后的 Gvf(z)。
数字控制        H(z) 控制器 + 采样/ADC 延迟 + 调制器 + PWM 延时的完整闭环，
                输出 Bode、PM/GM、闭环极点与单文件 C99 控制器。
""",
        ),
        HelpSection(
            "模型边界与已知限制",
            """* FHA 只保留基波，轻载/宽范围偏离谐振点时误差最大，不能用来宣称 ZVS 裕度。
* HB 求解选定的奇次谐波与整流极性/换向状态的耦合，不是各次谐波的线性叠加；
  在轻载/断续拓扑切换附近可能返回带警告的正则化投影，而不是精确解。
* TD 使用理想开关与分段线性模型，不包含寄生参数、死区细节与器件非线性。
* Q/ZVS 图上的“工程 ZVS”边界是工程判据，不是器件级死区/结电容仿真。
* 小信号与数字环均为平均模型，不含开关纹波、采样抖动与量化噪声。
* 导出的 C99 控制器只包含控制器本身的系数与本文件描述的时序约定；
  实际固件中的采样点、更新时刻与饱和/抗积分饱和仍需在项目侧对齐。
""",
        ),
    ),
    docs=(
        ("README", "README.md"),
        ("V9 数字控制基线", "V9_DIGITAL_CONTROL_BASELINE.md"),
        ("V8 实现说明", "V8_IMPLEMENTATION_NOTES.md"),
        ("V9.2.0 发布说明", "DC/release_v9.2.0.md"),
    ),
)

_PFC = HelpTopic(
    key="pfc",
    heading="PFC Design 工作区 — 使用说明",
    intro="单相 TTPL PFC 与三相 Vienna PFC 的固件形状双环控制、采样链、Bode、完整 AC 周期、开关波形与 PF/THD 分析。",
    sections=(
        HelpSection(
            "快速上手",
            """1. 先在上方子页签选择拓扑：Single-Phase TTPL PFC 或 Three-Phase Vienna PFC。
2. 左侧输入页签依次填写「功率级」「双环控制器」「外置滤波/ADC」；Vienna 为
   「功率级」「双环 / Balance」「8 路采样链」。
3. 电感参数可手填，也可用「电感设计」页由磁芯与绕组反算并一键回填。
4. 按工具栏「运行当前 PFC 分析」（或页面上的分析按钮）执行；结果页给出
   电流环 / 电压环 / 采样链 Bode、完整 AC 周期、开关工作点波形与 PF/THD。
5. 需要量产代码时使用 `power_codegen` CLI 生成 C99 float32 控制器。

TTPL 结果页：电流环 Bode、电压环 Bode、采样链 Bode、电感设计、分析摘要。
Vienna 结果页：Current Bode、Vdc Bode、Balance Bode、Sampling Bode、电感设计、Summary。
""",
        ),
        HelpSection(
            "控制结构与页面说明",
            """* 三相 Vienna：直流电压外环 + 三个 ABC 静止坐标系电流内环；另有
  Split DC Bus 中点平衡环与三电平调制（含共模/三次谐波注入支持）。
* 采样链页把 ADC/SOC 延迟计入采样块，把计算与 PWM 更新延迟计入固件块，
  两者不重复计数；改动采样链会同时影响三个环路的 Bode。
* 「电流环 Bode / 电压环 Bode / 采样链 Bode」默认只画开环响应，可逐条传递函数
  单独显隐，便于定位是哪一段引入的相位损失。
* 完整 AC 周期求解器按时间推进一整条工频周期并给出 settled 结果；
  零交越分析器与开关工作点波形用于检查过零畸变与电流整形质量。
* PF/THD/谐波按整周期结果统计，不是单点近似。
""",
        ),
        HelpSection(
            "电感设计",
            """* 磁芯数据来自 Magnetics High Flux Core Data 254，包含直流偏置下的磁导率跌落
  与磁芯损耗拟合，输出的 L(I) 曲线为满载跌落后的电感。
* 铜损按漆包铜线热态 DCR 计算直流 I²R；交流绕组损耗按项目既有模型核算。
* 「一键回填」把电感量与匝数写回功率级参数，回填后请重新运行分析确认
  电流纹波、环路增益与 THD 仍在设计目标内。
""",
        ),
        HelpSection(
            "模型边界与已知限制",
            """* 控制模型为平均模型：不含开关纹波、死区、器件非线性与数字量化噪声。
* 采样链延迟是决定性假设；如果固件实际采样/更新时刻与页面不一致，
  Bode 的相位裕度会随之偏移，必须按实际固件核对。
* AC 周期求解器假设电网为理想正弦、三相平衡；不平衡与谐波电网需另行验证。
* PF/THD 为模型结果，不能替代 EMI 预合规与实测功率计数据。
""",
        ),
    ),
    docs=(
        ("README", "README.md"),
        ("V9.2.0 发布说明", "DC/release_v9.2.0.md"),
        ("PFC 控制台变更", "PFC_CONTROL_LAB_CHANGELOG.txt"),
    ),
)

_CONTROL = HelpTopic(
    key="control",
    heading="Digital Control Tools 工作区 — 使用说明",
    intro=(
        "数字控制器与滤波器设计：选结构 → 实时看 H(s)/H(z) → 看 Bode/Step/Impulse/"
        "Pole-Zero/Group Delay → 导出单文件 C99 float32。"
    ),
    sections=(
        HelpSection(
            "快速上手",
            """1. 在上面板设置采样率 Fs 与离散化方法 S→Z（tustin / prewarp_tustin /
   backward_euler）。选择 prewarped tustin 时需要填写预畸变频率。
2. 在「Controller」页选控制器类型，只显示该结构真正需要的参数。
3. 面板下方「实时传递函数」立即显示 H(s) 与 H(z)，拖动 Slider 时同步更新。
4. 右侧页签查看 Bode / Step / Impulse / Pole-Zero / Group Delay /
   Coefficients·SOS / Transfer Function / C99 单文件。
5. 导出：填 Symbol Prefix → 工具栏「导出单文件 C99」。
""",
        ),
        HelpSection(
            "控制器类型一览",
            """Integrator            gain
PI                    Kp, Ti
PIF                   Kp, Ti, LPF 极点
PID                   Kp, Ti, Td
PIDF                  Kp, Ti, Td, LPF 极点
Analog Type-II        fp0, fz1, fp1   或 R1/R2/C1/C2
Analog Type-III       fp0, fz1, fz2, fp1, fp2   或 R1/R2/R3/C1/C2/C3
Modified PI           gain, fz1, fp1
Lead / Lag / 1P1Z     gain, fz1, fp1
2P2Z / 3P3Z           等阶有限零极点（gain + N 个零点 + N 个极点）
General H(s)          直接给分子/分母系数

Filter Designer 页签另有 IIR（Butterworth/Bessel/Chebyshev I/II/Elliptic）、
FIR 窗函数、滑动平均与 DC Blocker。

注意：2P2Z / 3P3Z 在手动入口是等阶有限零极点形式；FRA 的 Auto Design 使用
含积分极点的电源补偿器模板，两者语义不同，因此自动设计结果以精确 H(z) 回写。
""",
        ),
        HelpSection(
            "指标与稳定性语义",
            """* 系数约定：H(z) = (b0 + b1 z^-1 + …)/(1 + a1 z^-1 + …)，
  y[n] = Σb[k]x[n−k] − Σa[k]y[n−k]。
* 理想积分器 / PI 在 z=1 有极点，属于 MARGINAL，是可用的控制器但本身不是
  稳定传递函数；真正的不稳定是极点落在单位圆外（UNSTABLE），此时禁止导出。
* 极点半径 > 0.995 时提示：float32 实现与瞬态鲁棒性需要复核。
* 临界频率 > 0.2·Fs 时提示：频率畸变值得关注，考虑预畸变 Tustin。
* Step/Impulse 为离散系统直接响应；Group Delay 为采样点单位，页面换算为秒。
""",
        ),
        HelpSection(
            "C99 导出与已知限制",
            """* 导出为单个 header-only 文件，float32、DF2T/SOS 结构，包含系数、
  初始化/复位与逐步计算接口。
* 工具会用本机 C 编译器编译生成的代码并对比 Python 参考的脉冲/阶跃响应；
  若本机 PATH 中没有 C 编译器，校验会报告 `C compiler not found`，
  这属于环境限制，不代表生成的代码有问题。
* 导出的是控制器数学本身；采样时刻、更新时刻、饱和与抗积分饱和、
  状态复位策略需要在实际固件中按项目约定实现。
* 滤波器设计为理想系数设计，未包含定点量化误差预算。
""",
        ),
    ),
    docs=(
        ("README", "README.md"),
        ("V9.2.0 发布说明", "DC/release_v9.2.0.md"),
    ),
)

_FRA = HelpTopic(
    key="fra",
    heading="FRA Loop Designer 工作区 — 使用说明",
    intro=(
        "基于实测频响（Bode100 / SIMPLIS / 通用 频率-增益-相位）的控制器整定、"
        "自动设计、模型辨识，以及辨识被控对象模型与控制器库的连接分析。"
    ),
    sections=(
        HelpSection(
            "快速上手",
            """1. 「1. FRA / Bode 数据」选择来源格式与 TS 类型，导入文件。
   若数据是完整闭环回路增益（含当前控制器），必须选 Complete Loop TS，
   并在第 2 节如实填写当前控制器，否则剥离出来的被控对象是错的。
2. Complete Loop + Quick Tune：拖动增益/系数尺度观察裕度变化，单位尺度严格复现
   实测环路。Plant TS 或需要换结构时用 New Structure。
3. 右侧「Loop Bode」看开环与新环路，「S / T」看灵敏度与补灵敏度，
   「Closed-Loop Step」看模型推算阶跃（需勾选计算）；
   「Analysis Details」给出全部 0 dB / 相位穿越与门控原因。
4. 目标 Fc/PM 一键综合用工具栏「Auto Design」；
   需要被控对象模型用「Model ID / Fit」辨识，再回传为 Plant Source 做闭环分析。
5. 导出最终 H(z)：第 5 节填 Symbol Prefix → 「导出最终 H(z) — C99 float32_t」。
""",
        ),
        HelpSection(
            "测量语义：TS 类型不能猜",
            """Plant TS           数据已是被控对象，不含控制器：L_new = G_plant · C_new
Complete Loop TS   数据是闭环回路增益 / return ratio，且包含当前控制器：
                   G_eq = H_scan / C_old

Complete Loop 模式下 PWM、ADC、采样/滤波与真实延时不会重复添加，
因为它们已经在实测数据里。软件会立即代数重建 H_scan 并显示幅值/相位重建误差。

重要：重建检查只验证复数除乘与数值实现。填错 C_old 时，错误的控制器会被
数学上除出去再乘回来，重建依然“通过”。硬件控制器来源必须由使用者保证。

若导入的是普通闭环参考→输出传递 T 而不是回路增益，不能直接做剥离；
需先按 L = T/(1−T) 转成回路增益（单位反馈情形）。
""",
        ),
        HelpSection(
            "控制器输入与整定",
            """* 当前控制器可用精确 B/A 系数（推荐），支持规范约定
  y = Σbx − Σay 与固件约定 y = Σbx + ΣAy（软件做 a = −A 转换，绝不猜符号），
  也可用 PI Kp+Ti 加 Fs 与离散化方法。
* Quick Tune 对精确系数控制器直接做系数域尺度调整：
  全局增益 Kx、逐分子系数 b0x…b3x、逐反馈系数 a1x…a3x（支持到 3 阶 / 3P3Z）。
  当前控制器为 PI Kp+Ti 时只提供 Kp 与 Ti 尺度，并保持同一采样率与离散化方法。
* New Structure 可用与 Control Tools 完全一致的控制器库（14 种结构，
  含 Type-II/III 的 R/C 输入）以及 Custom H(z) 精确系数入口。
""",
        ),
        HelpSection(
            "裕度判据与输出",
            """* 输出：开环 Bode、主 0 dB 穿越、全部 0 dB 穿越、PM、GM、最差 PM/GM、
  灵敏度 S = 1/(1+L)、补灵敏度 T = L/(1+L)、Ms、Mt。
* 出现多个 0 dB 穿越时显式告警（多穿越系统不能只看主穿越点的 PM）。
* 有限扫频窗口内从未出现奇数倍 180° 相位穿越时，GM 标记为「未证实」，
  而不是静默判为满足。
* 控制器分析上限为 0.49·Fs；超限数据只作为测量上下文显示，不参与裕度计算。
* Bode100 保留原始相位并默认施加 −180° 回路注入修正，可手动覆盖。
""",
        ),
        HelpSection(
            "Auto Design 与 Model Identification",
            """Auto Design       在原始实测点上按目标 Fc、目标 PM、最小 GM、最大 Ms 综合控制器；
                  目标点不满足全部实测约束时自动降低 Fc 重试。接受与否始终以
                  原始 FRA 点判定，不只看目标点。
Model ID / Fit    对 Equivalent Plant 或当前新开环做 ≤5 极点稳定有理近似
                  （实极点 / 复共轭极点、可选纯延时、控制频段加权）。
                  这是约束非线性有理逼近，不是 Vector Fitting；
                  拟合极点/零点不是物理元件辨识，Raw FRA 始终是稳定性判据。
""",
        ),
        HelpSection(
            "辨识模型 × 控制器与 Step 门控",
            """把 Model ID 得到的「Equivalent Plant」模型点「用于环路设计」回传后，
Plant Source 变为 Identified model，可与控制器库中任意类型组成：

    L(jw) = G_fit(jw) · H_ctrl(e^{jwT})     裕度 / S / T
    T(z)  = L(z) / (1 + L(z))               闭环 Step（精确数字控制器）

Step 是模型推算结果。以下任一情况一律不给 Step，并显示原因：

    拟合置信度 LOW                          WITHHELD_LOW_FIT_CONFIDENCE
    辨识被控对象含右半平面极点              WITHHELD_PLANT_MODEL_NOT_STABLE
    相连开环裕度 FAIL                       WITHHELD_LOOP_MARGIN_FAIL
    离散闭环不稳定                          WITHHELD_CLOSED_LOOP_UNSTABLE
    频带不满足 Fc/Fmin ≥ 10 且 Fmax/Fc ≥ 5  WITHHELD_INSUFFICIENT_STEP_BANDWIDTH

Step 被扣留时 Fc/PM 仍然照常给出：窄带局部拟合可以解释环路形状，
但不足以授权时域预测。任何发送到硬件的阶跃结论都必须与实测瞬态对照。
""",
        ),
        HelpSection(
            "假设与已知限制",
            """1. 实测文件本身不含固件控制器参数；没有真实 C_old 时，Complete Loop 数据
   只能作为导入/裕度/拟合的数值压力样本，不能产出物理有效的被控对象。
2. PM / GM / Ms / Mt 是在“被控对象不含未计入的右半平面极点”这一常规电源
   假设下成立的频域鲁棒性证据，不是与拓扑无关的闭环稳定性证明；
   频响本身无法推断开环 RHP 极点数 P。
3. Ms / Mt 在采样点之间为低置信度：比扫频间隔更窄的谐振峰可能落在采样点之间，
   采样值不能当作数学上界。
4. 有理拟合被约束在稳定半平面，因此无法辨识真正不稳定的开环被控对象。
5. Auto Design 的自动综合当前覆盖 PI / PIF / PID / Power 2P2Z / Power 3P3Z；
   其余结构支持手动设计与精确 H(z) 入口。
""",
        ),
    ),
    docs=(
        ("FRA Loop Designer V1 契约", "DC/FRA_LOOP_DESIGNER_V1.md"),
        ("V1.5 / V2 契约", "DC/FRA_LOOP_DESIGNER_V1_5_V2.md"),
        ("FRA 深度审计", "DC/FRA_DEEP_AUDIT_2026-09-15.md"),
        ("审计附加说明", "DC/FRA_DEEP_AUDIT_ADDENDUM_2026-09-15.md"),
        ("V9.2.0 发布说明", "DC/release_v9.2.0.md"),
    ),
)

_SELECTOR = HelpTopic(
    key="selector",
    heading="功能选择 — 该进哪个工作区",
    intro="工具箱按工程任务分成四个独立工作区，各自保持自己的状态，可用工具栏随时切换。",
    sections=(
        HelpSection(
            "四个工作区",
            """LLC Design          谐振腔设计、多精度电气分析、磁件与损耗、SR、交错、波形、
                    小信号与数字电压环。从规格出发做完整变换器设计时进这里。
PFC Design          单相 TTPL 与三相 Vienna 的固件形状双环控制、采样链、Bode、
                    完整 AC 周期、开关波形、PF/THD 与电感设计。
Control Tools       单独的数字控制器/滤波器设计：选结构、看 H(s)/H(z)、
                    Bode/Step/Impulse/极零点/群延迟、导出单文件 C99。
FRA Loop Designer   基于实测频响（Bode100/SIMPLIS/通用）做控制器整定、目标 Fc/PM
                    自动设计、低阶模型辨识，以及辨识模型 × 控制器的闭环分析。
""",
        ),
        HelpSection(
            "怎么选",
            """* 手上只有规格、要从零设计 LLC 或 PFC → LLC Design / PFC Design。
* 已有功率级，只想设计或复核控制器系数 → Control Tools。
* 手上有一台机器和一份扫频数据，想知道当前环路还差多少裕度、怎么改控制器
  → FRA Loop Designer；若要时域预测，先做 Model ID 再回传为 Plant Source。
* 从 FRA 得到目标控制器后，可在 Control Tools 中复核系数与导出格式，
  或直接在该工作区导出 C99。
""",
        ),
        HelpSection(
            "通用提示",
            """* 每个工作区工具栏右侧都有「帮助」（F1）、「检查更新」与联系方式。
* 更新检查在启动后静默执行一次，有新版本时才提示；可在弹窗中选择忽略该版本。
* 「功能选择」可随时回到本页；各工作区窗口不会因切换而丢失状态。
""",
        ),
    ),
    docs=(("README", "README.md"),),
)

HELP_TOPICS: dict[str, HelpTopic] = {
    topic.key: topic for topic in (_SELECTOR, _LLC, _PFC, _CONTROL, _FRA)
}

_SHORTCUTS = HelpSection(
    "快捷键与操作",
    """F1            打开本工作区帮助
F4            LLC：显示/隐藏设计参数面板
F8            LLC：显示/隐藏运行日志
F9            LLC：专注模式（隐藏参数与日志）
Ctrl+R        LLC：运行当前页面对应的分析
工具栏        各工作区顶部：切换工作区、运行/重算、导出、帮助、更新检查、联系方式
""",
)

_ABOUT = HelpSection(
    "关于",
    f"""电源设计工具箱 Power Design Toolkit V{__version__}
工具设计人：杨帅锅（maileyang@qq.com / 微信 maileyang）
LLC / PFC / Vienna 设计、数字控制工具与 FRA 环路设计。
许可证：GNU GPL v3。

本软件为工程设计辅助工具，所有结果均基于文档化的模型与假设；
发送到硬件前必须按项目流程完成实测验证。
""",
)


def help_topic(key: str) -> HelpTopic:
    if key not in HELP_TOPICS:
        raise KeyError(f"unknown help topic {key!r}; expected one of {sorted(HELP_TOPICS)}")
    topic = HELP_TOPICS[key]
    return HelpTopic(topic.key, topic.heading, topic.intro, topic.sections + (_SHORTCUTS, _ABOUT), topic.docs)


def topic_titles(key: str) -> list[str]:
    return [section.title for section in help_topic(key).sections]


class HelpDialog(QDialog):
    """Section list + text browser, navigable without a browser or PDF reader."""

    def __init__(self, parent=None, topic: str = "selector", section: str | None = None) -> None:
        super().__init__(parent)
        content = help_topic(topic)
        self.setWindowTitle(content.heading)
        self.resize(1000, 700)

        root = QVBoxLayout(self)
        intro = QLabel(content.intro)
        intro.setWordWrap(True)
        root.addWidget(intro)

        row = QHBoxLayout()
        self.section_list = QListWidget()
        self.section_list.setMaximumWidth(230)
        for item in content.sections:
            self.section_list.addItem(QListWidgetItem(item.title))
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        row.addWidget(self.section_list, 0)
        row.addWidget(self.browser, 1)
        root.addLayout(row, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

        self._sections = content.sections
        self.section_list.currentRowChanged.connect(self._render_section)
        index = 0
        if section:
            for i, item in enumerate(content.sections):
                if item.title == section:
                    index = i
                    break
        self.section_list.setCurrentRow(index)

    def _render_section(self, row: int) -> None:
        if row < 0 or row >= len(self._sections):
            self.browser.setPlainText("")
            return
        item = self._sections[row]
        self.browser.setHtml(
            f"<h2>{item.title}</h2><pre style='font-family:inherit;white-space:pre-wrap;'>{_escape(item.body)}</pre>"
        )


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def document_target(relative_path: str) -> tuple[Path, QUrl]:
    """Return the local path (may not exist) and a fallback web URL."""
    local = REPO_ROOT / relative_path
    url = QUrl(f"https://github.com/{REPO_SLUG}/blob/v{__version__}/{relative_path}")
    return local, url


def open_document(parent, relative_path: str) -> None:
    local, url = document_target(relative_path)
    if local.exists():
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(local)))
        return
    # Packaged builds do not ship the Markdown sources; fall back to GitHub.
    QDesktopServices.openUrl(url)
    name = Path(relative_path).name
    if parent is not None:
        QMessageBox.information(
            parent,
            "文档位置",
            f"本地未包含 {name}（打包版本不携带 Markdown 源文件），已尝试在浏览器打开 GitHub 上的同名文档。",
        )


def show_help(parent, topic: str = "selector", section: str | None = None) -> None:
    HelpDialog(parent, topic, section).exec()


class HelpButton(QToolButton):
    """Toolbar button with a section menu; F1 opens the first section."""

    def __init__(self, window, topic: str) -> None:
        super().__init__(window)
        content = help_topic(topic)
        self._window = window
        self._topic = topic
        self.setText("帮助")
        self.setToolTip("本工作区使用说明与模型边界（F1）")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        menu = QMenu(self)
        first = content.sections[0]
        quick = QAction(f"快速上手：{first.title}" if first.title != "快速上手" else "快速上手", self)
        quick.triggered.connect(lambda: show_help(window, topic))
        menu.addAction(quick)
        menu.addSeparator()
        for section_item in content.sections[1:]:
            action = QAction(section_item.title, self)
            action.triggered.connect(
                lambda checked=False, title=section_item.title: show_help(window, topic, title)
            )
            menu.addAction(action)
        if content.docs:
            menu.addSeparator()
            docs_menu = menu.addMenu("打开文档")
            for label, path in content.docs:
                action = QAction(label, self)
                action.triggered.connect(lambda checked=False, p=path: open_document(window, p))
                docs_menu.addAction(action)
        self.setMenu(menu)

        self.help_action = QAction("帮助", window)
        self.help_action.setShortcut(QKeySequence.StandardKey.HelpContents)
        self.help_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.help_action.triggered.connect(lambda: show_help(window, topic))
        window.addAction(self.help_action)


def install_help(window, topic: str) -> HelpButton:
    """Attach the help button and the F1 shortcut to a workspace window.

    The button is appended to the window's last toolbar, so call this *before*
    ``add_toolbar_right_side`` to keep 帮助 next to the other actions instead of
    after the trailing spacer.
    """
    button = HelpButton(window, topic)
    bars = window.findChildren(QToolBar)
    if bars:
        bars[-1].addWidget(button)
    return button


__all__ = [
    "HELP_TOPICS",
    "HelpButton",
    "HelpDialog",
    "HelpSection",
    "HelpTopic",
    "document_target",
    "help_topic",
    "install_help",
    "open_document",
    "show_help",
    "topic_titles",
]
