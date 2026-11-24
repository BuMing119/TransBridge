# ParaTranz 仅填补云端空译文

2026-11-24 · paratranz-categorized-upload / S32 · [上传使用说明](../../../dev/paratranz-categorized-upload.md)

## 使用效果

普通和分类上传新增「仅填补云端空译文」。软件逐文件读取当前译文，用云端完整 key 安全导入本地有文字、云端为空的匹配项。已有人工译文和机翻都跳过，包括只有状态不同的词条。手动补传重新核对尚未完成的文件。文件详情分别报告提交填空和因云端已有译文而跳过的数量。

## 实现和测试

- 修改 `application/file_upload_diff.py` 的 `plan_file_changes`，校验云端字段，区分缺少字段与合法空值，保留提交正文的首尾空白。该模块集中声明支持的模式。
- 修改 `application/file_upload.py` 的 `execute_file_upload`，填空模式直接逐文件读取。修改 `application/file_upload_execution.py` 的 `upload_one`，缺失文件跳过，成功比较及导入后返回相应条数。
- 修改 `ui/operations/categorized_upload_dialog.py` 的策略下拉框和 `smart_assistant/tools/tool_paratranz.py` 的共享参数说明。桌面与助手共用同一套比较与导入逻辑。
- 新增 `tests/paratranz/test_file_upload_fill_empty.py`，验证普通和分类路径、请求正文、空值与无效字段、同内容第二次零写入、超时后的手动补传，以及达到 50 个文件时的直接读取。
- 扩展 `tests/ui/operations/test_force_upload_confirmation.py` 的策略选择和补传测试，以及 `tests/smart_assistant/tools/test_project_file_upload.py` 的缺文件预览与执行测试。
- 更新 `docs/requirements.md`、ADR-047、S32 计划、`docs/dev/paratranz-categorized-upload.md`、`docs/dev/paratranz.md` 和 `docs/dev/ui.md` 中的策略范围及读取规则。

## 验证

以下命令按顺序列出首轮测试、扩大回归和最终检查。两轮分别通过 47 项和 721 项，覆盖有重叠。Ruff 静态检查通过，格式检查通过 2773 个文件。

```powershell
uv run --offline --no-sync --no-cache pytest tests/paratranz/test_file_upload_fill_empty.py tests/ui/operations/test_force_upload_confirmation.py tests/smart_assistant/tools/test_project_file_upload.py -q --tb=short
uv run --offline --no-sync --no-cache pytest tests/paratranz tests/application/categorized_upload tests/ui/operations/test_force_upload_confirmation.py tests/ui/operations/test_categorized_upload_dialog.py tests/ui/operations/test_categorized_upload_bridge.py tests/ui/operations/test_categorized_upload_retry.py tests/smart_assistant/tools/test_project_file_upload.py -q --tb=short
uv run --offline --no-sync --no-cache ruff check --no-cache src tests
uv run --offline --no-sync --no-cache ruff format --no-cache --check src tests
git -c core.safecrlf=false diff --check
```

本轮使用模拟服务验证请求与回执，未执行线上上传或全库 pytest。空白判断以该文件读取时为准，读取和提交之间仍可能发生协作编辑。
