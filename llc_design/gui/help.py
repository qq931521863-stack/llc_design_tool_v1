"""In-application help for every workspace.

The toolkit spans four engineering workspaces with a large number of
parameters.  Before V9.2 the only in-app documentation was a one-paragraph
"关于" message box, which left users to read the README to find out what a page
computes, which inputs matter, how the numbers are actually produced, and —
most importantly — which assumptions those numbers do *not* cover.

This module provides one shared help implementation:

* ``HelpTopic`` content per workspace: quick start, page/parameter reference,
  **implementation notes**, model boundary and known limits, shortcuts and
  contact information;
* ``install_help(window, topic)`` — a toolbar button with a section menu plus an
  application-wide F1 shortcut;
* ``show_help(parent, topic, section)`` — a section list + text browser dialog.

Content travels inside the application so it still works from a PyInstaller
bundle; "open document" entries fall back to the GitHub blob URL when the local
Markdown file is not part of the build.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

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

# Single source of truth for how users can reach the author.  Keep in sync with
# llc_design/gui/updater.py (toolbar) and README.md.
CONTACT_EMAIL = "maileyang@qq.com"
CONTACT_WECHAT = "maileyang"
CONTACT_BLOG = "开关电源仿真与实用设计"

# WeChat official-account QR code, shipped inside the package so the help
# dialog works from a PyInstaller bundle as well as from a source checkout.
CONTACT_QR_FILE = Path(__file__).resolve().parents[1] / "data" / "wechat_official_account.jpg"


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
   增益在整条频率范围内可达（轻载/重载曲线都必须覆盖）。
3. 「Q / ZVS」检查谐振腔 Q 值与理论/工程 ZVS 边界；工程 ZVS 边界之外的工作点
   应当在设计上避开。
4. 需要更精确的电气结果时进入「FHA / HB / TD」比较三种精度。
5. 磁件按「变压器」「SR Timing / Loss」逐页核算，或用变压器规格书驱动设计
   （TDK PQ 预设 + 整数匝数搜索 + Litz 选型）。
6. 环路设计进入「小信号」得到 Gvf(s)/Gvf(z)；再进入「数字控制」把控制器、
   采样/ADC、FM LUT/TBPRD 调制器与 PWM 延时串成完整 L(z) 并导出 C99。

工具栏：F4 设计参数，F8 运行日志，F9 专注模式，Ctrl+R 运行当前页面。
""",
        ),
        HelpSection(
            "页面说明",
            """设计总览        规格 → 工作点、增益需求、初级/次级电流与电容应力的第一屏汇总。
增益 / 工作区   归一化增益随频率的曲线族，叠加真实工作点；确认目标增益可达。
Q / ZVS         谐振腔 Q 值扫描 + 理论/工程 ZVS 边界。
FHA / HB / TD   同一份电气契约下的三种精度，并比较收敛状态、频率、增益、
                RMS/峰值电流、谐振电容应力与相对最高精度的误差。
变压器          AP/窗口/绕组方案、匝数、电流密度、Dowell 交流铜损、铁损。
SR Timing/Loss  同步整流 Qrr、第三象限导通、Timing、LUT 与损耗核算。
2P / 3P Interleaved  固定 90° 双相与固定 120° 三相交错 LLC 引擎。
波形            工作点对应的开关周期波形与动态相量波形重建。
小信号          LLC 功率级小信号 Gvf(s) 与离散化后的 Gvf(z)。
数字控制        控制器 + 采样/ADC + 调制器 + PWM 延时的完整闭环，输出 Bode、
                PM/GM、闭环极点与单文件 C99 控制器。
""",
        ),
        HelpSection(
            "实现说明 — FHA / HB / TD 三种模型怎么算的",
            """FHA（基波近似）
  桥输出电压只取基波：全桥 4/π·Vbus、半桥 2/π·Vbus；
  副边整流+负载折算成等效交流负载 Rac（8n²/π² 量级，并计入 SR 压降折算）；
  谐振腔按 Lr–Cr–Lm 串联分压求增益，工作点方程用一维标量求根求解。
  只保留基波，所以轻载、远离谐振频率时误差最大。

HB（自洽非线性多谐波平衡）
  不是把各次谐波分别套 FHA 电路再相加。求解器把全波整流钳位这个非线性环节
  与所有选定奇次谐波电流一起自洽求解，对每个奇次谐波 h：
      Ir_h    = (Vbridge_h − Vprimary_h) / Zseries(h·ωs)
      Im_h    = Vprimary_h / Zm(h·ωs)
      Iload_h = Ir_h − Im_h
  Vprimary(t) 由重构负载电流的极性产生，因此整流换向时刻与谐波系数互相自洽；
  输出侧再用平均整流电流平衡方程约束指定阻性负载。
  默认谐波序列自适应 H1 → H1/H3/H5 → H1/H3/H5/H7。
  在轻载/断续拓扑切换附近可能返回带警告的正则化投影，而不是精确解。

TD（分段时域）
  非线性分段线性开关周期稳态求解器：按拓扑分段建立状态方程，
  求周期不动点（稳态），开关按理想器件处理。

小信号（dynamics/plant.py）
  动态相量模型：对基波包络线性化得到 Gvf，再离散化得到 Gvf(z)，
  供数字环页面使用。
""",
        ),
        HelpSection(
            "实现说明 — 数字环与延时怎么建模的",
            """频率域是**混合域**求值（control/digital_loop.py）：
  连续功率级与模拟块在 s = jω 求值，数字块在 z = exp(jωTs) 求值。
  级联顺序：

      模拟分压/滤波 → ADC 多 SOC 递归平均 → PI/PIF/2P2Z → PCMD
      → 分段 FM LUT → PWM/ZOH → Gvf(s)

  另外构造一个全离散近似，专门用于 z 平面极点检查。

延时：先由 application_delay 减去 ADC 有效采样偏移，得到「采样到动作」延时，
再拆成整数采样 + Thiran 一阶分数延时；同时给出 MIN / NOMINAL / MAX
三种延时包络下的裕度，用来观察固件时序抖动对相位裕度的影响。

饱和、burst、软启动、限流选择与保护状态机是非线性的，
因此它们作为「有效条件」列出，不进入线性 Bode 模型。
""",
        ),
        HelpSection(
            "模型边界与已知限制",
            """* FHA 只保留基波，不能用来宣称 ZVS 裕度。
* HB 是选定奇次谐波与整流换向状态的自洽解，不是线性叠加；
  轻载/断续切换附近可能只给出带警告的正则化结果。
* TD 使用理想开关与分段线性模型，不含寄生参数、死区细节与器件非线性。
* Q/ZVS 图上的「工程 ZVS」是工程判据，不是器件级死区/结电容仿真。
* 小信号与数字环都是平均模型，不含开关纹波、采样抖动与量化噪声；
  FM LUT 的分段线性增益、比较器量化等非线性效应未进入线性模型。
* 导出的 C99 只包含控制器系数与文档描述的时序约定；实际固件的采样时刻、
  更新时刻与饱和/抗积分饱和仍需在项目侧对齐。
""",
        ),
    ),
    docs=(
        ("README", "README.md"),
        ("V9 数字控制基线", "V9_DIGITAL_CONTROL_BASELINE.md"),
        ("V8 实现说明", "V8_IMPLEMENTATION_NOTES.md"),
        ("V9.2.1 发布说明", "DC/release_v9.2.1.md"),
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
4. 按工具栏「运行当前 PFC 分析」执行；结果页给出电流环 / 电压环 / 采样链 Bode、
   完整 AC 周期、开关工作点波形与 PF/THD。
5. 需要量产代码时使用 `power_codegen` CLI 生成 C99 float32 控制器。

TTPL 结果页：电流环 Bode、电压环 Bode、采样链 Bode、电感设计、分析摘要。
Vienna 结果页：Current Bode、Vdc Bode、Balance Bode、Sampling Bode、电感设计、Summary。
""",
        ),
        HelpSection(
            "实现说明 — 采样链与延迟拆分（最容易出错的地方）",
            """每个环路的采样链都是显式建模的，包含：

      soc_count / soc_spacing   多 SOC 采样序列
      computation_delay_s       计算延时
      pwm_update_delay_s        PWM 更新延时
      multi_soc_recursive       多 SOC 递归平均

  工程约定（必须与固件一致）：

      ADC / SOC 造成的延时      归「采样块」
      计算与 PWM 更新造成的延时 归「固件块」

  两块**不重复计数**。早期版本曾把两者同时计入而双重计数，
  结果是 Bode 相位多损失一段、裕度偏悲观，现已修正。

  电流环使用 current_computation_delay_s；电压环使用 amc_update_delay_s +
  voltage_computation_delay_s。采样块里的 multi_soc_recursive 平均同时出现在
  电流与电压 Bode 中，所以改动采样链参数会同时影响所有环路。
""",
        ),
        HelpSection(
            "实现说明 — 控制结构与求解器",
            """TTPL（单相）
  固件形状双环：电流内环 + 电压外环；三条采样链（电感电流、输入电压、
  输出电压）分别建模。
  开环 Bode 默认只画开环响应，每条传递函数可单独显隐，便于定位是哪一段引入的相位损失。
  开关电流重构按**跨 PWM 周期连续积分**，不再每周期重置为三角纹波模板
  （那会让重构电流在纹波上出现人为不连续）。
  完整 AC 周期求解器按时间推进整条工频周期直到 settled；
  过零分析器与开关工作点波形都由该结果派生。
  PF/THD/谐波按整周期结果统计，不是单点近似。

Vienna（三相）
  直流电压外环 + 三个 ABC 静止坐标系电流内环 + Split DC Bus 中点平衡环；
  三电平调制支持共模 / 三次谐波注入。每相独立电流环与采样链，
  Balance 环单独给出 Bode 与中点电压响应。
""",
        ),
        HelpSection(
            "电感设计",
            """* 磁芯数据来自 Magnetics High Flux Core Data 254，包含直流偏置下的磁导率跌落
  与磁芯损耗拟合，输出的 L(I) 曲线是满载跌落后的电感。
* 铜损按漆包铜线热态 DCR 计算直流 I²R；交流绕组损耗按项目既有模型核算。
* 「一键回填」把电感量与匝数写回功率级参数，回填后请重新运行分析确认
  电流纹波、环路增益与 THD 仍在设计目标内。
""",
        ),
        HelpSection(
            "模型边界与已知限制",
            """* 控制模型为平均模型：不含开关纹波、死区、器件非线性与数字量化噪声。
* 采样链延迟是决定性假设；固件实际采样/更新时刻与页面不一致时，
  Bode 相位裕度会随之偏移，必须按实际固件核对。
* AC 周期求解器假设电网为理想正弦、三相平衡；不平衡与谐波电网需另行验证。
* PF/THD 为模型结果，不能替代 EMI 预合规与实测功率计数据。
* 中点平衡环与三电平调制的死区/最小脉宽约束不在平均模型内。
""",
        ),
    ),
    docs=(
        ("README", "README.md"),
        ("V9.2.1 发布说明", "DC/release_v9.2.1.md"),
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
   backward_euler）。选 prewarped tustin 时需要填预畸变频率。
2. 在「Controller」页选控制器类型，面板只显示该结构真正需要的参数。
3. 面板下方「实时传递函数」立即显示 H(s) 与 H(z)，拖动 Slider 同步更新。
4. 右侧页签：Bode / Step / Impulse / Pole-Zero / Group Delay /
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
含积分极点的电源补偿器模板，两者语义不同，所以自动设计结果按精确 H(z) 回写。
""",
        ),
        HelpSection(
            "实现说明 — 系数约定与离散化",
            """系数约定（全工具统一）：

      H(z) = (b0 + b1 z^-1 + b2 z^-2 + …) / (1 + a1 z^-1 + a2 z^-2 + …)
      y[n] = Σ b[k]·x[n−k] − Σ a[k]·y[n−k]

  所有数字传递函数在内部先按 a0 归一化，所以页面显示的 a0 恒为 1。

离散化实现（power_control_tools/discretize.py）不用 scipy.cont2discrete，
而是自己做二项式展开映射：

      Tustin (bilinear)   s = 2·Fs·(1 − q)/(1 + q)，q = z^-1
      Prewarped Tustin    同上，但 k = ωp / tan(ωp/(2·Fs))
                          （ωp = 2π·预畸变频率），保证该频率处
                          H(z) 与 H(s) 完全一致
      Backward Euler      s = Fs·(1 − q)

  自己实现的原因：理想 PID 的传递函数是非真分（分子阶数 > 分母阶数），
  cont2discrete 会拒绝，而逐项二项式映射可以直接得到因果 z 形式。
  预畸变频率必须落在 0..Nyquist 之间，否则报错。
""",
        ),
        HelpSection(
            "实现说明 — C99 导出结构",
            """单文件 header-only，typedef float float32_t；二阶节级联（SOS）+ DF2T
 （Direct Form II Transposed）：

      y  = b0*x + d1
      d1 = b1*x − a1*y + d2
      d2 = b2*x − a2*y

  * 系数符号与页面显示的 H(z) 完全一致，不做任何隐式变号；
  * 每节状态只有 d1/d2，状态数最少、数值动态范围最小，适合 float32；
  * 三阶以上自动拆成多个二阶节级联，避免高阶直接型的数值敏感性；
  * Reset() 把全部 d1/d2 清零；Run() 按节依次级联；
  * 导出后会用本机 C 编译器编译生成的代码，与 Python 参考逐点比较脉冲与
    阶跃响应。若 PATH 中没有 C 编译器，校验报告 `C compiler not found`
    —— 这是环境限制，不代表生成的代码有问题，也不假装通过。
""",
        ),
        HelpSection(
            "实现说明 — 稳定性判定",
            """极点按半径分类（power_control_tools/models.py）：

      |p| > 1 + 1e-9     →  UNSTABLE，禁止导出
      |p| ≥ 1 − 1e-9     →  MARGINAL（积分器 / PI 的 z = 1 极点属于此类）
      其余                →  STABLE

  理想积分器与 PI 本身是 MARGINAL，作为控制器构件是合法的，
  因此「可导出」判定只排除 UNSTABLE，同时在面板上提示需要对闭环另行检查。

  另外两条工程提示：
      极点半径 > 0.995       float32 实现与瞬态鲁棒性需要复核
      临界频率 > 0.2·Fs      频率畸变值得关注，考虑预畸变 Tustin
""",
        ),
        HelpSection(
            "模型边界与已知限制",
            """* 导出的是控制器**数学**本身；采样时刻、更新时刻、饱和与抗积分饱和、
  状态复位策略需要在固件中按项目约定实现。
* 滤波器为理想系数设计，不含定点量化误差预算。
* Bode 是离散系统响应；模拟 H(s) 曲线只作对照，二者在接近 Nyquist 时必然分离。
* Group Delay 以采样点为单位计算，页面换算为秒。
""",
        ),
    ),
    docs=(
        ("README", "README.md"),
        ("V9.2.1 发布说明", "DC/release_v9.2.1.md"),
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
            """1. 「1. FRA / Bode 数据」选来源格式与 TS 类型，导入文件。
   若数据是完整闭环回路增益（含当前控制器），必须选 Complete Loop TS，
   并在第 2 节如实填写当前控制器，否则剥离出来的被控对象是错的。
2. Complete Loop + Quick Tune：拖动增益/系数尺度观察裕度变化，单位尺度严格复现
   实测环路。Plant TS 或需要换结构时用 New Structure。
3. 右侧「Loop Bode」看开环与新环路，「S / T」看灵敏度与补灵敏度，
   「Closed-Loop Step」看模型推算阶跃（需勾选计算）；
   「Analysis Details」给出全部穿越点与门控原因。
4. 目标 Fc/PM 一键综合用工具栏「Auto Design」；
   需要被控对象模型用「Model ID / Fit」辨识，再回传为 Plant Source。
5. 导出最终 H(z)：第 5 节填 Symbol Prefix → 「导出最终 H(z) — C99 float32_t」。
""",
        ),
        HelpSection(
            "测量语义：TS 类型不能猜",
            """Plant TS           数据已是被控对象，不含控制器：L_new = G_plant · C_new
Complete Loop TS   数据是闭环回路增益 / return ratio，且包含当前控制器：
                   G_eq = H_scan / C_old

Complete Loop 模式下 PWM、ADC、采样/滤波与真实延时不会重复添加，
因为它们已经在实测数据里。软件会立即代数重建 H_scan 并显示重建误差。

重要：重建检查只验证复数除乘与数值实现。填错 C_old 时，错误的控制器会被
数学上除出去再乘回来，重建依然「通过」。硬件控制器来源必须由使用者保证。

若导入的是普通闭环参考→输出传递 T 而不是回路增益，不能直接做剥离；
需先按 L = T/(1−T) 转成回路增益（单位反馈情形）。
""",
        ),
        HelpSection(
            "控制器输入与整定",
            """* 当前控制器可用精确 B/A 系数（推荐），支持规范约定
  y = Σbx − Σay 与固件约定 y = Σbx + ΣAy（软件做 a = −A 转换，绝不猜符号），
  也可用 PI Kp+Ti 加 Fs 与离散化方法。
* Quick Tune 对精确系数控制器做系数域尺度调整：
  全局增益 Kx、逐分子系数 b0x…b3x、逐反馈系数 a1x…a3x（支持到 3 阶 / 3P3Z）。
  当前控制器为 PI Kp+Ti 时只提供 Kp 与 Ti 尺度，并保持同一采样率与离散化方法。
* New Structure 可用与 Control Tools 完全一致的控制器库（14 种结构，
  含 Type-II/III 的 R/C 输入）以及 Custom H(z) 精确系数入口。
""",
        ),
        HelpSection(
            "实现说明 — 频响求值与裕度判据",
            """H(z) 在任意频点直接按 z^-1 定义代入（不插值、不拟合）：

      q = exp(−j·2πf / Fs)
      H = Σ b[k]·q^k / Σ a[k]·q^k

  相位先 np.unwrap 展开，再按「离最近的奇数倍 180° 分支」计算相位裕度：

      PM = ((phase + 180) mod 360) − 180

  这样多圈相位（例如 −313°、−407°）也能得到有符号且正确的裕度，
  而不是套用只在 −180° 附近成立的 180 + phase。
  相位穿越会遍历 −180° + 360°·k 的所有分支，所以重复穿越与多圈情况都会被列出，
  增益裕度取最差者；出现多个 0 dB 穿越时显式告警。

  分析上限 = 0.49·Fs（用 min(新控制器 Fs, 旧控制器 Fs)）；
  超过上限的数据只作为测量上下文显示，不参与裕度计算。
  Bode100 保留原始相位并默认施加 −180° 回路注入修正，可手动覆盖。

  有限窗口内从未出现奇数倍 180° 相位穿越时，GM 标记为「未证实」，
  而不是静默判为满足。
""",
        ),
        HelpSection(
            "实现说明 — 剥离、重建与辨识算法",
            """剥离与重建（analysis.py）
      G_eq = H_scan / C_old
      H_rebuild = G_eq · C_old
  立即报告幅值/相位最大重建误差（默认阈值 1e-9 dB / 1e-8°）。
  这是复数除乘的实现自检，不是 C_old 来源证明。

有理辨识（fitting.py）——变量投影法：
  1. 先参数化极点：若干个实极点频率 + 若干复共轭对（自然频率 fn、阻尼 ζ）；
  2. 极点给定后分母已知，分子系数用**线性最小二乘**一次解出；
  3. 外层用 least_squares(soft_l1) 搜索极点、阻尼与纯延时；
  4. 多项式变量归一化为 x = s/w_ref（w_ref 取拟合频带几何中心），
     显著改善高阶多项式的条件数；
  5. 阶数 1..5 逐阶尝试，实极点/复共轭组合都试；候选按
     RMS 幅值误差 + 0.2·RMS 相位误差排序，并对控制频段加权；
  6. 纯延时在频域是显式的 exp(−jωTd)；只有做阶跃/极点计算时才转一阶 Padé；
  7. 对最高拟合频点贡献小于 0.1° 相位的延时会被归零，
     避免优化噪声造出假的高频极点对。

  ★ 这是**约束非线性变量投影有理逼近，不是 Vector Fitting**；
    极点/零点是对实测复响应的工程近似，不是物理元件辨识。
    拟合器把极点约束在稳定半平面，因此无法辨识真正不稳定的开环被控对象。
""",
        ),
        HelpSection(
            "实现说明 — 辨识模型 × 控制器与 Step",
            """频域：L(jω) = G_fit(jω)·H_ctrl(e^{jωT})，与原始 FRA 共用同一套裕度引擎。

  时域：先用 ZOH 把 G_fit 采样到控制器采样率，再用**精确离散控制器**闭环：

      T(z) = L(z) / (1 + L(z))      然后用 dstep 求阶跃

  实现细节：项目的系数约定是 z^-1 升幂，而 scipy 时域函数要正幂降序；
  分子需要补 (n−m) 个**尾部**零才能保持采样对齐。
  这一步做错会让整条曲线差一个采样，因此已用独立的 z^-1 差分方程
  （lfilter 直接递推）交叉验证，两者一致到 1e-14。

  Step 授权链按顺序判定，先命中先返回：

      拟合置信度 → 被控对象稳定性 → 环路裕度 → 带宽覆盖 → 闭环稳定性

      拟合置信度 LOW                          WITHHELD_LOW_FIT_CONFIDENCE
      辨识被控对象含右半平面极点              WITHHELD_PLANT_MODEL_NOT_STABLE
      相连开环裕度 FAIL                       WITHHELD_LOOP_MARGIN_FAIL
      频带不满足 Fc/Fmin ≥ 10 且 Fmax/Fc ≥ 5  WITHHELD_INSUFFICIENT_STEP_BANDWIDTH
      离散闭环不稳定                          WITHHELD_CLOSED_LOOP_UNSTABLE

  带宽覆盖阈值与环路拟合路径共用同一常量定义，两条路径不会各自漂移。
  Step 被扣留时 Fc/PM 仍然照常给出：窄带局部拟合可以解释环路形状，
  但不足以授权时域预测。
""",
        ),
        HelpSection(
            "实现说明 — Auto Design 怎么综合的",
            """1. 在当前试探频率 Fc 上，由目标相位裕度反推控制器需要的相位：

         desired_loop_phase = −180° + 目标 PM
         控制器所需相位     = desired_loop_phase − 被控对象在 Fc 的相位

2. 对所选结构，用一维有界搜索（minimize_scalar, bounded）找一个仍可调的
   参数（例如 PI/PIF 的零点频率、Power 2P2Z/3P3Z 的零点基准频率），
   使离散化后控制器在 Fc 的相位等于所需相位；相位误差超过允许值时该结构
   直接判定不可行。
3. 再用一次解析增益求解，使 |L(Fc)| = 1（在 z 域求值，含离散化影响）。
4. 在**原始实测点**上计算全频段 0 dB 穿越、PM、GM、Ms、Mt 并打分：
   多 0 dB 穿越、GM 未观测、Ms 超限都会显著扣分，因此不会只优化目标点。
5. 目标点不满足全部约束时，按几何序列逐级降低 Fc 重试；
   只有全部约束在实测点上同时成立才标记 PASS，否则给出 REVIEW 供人工判断。

   Power 2P2Z / 3P3Z 使用含积分极点的电源补偿器模板：
      K·(s+wz1)(s+wz2)/[s(s+wp1)] 与 K·(s+wz1)(s+wz2)(s+wz3)/[s(s+wp1)(s+wp2)]
  这与手动入口的等阶有限零极点语义不同，因此这类结果以精确 H(z) 系数回写。
""",
        ),
        HelpSection(
            "假设与已知限制",
            """1. 实测文件本身不含固件控制器参数；没有真实 C_old 时，Complete Loop 数据
   只能作为导入/裕度/拟合的数值压力样本，不能产出物理有效的被控对象。
2. PM / GM / Ms / Mt 是在「被控对象不含未计入的右半平面极点」这一常规电源
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
        ("V9.2.1 发布说明", "DC/release_v9.2.1.md"),
    ),
)

_SELECTOR = HelpTopic(
    key="selector",
    heading="功能选择 — 该进哪个工作区",
    intro="工具箱按工程任务分成四个独立工作区，各自保持状态，可用工具栏随时切换。",
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
            """* 手上只有规格、要从零设计 → LLC Design 或 PFC Design。
* 已有功率级，只想设计或复核控制器系数 → Control Tools。
* 手上有机器和一份扫频数据，想知道当前环路还差多少裕度、怎么改 →
  FRA Loop Designer；需要时域预测时先做 Model ID 再回传为 Plant Source。
* 从 FRA 得到控制器后，可在 Control Tools 复核系数与导出格式，
  或直接在 FRA 工作区导出 C99。
""",
        ),
        HelpSection(
            "每个工作区的帮助里有什么",
            """每个工作区的帮助都包含：快速上手、页面与参数说明、
**实现说明**（算法与系数约定到底是怎么算的）、模型边界与已知限制、
快捷键与操作、联系方式。

如果某一步看不明白，或结果与实测/理论不符，建议直接看「实现说明」，
再对照「模型边界」确认该结论在不在模型能力范围内。
""",
        ),
        HelpSection(
            "通用提示",
            """* 每个工作区工具栏右侧有「帮助」（F1）、联系方式与「检查更新」。
* 更新检查在启动后静默执行一次，有新版本时才提示；可记住并忽略某版本。
* 「功能选择」可随时回到本页；各工作区窗口不会因切换而丢失状态。
* 工程问题欢迎直接发邮件讨论（见「联系方式与支持」）。
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
    """F1            打开本工作区帮助（任何工作区）
F4            LLC：显示/隐藏设计参数面板
F8            LLC：显示/隐藏运行日志
F9            LLC：专注模式（隐藏参数与日志）
Ctrl+R        LLC：运行当前页面对应的分析
工具栏        各工作区顶部：切换工作区、运行/重算、导出、帮助、联系方式、更新检查
""",
)

