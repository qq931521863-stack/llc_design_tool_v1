# Codex MCP 本地验收记录

日期：2026-10-19（Asia/Shanghai）。项目：`/Users/yangshuai/Applications/LLC_TOOLS/`。
基线：`main / c19e208466d51e09ab657c2c612d2dd6fa2857cb`，加本轮未提交的 `power_agent` 修改。
通过 `git pull --ff-only origin main` 从 e02b0de 更新；原有 14 个未提交文件内容哈希保持不变。验收时尚未提交、推送或发布；本文记录该验收时点。

## 源码与环境身份

- 计算源码 SHA-256：`d9bf745c0e1d0c4f8295ffc0e8696c1fbfa117421e3ac812ee473fd820eee956`
- 数据 SHA-256：`9a3346810c668230230eb59e1876847afabc313e440b58d5348d15eaefaec017`
- Toolkit `9.9.0`；Python `3.12.14`。
- 数值/接口依赖：`numpy=2.5.3`, `scipy=1.18.1`, `pandas=3.0.6`, `pydantic=2.14.1`, `mcp=2.3.0`。
源码/数据哈希按 `power_agent.storage.runtime_identity()` 定义；包含相关包 Python/JSON，不是编译器、操作系统或硬件身份。
验收结束重新计算并确认与示例快照一致。

## 软件检查

- 最终相关回归：**104 passed, 2 xfailed, 12 warnings**；106 个用例，0 failures、0 errors。耗时 60.68 s。
- 范围：`power_agent/tests`；LLC 系统/优化、工况包络、ZVS、项目来源；FRA 自动设计/C99；PSD 已验证 C99 交付；Web 和 backend 测试。
- 两个 xfail 保留既有预期失败；12 个 warning 为既有 LLC 图中的中文字形缺失。未将其描述为无警告或全量仓库回归。
- Ruff `power_agent`、`git diff --check`：PASS。
- 真实 SDK stdio：初始化、14 工具发现、输入 JSON Schema、错误输入、设计快照、后台角点、状态轮询、JSON 导出和 capability resource：PASS。
- 回归另覆盖 SHA 篡改、源码/依赖失配、零负载 UNKNOWN、预算、取消/硬超时、未知证据不提升、控制目标降级、C99 失败阻断及实际主机验证。
- 本机 Codex 已添加 `power_design_toolkit`，使用项目 `.venv/bin/power-design-mcp`。既有 MCP 配置条目不变；新会话/连接加载。

复现最终相关回归：

```bash
MPLCONFIGDIR=/private/tmp/power-agent-mpl XDG_CACHE_HOME=/private/tmp/power-agent-cache QT_QPA_PLATFORM=offscreen \
  .venv/bin/python -m pytest -o addopts='' power_agent/tests \
  llc_design/tests/test_system_and_optimization.py llc_design/tests/test_operating_envelope.py \
  llc_design/tests/test_zvs_margin.py llc_design/tests/test_project_provenance.py \
  power_control_tools/tests/test_fra_auto_codegen.py power_stage_design/tests/test_verified_c99_delivery.py \
  webapp/tests backend/tests -q -p no:cacheprovider
```

## 真实 stdio 工程示例

设计 ID：`9abf7ed67ab02049f8626fea1a90faa940730e40a25b6c2b6652718dc835ed78`。400 V 标称、360–420 V 正常、300 V 保持末端、53 V / 3 kW、全桥，其他项显式保留为默认。

| 调用 | 执行状态 | 模型结果 | 含义 |
|---|---|---|---|
| 单次系统分析 | COMPLETED | PASS | 既有系统模型的筛选结果 |
| Ln=5、Q=0.35、fr=100 kHz 两阶段校核 | COMPLETED | NO_SOLUTION | 本次仅一个显式候选；FHA/TD 校核拒绝，不能称为规格在物理上无解 |
| 48 个显式容差角点 | COMPLETED | FAIL | 4 母线 × 3 负载 × 2 Lr 比例 × 1 Lm 比例 × 2 Cr 比例 |
| 独立合成三极点 FRA 的 2P2Z 自动设计 | 完成 | PASS | 该合成对象与上述 LLC 无物理关联 |
| 上述数字系数的 C99 主机验证 | 完成 | PASS | float32 脉冲/阶跃；不是目标 DSP 或硬件证明 |

