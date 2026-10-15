# 磁计算、DAB 和三相 PFC 工程扩展

本轮为桌面工具的软件建模与验证扩展，尚无本机硬件实测。所有默认值与图形都应结合列出的模型范围使用。此前 **2.09 倍提速来自缓存和重复计算优化**，不是 AI 的收益。

## 使用入口

| 工作区 | 新入口 | 可直接使用的功能 |
|---|---|---|
| LLC → 变压器设计 | 搜索磁芯 / 形状筛选 / 浏览磁芯参数库 | 选型号自动填写几何、骨架和材料；来源与假设随设计输出 |
| 磁芯参数库 | 实测校核 / 可选 AI | 实测 CSV 对比；独立验证后启用的 3C90 波形损耗预测 |
| DAB | 查看工况 | 选择即后台计算；参数修改后重新选择会使用新参数 |
| DAB → 电气波形 | 周期波形循环播放 | 100 ms 定时器、40 帧一轮、暂停、相位复位；切换页面后停止绘图 |
| DAB | 电压环 / TPS 优化 | PI 输出功率指令到 Vout 的模型、离散 Bode、极点、闭环阶跃、约束搜索、JSON/C99 |
| 三相两电平 | PLL 算法 | SRF / DDSRF / DDSOGI 可用于 dq 平均仿真 |
| 三相两电平 / Vienna | 三种 PLL 对比 / C99 | 同一电压输入下的锁相过程、频率、误差、锁定标志、正序 alpha/beta、代码导出 |
| 三相两电平 / Vienna | 电流应力 / 损耗 | 每相电感、每个器件、电容 RMS/PK；电流波形、损耗分解、PF 与 ITHD |
| 三相两电平 | 控制框图 / 双环稳定性 | 实际多速率采样与 PWM 延迟、电流内环到电压外环、系数与 C99 |
| Vienna | 原 Current / Vdc / Balance Bode | 保留原有采样链与中点平衡分析；完善储能零点和负载模型 |

工况选择会计算当前配置的工况集合；“运行 TPS 扫描”仍可用于主动刷新全部点。动画移动已计算的同一周期数据，不重新求解，也不表示开关暂态仿真。电压环暂态在另一个页面中按能量方程实际积分。

## 数据库与磁计算基准

- 原始 XLS 保留 **137 条**记录，来源精确到 sheet/行；EPC19、ETD19 的 Ve 与 Ae·le 约差 10 倍，PTS14/8 也有几何一致性异常，均保留原值并标记。
- 新增 **44 个**有明确骨架配对的 TDK 2002 年目录预设，加原有 2 个，共 **46 个完整设计预设**。历史记录缺少骨架或材料时仅作参考，不自动杜撰缺失参数。
- 磁芯窗口面积与骨架可绕面积分别保存。例如 PC44PQ35/35：Ae=196 mm²、Ve=17200 mm³、骨架面积154.2 mm²；历史表中的同族型号不能直接覆盖这一组合。
- Amin 缺失时用 Ae 做初筛；可用宽度由骨架尺寸推算；热阻为面积缩放估值。界面和导出均携带这些假设，绝不视为厂家实测值。
- PC40 / PC44 模型来自 PDF p.9 的100°C损耗曲线，温度比例来自 p.6。它们是近似曲线拟合，未宣称是当期产品参数。

数值检验中，4096 点正弦的 iGSE 与 OSE 相对差约 2×10⁻⁷。独立表格点（100 kHz / 0.2 T / 100°C）中，PC40、PC44 的误差分别为 **+12.76%、+8.28%**；该点未参加曲线拟合。数据与脚本：

- [磁计算基准](benchmarks/magnetic_physics.json)
- [独立厂商表格点 / CSV 格式示例](benchmarks/tdk_reference_points.csv)
- `python tools/benchmark_magnetic_physics.py`
- 原始资料重建：`python tools/import_magnetic_catalog.py <磁芯参数列表.xls> <TDK磁性材料规格参数.pdf>`

实测 CSV 的五列为 `material_key,frequency_hz,temperature_c,b_peak_t,measured_pv_w_m3`。当前比较针对正弦激励下的磁芯损耗密度，整机损耗、绕组损耗、非正弦波形不能直接混用。不会自动修改系数“消除”误差。**本机实测误差归因仍需要实际 B(t)、电压/电流波形、温度和独立损耗数据。**

## AI 的独立验证与接入边界

在 Fundamentals_of_AI_for_PE 固定版本的 MagNet 派生 3C90 数据上重新训练；没有复制原训练代码。共3257条1024点波形；**全部70°C数据821条留作独立测试**；其余1948条训练、488条验证。缩放、物理系数拟合和网络选择均只使用开发集。