_CONTACT = HelpSection(
    "联系方式与支持",
    f"""邮箱    {CONTACT_EMAIL}
微信    {CONTACT_WECHAT}
公众号 / 技术博客  {CONTACT_BLOG}（见下方二维码，微信扫码关注）

有不懂的地方、拿不准的取舍，或者发现结果与实测/理论不符，
欢迎**直接发邮件**给我。工程问题往往要结合具体拓扑、工况和波形才能判断，
讨论和研究通常比读文档更快，我也很希望知道设计工具在真实项目里卡在哪里。

发邮件时建议附上：

  1. 工作区与版本号（帮助 → 关于，或工具栏「检查更新」旁显示的版本）
  2. 关键输入参数（可截图设计参数面板）
  3. 现象：期望结果 vs 实际结果，最好带截图或导出的 CSV/JSON
  4. 如果是实测相关（尤其 FRA），请说明测量方式、注入点与是否含当前控制器

常见的「看起来不对」其实属于模型边界而不是缺陷，例如 FHA 远离谐振点、
采样 Ms/Mt 落在扫描点之间、Complete Loop 没填真实 C_old；
这些在对应工作区的「模型边界与已知限制」里都有说明。
""",
)

_ABOUT = HelpSection(
    "关于",
    f"""电源设计工具箱 Power Design Toolkit V{__version__}
工具设计人：杨帅锅
邮箱  {CONTACT_EMAIL}
微信  {CONTACT_WECHAT}
公众号  {CONTACT_BLOG}

LLC / PFC / Vienna 设计、数字控制工具与 FRA 环路设计。
许可证：GNU GPL v3。

本软件为工程设计辅助工具。所有结果都建立在各组工作区「模型边界与已知限制」
中写明的模型与假设之上；发送到硬件前必须按项目流程完成实测验证。
有问题欢迎直接发邮件讨论。
""",
)


