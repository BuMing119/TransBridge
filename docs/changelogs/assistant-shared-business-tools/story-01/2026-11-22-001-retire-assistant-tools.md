# 助手工具删减与文件上传切换

2026-11-22 · 助手工具复用桌面功能 · S1，含 S2–S5 的删减项

原始基线为 `12b9d7da`。助手注册的工具从 67 个减至 64 个。实施范围及后续工作见[整改方案](../../../../plans/assistant-shared-business-tools/plan.md)。

## 目录与旧计划

- `tool_migrator.py` 删除恒返不可用的 `migrate_entries`，保留词典操作。`tool_paratranz.py` 删除助手项目级 `plan_sync` 和 `recover_upload_associations`。`graph_executor.py` 在执行用户确认的步骤列表前核对工具是否仍可用，避免先写入再发现后续步骤已下线。
- `prompts.py` 删除旧上传恢复指令和旧长期记忆承诺。`agent_registry.py` 修订解析角色说明。`data/skills/translate_with_terms.toml` 的依赖、允许列表和提示统一使用 `start_translation`。加载生产目录和内置预设的测试验证角色能调用该工具。

## ParaTranz

- 移除 `_entry_upload.py`、`_entry_upload_associations.py` 和 `_entry_upload_recovery.py`。删除 `paratranz/service.py` 和 `application/ports/paratranz.py` 中供旧链使用的 `upsert_entry`。`translation_task_results.py` 移除助手专项回执查询；`entry_upload_results.py` 移除回执创建、待恢复缓存及写入方法，保留桌面任务历史使用的既有读取与管理。修改上传撤销提示，说明已发送的文件无法用本轮撤销收回。
- 将桌面 `categorized_upload_bridge.py` 中的文件上传会话及相关数据类型提取到 [project_upload.py](../../../../src/transbridge/paratranz/project_upload.py)。桌面继续使用原窗口及窗口内分片补传，助手通过 [_project_upload.py](../../../../src/transbridge/smart_assistant/tools/_project_upload.py) 选择相同来源、布局、文件和模式。`file_upload_execution.py` 供只读预览与正式上传共用差异计算。文件上传须有明确模式和文件选择，拒绝旧参数和无法精确表示的词条选区。
- 用 [文件上传回归](../../../../tests/smart_assistant/tools/test_project_file_upload.py) 替换旧逐词条上传、远端 ID 写回、本地关联恢复和助手双向规划测试。覆盖只读比较、精确目标、已取消请求、跳过文件、已部分提交且后续响应丢失。保留独立同步核心、远端文件修订和桌面任务中心的测试。

- 助手面板在界面线程取得本次授权，再用已有后台执行器上传，按原请求记录每个文件的结果。拒绝确认时不会发起远端读写。取消或撤销执行权限后停止后续写入，已提交部分按实际回执记录。对应[面板确认与取消回归](../../../../tests/ui/tools/smart_assistant/test_confirmed_file_upload.py)。

## 范围、后处理与导入

- `application/translation/task_scope.py` 提供桌面和助手共用的翻译范围。工作台 `scope_presenter.py`、助手 `translation_settings.py`、`base.py`、`operation_preview.py` 和启动工具按该范围选择候选。已有排除范围在预览或启动前拒绝处理，空过滤结果保持为空。后处理仅公开 `max_concurrent` 和现行策略。已保存的配置由现有策略读取器处理。
- `tool_parser.py` 与 `_source_import.py` 按桌面实际操作区分登记来源与导入匹配译文。内部 TransBridge JSON 用于导入现有来源，ParaTranz 和 DSD JSON 可按工作台规则登记。同步修订 FR9、FR16、FR19 及相关 ADR、助手开发说明。

## 验证范围

使用仓库现有环境、临时工程、模拟 ParaTranz 网关及离线模型替身进行验证。未连接生产项目或调用真实模型。其余工具的业务实现统一按原计划分批推进。

以下跨模块回归覆盖工具目录、助手请求、真实工程下的文件操作、界面组件与任务历史。第一组通过 3258 项、跳过 13 项；第二组通过 731 项。两组包含部分相同的上传用例。

```powershell
uv run --offline --no-sync --no-cache pytest tests/smart_assistant tests/paratranz tests/contracts/paratranz tests/application/categorized_upload tests/application/assistant_requests tests/ui/operations tests/ui/tools/smart_assistant tests/integration/gui/test_task_storage_history.py tests/ui/tools/test_ai_project_task_scope.py -q -m "not llm" --tb=short
uv run --offline --no-sync --no-cache pytest tests/contracts/io tests/contracts/ui/test_public_ui_imports.py tests/application/projects/test_categorized_upload.py tests/ui/operations/test_categorized_upload_dialog.py tests/smart_assistant/tools/test_project_file_upload.py -q -m "not llm" --tb=short
```

确认上传切到后台后，助手面板、权限、会话控制与文件上传回归通过 596 项，跳过 12 项。新增的 6 个用例均通过，用 Qt 定时器验证上传期间界面能处理事件，并核对拒绝、取消、执行权限失效、账号切换和远端响应丢失时的写入与回执。Ruff、格式与 Git 空白检查均通过。

```powershell
uv run --offline --no-sync --no-cache pytest tests/ui/tools/smart_assistant tests/smart_assistant/test_workflow_closure_audit.py tests/smart_assistant/test_session_controller.py tests/smart_assistant/test_tool_execution_security.py tests/smart_assistant/test_plan_startup_failure.py tests/smart_assistant/tools/test_project_file_upload.py -q -m "not llm" --tb=short
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```
