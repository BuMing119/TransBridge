# S14：单文件 Excel 冲突主表与证据子表

- 日期：2026-11-13
- Epic / Story：project-terminology-build-versioning-reporting / S14
- 计划：[项目术语计划](../../../../plans/project-terminology-build-versioning-reporting/plan.md)

用户明确主表和子表应在同一个文件中，通过 Excel 底部工作表标签切换。新增“导出 Excel 工作簿（主表＋证据子表）…”入口，生成一个 .xlsx，包含“冲突主表”“少数译名证据”“全部证据”。保留单独 CSV/JSON 导出并明确标签。

## 实现

- 新增 `infra/terminology_workbook.py`：复用已有 openpyxl 依赖、共享冲突统计和 Excel 文本安全处理。主表不再塞入证据全文，提供跳转少数证据的链接；子表包含使用次数、原文、译文、插件和来源。固定适当列宽/行高、自动换行、冻结表头及筛选。超长单元格分段并标明段号，不静默截断，继续行保留冲突编号。
- 修改 `infra/terminology_export.py`：增加 conflict_workbook 类型，复用全量冻结快照、后台调用及同目录临时文件原子替换，取消保留原目标。
- 修改 `ui/tools/terminology/export_controller.py`：明确单工作簿入口、默认 .xlsx 文件名与缺省扩展名处理；原 CSV/JSON 功能继续可用。
- 新增 `tests/infra/test_terminology_workbook.py`，修改 `tests/ui/tools/terminology/test_exports.py`：验证标签页、跳转、布局属性、并列计数、超长文本、公式文本安全、取消保留目标及真实按钮输出。

## 验证

```powershell
uv run --no-sync --cache-dir .tmp-terminology-workbook/uv pytest tests/infra/test_terminology_workbook.py tests/infra/test_terminology_export.py tests/ui/tools/terminology/test_exports.py -q --basetemp .tmp-terminology-workbook/final --tb=short
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```

24 项通过。通过 openpyxl 重新打开验证实际工作簿内容与格式属性；未启动 Excel 做视觉检查，未运行全仓 pytest，未打包发布。没有新增依赖或改写用户文件。任务临时目录结束后清理。
