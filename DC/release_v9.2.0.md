# Power Design Toolkit V9.2.0 发布说明

发布日期：2026-09-15

V9.2 的主题是 **把实测频响（FRA / Bode）接进数字控制设计链**：新增独立的 FRA Loop Designer
工作区，把控制器类型与控制工具彻底配齐，并把「辨识出的被控对象传递函数」与控制器库真正连起来，
输出开环 Bode、裕度、S/T 与闭环 Step。

## 1. 新增 FRA Loop Designer 工作区

启动后功能选择页新增第 4 个工作区（工具栏可随时切换）。

### 1.1 输入语义（不猜）

| 模式 | 含义 | 处理 |
| --- | --- | --- |
| Plant TS | 数据已是被控对象，不含控制器 | `L_new = G_plant · C_new` |
| Complete Loop TS | 数据是闭环回路增益 / return ratio，**包含**当前控制器 | 只剥离当前控制器：`G_eq = H_scan / C_old` |

Complete Loop 模式下，PWM、ADC、采样/滤波与真实延时**不重复添加**，因为它们已经在实测数据里。
软件会立即代数重建 `H_scan` 并报告幅值/相位重建误差。

> **边界**：重建检查只验证复数除乘与数值实现。用户填错 `C_old` 时，错误的控制器在数学上会被
> 除出去再乘回来，重建依然「通过」。硬件控制器来源仍然是必须的工程输入。

支持的导入格式：Bode100 CSV、SIMPLIS TXT、通用 频率/增益/相位 文本或 CSV。
Bode100 保留原始相位，默认 `-180°` 回路注入修正，并允许手动覆盖。

### 1.2 两种整定方式

- **Quick Tune** — 以当前控制器为基准，只做尺度调整：全局环路增益 `Kx`、逐分子系数 `b0x…b3x`、
  逐反馈系数 `a1x…a3x`（支持规范 `y=Σbx−Σay` 与固件 `y=Σbx+ΣAy` 两种系数约定，`a=-A`）。
  当前控制器为 `PI Kp+Ti` 时，只暴露 `Kp` 与 `Ti` 尺度。单位尺度严格复现实测环路。
  超过 3 阶（4 个分子 / 3 个反馈系数）时提示改用 New Structure 或精确系数。
- **New Structure** — 使用与控制工具完全一致的控制器库重新设计（见第 3 节）。

### 1.3 输出

开环 Bode、主 0 dB 穿越、**全部** 0 dB 穿越、相位裕度、增益裕度、最差 PM/GM、灵敏度 `S = 1/(1+L)`、
补灵敏度 `T = L/(1+L)`、`Ms`、`Mt`，以及最终 H(z) 的单文件 C99 float32 导出。

- 出现多个 0 dB 穿越时显式告警。
- 有限扫频窗口内**从未**出现奇数倍 180° 相位穿越时，增益裕度标记为**未证实（REVIEW）**，
  不静默判为满足。
- 控制器分析上限为 `0.49·Fs`；超限数据仅作为测量上下文显示，不参与裕度计算。

### 1.4 Auto Design（目标 Fc / PM 自动综合）

在**原始实测点**上按目标穿越频率、目标相位裕度、最小增益裕度与最大 `Ms` 综合控制器；
目标点无法满足全部实测约束时自动逐级降低 `Fc` 重试。候选接受与否始终在原始 FRA 点上判定，
**不需要有理拟合**，也**不会**只优化目标点而忽略全频段。

### 1.5 Model Identification（低阶有理辨识）

对实测 Equivalent Plant 或当前新开环做 ≤5 极点稳定有理近似（实极点 / 复共轭极点、可选纯延时、
控制频段加权、`soft_l1` 鲁棒损失、多次起点）。

> 这是**约束非线性变量投影有理逼近，不是 Vector Fitting**；拟合极点/零点是对实测复响应的工程近似，
> **不是**物理元件辨识。Raw FRA 始终是稳定性判据。

对「新开环」拟合时还会做控制关键行为校验：穿越个数一致、`Fc` 一致、`PM`/`GM` 一致、
拟合闭环无右半平面极点，**并**要求测量带宽覆盖足够（`Fc/Fmin ≥ 10`、`Fmax/Fc ≥ 5`）。

## 2. 辨识被控对象模型 × 控制器（本版重点）

此前 Model Identification 的结果是一条「死路」：只能看，不能接到控制器上做开环/时域分析。

现在：

1. Model ID 对 **Equivalent Plant** 拟合完成后，点击 **「用于环路设计」** 把模型（含辨识频带与
   残差置信度）回传到 FRA Loop Designer，Plant Source 切换为 `Identified model`。
2. 此后开环 Bode、裕度、`S`/`T` 与闭环 Step 全部基于该辨识传递函数，控制器可任选控制器库中类型：

```text
L(jw) = G_fit(jw) · H_ctrl(e^{jwT})      频域：与原始 FRA 使用同一套裕度引擎
T(z)  = L(z) / (1 + L(z))                时域：ZOH 采样辨识被控对象 + 精确数字控制器
```

3. 新增 **Closed-Loop Step** 标签页。Measured 被控对象按「被控对象 / 频带 / 阶数」缓存拟合结果一次，
   之后拖动控制器 Slider 实时更新 Step；已回传的辨识模型无需再拟合。

### Step 的授权边界（重要）

Step 是**模型推算结果**，不是实测瞬态。以下任一情况**一律不给 Step**，并显示原因：

