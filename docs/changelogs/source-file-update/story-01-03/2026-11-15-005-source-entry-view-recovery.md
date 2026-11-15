# 其余来源操作接入工作台恢复

2026-11-15 · source-file-update / Story 01–03 · [实施计划](../../../../plans/source-file-update/plan.md)

## 本次改动

- `persistence/project_store/historical_restore.py` 把跨来源快照的恢复前备份留在恢复事务内，通过同一事务的备份确认丢失的 SQLite 提交回执。来源基准更新失败后清除缓存项。`application/projects/snapshots.py` 保留同来源快照恢复前备份工作副本的行为。
- `ui/coordinators/project_coordinator.py` 提供统一的已提交来源刷新入口，来源替换和跨来源快照恢复用该入口安装工作台，失败时可重试刷新。
- `ui/project_view_binding.py` 与 `ui/context.py` 提供工作副本槽位安装入口。普通打开文件、拖入插件、助手登记和移除来源复用它。保留已有分页集合和译文编辑，来源增删随显式保存落盘。刷新重试复用提交结果。
- `ui/coordinators/source_input_coordinator.py` 支持使用扩展服务返回的提交凭据刷新。正式 SQLite 运行时没有注册旧格式缺失原文补充服务，写回时临时选择原插件继续走原有入口。
- 更新 `docs/adr/044-source-file-update.md` 的快照和工作副本恢复边界。

## 验证

离屏 Qt 与临时 SQLite 工程覆盖了基准缓存失败、SQLite 提交回执丢失、Qt 通知丢失和工作台首次安装失败。用例核对未保存译文、来源增删的保存边界、分页对象以及重试前后的 SQLite 内容和备份数。

在 `QT_QPA_PLATFORM=offscreen` 下运行 `uv run --offline --no-sync pytest <下列文件> -q --basetemp .tmp-connect-sources-final`，245 项通过，9 条弃用警告。

```text
tests/integration/gui/test_historical_source_restore_recovery.py
tests/integration/gui/test_source_replacement_recovery.py
tests/integration/gui/test_project_paged_workbench.py
tests/integration/gui/test_working_source_recovery.py
tests/ui/test_plugin_drop_workflow.py
tests/ui/test_open_file_workflow.py
tests/ui/test_workbench_slices.py
tests/ui/test_destructive_confirmation_defaults.py
tests/ui/test_project_view_installation.py
tests/ui/test_project_open_operation.py
tests/ui/test_project_open_guards.py
tests/ui/test_source_update.py
tests/ui/test_source_input_batch_coordinator.py
tests/ui/test_binary_eet_sources.py
tests/ui/test_legacy_migration_safety.py
tests/ui/test_project_text_encoding.py
tests/smart_assistant/tools/test_source_import_authority.py
tests/application/projects/test_source_update.py
tests/application/projects/test_batch_source_update.py
tests/application/projects/test_source_supplement.py
tests/persistence/v2/test_project_snapshots.py
tests/ui/test_workbench_draft_focus.py
tests/ui/test_project_editing_boundary.py
```

打开文件、助手工具与旧格式适配器合并回归另有 465 项通过，包含上面部分用例。执行 `uv run --offline --no-sync pytest tests/ui/test_open_file_workflow.py tests/smart_assistant/tools tests/smart_assistant/test_agent_tool_integration.py tests/contracts/io/test_legacy_format_adapters.py -q`。

`uv run --offline --no-sync ruff check src tests`、`uv run --offline --no-sync ruff format --check src tests` 和 `git -c core.safecrlf=false diff --check` 通过。额外运行的 `test_plugin_write_binds_only_the_current_operation[wrong]` 等待后台任务结束时超时，已在独立的 Git HEAD 基线复现。全量套件及真实用户工程未运行。
