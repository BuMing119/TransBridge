# 暂缓主题切换性能硬门槛

- 日期：2026-11-09
- Epic/Story：maintenance/regression-suite
- 依据：用户明确要求暂不处理此前的主题性能问题，放宽其 QA 门槛。
- 前序证据：[存储、任务恢复与界面修复](2026-11-09-001-storage-and-task-repair.md)。该记录保留当时的失败事实。

## 变更

- `tests/performance/test_ui_foundation_performance.py`：最终 Foundation profile 的主题切换耗时 P95（250 ms）及心跳 P95（200 ms）由硬断言改为超标时发出 PytestWarning；保留实际测量值，在 JSON 摘要中写入两项原预算及 `theme_switch_budget_enforcement=advisory`。用例名称同步为 `test_final_gui_foundation_profile_enforces_budgets_with_advisory_theme_switch`。
- 仅此用例的两项主题切换指标暂缓。共享 Thresholds V1 数值不变，初始化、窗口打开、内存、缓存、订阅释放及空闲/no-op 行为仍执行原有硬检查；未修改生产代码。
- `docs/changelogs/INDEX.md`：加入本次暂缓记录，不改写此前历史结果。
- 恢复条件：主题刷新优化完成，并以原 250 ms 切换和 200 ms 心跳预算复验后，恢复两项硬断言。当前只是放宽 QA 策略，性能问题没有因此修复。

## 验证

- 设置 `$env:QT_QPA_PLATFORM='offscreen'` 后运行 `uv run --offline --no-sync pytest tests/performance/test_ui_foundation_performance.py -q -p no:cacheprovider --basetemp .qa-perf-defer-1109`：初版仅放宽心跳时为 7 通过、1 失败；失败为同一主题路径切换耗时 260.1 ms 高于 250 ms，心跳为 226.9 ms。因此将两项主题切换指标一起暂缓。
- 最终运行 `uv run --offline --no-sync pytest tests/performance/test_ui_foundation_performance.py::test_final_gui_foundation_profile_enforces_budgets_with_advisory_theme_switch -q -p no:cacheprovider --basetemp .qa-perf-defer-1109`：1 通过，183.48 秒。其余 7 项未受后续局部修改影响；未重复运行全库。
- `uv run --offline --no-sync ruff check src tests`：通过。
- `uv run --offline --no-sync ruff format --check src tests`：2,524 文件通过。
- `git -c core.safecrlf=false diff --check`：通过。
- 本轮临时测试目录在验证后清理；未执行原生 Windows 可见窗口性能验收。
