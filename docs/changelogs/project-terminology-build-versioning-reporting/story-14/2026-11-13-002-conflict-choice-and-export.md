# S14：明确冲突选择、全量术语导出与项目范围修复

- 日期：2026-11-13
- Epic / Story：project-terminology-build-versioning-reporting / S14
- 计划：[项目术语计划](../../../../plans/project-terminology-build-versioning-reporting/plan.md)
- 前一增量：[任务式术语界面](2026-11-13-001-task-first-workbench.md)

## 用户可见行为

冲突表显示候选译名，而非仅显示译法数量。每行提供“选择译名…”按钮，保留双击和当前行操作；选择窗口提供候选单选按钮，也可输入自定义译名。保存期间不可再次修改候选，保存失败保留输入。

冲突窗口可导出全部冲突的 JSON/CSV，包含各候选、处理状态、来源证据与上下文。首页和术语编辑窗口可导出当前方案完整已生效术语，不受分页或搜索过滤影响；尚未应用的结果需先应用。导出后台执行，先写临时文件再原子替换，失败或窗口关闭保留已有目标文件。

“重新检查”不再把范围限制成界面已加载的插件；默认捕获项目所有已登记且启用的来源，禁用来源不参与。

## 文件与设施复用

- 修改 `ui/tools/terminology/table_models.py`：候选译名文本列、选择操作列。
- 修改 `conflicts_view.py`：每行选择按钮、明确操作说明、加载中禁用操作、适配列宽与行高。
- 修改 `conflict_review_dialog.py`：候选单选与自定义输入联动，沿用原有保存、重试、上下文校验。
- 修改 `window.py`：去掉重新检查时由已加载 slots 派生 source_ids 的隐式范围限制，接入独立导出控制器。
- 新增 `ui/tools/terminology/export_controller.py`：冻结全部生效快照/冲突结果、选择文件路径、后台导出、状态反馈和生命周期取消。当前发布版本与构建匹配时保留已解决的冲突状态。
- 新增 `infra/terminology_export.py`：完整术语复用已有 `TermEntry`、`dump_terms_json`、`dump_terms_csv`，保留插件范围、变体和备注。冲突清单使用独立业务字段及标准库 JSON/CSV，不新增通用格式框架或依赖。
- 新增 `tests/infra/test_terminology_export.py`、`tests/ui/tools/terminology/test_exports.py`、`test_conflict_choices.py`；更新 `test_conflict_filters.py` 验证真实候选和逐行操作。

## 验证

使用既有 uv 环境，缓存和测试数据放在任务临时目录 `.tmp-terminology-exports`，结束后清理。需要 Windows 项目硬链接的测试通过提升权限运行。

```powershell
uv run --no-sync --cache-dir .tmp-terminology-exports/uv pytest tests/ui/tools/terminology/test_conflict_choices.py tests/ui/tools/terminology/test_conflict_filters.py tests/ui/tools/terminology/test_draft_interactions.py tests/ui/tools/terminology/test_conflict_review_save_lifecycle.py tests/ui/tools/terminology/test_preflight_context.py tests/ui/tools/terminology/test_filter_interactions.py tests/ui/tools/terminology/test_task_first_workbench.py tests/ui/tools/terminology/test_window_lifecycle.py tests/ui/tools/terminology/test_workbench_simplification.py tests/ui/tools/terminology/test_exports.py tests/infra/test_terminology_export.py tests/ai_translator/test_term_formats.py tests/application/terminology/test_input_capture.py -q --basetemp .tmp-terminology-exports/final --tb=short
uv run --no-sync --cache-dir .tmp-terminology-exports/uv ruff check src tests
uv run --no-sync --cache-dir .tmp-terminology-exports/uv ruff format --check src tests
git -c core.safecrlf=false diff --check
```

104 项通过；全仓 Ruff check、2565 文件 format check 与 diff check 通过。覆盖 305 条术语 JSON/CSV 往返、101 组冲突全量导出、已发布决定覆盖、旧版本隔离、取消保存、失败保留目标、关闭窗口中止替换、全部启用插件范围和冲突选择。Qt 实际渲染检查冲突表及候选窗口，修正初始列宽未布局导致的过高行高。

## 边界

未迁移持久化数据，未导出真实用户数据，未请求付费模型，未打包发布。本次提供 JSON/CSV；冲突文件是审核清单，不是可直接作为成品术语导入的文件。导出成品术语使用现有兼容格式，不混入未解决或停用的决定。
