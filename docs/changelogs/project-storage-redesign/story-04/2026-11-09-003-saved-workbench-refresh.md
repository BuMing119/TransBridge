# S04 已保存译文自动刷新工作台

2026-11-09 · project-storage-redesign / S04 · [实施计划](../../../../plans/project-storage-redesign/plan.md#s04按需打开与完整范围查询)

ParaTranz 译文合入并保存后，当前来源的译文、状态、筛选结果和统计会自动更新。工作台收到保存通知时，已落盘的词条已经移出待保存列表。此前完整表格只读取这份列表，先前读取的分页也可能沿用旧状态。

## 本次改动

- `persistence/v2/deferred_variant_rows.py` 的 `rebased` 和 `committed_from` 保留最近一次保存的稀疏差分及其基准。正常保存后可复用已显示的行，连续保存且通知乱序时可识别差分基准缺口。
- `ui/project_rows.py` 在 `sync_paged_collections` 中合并已提交和待保存的状态变化，按来源分发给缓存。分页只写入已落盘状态，完整表格再叠加工作副本变化。无法确认差分时清除失效缓存，交由现有后台读取恢复。
- `ui/project_view_binding.py` 根据词条视图的实际变化通知工作台。`ui/workbench/project_paging.py` 根据驻留结果的版本应用差分，或清理旧查询结果并重新加载当前来源。
- `tests/integration/gui/test_project_paged_workbench.py` 覆盖直接保存、先应用后保存、其他来源缓存、译文筛选与统计、保留本地编辑、加载中的分页刷新，以及保存通知乱序。旧实现的直接保存回归先失败，修复后通过。

排查了 GUI 与助手的 ParaTranz 下载、AI 成果应用及恢复、翻译文件导入、结构化迁移、来源更新、词典应用、对话编辑和上传结果。GUI 与助手的下载共用保存路径。其余入口在相关回归中未复现同类遗漏。

## 验证

使用 `QT_QPA_PLATFORM=offscreen` 和 `UV_NO_CACHE=1`，经 `uv run --offline --no-sync python -` 调用 `pytest.main`。临时目录设置限于本次测试根，以避开 Windows 沙箱的默认临时目录权限限制。

- 聚焦组 `pytest tests/integration/gui/test_project_paged_workbench.py -k applies_results_to_cached -q`，修改前 1 失败、1 通过，修改后 2 通过。
- 最终联合 pytest 分组，274 通过。运行的测试文件如下。

  ```text
  tests/integration/gui/test_project_paged_workbench.py
  tests/ui/operations/test_download_workbench_projection.py
  tests/ui/operations/test_batch_download_autosave.py
  tests/ui/operations/test_batch_download_production.py
  tests/ui/operations/test_batch_download_session.py
  tests/application/projects/test_batch_download_sources.py
  tests/application/projects/test_durable_variant.py
  tests/integration/bootstrap/test_project_result_receipts.py
  tests/application/projects/test_project_result_merge.py
  tests/ui/test_workbench_dialogue_reading_order.py
  tests/ui/test_workbench_translation_import.py
  tests/ui/test_structured_migration_import.py
  tests/ui/test_source_update.py
  tests/ui/test_dialogue_authority.py
  tests/ui/test_dialogue_projection_sync.py
  tests/ui/tools/test_dictionary_apply_feedback.py
  tests/ui/tools/test_ai_result_lifecycle.py
  tests/ui/tools/test_ai_result_recovery.py
  tests/smart_assistant/test_immediate_entry_projection.py
  tests/smart_assistant/tools/test_entry_result_lifecycle.py
  tests/smart_assistant/tools/test_paratranz_tools.py
  tests/smart_assistant/tools/test_upload_result_lifecycle.py
  ```

- `pytest tests/performance/test_project_storage_scaling.py -q`，3 通过。
- `uv run --offline --no-sync ruff check src tests`、`uv run --offline --no-sync ruff format --check src tests` 和 Git 差异空白检查通过。

验证使用模拟云端和测试工程。全量 pytest、真实 ParaTranz/LLM 及发行构建未在本次执行。现有工程可直接使用，无需迁移。
