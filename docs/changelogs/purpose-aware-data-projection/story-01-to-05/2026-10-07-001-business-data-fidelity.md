# 业务数据保真与凭据用途边界

日期：2026-10-07。Epic：purpose-aware-data-projection；Story：S01～S05。

关联：[计划](../../../../plans/purpose-aware-data-projection/plan.md)、[ADR-046](../../../adr/046-purpose-aware-data-projection.md)、FR7.13.8.4、FR22.5。

## 原因与结果

工具、重试、报告和日志曾对所有字符串及字典键套用凭据正则，误改游戏中的 password/token 文本。包含原文的 EntryKey 因而无法往返，查询重试改变检索条件，报告诊断不能归属原词条。改为按来源区分业务数据、显式凭据和技术诊断，保留原 key/id。真实异常在产生处保护后再进入业务结果；请求作用域结束后仍能安全呈现技术错误。

本次由主会话与三个 Agent 分别完成工具、报告、安全与日志工作线，并交叉复核。开始时工作区干净；以下为本次实际新增或修改，没有提交 Git。

## 逐文件变化

以下路径相对仓库根目录；“新增”外均为修改。

### S01：共享分类、凭据与日志

- 新增 `src/transbridge/application/security/data_projection.py`：`SecretValue`、`DiagnosticValue`、`project_business_data`，普通业务值保真并分离容器副本。
- 新增 `src/transbridge/application/security/diagnostic_logging.py`：`credential_diagnostics` 绑定请求凭据；`diagnostic_exception_text` 保留已保护的异常呈现；`DiagnosticLogFormatter` 处理完整异常链与异常对象日志参数，无永久明文凭据注册表。
- `src/transbridge/application/security/__init__.py`：导出上述分类与异常呈现 API。
- `src/transbridge/application/security/redaction.py`：限定技术用途，接受 known_secrets，处理认证头、显式 SecretValue 及异常对象。
- `src/transbridge/config/paratranz_credentials.py`：SecretValue 移至共享层并兼容旧导入，ParaTranz 已知凭据投射复用技术 redactor。
- `src/transbridge/infra/llm_client.py`：OpenAI/Anthropic 请求诊断绑定本次 client；repr 的服务端点保护。
- `src/transbridge/infra/openai_tool_calling.py`：原生工具流式请求绑定凭据诊断作用域。
- `src/transbridge/infra/anthropic_tool_calling.py`：原生工具流式请求绑定同一作用域。
- `src/transbridge/infra/embedding_client.py`：初始化与编码诊断保护当前凭据，初始化错误存入公开状态前先处理。
- `src/transbridge/entrypoints/mcp.py`：stderr 使用完整诊断 Formatter，停止重复安装旧 Filter；Filter 类保留兼容。
- `src/transbridge/ui/app.py`：GUI 文件日志使用完整诊断 Formatter。
- `src/transbridge/ui/tools/ai_translator/workflow_logging_client.py`：请求、响应、流式片段、无效响应和 validation_details 保真；SDK 错误体、网络头和异常消息单独保护。
- `src/transbridge/ui/tools/ai_translator/run_spec.py`：`_safe_config_value` 仅处理明确配置凭据字段，token 预算与工作流业务字典按真实值参与指纹。

### S02：助手工具、重试与 UI

