# 011: QA 复验 — 5 Blocker + 2 Critical 修复通过

**日期**: 2026-05-14
**类型**: 改
**关联**: Epic: 智能助手侧边栏面板 > Story 08: AI助手页面体验全面翻新

## 修改文件

### `ui/tools/smart_assistant/chat_widget.py` (改)
- **修改内容**:
  - **B1**: 删除 `_init_ui_stage3` 中重复的 `self._main_layout.addLayout(toolbar)`（line 223），修复工具栏 chips + 上传按钮行在 UI 中重复显示
  - **B2**: `_on_send` 移除 `self._worker.join(timeout=3)`（line 918），仅保留 `cancel()` + `self._worker = None`，GUI 不再阻塞
  - **B3**: `_clear_conversation` 移除 `self._worker.join(timeout=2)`（line 995），仅保留 `cancel()` + `self._worker = None`
  - **B4**: `_on_retry` 移除 `join(timeout=3)` + try/except 警告消息（line 887-897），简化为 `cancel()` + `self._worker = None`
  - **C2**: `MemoryStore` 实例化追加 `persist_to_disk=False` 参数（line 133），禁用对话记忆磁盘持久化
- **原因**: 4维度并行 QA 审查发现工具栏 UI 重复、4处 GUI 线程阻塞（join 冻结事件循环 2-3s）、对话记忆明文存储，复验全部修复通过

### `ui/tools/smart_assistant/panel.py` (改)
- **修改内容**: **B5**: `closeEvent` 移除 `self._chat._worker.join(timeout=3)`（line 88），仅保留 `cancel()`
- **原因**: 关闭面板时 join 阻塞 GUI 最多 3 秒

### `smart_assistant/memory/memory_store.py` (改)
- **修改内容**: **C2**: `MemoryStore.__init__()` 新增 `persist_to_disk: bool = False` 参数。`_load_metadata()` 仅在 `persist_to_disk=True` 时调用；`MemoryWriterThread` 仅在该标志为 True 时创建和启动；`add()`/`delete()`/`close()` 中的 writer 操作均添加 `if self._writer:` 守卫
- **原因**: 对话记忆默认不再写入 `data/memory/memory_metadata.json`，消除明文存储敏感对话数据的安全风险

### `smart_assistant/chat_worker.py` (改)
- **修改内容**: **C3**: `ChatWorker.run()` 中新增 `chunk_buffer` 批量累积逻辑。新增 `import time`。`_chunk_cb` 闭包中 chunk 先追加到 buffer，每 50ms 或每 20 tokens 才调用 `self.on_chunk("".join(chunk_buffer))`。流结束后强制 flush 剩余 buffer。信号频率从 200+/s → ~20/s
- **原因**: 每个 LLM chunk 独立发射跨线程信号造成 Qt 事件队列积压，批量累积降低 ~10x 信号量

### `plans/llm-chat/plan.md` (改)
- **修改内容**: 状态栏从 "Story-08 待编码" 更新为 "Story-01~08 全部完成，含 FR7.16 文档流重构 + QA复验通过"
- **原因**: Story-08 编码与 QA 均已完成

### `docs/test-reports/llm-chat-story-08-frontend-qa.md` (增)
- **修改内容**: 新建完整 QA 测试报告（~215 行），含 4 维度并行审查结果、29/29 AC 覆盖验证、36 项问题清单、7 项修复验证、复验记录。综合评分 38→51/60
- **原因**: QA 审查流程产出
