# Codex 本地电源设计 MCP

本地源码：`/Users/yangshuai/Applications/LLC_TOOLS/`。入口：`power-design-mcp` / `python -m power_agent.mcp_server`，使用官方 Python MCP SDK v2 和 stdio。无需启动 Web、Qt、C2C 或公开隧道。

本轮范围是 LLC 输入校验、不可变设计快照、单设计分析、Ln/Q/fr 有界优化、显式 FHA 角点校核、FRA 数字控制器自动设计、主机验证的 C99 导出，以及 JSON/HTML 证据报告。PFC/DAB/PSD 尚未通过本入口自动编排；它们的桌面能力不能当作已暴露的 MCP 能力。

## 安装与连接

在项目目录使用 Python 3.10+ 的独立环境（本机已验证 Python 3.12）：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[agent,dev,web]'
```

已有 uv 环境时可用 `uv pip install --python .venv/bin/python -e '.[agent,dev,web]'`。运行 MCP 本身只需要核心与 `agent` 依赖，`dev,web` 用于相关回归。

本机 Codex 注册命令：

```bash
codex mcp add power_design_toolkit \
  --env POWER_DESIGN_WORKSPACE=/Users/yangshuai/Applications/LLC_TOOLS/output/agent \
  --env MPLCONFIGDIR=/Users/yangshuai/Applications/LLC_TOOLS/output/agent/cache/matplotlib \
  --env XDG_CACHE_HOME=/Users/yangshuai/Applications/LLC_TOOLS/output/agent/cache \
  -- /Users/yangshuai/Applications/LLC_TOOLS/.venv/bin/power-design-mcp
