# 翻译设置、动态术语库与入站草稿审核

2026-11-22 · [助手工具复用桌面功能](../../../../plans/assistant-shared-business-tools/plan.md) · S4 第六批

- `get_translation_config` 与 AI 翻译窗口“查看术语库”共用活动来源解析和动态库加载。插件按路径取库，没有原始文件的工程来源沿用已登记来源的缓存身份。查询使用当前窗口的 ParaTranz 凭据状态，返回正式动态库的路径与实际术语数，缺库时不会创建该来源的目录。已删除按工作目录猜测文件和统计 JSON 根对象键数的实现。[来源解析](../../../../src/transbridge/ui/dynamic_terminology_source.py) · [编辑器入口](../../../../src/transbridge/ui/tools/ai_translator/config_dialogs.py) · [动态库](../../../../src/transbridge/ai_translator/term_database.py) · [目录解析](../../../../src/transbridge/config/llm.py)
- `set_translation_config`、`set_term_config` 与设置中心共用服务字段、路径、术语来源顺序和保存校验。服务端点仍须同时提交供应商、地址与模型。仅开放桌面可选的供应商、温度精度与输出上限。来源排序接受五项各一次，旧配置缺少来源时按设置页补全显示。已从工具参数和说明撤下写入 `game_profile`、用空列表或子集停用术语来源的操作。[AI 服务页](../../../../src/transbridge/ui/settings/ai_service_page.py) · [术语页](../../../../src/transbridge/ui/settings/terminology_page.py) · [设置草稿](../../../../src/transbridge/ui/settings/draft.py) · [共享设置规则](../../../../src/transbridge/config/translation_preferences.py) · [工具实现](../../../../src/transbridge/smart_assistant/tools/translation_settings.py) · [工具注册](../../../../src/transbridge/smart_assistant/tools/tool_translator.py)
- 配置工具仅按载入时的修订写入请求明确指定的字段。受助手请求管理的写入继续经过本轮撤销服务，过期或取消时保留已保存的设置和撤销记录。[配置写入](../../../../src/transbridge/smart_assistant/tools/config_undo_capture.py) · [修订校验](../../../../src/transbridge/application/assistant_requests/config_undo_capture.py) · [并发与撤销回归](../../../../tests/application/assistant_requests/test_config_undo.py)
- 桌面启动时将术语工作台使用的同步工厂注入工作台上下文。助手按请求中的所有者、工程、版本和会话取得该工厂创建的服务。会话间隔离计划、授权和待提交的预览引用。[桌面装配](../../../../src/transbridge/ui/app.py) · [工作台上下文](../../../../src/transbridge/ui/context.py) · [正式工厂](../../../../src/transbridge/bootstrap/terminology.py)
- 术语工作台的预检、版本映射、计划、授权及执行共用同一应用服务。计划每页 100 项，后续页使用已保存计划的游标。入站列表返回变化集 ID、时间和条目数，详情每页 50 项并使用桌面同样的偏移边界。三个分页工具已撤下额外的 `limit` 参数。[桌面操作绑定](../../../../src/transbridge/ui/tools/terminology/sync_view.py) · [应用服务](../../../../src/transbridge/application/terminology_sync/service.py) · [入站分页](../../../../src/transbridge/application/terminology_sync/inbound.py)
- 术语任务的状态、重试和核对使用包含运行 ID 的任务引用。应用服务校验同步任务身份和归属，从该次运行的已保存结果读取重试证据。失败项可重试，远端结果未知时先核对。状态返回桌面同样的结果行及无条目结果时的失败摘要。重试与核对工具已撤下模型传入的 `retry_token` 参数。[桌面操作](../../../../src/transbridge/ui/tools/terminology/sync_presenter.py) · [同步任务结果](../../../../src/transbridge/application/terminology_sync/service.py) · [工具声明](../../../../src/transbridge/smart_assistant/tools/tool_terminology_sync.py)
- `terminology_sync_inbound_preview` 仅接收变化集 ID、审核行和用户的接受、拒绝或修改决定，修改时提供新译文。两端从已保存的术语行构造同一审核选择，调用 `prepare_import_selection` 获取当前修订和草稿期望，再调用 `preview_import`，展示同一组变更、冲突和诊断。已删除助手从模型参数重建权威修订、摘要和整条术语内容的路径。入站集合和提交回执中的时间按 ISO 格式传回聊天。[审核决定](../../../../src/transbridge/application/terminology_sync/draft_import_models.py) · [桌面审核行](../../../../src/transbridge/ui/tools/terminology/inbound_review.py) · [桌面预览](../../../../src/transbridge/ui/tools/terminology/sync_presenter.py) · [助手适配](../../../../src/transbridge/smart_assistant/tools/tool_terminology_sync.py)
- 入站提交、签发远端变更授权及替换版本映射授权经过人工确认。助手仅在当前请求对指定参数完成确认后传递审核身份。ReAct 确认期间切换会话会使确认失效。计划步骤沿用已有人工决策窗口。已确认的入站导入只写草稿，发布由桌面原操作完成。[确认边界](../../../../src/transbridge/smart_assistant/tools/terminology_approval.py) · [工具护栏](../../../../src/transbridge/smart_assistant/tools/base.py) · [ReAct 确认](../../../../src/transbridge/smart_assistant/tool_execution_handler.py) · [计划确认](../../../../src/transbridge/smart_assistant/graph_executor.py)