| 状态 | 条件 |
| --- | --- |
| `WITHHELD_LOW_FIT_CONFIDENCE` | 拟合残差置信度为 LOW |
| `WITHHELD_PLANT_MODEL_NOT_STABLE` | 辨识被控对象含右半平面极点 |
| `WITHHELD_LOOP_MARGIN_FAIL` | 相连开环裕度检查 FAIL |
| `WITHHELD_CLOSED_LOOP_UNSTABLE` | 离散闭环有单位圆上/外极点 |
| `WITHHELD_INSUFFICIENT_STEP_BANDWIDTH` | 分析频带不满足 `Fc/Fmin ≥ 10` 且 `Fmax/Fc ≥ 5` |

只要 Step 被扣留，`Fc`/`PM` 仍然照常给出——窄带局部拟合适合解释环路形状，但不足以授权时域预测。

## 3. 控制器类型与控制工具配齐

此前 FRA Loop Designer 只提供 PI / PIF / PID / 2P2Z / 3P3Z 五种，与控制工具引擎实际支持的类型不一致。
本版把 `CONTROLLER_LABELS` 与 `controller_parameter_keys()` 提为**唯一事实来源**，
两个工作区共用，并把 Control Tools 的显隐规则也改为由它派生（行为逐类型核对一致）：

Integrator、PI、PIF、PID、PIDF、Analog Type-II（P/Z 或 R/C）、Analog Type-III（P/Z 或 R/C）、
Modified PI、Lead、Lag、1P1Z、2P2Z、3P3Z、General H(s)，外加 **Custom H(z) 精确系数**入口。

同时修复两个此前不可见的参数映射缺陷：

- LEAD / LAG / 1P1Z / Modified PI 在 FRA 界面只传了 `fz1_hz`/`fp1_hz`，而引擎读的是 `fz_hz`/`fp_hz`，
  导致静默使用 1000 Hz / 10 kHz 默认值。现在两边互为别名（旧调用不变）。
- Type-II / Type-III 在 FRA 界面缺少 `fp0`（积分极点）。

### 2P2Z / 3P3Z 语义区分

- 控制工具手动入口的 2P2Z / 3P3Z 仍是**等阶有限零极点**形式，语义未变。
- Auto Design 使用**电源补偿器**模板（含积分极点）：`K(s+wz1)(s+wz2)/[s(s+wp1)]`。
- 两者不能用通用 P/Z Slider 等价表示，因此 Auto Design 的这类结果按**精确 H(z) 系数**回写到
  `Custom H(z)` 模式，而不是静默换参数化。

## 4. 其它修复

- **Complete Loop TS 导入后 summary/details 全部变成 ERROR**：PySide6 会把 str-Enum 的
  `itemData` 还原为普通 `str`，`measurement_kind.currentData().value` 抛 `AttributeError`，
  使导入后的摘要与详情被 ERROR 覆盖。此问题在 v9.1.0 即已存在。
- 导出按钮在没有有效计算时置灰。
- Quick Tune 与「辨识被控对象模型」被设为互斥，避免无意义的报错。
- 清空 log 轴前先复位坐标轴尺度，消除 matplotlib 噪声警告。

## 5. 构建与 CI

- 版本号统一为 9.2.0（`pyproject.toml`、`llc_design/__init__.py`、版本一致性测试）。
- 说明：`dev` 依赖中的 `httpx2` **是有意为之**，不是笔误。`starlette.testclient`
  会优先 `import httpx2 as httpx`，仅在缺失时才回退到 `httpx` 并发出弃用警告。因此
  `httpx2>=2.12,<3` 保持不变。
- 此前 `main` 分支 CI 的失败与依赖无关，而是 `ca21040` 时仍存在的过期测试
  `test_auto_design_pi_hits_requested_fc_and_pm_on_simple_plant`（GM 证据门控加入后未同步），
  该测试已随审计分支合并修正。

## 6. 已知限制

1. Real Bode100 的 Complete Loop 数据文件本身**不含**实际固件控制器参数；没有真实 `C_old` 时，
   它只能作为导入/裕度/拟合的数值压力样本，不能产出物理有效的被控对象或量产控制器。
2. `PM`/`GM`/`Ms`/`Mt` 都是在「被控对象不含未计入的右半平面极点」这一常规电源假设下成立的
   频域鲁棒性证据，不是与拓扑无关的闭环稳定性证明。频响本身无法推断开环 RHP 极点数 `P`。
3. `Ms`/`Mt` 采样点之间为低置信度：比扫频间隔更窄的谐振峰可能落在采样点之间，采样值不能当作数学上界。
4. 有理拟合被约束在稳定半平面，因此**无法**辨识真正不稳定的开环被控对象。
5. Auto Design 的自动综合当前覆盖 PI / PIF / PID / Power 2P2Z / Power 3P3Z；其余结构支持手动设计
   （New Structure）与精确 H(z) 入口，尚未提供自动综合。
6. Step 为模型推算结果，任何发送到硬件前都必须与实测瞬态对照。

## 7. 验证

- `power_control_tools`：70 项测试。在带 gcc 的 Linux 环境下 **0 失败**；
  本机 Windows（PATH 无 gcc）有 5 项因 `C compiler not found` 无法执行，属环境限制。
- 回归重点：Bode100 实测夹具列一致性、多圈相位与重复穿越裕度、
  Auto Power 2P2Z 精确 H(z) 导出并与 float32 C99 逐点对照、辨识模型 × 控制器的开环裕度一致性、
  闭环 Step 与 `z^-1` 差分方程 `lfilter` 独立实现对照（一致到 1.8e-14）、
  全部控制器类型在 FRA 界面可构造且参数显隐正确。
