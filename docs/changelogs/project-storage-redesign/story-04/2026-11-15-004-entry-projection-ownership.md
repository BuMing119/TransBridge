# S04 词条提交后统一刷新工作台

2026-11-15 · project-storage-redesign / S04 · [实施计划](../../../../plans/project-storage-redesign/plan.md#s04按需打开与完整范围查询)

对话编辑、助手词条操作和译文导入通过现有工程命令提交，由 `ProjectViewBinding` 统一刷新工作台。导入原先会重复通知。在分页来源与内存集合并存时，第一次通知还可能带出旧译文。

## 本次改动

- `ui/project_view_binding.py` 同步同一工程版本内的分页和内存集合，在两者都更新后统一通知。`ui/entry_projection.py` 在已有完整 `EntryKey` 时只读取对应的延迟词条，用于原有的乐观并发校验。
- `ui/coordinators/migration_coordinator.py` 将工程模式的词条安装和刷新交给绑定，保留非工程集合的本地应用以及导入附带的路径和 Strings 查找表。
- `ui/dialogue/editing.py` 和 `ui/dialogue/consistency.py` 保留提交前的目标、快照和版本校验，提交成功后消费工作台投影。`ui/dialogue/controller.py` 去除打开、导航和应用时的额外投影，并将分页关系索引的读取交给现有后台任务。`ui/dialogue/index_state.py` 复用分页稀疏差分，普通译文变更沿用关系索引，词条移除后更新导航。删除编辑器专用的 `ui/dialogue/projection_sync.py`。
- `smart_assistant/tools/_project_tool_mutations.py` 和 `smart_assistant/tools/_entry_upload_associations.py` 去除命令成功后对界面集合的二次写入，保留原有目标检查、执行范围确认和回执处理。
- `tests/integration/gui/test_project_paged_workbench.py` 与 `tests/ui/test_legacy_migration_safety.py` 验证分页、混合和内存集合的导入只通知一次，并核对屏外译文和筛选结果。
- `tests/smart_assistant/test_immediate_entry_projection.py` 验证连续助手编辑复用真实 SQLite 分页集合，按目标读取并逐次通知。`tests/ui/test_dialogue_authority.py`、`test_dialogue_consistency.py` 和 `test_dialogue_reopen.py` 验证一次发布、失败草稿、重开复用和真实分页导航。随编辑器独立投影缓存移除 `tests/ui/test_dialogue_projection_sync.py` 的实现专用测试。

## 验证

- `QT_QPA_PLATFORM=offscreen` 下经 `uv run --offline --no-sync pytest <下列文件> -q --basetemp .tmp-phase1-projection-final` 执行，357 项通过，28 条弃用警告。

  ```text
  tests/integration/gui/test_project_paged_workbench.py
  tests/integration/gui/test_app_context_projection.py
  tests/ui/test_legacy_migration_safety.py
  tests/ui/test_structured_migration_import.py
  tests/ui/test_workbench_translation_import.py
  tests/ui/test_parse_migrations.py
  tests/ui/test_project_view_installation.py
  tests/ui/test_prepared_entry_projection.py
  tests/ui/test_authoritative_content_projection.py
  tests/ui/test_inline_edit_recovery.py
  tests/ui/test_save_recovery.py
  tests/ui/test_workbench_dialogue_reading_order.py
  tests/ui/operations/test_download_workbench_projection.py
  tests/smart_assistant/test_immediate_entry_projection.py
  tests/smart_assistant/tools/test_entry_result_lifecycle.py
  tests/smart_assistant/tools/test_upload_result_lifecycle.py
  tests/smart_assistant/tools/test_paratranz_tools.py
  tests/smart_assistant/tools/test_source_import_authority.py
  tests/application/projects/test_alignment_epoch_publication.py
  tests/ui/test_dialogue*.py
  ```

- `uv run --offline --no-sync ruff check src tests`、`uv run --offline --no-sync ruff format --check src tests` 和 `git -c core.safecrlf=false diff --check` 通过。

本次验证范围为离屏 Qt、临时 SQLite 工程和模拟服务。全量 pytest、真实工程及在线服务未验收。同原文批量同步沿用现有的来源内匹配行为。本次范围内的遗留项为无。