- `src/transbridge/smart_assistant/guardrails/output_validator.py`：业务投射替代全量扫描；保留类型/展示大小控制；旧 redactor 构造参数接受但弃用，不再改变业务文本。
- `src/transbridge/smart_assistant/tools/base.py`：工具执行出口投射业务结果。
- `src/transbridge/smart_assistant/tools/types.py`：ToolResult 序列化及观察保真，get/下标一致。
- 新增 `src/transbridge/smart_assistant/tools/diagnostics.py`：技术异常出口统一使用安全异常呈现。
- `src/transbridge/smart_assistant/tools/_entry_upload.py`：上传异常使用 technical_error。
- `src/transbridge/smart_assistant/tools/tool_archive.py`：压缩包工具异常在来源保护。
- `src/transbridge/smart_assistant/tools/tool_editor.py`：编辑工具真实异常在来源保护，正常业务失败消息保真。
- `src/transbridge/smart_assistant/tools/tool_migrator.py`：迁移异常在来源保护。
- `src/transbridge/smart_assistant/tools/tool_paratranz.py`：ParaTranz 工具技术失败显式保护。
- `src/transbridge/smart_assistant/tools/tool_parser.py`：解析错误先保护技术消息，再执行已有路径展示处理。
- `src/transbridge/smart_assistant/tools/tool_proofreader.py`：校对工具异常在来源保护。
- `src/transbridge/smart_assistant/tools/tool_terminology_sync.py`：术语同步异常在来源保护。
- `src/transbridge/smart_assistant/tools/tool_translator.py`：翻译工具异常在来源保护。
- `src/transbridge/smart_assistant/tools/tool_writer.py`：写出异常在来源保护。
- `src/transbridge/smart_assistant/mcp/adapter.py`：MCP 正常工具结果业务投射。
- `src/transbridge/smart_assistant/mcp/server.py`：异常响应使用安全异常呈现。
- `src/transbridge/smart_assistant/reflexion/retry_handler.py`：保留业务参数和 schema，只保护 SecretValue/writeOnly；剔除 schema 禁止的额外参数，程序恢复显式凭据。
- `src/transbridge/smart_assistant/reflexion/retry_executor.py`：捕获异常转换为结果前保护。
- `src/transbridge/smart_assistant/tool_execution_handler.py`：工具观察数据业务保真。
- `src/transbridge/smart_assistant/observability/collector.py`：trace 在转字符串前按类型投射，正常业务证据保真。
- `src/transbridge/smart_assistant/chat_worker.py`：异常转 Qt 字符串信号前使用安全呈现。
- `src/transbridge/smart_assistant/conversation_orchestrator.py`：技术错误展示复用共享诊断策略，删除独立重复正则。
- `src/transbridge/ui/tools/smart_assistant/react_execution_binding.py`：ReAct 异常转换前保护。
- `src/transbridge/ui/tools/smart_assistant/request_context_status.py`：上下文准备的技术异常使用安全呈现，业务 RequestError 保持原合同。
- `src/transbridge/ui/tools/smart_assistant/task_binding.py`：任务技术错误展示复用共享策略。

### S03：报告、校对和术语作用域

- `src/transbridge/application/translation/postprocess.py`：报告 metadata 与诊断身份保真；新增 terminology_plugin_id，兼容旧 report_details；已核验旧断点按输入 EntryKey 恢复缺失作用域。
- `src/transbridge/application/translation/postprocess_checkpoint.py`：作用域写入、读取、恢复及候选 hash；无字段旧 hash 保持兼容。
- `src/transbridge/application/translation/task_history.py`：历史报告恢复明确术语字段。
- `src/transbridge/application/translation/postprocess_stages.py`：checker 视图传递术语字段，技术 safe_message 在产生处保护。
- `src/transbridge/application/translation/polish_report.py`：模型 note/issues 业务投射，诊断 EntryKey 保真。
- `src/transbridge/ai_translator/project_terminology_adapter.py`：优先读取明确作用域字段，兼容旧字段。
- `src/transbridge/ai_translator/post_processor/proofread_pipeline.py`：构造候选时传递明确术语作用域。
- `src/transbridge/ai_translator/post_processor/polisher.py`：技术异常 note 在来源保护，正常模型说明保真。
- `src/transbridge/ai_translator/post_processor/quality_gate.py`：技术异常 reason 在来源保护。
- `src/transbridge/ai_translator/post_processor/llm_refiner.py`：单条/批量技术失败保护当前 client 凭据。
- `src/transbridge/ai_translator/post_processor/llm_arbiter.py`：仲裁技术错误与模型说明分开处理。
- `src/transbridge/ai_translator/post_processor/post_processor.py`：四阶段异常先保护后传 UI 日志和 StrictExecutionEvidence，防止真实凭据进入最终处理说明。

### S04：回归证据

- 新增 `tests/security/test_data_boundaries.py`：业务容器、显式凭据/诊断、异常对象、作用域释放与完整日志保护。
- 新增 `tests/smart_assistant/tools/test_business_data_boundaries.py`：实际查询→护栏→观察→编辑、MCP、set_filters 重试、schema/令牌保真、异常跨作用域及旧构造兼容。
- 新增 `tests/contracts/translation/test_report_data_boundaries.py`：身份/说明往返、诊断归属、术语作用域、旧报告/断点恢复。
- 新增 `tests/ai_translator/post_processor/test_diagnostic_data_boundaries.py`：四类 provider 单条/批量异常保护与正常模型说明保真。
- 新增 `tests/ui/tools/test_run_config_projection.py`：凭据隐藏、业务字典保真及 token 预算指纹变化。
- `tests/contracts/security/test_tool_schema_security.py`：凭据反例改为显式来源，同时保留业务身份。
- `tests/contracts/translation/test_polish_report.py`：业务 note 保真断言。
- `tests/contracts/translation/test_postprocess_report.py`：显式凭据/技术诊断保护与业务字段保真并测。
- `tests/smart_assistant/test_retry_executor.py`：凭据 schema writeOnly 声明，保留安全回归。
- `tests/ai_translator/post_processor/test_strict_execution_evidence.py`：四阶段日志与最终 note 不泄露技术凭据。
- `tests/ui/tools/test_workflow_logging_client.py`：业务请求/响应/分片/无效响应/validation_details 保真，技术异常与认证头保护。