codex mcp list
```

新配置需要新的 MCP 连接/会话加载；已经运行的对话不保证动态获得工具。更换源码、数据或依赖后重启 MCP，并创建新设计快照。不要让模型运行 shell 来代替本工具的计算接口。可移植环境请换成实际绝对路径。

官方配置依据：https://learn.chatgpt.com/docs/extend/mcp?surface=cli 。`~/.codex/config.toml` 是个人配置，不提交到仓库。本文提供的 stdio 配置不改变现有 `mcp/server.mjs` 的服务或授权。

## 工具与顺序

| 工具 | 行为 |
|---|---|
| `toolkit_capabilities` | 当前已注册工具、流程、预算和证据边界 |
| `llc_defaults` | 默认参数、SI 单位和完整公共输入 JSON Schema |
| `design_validate_spec` | 拒绝未知字段、布尔数值、非有限值和超界输入；展示实际默认项，无可行性声明 |
| `llc_create_design` | 保存输入、有效默认值、源码/数据哈希和数值依赖版本，返回 `design_id` |
| `llc_analyze` | 保留原来的同步单设计分析接口，添加输入与版本校验 |
| `llc_submit_analysis` | 对快照启动后台完整系统分析 |
| `llc_optimize` | 显式搜索 Ln/Q/fr，其他参数锁定；默认执行现有两阶段模型校核 |
| `llc_check_corners` | 扫描显式母线、负载及 Lr/Lm/Cr 比例的笛卡尔积，保留最差点与未知状态 |
| `job_get` / `job_cancel` | 查询任务/获取结果，或终止本服务会话拥有的子进程 |
| `design_export` | 将完成的计算及原输入导出 JSON/HTML，包括 FAIL/UNKNOWN 等结果 |
| `control_auto_design` | 根据用户提供的等效对象 FRA 自动设计 PI/PIF/PID/2P2Z/3P3Z |
| `control_export_verified_c99` | 调用现有 C99 生成及主机 float32 验证器，失败/缺编译器时保持编译阻断 |
| `validate_engineering_data` | 保留原来的数据来源和硬件就绪校验 |

推荐提示词：

> 使用 power_design_toolkit，设计 400 V 标称母线、360–420 V 正常范围、300 V 保持末端、53 V / 3 kW 的全桥 LLC。先展示缺失条件及默认项，固定匝数与器件，在我给定的 Ln/Q/fr 范围内搜索；保存设计、执行两阶段模型校核及指定角点扫描，最后输出候选比较与报告。不得把模型结果称作硬件验证。

按工具参数发送的例子：

```json
{"spec":{"vbus_nom_v":400,"vbus_min_normal_v":360,"vbus_max_v":420,"vbus_hold_end_v":300,"vout_v":53,"pout_w":3000,"primary_topology":"FULL_BRIDGE"}}
```

先给 `design_validate_spec`，审视返回的 `defaulted_fields`；使用同一 `spec` 调用 `llc_create_design`。若条件不足以决定设计，Codex 应向用户确认缺失约束；工具不会把缺失条件变成用户要求。

创建后调用 `llc_optimize`：

```json
{"design_id":"返回的64位ID","request":{"ln_values":[4.5,5.0],"q_values":[0.30,0.35],"fr_values_hz":[100000],"top_n":1,"verify_time_domain":true,"verification_scope":"critical"},"timeout_seconds":600}
```

工具立即返回 `job_id`；用 `job_get` 查询。`include_result=true` 只在需要完整数据时使用。优化默认只搜索快照中的一个点；扩展轴必须显式给出。仅 `AUTO_DESIGN` 可优化，用户指定 Lr/Cr/Lm 的模式会被拒绝，不能悄悄转换。

角点示例：

```json
{"design_id":"待校核候选的64位ID","request":{"vbus_values_v":[300,360,400,420],"load_fractions":[0.1,0.5,1.0],"lr_scales":[0.95,1.05],"lm_scales":[1.0],"cr_scales":[0.95,1.05]},"timeout_seconds":300}
```

对优化候选，应使用候选自己的 `design_id`。修改规格会产生新 ID，既有任务不会自动绑定新输入。零负载保留 UNKNOWN；非零但低于 FHA 最小建模负载的请求被拒绝，不进行隐式夹紧。扫描结果只覆盖明确的点，不代表连续范围、磁件热设计或完整损耗验证。

## 状态与约束

任务状态与设计状态分开：

- `RUNNING / COMPLETED / FAILED / CANCELLED / TIMED_OUT` 是任务执行状态；`UNMANAGED` 表示本连接未持有原进程，不能跨会话杀 PID。
- `PASS / WARN / FAIL / REVIEW / UNKNOWN / NO_SOLUTION / SCREENED` 是相应软件模型范围内的判定。
- 关闭 TD 只能得到 FHA `SCREENED`，不会得到已校核候选。`NO_SOLUTION` 仅表示本次显式网格内没有合格候选，不表示物理上无解。
- 默认最多 32 个优化点、128 个显式角点、一个服务实例一个后台任务、1–600 秒硬超时。取消/超时终止隔离子进程；被终止任务不可导出成完成报告。
- 源码、数据或依赖变化时，旧快照拒绝重新计算；历史完成结果仍可按保存的哈希导出。旧报告不会被重新标成新源码版本的验证。
- 标量非有限输出写成 JSON null，绝不换成通过的零值。每个结果保留模型边界和 `verified_for_hardware=false`。

## 控制与 C99

`control_auto_design` 的 `frequency_hz / plant_real / plant_imag` 必须是同长度、严格递增频率、低于 Nyquist 的等效对象响应，并提供 `source_description`。完整环路测量必须先去嵌入旧控制器。输入说明是用户声明，不是自动验证的物理关联。该工具不自动从 LLC 快照推导对象或验证测量含义。

返回原始目标、实际交越频率、相位裕度、增益裕度、Ms/Mt、系数和 `fallback_used`。默认 `allow_lower_crossover=false`，内核降低带宽得到的候选仍需 REVIEW。用户明确允许降低带宽时，用 `PASS_WITH_FALLBACK` 与 `requested_target_met=false` 显示差异。频段内未观测到增益裕度所需相位穿越，也不会自动认定裕度足够。

`control_export_verified_c99` 输入 `b / a / sample_rate_hz`，数字系数为 z^-1 升幂，分母含 a0，最多 8 阶：

```json
{"request":{"b":[0.1,0.1],"a":[1.0,-0.8],"sample_rate_hz":50000}}
```

调用共享验证器生成 DF2T/SOS 头文件及哈希绑定的 `.verification.json`。主机 PASS 仅证明该验证器的 float32 脉冲/阶跃误差预算；不证明闭环稳定、限幅/抗积分饱和、目标编译、WCET、PWM 或硬件行为。不修改或烧写固件。

## 产物与回归

默认产物根目录是服务工作目录下的 `output/agent`，建议用环境变量固定绝对路径。`designs/` 保存不可变输入，`jobs/` 保存请求、状态、结果和诊断，`exports/` 保存报告/代码。工具不接受任意读取路径或 shell 命令。产物属于本地工程资料，不自动上传或发布。

相关检查：

```bash
MPLCONFIGDIR=/private/tmp/power-agent-mpl XDG_CACHE_HOME=/private/tmp/power-agent-cache \
  .venv/bin/python -m pytest power_agent/tests -q -p no:cacheprovider
.venv/bin/python -m ruff check --no-cache power_agent
```

真实 stdio 测试覆盖握手、工具发现、字段 Schema、错误参数、创建设计、提交角点、轮询和导出。单元/集成测试另外覆盖内核一致性、默认项、快照篡改、版本失配、网格限制、零负载 UNKNOWN、目标降级、超时/取消、报告哈希和 C99 主机验证。首次字体缓存可能需要额外启动时间。
