# 多谐波准 PR 与磁计算更新

## 使用入口

- Digital Control Tools → **Controller — 多谐波准 PR**。
- 勾选 1、3、5、7、9、11 次中的任意组合，各支路独立设置 Kr、wc/(2π)。
- 左侧设置 Fs、基频、公共 Kp、输出上下限；右侧查看各支路与合成 Bode、float32 系数、全部 C99 函数。
- 工具栏和页面上的 C99 导出均导出当前 PR 设计；另可导出 JSON 参数与系数。
- PFC 的 TTPL / Vienna 电感结果页 → **实测复阻抗拟合 / SPICE 模型**。

## 模型与用户代码对应

\[
G(s)=K_p+\sum_{h\in\mathcal H}\frac{2K_{r,h}\omega_{c,h}s}
{s^2+2\omega_{c,h}s+(2\pi h f_g)^2},\quad\mathcal H\subseteq\{1,3,5,7,9,11\}.
\]

用户提供的 `updata_pr_ctrl_coeff` 当前 `#else` 为纯 R，原 `pr_ctrl_ys_func` 仅返回基波 R 输出，Kp 未参与输出。新实现使用同一 Tustin 系数公式，并联相加后只叠加一次公共 Kp。Kp=0 是纯谐振补偿；所有支路不选中时是纯 P。不将并联支路展开成十二阶多项式，也不把它们错误地串联为 SOS。

每支路令 q=(Ts·wr)²，c=4wc·Ts，D=q+c+4：

| 系数 | 纯 R 支路 |
|---|---|
| B0 | Kr·c/D |
| B1 | 0 |
| B2 | −B0 |
| A1 | (2q−8)/D |
| A2 | (q−c+4)/D |

分母约定 `1+A1 z⁻¹+A2 z⁻²`，递推保持用户 DFII 形式：

```text
W0 = error − A1·W1 − A2·W2
y  = B0·W0 + B1·W1 + B2·W2
W2 = W1; W1 = W0
u  = Kp·error + sum(y_h)
```

wc、wr 在 C 系数函数中均为 rad/s；Ts 是秒。GUI 的 `wc/2π [Hz]` 是带宽参数，模拟支路完整 −3 dB 带宽为其两倍。各阶 wr=h·2π·fgrid，wc 不自动乘 h。

保持用户无预畸变 Tustin。实际数字峰值中心为 `Fs/π·atan(π·h·fgrid/Fs)`，高频支路存在频率压缩。Bode 同时绘制模拟、float64 数字和实际 float32 系数响应，在各谐振中心加密采样。Bode 不含限幅/冻结的非线性，也不是含功率级的开环或闭环稳定性结论。

## C99 函数与接入约定

已有 TI 头文件定义 `float32_t` 时，包含导出文件前定义 `POWER_CTRL_FLOAT32_T_DEFINED`，复用该类型。

默认前缀 `MULTI_PR`，保留用户字段 `coeff_B0/B1/B2/A1/A2`、`filter_W0/W1/W2/filter_out`。导出类型带前缀以避免与附件已有定义冲突；**不是整个 pwr_ctrl.h 的替换文件**，没有复制其硬件配置。

| 导出函数 | 用途 |
|---|---|
| `MULTI_PR_updata_pr_ctrl_coeff` | 在控制停用的后台更新一个二阶支路系数；返回成功/失败，失败不改系数。kp=0 为 R，kp>0 为单个完整 PR；多谐波并联支路应传 kp=0。 |
| `MULTI_PR_iir_2order` | 用户形式的单节 DFII 更新；调用者负责有效指针及有限输入，通常使用顶层函数。 |
| `MULTI_PR_Init` | 写入当前导出参数/实际 float32 系数，清零全部状态，标记初始化完成。 |
| `MULTI_PR_Reset` | 保留配置并清零所有支路状态、输出和诊断标志。 |
| `MULTI_PR_pr_ctrl_ys_func` | 每 Ts 执行一次，公共 Kp + 所选 R 支路、限幅、状态提交及非有限值处理。 |

```c
#include "multi_pr.h"
static MULTI_PR_PR_CTRL_YS_DATA_DEF g_pr;
/* Before enabling the control loop: */
MULTI_PR_Init(&g_pr);
/* Once per configured Ts, after fresh scaled measurements/protection checks: */
float32_t command = MULTI_PR_pr_ctrl_ys_func(current_error, &g_pr);
/* Existing fast-loop safety gate checks g_pr.fault and owns the PWM commit. */
```

- 示例误差/输出单位由应用定义，增益单位为输出/输入；不是自动转换为电压、安培或占空比。
- 一个 CPU/CLA 任务拥有状态。不要在 ISR 执行期间修改系数；停用控制、成组更新、复位后再启用。系数更新使用 double 中间量，只放后台，不用于每次 ISR 跟随 PLL。
- Init 先执行，首个 Run 即处理误差，不丢弃首个采样。禁用控制时调用 Reset。
- 默认与用户代码一致，仅对总输出限幅，**不是抗饱和措施**。可选冻结所有支路的本次状态更新；该策略需在具体闭环中验证，不能视为普适抗饱和解法。
- NaN/Inf 输入、状态运算溢出：清状态、返回 0、置 fault；零输出是否安全由原有安全门判断。本模块不诊断断线、不授权 PWM、不改 Trip Zone。
- 导出前对量化后的每个分母执行严格 Jury 稳定性检查。极窄带/极高 Fs 导致 float32 极点舍入到单位圆时拒绝导出。通过该检查仍不消除 DFII 内部大状态和舍入误差。
- GCC C99 测试使用 `-O2 -Wall -Wextra -Werror -pedantic -ffp-contract=off`。没有进行 TI 编译、ISR 周期测量或硬件闭环验证；不能据此宣称 MISRA 合规或可直接上高压。