| 模型 | 独立测试 MAPE | P95绝对百分比误差 |
|---|---:|---:|
| 拟合 OSE | 34.92% | 85.45% |
| 拟合 iGSE | 23.26% | 52.89% |
| iGSE＋残差 FNN | **3.95%** | **8.39%** |

门槛为相对 iGSE 至少降低10% MAPE，且 P95不恶化，测试通过后才导出模型。已核对 NumPy 独立推理与训练模型的测试误差一致。

可选推理要求：3C90、1024点等间隔周期 B[T]、不重复端点、训练数据的 f/T/Bpk 包络内、无明显直流偏置。超范围会拒绝；不替代 PC40/PC44 等其他材料，也不预测饱和、绕组损耗或温升。这只是温度工况留出验证，不能证明任意波形或不同磁芯的一般化能力。

[完整结果、数据哈希和范围](benchmarks/magnetic_ai.json)。复现需研究依赖：

```bash
python -m pip install -e '.[research]'
OPENBLAS_NUM_THREADS=1 python tools/benchmark_magnetic_ai.py --data <Magnetic_Modeling目录>
```

## DAB 电压环与约束优化

电压误差 → LLC 共用的离散 PI/PIF/导入 C(z) → Pcmd[W] → `p=Pcmd/Pbase(Vout)` → TPS 的 D1/D2/D3 → 周期传输功率 → 输出电容/负载 → Vout。

局部传递函数为

\[
G_{vP}(s)=\frac{\partial P_{out}/\partial P_{cmd}}
 {C_oV_0s+\partial P_{load}/\partial V-\partial P_{out}/\partial V}.
\]

局部导数由同一 TPS 周期功率映射差分得到，支持理想 L 或包含 Cp 的周期映射。闭环对 ½CoV² 积分，包含指令限幅、控制器状态处理和整拍延迟；支持电阻/CPL负载。**没有 Cp 的动态状态，不是 Cp/L 完整暂态小信号模型**。控制系数原样复用 LLC 数字模块，C99 仅输出线性控制器，不生成 PWM/ADC/BSP。

默认400 V / 3500 W：相位裕度84.3°、交越约40.0 Hz；参考＋5 V、负载＋20%测试最终误差小于0.001 V。稳定性只针对所选局部工作点，不包含限幅后的全局保证。

优化采用理想 L 精确分段积分，比较原公式、9³网格、多起点SLSQP；输出经过 PWM 整数计数量化，再逐项检查功率、峰值电流、有效脉宽及可选换流电流极性。低功率时会扩展局部计数邻域。不保证全局最优；换流极性仅为代理条件，**ZVS仍为UNKNOWN**。

| 目标功率 | 量化复核功率 | 优化 Irms |
|---:|---:|---:|
| 1000 W | 996.27 W | 2.555 A |
| 3500 W | 3499.61 W | 9.530 A |
| 6000 W | 5981.78 W | 17.942 A |

这三点均满足本次0.5%功率容差。粗网格可能找不到满足功率容差的点，原公式四舍五入后也可能不满足；界面保留各自失败原因。

另训练6000条不同电压的理想 L 标签、留出400 V，比较代理模型预选＋物理约束精修。包含在线预测和量化复核后，速度比约 **0.78×**，未通过收益门槛，**不接入正式优化器**。训练耗时单列，未算成在线提速。仅3个优化点也不足以证明全工况收益。

- [优化结果与计时](benchmarks/dab_optimization.json)
- `OPENBLAS_NUM_THREADS=1 python tools/benchmark_dab_optimization.py`

## PLL 与三相/Vienna 控制

DDSOGI 在界面中明确表示 **双 SOGI＋正序提取的 DSOGI-PLL**，不是多谐波 MSOGI。三种实现均只读取三相电压，包含归一化误差、PI/VCO、频率限制、低电压失锁和连续锁定资格。理想角度仅用于评估，不反馈到控制器。

默认0.2 s同时施加52 Hz、+20°相位、15%负序、5%五次、3%七次谐波及0.8 pu跌落：

| PLL | 尾段相位误差 RMS | 频率纹波 p-p | 扰动后重新锁定 |
|---|---:|---:|---:|
| SRF | 1.665° | 7.945 Hz | 未持续锁定 |
| DDSRF | 0.071° | 1.085 Hz | 78.45 ms |
| DDSOGI / DSOGI | 0.0085° | 0.129 Hz | 78.30 ms |

这是相同默认参数下的一组对比，不能视为全工况排名。DDSRF采用双同步坐标解耦和低通，DSOGI采用双线性离散的两路SOGI与序分离。C99为独立实现，通过主机 GCC `-std=c99 -Wall -Wextra -Werror` 编译，并逐点对比三种算法的相位、频率及锁定标志；未进行 TI 编译器/目标板验证。

