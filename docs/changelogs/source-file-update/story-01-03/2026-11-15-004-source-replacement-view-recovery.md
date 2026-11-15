# 来源替换提交与工作台恢复

2026-11-15 · source-file-update / Story 01–03 · [实施计划](../../../../plans/source-file-update/plan.md)

来源替换提交成功后，活动工作台用 SQLite 中的来源与当前工作副本重新安装视图。缓存或 Qt 投影通知故障仍可恢复。若视图准备失败，可使用「重试刷新工作台」。

## 本次改动

- `application/projects/source_update.py` 对来源替换启用提交恢复。`application/projects/source_input_publication.py` 在 SQLite 提交回执异常时确认事务结果，来源基准缓存写入失败时失效旧项，交给既有 SQLite 基准读取恢复。`application/projects/source_update_models.py` 随活动工程的更新结果返回提交后的 `ProjectReadToken`。
- `persistence/project_store/source_updates.py` 用同一事务产生的备份 ID 和已提交的 Project、Variant 版本确认结果。原确认令牌仍只能使用一次，重试视图复用已提交版本。
- `ui/coordinators/source_update_coordinator.py` 在活动工程确认时启动视图安装保护，提交后启动刷新并在失败时提供单独的重试。`ui/coordinators/project_coordinator.py` 和 `ui/coordinators/project_open_operation.py` 复用后台分页准备与原子安装，以当前工作副本叠加尚未保存的译文。`ui/context.py` 与 `ui/project_view_binding.py` 允许该恢复流程跳过同工程版本的旧排队通知，保护其他工程或更新的投影。
- `tests/application/projects/test_source_update.py` 注入 SQLite 提交回执丢失，并核对原令牌重复确认不会写入。`tests/integration/gui/test_source_replacement_recovery.py` 注入提交后基准失败、投影通知全部丢失或只保留旧通知、首次视图准备失败。验证来源和译文恢复、草稿可随后保存、重试时 SQLite 字节和备份数量保持一致。
- 更新 `docs/adr/044-source-file-update.md` 与 `plans/source-file-update/plan.md` 的活动工程提交及恢复说明。

## 验证

在 `QT_QPA_PLATFORM=offscreen` 下执行 `uv run --offline --no-sync pytest <下列文件> -q --basetemp .tmp-source-replacement-verified`，168 项通过，7 条弃用警告。

```text
tests/integration/gui/test_source_replacement_recovery.py
tests/integration/gui/test_project_paged_workbench.py
tests/ui/test_source_update.py
tests/ui/test_project_open_operation.py
tests/ui/test_project_open_guards.py
tests/ui/test_project_view_installation.py
tests/application/projects/test_source_update.py
tests/application/projects/test_batch_source_update.py
tests/application/projects/test_source_update_plugin.py
tests/application/projects/test_source_supplement.py
tests/application/projects/test_lifecycle.py
tests/persistence/v2/test_source_update_store.py
tests/persistence/v2/test_source_update_archive.py
tests/persistence/v2/test_project_open_activation.py
tests/persistence/v2/test_project_snapshots.py
tests/ui/test_project_text_encoding.py
```

`uv run --offline --no-sync ruff check src tests`、`uv run --offline --no-sync ruff format --check src tests` 和 `git -c core.safecrlf=false diff --check` 通过。

验证使用临时工程和离屏 Qt。全量测试与真实工程验收未执行。本次范围内的遗留项为无。