可重现示例：`python -m examples.multi_pr.generate`。示例为 Fs=40 kHz、fgrid=50 Hz、Kp=0.1，各阶 Kr=10、wc/2π=1 Hz、限幅 ±1，仅用于展示，不是附件硬件参数或闭环整定值。

- [示例 C99](../examples/multi_pr/multi_pr.h)
- [示例系数 JSON](../examples/multi_pr/coefficients.json)
- [示例 Bode](../examples/multi_pr/bode.svg)

## 磁计算实际改动

1. LLC Litz 与 Dowell 共用 RMS 复数频谱计算；修正偶数采样数时 Nyquist 单边频点被额外乘 √2 的问题。用奇偶采样数的 Parseval 等式验证，不丢弃相位关系。
2. Litz 单次绕组计算复用相同波形对象的 FFT/RMS；圆线趋肤因子使用 4096 项有界缓存（键含线径、频率、温度）。这是纯计算缓存，不复用旧温度/频率的结果。
3. 贝塞尔比值改为相同指数缩放的 `ive(0,z)/ive(1,z)`，避免大参数下 `iv/iv` 的 inf/inf。常规范围与原公式一致。
4. 新增无源等效电路 `(Rs+sL) ∥ Rp ∥ Cp` 的复阻抗拟合：同时拟合实/虚部，按 |Z| 归一残差，正参数对数域、有界最小二乘、三个确定性初值和 soft-L1 损失。
5. 输出残差、参数可辨识性条件数、测量频段和诊断；导出 JSON/SPICE。只拟合真实提供的数据，不外推补点。未覆盖谐振时提示 Cp/Rp 缺少独立验证。

CSV 列名和单位必须明确：

```csv
frequency_hz,real_ohm,imag_ohm
100,0.02,0.0754
```

至少 8 个频率递增的有效复阻抗样本。若仪表给出 Rs 与 Ls，可在相应频段用 `Im(Z)=2πf·Ls` 转换；不可将阻抗模值直接当实部。模型仅代表测量温度、偏置和频段下的小信号阻抗，**不会代替大信号磁芯损耗、饱和或 DC bias 模型**，也不会自动覆盖原设计数据库。

性能测量：对比提交 `5351b1f`，6 层共享两组周期波形、2048 点、31 次谐波、100 次循环取 5 轮最短时间，热缓存。此环境单次 2.7447 ms → 1.3135 ms（约 2.09×），总损耗最大差异 8.88e−16 W。只是该本机计算场景，不是整个 GUI/搜索流程或 C2000 时间；不同数据和硬件结果会变化。

## 四个参考仓库的处理

所有仓库按请求浅克隆至参考目录，仅做源码和文档核对，没有执行仓库脚本，没有将其源代码或测量数据直接复制进本项目。

| 仓库与快照 | 参考点与本次边界 |
|---|---|
| [tinix84/MKF](https://github.com/tinix84/MKF), `b26ea2135e01ee380e71df20b4bbe7739d57ceca` | README、CoreLosses、WindingSkinEffectLosses：逐谐波建模、模型适用范围。已有 LLC iGSE/Litz 路径保留；本次增强频谱正确性和重复计算性能。许可证文件为 BSD-4-Clause，没有移植其实现。 |
| [tinix84/inductor_optimizer](https://github.com/tinix84/inductor_optimizer), `9e568512b9b694339f3af63136b87768a40f2705` | ObjectiveFunction / InductCalculation：损耗、重量、电感误差联合评估，依赖 MATLAB/FEMM；当前快照未发现许可证。未移植源代码，也未宣称已接入 FEMM 优化。 |
| [tinix84/choke_ac_model_fitting](https://github.com/tinix84/choke_ac_model_fitting), `cbe070c874c77876a163a3761beab2326612bfa0` | f_Zeq / runme：扼流圈等效电路与测量拟合思路；GPL-3.0。新模块由电路导纳公式独立实现，同时使用复数实/虚部，未复制 MATLAB 脚本或样本数据。 |
| [tinix84/Fundamentals_of_AI_for_PE](https://github.com/tinix84/Fundamentals_of_AI_for_PE), `8dd961c185fcffd777382faa2aefe67e23cef4ca` | README / Classic ML：代理模型、验证集、贝叶斯优化的教学案例。代码 Apache-2.0，教学内容 CC BY-NC 4.0；未复制材料。缺少本项目实测磁性训练集，因此未引入未经训练/验证的 AI 损耗预测器。 |

## 验证范围

本轮 **161 passed**；GCC 编译无警告，wheel 构建通过并检查新增模块已包含。76 条 Python 警告均来自既有 FRA GUI 测试的 DejaVu Sans 中文缺字，不是本次 PR/拟合计算警告。

- 64 种谐波组合、独立 SciPy bilinear 交叉校验、模拟/数字峰值及频率压缩、并联时域与独立 lfilter 求和。
- float32 稳定性拒绝、限幅/冻结、初始化/复位、NaN/Inf、纯 P 和多支路导出；编译 C 与 Python float32 逐样点一致。
- 40 kHz 下 120000 点多谐波输入，稳态频响与实际 float32 系数 Bode 比较。
- 无噪声及有噪声合成复阻抗参数恢复、频段诊断、错误 CSV、Nyquist/Parseval、缩放贝塞尔与缓存。
- 桌面 PR 页面选择/导出、错误参数清理、PFC 拟合入口和 JSON/SPICE 导出；现有控制器、Litz、Dowell、PFC 电感相关回归。

完整硬件头文件只用于核对 PR/IIR 类型与接口，没有复制或改动其中 ADC、PWM、保护、继电器或状态机配置。
