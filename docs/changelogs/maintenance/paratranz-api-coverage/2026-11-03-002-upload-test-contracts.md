# 修复五项既有上传测试失败

- 日期：2026-11-03
- Epic/Story：maintenance/paratranz-api-coverage
- 关联记录：[API 补全与注释校准](2026-11-03-001-openapi-coverage.md)

## 根因与变更

五项失败均为测试与现有生产契约脱节，未发现需要修改生产逻辑的问题。

- 新增 `tests/paratranz/conftest.py`，复用已有 `current_project_factory` 工程夹具。
- 修改 `tests/paratranz/test_entry_upload_order.py` 两项测试：旧上下文只有 collection，缺少持久化上传回执所需的工程/版本身份。改用真实工程命令夹具与 ExecutionContext，将回执目录隔离到 pytest tmp_path。保留选中条目全局顺序、稀疏插件顺序、自由文本断言，增加回执持久化、关联完成和本地上下文保留检查。
- 修改 `tests/paratranz/test_entry_upsert.py` 两项参数化测试：旧 SimpleNamespace 缺少当前上传入口读取的 config 以及工程状态。改用相同工程夹具，保留仅复用目标项目远端身份、不扫描远端、冲突不修改远端记录的断言；增加回执持久化及本地引用保留检查。
- 修改 `tests/paratranz/test_file_transport_keys.py` 一项测试：分类器现在在构建阶段拒绝重复身份，旧测试误期待进入上传执行阶段。断言分类阶段抛出明确异常且零网络请求；另增普通上传路径测试，确认只读取文件列表且不发送 POST。

生产行为、公共 API、依赖和锁文件均未更改，无数据迁移。

## 验证

- `uv run pytest tests/paratranz/test_entry_upload_order.py tests/paratranz/test_entry_upsert.py tests/paratranz/test_file_transport_keys.py -q`：24 passed。
- `uv run pytest tests/paratranz tests/contracts/paratranz tests/contracts/io/test_paratranz_facades.py tests/ui/test_paratranz_secondary_state.py tests/ui/test_paratranz_async_lifecycle_regressions.py tests/ui/test_paratranz_profile_contribution_state.py tests/smart_assistant/tools/test_upload_result_lifecycle.py tests/smart_assistant/tools/test_upload_cancellation_receipt.py tests/smart_assistant/tools/test_dictionary_upload_authority.py -q`：597 passed、1 skipped，30.98 秒。
- `uv run ruff check src tests`：通过。
- `uv run ruff format --check src tests`：通过，2362 个文件。
- `git diff --check`：通过。

1 项跳过为未显式配置专用凭据/项目的真实术语 API 集成测试；未执行全仓库测试或真实远端写入。五项既有失败已全部关闭，无本次范围内遗留项。
