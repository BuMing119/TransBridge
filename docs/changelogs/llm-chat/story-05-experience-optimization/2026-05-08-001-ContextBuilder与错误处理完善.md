# 001: ContextBuilder 与错误处理完善

**日期**: 2026-05-08
**类型**: 增/改
**关联**: Epic: 智能助手侧边栏 > Story 5: 体验优化

## 修改文件

### `src/transbridge/ui/tools/smart_assistant/context_builder.py` (增)
- **修改内容**: 新建 `ContextBuilder` 类，`build(ctx: AppContext) -> str` 静态方法。从 AppContext 收集当前工作环境信息：插件名（从 `ctx.esp_path` stem 提取）、集合概况（总计/已翻译/待翻译条数）、按 context 分类的分类分布（INFO/DIAL 归入"对话"，其余取 context 冒号前缀）。空集合时返回"未加载任何集合"提示。每次 LLM 调用前动态构建，不缓存结果
- **原因**: FR7 智能助手需要将当前翻译工作上下文注入 system prompt，使 LLM 能理解当前工作状态（哪些插件、多少词条、分类分布），从而给出有针对性的建议

### `src/transbridge/ui/tools/smart_assistant/chat_widget.py` (改)
- **修改内容**:
  1. 新增 `_consecutive_errors` 计数器，跟踪连续错误次数
  2. `_on_llm_error()` 重写——区分三类错误：API 认证错误（401/403）→ 提示检查 Key；网络错误（timeout/connection/refused/network/reset/unreachable）→ 显示重试按钮，连续 3 次后建议检查网络/VPN；其他错误 → 显示错误信息
  3. 新增 `_on_retry()` 方法——重试前 cancel + wait(3000) 清理旧 ChatWorker，防止线程泄漏，然后重新调用 `_run_llm_round()`
  4. `_on_llm_finished()` ——LLM 成功响应时重置 `_consecutive_errors = 0`
  5. `_on_tool_executed()` ——异常捕获增加 `error_type` 字段，记录异常类名
  6. `_handle_tool_result()` ——工具失败时显示异常类型（`error_type`），格式为 `❌ tool: msg\n  (类型: ErrorType)`
- **原因**: 原有错误处理仅显示原始错误信息，无分类、无重试、无连续失败检测。用户在网络不稳定或 API 配置错误时缺乏明确的故障排查指引

### `plans/llm-chat/plan.md` (改)
- **修改内容**: Story-05 状态从 📝 更新为 ✅ 已完成
- **原因**: 编码完成，更新 plan 状态