### S05：规范与记录

- 新增 `docs/adr/046-purpose-aware-data-projection.md`、`plans/purpose-aware-data-projection/plan.md`：确认用途合同、兼容性、验收与实施状态。
- `docs/requirements.md`：更新 FR7.13.8.4 与 FR22.5 的规范边界。
- `docs/adr/012-safety-observability-mcp.md`：替换当前输出扫描规则，声明 ADR-046 部分取代。
- `docs/adr/016-modular-monolith-application-composition.md`：标明共享脱敏边界由 ADR-046 澄清。
- `plans/platform-contract-foundation-v2/stories/story-04-tool-schema-hitl-security.md`：工具数据流按用途投射。
- `plans/smart-assistant-qa-fix/stories/story-03-security-hardening.md`：移除“脱敏防止提示词注入”的错误表述。
- `plans/native-structured-outputs/plan.md`：失败响应日志保留业务证据，技术诊断分开保护。
- `plans/ai-proofread-tolerance/plan.md`：同一失败日志合同更新。
- `plans/unified-task-translation-runtime-v2/stories/story-06-postprocess-report.md`：报告业务内容保真与凭据边界。
- `docs/INDEX.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md`：仅更新本增量直接相关入口。

## 实际验证

主会话最终分组执行，下列四组共 2270 passed、4 skipped，无失败；不同测试组不重叠。

```text
uv run --no-cache --no-sync pytest tests/ui/tools/test_run_config_projection.py tests/ui/tools/test_ai_task_consistency.py tests/ui/tools/test_task_recovery_binding.py tests/ui/tools/test_task_recovery_discovery.py tests/application/assistant_context tests/persistence/test_assistant_transcript_store.py -q --tb=short -p no:cacheprovider --basetemp=.tmp-rootboundary1007
# 154 passed

uv run --no-cache --no-sync pytest tests/contracts/translation tests/ai_translator tests/application/translation tests/ui/tools/test_ai_translation_reporting.py tests/ui/tools/test_ai_task_history_ui.py -m "not llm" -q --tb=short -p no:cacheprovider --basetemp=.tmp-finalreport1007
# 724 passed

uv run --no-cache --no-sync pytest tests/smart_assistant tests/contracts/security/test_tool_schema_security.py tests/ui/tools/smart_assistant -m "not llm" -q --tb=short -p no:cacheprovider --basetemp=.tmp-finaltools1007
# 1061 passed, 1 skipped

uv run --no-cache --no-sync pytest tests/security tests/infra tests/paratranz/test_credentials_security.py tests/ui/tools/test_workflow_logging_client.py tests/integration/entrypoints -m "not llm" -q --tb=short -p no:cacheprovider --basetemp=.tmp-finalsecurity1007
# 331 passed, 3 skipped
```

最后补充显式 DiagnosticValue 包装异常对象的保护后，重新执行共享安全边界、工作流和 ParaTranz 回归（与上组有重叠，不累计为独立测试数）：

```text
uv run --no-cache --no-sync pytest tests/security/test_data_boundaries.py tests/ui/tools/test_workflow_logging_client.py tests/paratranz/test_credentials_security.py -q --tb=short -p no:cacheprovider --basetemp=.tmp-finaledge1007
# 37 passed

uv run --no-cache --no-sync ruff check src tests
# All checks passed
uv run --no-cache --no-sync ruff format --check src tests
# 1550 files already formatted
git -c core.safecrlf=false diff --check
# exit 0
```

4 项跳过涉及当前 Windows 文件名/长路径/符号链接能力；不计为通过。早期测试因 sandbox 临时目录权限失败，改用授权执行和独占 basetemp 后完成。中途 Ruff 检出的新行长度、导入和换行问题已修复，最终检查通过。临时目录及测试 junction 已验证归属、目标范围并清理。

## 兼容性与未运行项

源 key/id、用户 ESP、权限确认、HTML/CSV 转义未改。旧 SecretValue 导入路径保留；OutputValidationGuard 的 redactor 参数仅兼容接受并警告，下次允许破坏接口的主版本可移除。新增术语字段有默认值，旧内容按可靠来源恢复。修正后的配置指纹包含真实 token 预算，旧指纹可能不再相等，继续按既有一致性规则处理。

未调用真实 LLM/ParaTranz 服务，未打包发行或提交代码；本次验证为离线合成与本地入口回归。过去已被替换成 REDACTED 的文字不猜测恢复。当前注册工具的凭据标注覆盖直接 schema；若未来在 oneOf/$ref 中定义凭据参数，需先补组合 schema 解析和回归。既有超阈值模块仅作当前职责内局部修复，新投射与日志职责已独立拆出。

当前验收范围内无未解决缺陷。
