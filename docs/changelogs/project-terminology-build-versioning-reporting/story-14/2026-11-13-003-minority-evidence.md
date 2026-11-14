# S14：候选使用次数、少数译名证据与审核主表

- 日期：2026-11-13
- Epic / Story：project-terminology-build-versioning-reporting / S14
- 计划：[项目术语计划](../../../../plans/project-terminology-build-versioning-reporting/plan.md)
- 前一增量：[冲突选择和导出](2026-11-13-002-conflict-choice-and-export.md)

## 用户可见行为

冲突主表与选择窗口显示各候选的去重词条数。选择冲突后，下方子表默认展示使用数低于最多候选的译名证据，包括原文、现有译文、插件来源；选中证据可复制完整文本及来源标识。数量全部并列时展示全部，可随时切换查看全部译名证据。次数仅供参考，不自动决定译名。

导出分为冲突审核主表和完整证据明细，支持 JSON/CSV。审核主表一组冲突一行，包含候选次数、少数译名代表证据摘录和关联编号；完整证据保留全文与编号。未知插件明确标记未识别。成品术语仍复用已有 JSON/CSV 序列化设施。

## 文件变化

- 新增 application/terminology/conflict_usage.py：按来源链、命名空间和稳定词条标识去重，统一计数和少数判定。
- 新增 ui/tools/terminology/conflict_evidence_panel.py：后台读取绑定构建的证据、主从表联动、完整文本、失败重试与生命周期取消。
- 修改 table_models.py、conflict_review_dialog.py：主表与候选选择使用相同计数；window.py 仅接入面板。
- 修改 infra/terminology_export.py、ui/tools/terminology/export_controller.py：审核汇总导出入口、共享计数及来源标识修正。
- 新增 test_conflict_usage.py、test_conflict_evidence_panel.py；更新导出、筛选与选择相关测试，覆盖去重、并列、切换构建、失败重试及全量导出。

## 验证

使用现有 uv 环境运行以下联合回归，64 项通过：

```powershell
uv run --no-sync --cache-dir .tmp-terminology-minority/uv pytest tests/ui/tools/terminology/test_conflict_evidence_panel.py tests/ui/tools/terminology/test_conflict_filters.py tests/ui/tools/terminology/test_conflict_choices.py tests/ui/tools/terminology/test_draft_interactions.py tests/ui/tools/terminology/test_conflict_review_save_lifecycle.py tests/ui/tools/terminology/test_filter_interactions.py tests/ui/tools/terminology/test_task_first_workbench.py tests/ui/tools/terminology/test_window_lifecycle.py tests/ui/tools/terminology/test_workbench_simplification.py tests/ui/tools/terminology/test_exports.py tests/application/terminology/test_conflict_usage.py tests/infra/test_terminology_export.py -q --basetemp .tmp-terminology-minority/regression-final --tb=short
uv run --no-sync --cache-dir .tmp-terminology-minority/uv ruff check src tests
uv run --no-sync --cache-dir .tmp-terminology-minority/uv ruff format --check src tests
git -c core.safecrlf=false diff --check
```

全仓 Ruff 和 2569 个文件格式检查通过；Qt 实际渲染检查主表、少数证据和完整文本。测试夹具使用 Windows 硬链接，联合回归在提升权限后运行。任务临时目录完成后清理。

## 边界

不迁移数据库、不更改用户原 CSV、不调用付费模型、未打包发布，未运行全仓 pytest。语义不同但同原名的候选、长名称误抽取等抽取质量问题不在本次修复范围。CSV 无原生子表，使用审核主表和完整明细两个导出入口，通过冲突编号关联；图形界面提供可联动的子表。