两电平电流对象：`Gi(s)=1/(Ls+R)`，PI输出单位为V。电压对象考虑电感能量，包含

\[
G_{vi}(s)=\frac{1.5(V_d-2RI_d)-1.5LI_ds}{CV_{dc}s+\partial P_{load}/\partial V}.
\]

将电流控制器、RL对象和PWM延迟在电流速率下组成状态模型，再提升到电压采样速率，保留内环和多速率保持效应。默认电流/电压环相位裕度约62.9°/72.2°。局部模型假设理想解耦和电压前馈，不包含PLL和传感链动态；abc非线性闭环仿真另行提供。

Vienna保留原有静止坐标电流控制及中点平衡结构，未冒用两电平SVPWM。电压对象更新为

\[
G_{vG}(s)=\frac{3V_{ph,rms}^2(1-2RG_0-LG_0s)}
 {C_{eq}V_{dc}s+\partial P_{load}/\partial V},\qquad
C_{eq}=\frac{C_+C_-}{C_++C_-}.
\]

原有采样链、延迟、内环传递和中点环分析仍保留。中点对象为配置增益近似，应结合非线性分裂母线响应校验。

## 电流、损耗与效率的范围

全工频周期抽取256个工况，每点重构128点中心对齐载波周期，采用浮动中性点和局部电感纹波；RMS/PK由重构电流积分。两电平按上下管分配电流；Vienna可选反串双MOS或二极管桥＋单MOS结构，分别列出内桥及外二极管。开关能量按硬换流器件及电流极性分配，避免将两个反串MOS都计作硬开关。

- 电感：低频 I²R、HF纹波铜损×用户 Rac/Rdc、可选 LF＋局部iGSE磁芯损耗、Bpk及饱和裕度。
- 器件：Rds(Tj)导通、参考Eon＋Eoff线性V/I缩放；不再额外叠加Coss。二极管按 Vf＋Rd 模型。
- 电容：瞬时母线流入电流减去负载电流；Vienna上下电容分别计算，ESR按每组输入。
- PF包含重构高频电流RMS；ITHD单列2～40次低频谐波，并另报含HF的总畸变。

参数为0表示缺失。输出包括缺项列表、已计损耗和“仅含已计项”的效率；不输出未经配置的完整效率结论。损耗为后处理，不反馈到原平均仿真。

这一重构不是独立开关暂态解，不含死区/器件寄生/独立反向恢复；Vienna过零的DCM区会警告。磁芯 LF＋HF 损耗分离、直流偏磁和小回线交互均需实测校核。

## 复现和参考来源

`OPENBLAS_NUM_THREADS=1 python tools/benchmark_controls.py` 生成[控制验证快照](benchmarks/control_validation.json)。测试范围和交付时的环境限制见[回归记录](benchmarks/regression.md)。

参考仓库固定版本：

| 仓库 | 固定版本 | 本轮吸收的方法 |
|---|---|---|
| [MKF](https://github.com/tinix84/MKF) | b26ea2135e01ee380e71df20b4bbe7739d57ceca | 波形和材料工况分离、损耗建模与验证 |
| [inductor_optimizer](https://github.com/tinix84/inductor_optimizer) | 9e568512b9b694339f3af63136b87768a40f2705 | 几何/损耗/约束的联合优化思路；未复制无明确许可的代码 |
| [choke_ac_model_fitting](https://github.com/tinix84/choke_ac_model_fitting) | cbe070c874c77876a163a3761beab2326612bfa0 | 复阻抗拟合与参数可辨识性；独立实现 |
| [Fundamentals_of_AI_for_PE](https://github.com/tinix84/Fundamentals_of_AI_for_PE) | 8dd961c185fcffd777382faa2aefe67e23cef4ca | 训练/验证/独立测试、物理残差模型和收益门槛 |

PLL交叉核对：[TI SPRABT4](https://www.ti.com/lit/pdf/sprabt4)、[TI DDSRF接口/源码文档](https://software-dl.ti.com/C2000/c2000_apps_public_sw/c2000ware_sdk/digitalpowersdk/5_03_00_00/html_guide/spll__3ph__ddsrf_8h_source.html)、[imperix SOGI说明](https://imperix.com/doc/implementation/sogi-pll)。未复制供应商源码。

原XLS/PDF未重新打包，JSON中记录SHA256、页码、表格行和数字化曲线坐标。AI原始数据也未打包；模型参数、数据哈希和再训练脚本单独记录来源，不把上游教育内容、第三方数据许可混同为本项目代码许可。