def help_topic(key: str) -> HelpTopic:
    if key not in HELP_TOPICS:
        raise KeyError(f"unknown help topic {key!r}; expected one of {sorted(HELP_TOPICS)}")
    topic = HELP_TOPICS[key]
    return HelpTopic(
        topic.key,
        topic.heading,
        topic.intro,
        topic.sections + (_SHORTCUTS, _CONTACT, _ABOUT),
        topic.docs,
    )


def topic_titles(key: str) -> list[str]:
    return [section.title for section in help_topic(key).sections]


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_body(text: str) -> str:
    """Render the small markup subset used by the help bodies.

    Bodies are shown inside ``<pre>`` so column-aligned tables survive, which
    means they are escaped first and then only a deliberately tiny markup set is
    applied: ``**bold**`` and the contact e-mail address.
    """
    escaped = _escape(text)
    bolded = re.sub(r"\*\*([^*\n]+?)\*\*", r"<b>\1</b>", escaped)
    return bolded.replace(
        CONTACT_EMAIL,
        f'<a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>',
    )


class HelpDialog(QDialog):
    """Section list + text browser, usable without a browser or PDF reader."""

    def __init__(self, parent=None, topic: str = "selector", section: str | None = None) -> None:
        super().__init__(parent)
        content = help_topic(topic)
        self.setWindowTitle(content.heading)
        self.resize(1040, 740)

        root = QVBoxLayout(self)
        intro = QLabel(content.intro)
        intro.setWordWrap(True)
        root.addWidget(intro)

        row = QHBoxLayout()
        self.section_list = QListWidget()
        self.section_list.setMaximumWidth(250)
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
        body = _format_body(item.body)
        qr = ""
        if item.title == _CONTACT.title and CONTACT_QR_FILE.exists():
            qr = (
                "<div style='margin-top:10px;'>"
                f"<img src='{CONTACT_QR_FILE.as_uri()}' width='220' height='220'><br>"
                f"微信公众号：{CONTACT_BLOG}（微信扫码关注）"
                "</div>"
            )
        self.browser.setHtml(
            f"<h2>{item.title}</h2>"
            "<pre style='font-family:inherit;white-space:pre-wrap;'>"
            f"{body}</pre>"
            f"{qr}"
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
            f"本地未包含 {name}（打包版本不携带 Markdown 源文件），"
            "已尝试在浏览器打开 GitHub 上的同名文档。",
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
        self.setToolTip(
            "本工作区使用说明、实现说明与模型边界（F1）\n"
            f"有问题可直接发邮件：{CONTACT_EMAIL}"
        )
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        menu = QMenu(self)
        quick = QAction("快速上手", self)
        quick.triggered.connect(lambda: show_help(window, topic))
        menu.addAction(quick)
        menu.addSeparator()
        for section_item in content.sections[1:]:
            if section_item.title in {"快捷键与操作", "联系方式与支持", "关于"}:
                continue
            action = QAction(section_item.title, self)
            action.triggered.connect(
                lambda checked=False, title=section_item.title: show_help(window, topic, title)
            )
            menu.addAction(action)
        menu.addSeparator()
        for label, title in (
            ("实现说明（算法与系数约定）", "实现说明"),
            ("模型边界与已知限制", "模型边界与已知限制"),
            ("联系方式与支持", "联系方式与支持"),
        ):
            action = QAction(label, self)
            action.triggered.connect(
                lambda checked=False, t=title: _show_first_matching(window, topic, t)
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


def _show_first_matching(window, topic: str, prefix: str) -> None:
    for section in help_topic(topic).sections:
        if section.title.startswith(prefix):
            show_help(window, topic, section.title)
            return
    show_help(window, topic)


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
    "CONTACT_BLOG",
    "CONTACT_EMAIL",
    "CONTACT_QR_FILE",
    "CONTACT_WECHAT",
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
