# 001: ConversationManager + ContextBuilder + MarkdownRenderer 单元测试

**日期**: 2026-05-12
**类型**: 增
**关联**: Epic: Smart Assistant QA 全面修复 > Story 07: 测试补充

## 修改文件

### `tests/test_conversation_manager.py` (增)
- **修改内容**: 新建测试文件，10 个测试用例覆盖：用户/助手/系统消息添加、max_turns 裁剪（未超出/超出/保留最后轮次）、observation 消息注入与裁剪联动、plan_result 消息、清空对话。约 90 行
- **原因**: C2 — ConversationManager 为对话状态管理核心组件，原零测试覆盖。验证 M10 修复后 _trim 正确处理 observation 消息

### `tests/test_context_builder.py` (增)
- **修改内容**: 新建测试文件，7 个测试用例覆盖：空集合返回提示、正常集合摘要（含已翻译/待翻译计数）、C6 Prompt 注入防护（验证原始文本不出现在上下文中）、空上传文件不渲染段落、分类分布、C1 依赖注入模式。使用 MockAppContext 模拟最小上下文。约 100 行
- **原因**: C2 — ContextBuilder 原无测试，C6 修复（移除 raw_text 直接拼接）和 C1 修复（移除 UI 依赖改为依赖注入）需验证正确性

### `tests/test_markdown_renderer.py` (增)
- **修改内容**: 新建测试文件，14 个测试用例（2 个 QApplication 依赖自动跳过）覆盖：纯文本/空字符串/空白文本解析、H1-H3 标题识别、代码块（含语言标注）、无序列表、有序列表、表格、水平分割线、粗体/斜体行内格式、未闭合标签降级为纯文本、混搭格式容错、不规范表格降级、render() 返回 QWidget 验证。约 105 行
- **原因**: C2 — MarkdownRenderer 为 infra/ 共享基础设施（ADR-010），被 SmartAssistant 的消息气泡和 AI 翻译报告使用，原无测试覆盖
