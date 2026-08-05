# 002: QA 修复 — get_tool_help 输出验证 + orchestrator 防御 + 自动保存防抖

**日期**: 2026-05-25
**类型**: 改
**关联**: Epic: 工具提示词分层加载 > Story 03: get_tool_help 注册 + build_system_prompt 重构

## 修改文件

### `src/transbridge/smart_assistant/guardrails/output_validator.py` (改)
- **修改内容**: `after_execute` 类型检查从 `dict | list | None` 放宽为 `dict | list | str | None`
- **原因**: `get_tool_help` 返回 `ToolResult.ok(data=help_text_string)`，工具帮助文本是纯字符串，不应被输出校验拒绝。原先的 dict/list 白名单阻止了 get_tool_help 的正常工作

### `src/transbridge/smart_assistant/conversation_orchestrator.py` (改)
- **修改内容**: `_stage_c()` 中 `self._round_messages` 和 `self._round_max_tokens` 的访问从直接属性访问改为 `getattr(..., default)`，删除操作改为重置为空值
- **原因**: 当 get_tool_help 输出校验失败触发错误恢复路径时，`_stage_c` 可能被重复调用而 `_stage_a` 尚未重新设置 `_round_messages`，导致 AttributeError 崩溃。防御性访问消除此竞态

### `src/transbridge/ui/tools/ai_translator/ai_translator_window.py` (改)
- **修改内容**: 
  - 新增 `_schedule_save()` 方法，通过 QTimer 实现 2 秒防抖
  - `_connect_auto_save()` 中所有控件的变更信号从直连 `_save_config` 改为连接 `_schedule_save`
  - 用户停止操作 2 秒后才执行实际保存，避免了编辑中途的半成品值被写入 INI
- **原因**: 原先所有 `textChanged`/`valueChanged` 信号直连 `_save_config`，改 model 字段时其他字段的旧值可能被连带全量写回 INI，导致用户手动修改的 model 被覆盖

### `tests/smart_assistant/test_agent_tool_integration.py` (改)
- **修改内容**: `TestTranslationConfig.setUp` 中新增 `unittest.mock.patch` mock 掉 `LLMConfig.save_to_file`，`tearDown` 中恢复
- **原因**: `test_set_translation_config_without_profile` 硬编码 `{"model": "gpt-4o"}` 调 `_tool_set_translation_config`，触发了真实的 `save_to_file()` 写入用户 INI 文件，每次运行测试都会把用户配置的 model 覆盖为 `gpt-4o`

### `data/paratranz_config.ini` (改)
- **修改内容**: `[llm]` 节 `model` 从 `gpt-4o` 改为 `deepseek-v4-pro`
- **原因**: 用户使用 DeepSeek API（base_url = https://api.deepseek.com），gpt-4o 是此前测试污染写入的错误值