两阶段诊断包含标称励磁峰值电流 FHA/TD 偏差约 20.34%，及部分 TD 点未收敛。保留内核原判定，没有调整阈值或将其提升为通过。
容差失败示例：300 V / 100% 负载、Lr×0.95、Cr×1.05，所需增益 1.3350 超出计算可达上限约 1.3343；该点 ZVS 为 UNKNOWN。
这些是软件模型证据，需要单独分析模型、求解收敛和设计裕量；不能推导实物一定失败或安全。

## 产物

产物位于 `output/agent/acceptance-20261019/`（Git 忽略的本地生成目录）。完整清单为 `acceptance-summary.json`，测试 XML 为 `tests.xml`。

- llc_submit_analysis json：`output/agent/acceptance-20261019/exports/a990f2f5619f5eea1c8c86a078c29820dc0f0b81913fd786d896253b6063e79a.json`；SHA-256 `363ea1a055b96dc950ad4f624c5d488f4e174c06bb962791741e0a57ff00d8ad`。
- llc_submit_analysis html：`output/agent/acceptance-20261019/exports/a990f2f5619f5eea1c8c86a078c29820dc0f0b81913fd786d896253b6063e79a.html`；SHA-256 `61b59685898f19c71e5c6646fd3a9515999d754280da485c456c7e13334c0eb0`。
- llc_optimize json：`output/agent/acceptance-20261019/exports/7e2bafce35481c40adf5515d32a3131384bf4de5f207f48711f2ca48af7d9fc0.json`；SHA-256 `ce41d9f48c475a9ef291e6fdaf1226c152742d4cb35d9862b5340df6530e40ca`。
- llc_optimize html：`output/agent/acceptance-20261019/exports/7e2bafce35481c40adf5515d32a3131384bf4de5f207f48711f2ca48af7d9fc0.html`；SHA-256 `6ed51986942a00d422adfbd2c2b45e700e6aa83148795ec273fe98bacaf33134`。
- llc_check_corners json：`output/agent/acceptance-20261019/exports/78d5b787cc471ee415e37f6a11e3d31f3cd6d6bf80a4c82682dee70e196cd1a8.json`；SHA-256 `468056a2b5b4e0b9f8e0f9ee971284165d237e2225082f985fa316e6ea00c7a8`。
- llc_check_corners html：`output/agent/acceptance-20261019/exports/78d5b787cc471ee415e37f6a11e3d31f3cd6d6bf80a4c82682dee70e196cd1a8.html`；SHA-256 `cee1d3422b1b1be568286bc155832ab11399115b8eed67a52ea003bdd7dc2b31`。

## 未执行与范围限制

全量 GUI/全仓库测试、独立外部 SPICE 关联、目标 DSP 构建、WCET、烧写、上电、故障注入和实物测量均 NOT_RUN。
本入口当前实现 LLC 闭环及输入 FRA 的控制路径；PFC/DAB/PSD 自动编排未实现。原有 Node/C2C MCP 未修改、未重新开放公网服务。
使用及参数契约见 [本地使用说明](power_design_codex_mcp.md)。

## 提交前的最新基线与独立暂存验证

提交前已快进整合远端最新 `main / 4e15d5035aa9d196037224478871551a88f70a52`；本轮仅提交 MCP 源码、测试、文档和四点搜索说明，共 15 个文件。原有 14 个无关文件内容未变，个人 MCP 配置和 `output/` 生成产物不随提交。

从 Git 暂存内容导出独立副本验证，没有带入原有未跟踪源码。测试快照 tree 为 `13dda4cc7b35ddaaca5b7f75f6b5cdd1d8864f92`；其计算源码 SHA-256 为 `47311b6fc3b5373069ce5ea1a9d7ca04f011895c70d6482d36a4597343eb01cf`，数据 SHA-256 为 `9a3346810c668230230eb59e1876847afabc313e440b58d5348d15eaefaec017`。验证完成后仅补充本节文档，计算源码未变。

- 最新相关回归：**119 passed, 2 xfailed, 12 warnings**（121 个用例，0 failures、0 errors），耗时 72.29 s。
- 相比上面的历史 104 项通过记录，增加了上游 `llc_design/tests/test_switched_integration_accuracy.py` 的 15 个精度回归。
- 独立副本中的真实 MCP stdio、工作流、超时/取消和主机 C99 验证通过；Ruff 与暂存补丁格式检查通过。
- 上文历史系统分析、两阶段校核及四点搜索报告保持原始源码身份；不得将旧数值或 NO_SOLUTION 结论归属于更新后的求解器。更新后重新计算需要新 MCP 连接和新设计快照。
- 全仓库 CI、原生包构建和硬件验证没有包含在上述本地测试结果中。测试 XML 保存在本地 `output/agent/commit-validation-20261019/tests.xml`。
