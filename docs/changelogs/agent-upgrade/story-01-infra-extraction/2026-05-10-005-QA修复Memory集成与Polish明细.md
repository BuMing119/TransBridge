# 005: QA Minor 修复 — Memory 集成 + Polish 明细

**日期**: 2026-05-10
**类型**: 改
**关联**: agent-upgrade > Story-04 / ai-translation > Story-12

## 修改文件

### `src/transbridge/ui/tools/smart_assistant/chat_widget.py` (改)
- **修改内容**: (1) `__init__` 中初始化 `MemoryStore` + `MemoryRetriever`（disabled 模式，零依赖）；(2) `_on_send` 中检索相关历史记忆（top_k=3），注入 `add_system`；(3) `_on_llm_finished` 中自动创建 `MemoryEntry` 记录本轮对话摘要（user 前100字 + assistant 前300字），异常不影响对话流程
- **原因**: QA 报告 Minor #2 — MemoryStore 仅独立模块未集成对话流程。修复后对话自动检索记忆上下文 + 自动记录历史

### `src/transbridge/ui/tools/ai_translator/_mixed_worker.py` (改)
- **修改内容**: `_do_polish()` 返回值从简单计数扩展为 `PolishResult(details=[{entry_id, key, original, translation, polished, success, error}])`，每条润色结果包含完整原文/原译文/润色文本/成功状态
- **原因**: QA 报告 Minor #3 — 润色结果仅成功/失败计数，无逐条目明细。修复后支持后续报告展示

### `src/transbridge/ui/tools/ai_translator/ai_translator_window.py` (改)
- **修改内容**: `_on_mixed_finished()` 增强显示：润色部分展示失败条目列表（key + 错误原因前50字），最多显示 5 条
- **原因**: 配合 Polish 明细增强，用户可在完成弹窗中直接看到失败条目