## 验证

配置回归分别操作真实设置页和注册工具，比对两个临时配置库中的字段。动态库测试从桌面编辑器写入一条，再从助手查询。术语集成测试通过正式 GUI 装配与工具执行器打开临时工程，覆盖拒绝确认、确认期间切换会话、另一会话重用原摘要、ReAct 与计划模式提交后 SQLite 草稿和审核身份，以及生效版本保持原状。草稿在预览后发生变化时，旧摘要提交失败。所有远端依赖均在离线夹具内验证。[配置对照](../../../../tests/smart_assistant/tools/test_translation_settings.py) · [术语来源](../../../../tests/smart_assistant/tools/test_term_source_config.py) · [草稿权限与过期](../../../../tests/smart_assistant/tools/test_terminology_sync_tools.py) · [正式桌面装配回归](../../../../tests/integration/gui/test_assistant_terminology_sync.py)

以下测试 185 项通过，1 项因 Windows 符号链接权限跳过。本批修改的 30 个 Python 文件均通过 Ruff 静态与格式检查。

```powershell
uv run --offline --no-sync --no-cache pytest tests/smart_assistant/tools/test_translation_settings.py tests/smart_assistant/tools/test_term_source_config.py tests/contracts/config/test_unified_repository.py `
  tests/application/assistant_requests/test_config_undo.py tests/ui/test_ui_settings_dialog.py tests/smart_assistant/tools/test_terminology_sync_tools.py `
  tests/smart_assistant/test_undo_admission.py tests/ui/tools/test_dynamic_terminology_sources.py tests/contracts/terminology_sync/test_entrypoint_parity.py `
  tests/ui/tools/terminology/test_inbound_review_edits.py tests/application/terminology_sync/test_draft_import.py tests/application/terminology_sync/test_inbound_service.py `
  tests/integration/terminology_sync/test_inbound_publish_boundary.py tests/integration/gui/test_assistant_terminology_sync.py tests/integration/gui/test_ui_foundation_startup.py `
  tests/smart_assistant/test_execution_engine.py tests/persistence/terminology/test_inbound_review.py tests/application/terminology_sync/test_service.py `
  tests/smart_assistant/tools/test_base.py tests/contracts/security/test_tool_schema_security.py tests/smart_assistant/test_english_prompt_surface.py `
  tests/smart_assistant/test_native_tools.py -q --tb=short
```

术语终验覆盖 14 个已注册工具。桌面 Presenter 和助手工具在相同输入下返回一致的预检、映射、计划页、任务状态、故障信息、结果行和入站预览。回归验证了确认拒绝与凭据重放、游标分页、旧参数、同归属的非术语任务、跨会话任务、部分失败重试和未知远端结果核对。入站从同一已发布版本出发，在独立测试存储中分别执行桌面及助手确认，对照两端保存的译文、审核状态和草稿操作记录。[同输入契约](../../../../tests/contracts/terminology_sync/test_desktop_assistant_parity.py) · [注册表](../../../../tests/smart_assistant/tools/test_terminology_sync_tools.py) · [桌面 Presenter 回归](../../../../tests/ui/tools/terminology/test_sync_presenter.py)

以下术语终验 167 项通过，1 项跳过。本机模拟 ParaTranz 的环回连接及临时项目的硬链接发布均已验证。术语相关源文件和测试通过 Ruff 静态与格式检查。

```powershell
$ui = Get-ChildItem tests/ui/tools/terminology/test_sync*.py | ForEach-Object FullName
uv run --offline --no-sync --no-cache pytest tests/application/terminology_sync tests/contracts/terminology_sync $ui `
  tests/ui/tools/terminology/test_inbound_review_edits.py tests/integration/terminology_sync `
  tests/integration/gui/test_assistant_terminology_sync.py tests/smart_assistant/tools/test_terminology_sync_tools.py `
  tests/contracts/security/test_tool_schema_security.py -q --tb=short
```

本批遗留项为零。
