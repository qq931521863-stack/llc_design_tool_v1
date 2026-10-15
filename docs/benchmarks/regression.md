# Latest desktop audit

The October 2026 fixes, 479-test combined regression, independent DAB model
checks and remaining headless Qt teardown issue are recorded in
[UI_ALGORITHM_AUDIT.md](../UI_ALGORITHM_AUDIT.md). The earlier results below
remain historical evidence for their stated revision and scope.

# 本轮回归范围（2026-10-15）

## 通过的定向回归

```bash
QT_QPA_PLATFORM=offscreen OPENBLAS_NUM_THREADS=1 python3 -m pytest \
  dab_design/tests \
  pfc_design/tests/test_three_phase_dq.py \
  pfc_design/tests/test_pll_comparison.py \
  pfc_design/tests/test_three_phase_performance.py \
  pfc_design/tests/test_v7_vienna.py \
  llc_design/tests/test_magnetic_catalog_validation.py \
  llc_design/tests/test_transformer_designer.py \
  llc_design/tests/test_i18n.py -q -o addopts=''
```

结果：**86 passed in 51.02s**。

覆盖：DAB原有工况与导出、自动选择触发/参数修改后刷新、100ms动画/暂停/复位、理想L精确积分与独立采样解、电压环能量方程/阶跃/极点、量化后的约束与不可行情况、三种PLL锁相与非有限输入处理、GCC C99逐点对比、两电平/Vienna电流分配和电容损耗、两种中点开关结构、电流/电压采样环及Vienna储能/负载项、磁芯库数据边界、实测CSV输入、AI模型拒绝越界，以及界面多语言词条。

另外运行的磁设计/材料相关原回归 **14项通过**。新模块及脚本的 Ruff 检查通过；`git diff --check` 通过。独立 NumPy 推理在821条70°C数据上的MAPE复核为3.946790711252323%，与训练脚本记录一致。

## 界面检查

实际构造并检查 Qt 窗口：DAB电气波形、DAB数字电压环、PLL三算法对比、两电平电流应力/损耗、磁芯目录搜索。README中的两张新增图均来自实际Qt应用截图，不是设计稿。100ms为播放定时器间隔，实际绘图速度取决于机器。

## 未通过或未覆盖的范围

- 顶层全量收集受当前环境缺少 FastAPI / MCP 依赖阻挡，不能宣称所有API/Agent测试通过。
- 不含上述API目录的1005项联合测试运行到会话结束，在 `llc_design/tests/conftest.py` 的 `desktop_qapp` 清理阶段出现 Qt segmentation fault，没有得到完整的通过报告。该崩溃归因尚未确定。
- 这次联合运行发现的新增词条缺失已修复，并由完整 `test_i18n.py` 定向回归通过。
- 以下4项在原始 `main` 提交 `ced88c71d9cfa259864342716fbd213627518475` 的独立工作树中也复现失败，未改动无关实现以掩盖它们：
  - `test_bode_cursor_chinese_annotation_uses_cjk_font_without_missing_glyphs`：当前环境字体告警断言。
  - `test_ttpl_apply_and_next_run_preserve_exact_hz_and_hidden_guided_values`。
  - `test_guided_reedit_preserves_adopted_controller_and_only_previews`。
  - `test_late_previous_worker_cannot_replace_newer_controller_source[ttpl]`。
- 上述TTPL测试涉及的替身函数不接受包装层传入的 `engineering_v3` 参数；这份记录不把它们算作本轮新增功能通过。
- 未重新验证Web/MCP新增拓扑支持，也没有声称已扩展这些接口。
- GCC主机验证不是TI编译器/MCU周期验证；没有实机或高压闭环数据。

完整测试和目标板验证仍是发布门槛。这里记录的是本轮可复现的软件证据，不替代最终Release CI和硬件验证。
