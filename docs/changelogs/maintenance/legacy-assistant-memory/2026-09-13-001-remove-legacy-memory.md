# 001：移除旧 AI 助手记忆系统

- 日期：2026-09-13
- Epic / Story：maintenance / legacy-assistant-memory
- 来源：用户要求删除旧记忆系统，为重新设计做准备；本次不实现新记忆方案。

## 已完成步骤

1. 核对旧记忆与当前会话历史、请求上下文及摘要压缩的边界。
2. 删除旧存储、检索、后台写入与所有聊天接线，更新回归测试。
3. 执行定向回归、全库静态/格式检查并检查差异。
4. 更新当前需求、ADR 状态及本增量索引。

## 文件变更

- 删除 `src/transbridge/smart_assistant/memory/__init__.py`：移除旧记忆导出。
- 删除 `src/transbridge/smart_assistant/memory/memory_store.py`：移除 MemoryEntry、MemoryStore、FAISS/JSON 存储和 LRU。
- 删除 `src/transbridge/smart_assistant/memory/memory_retriever.py`：移除文字与可选向量检索。
- 删除 `src/transbridge/smart_assistant/memory/memory_writer.py`：移除旧记忆后台写入线程。
- 修改 `src/transbridge/smart_assistant/conversation_orchestrator.py`：移除记忆参数、回调、系统提示词记忆注入和响应后的记忆写入；保留模型调用、请求准备及响应处理。
- 修改 `src/transbridge/ui/tools/smart_assistant/chat_composition.py`：移除记忆初始化和回调接线，聊天初始化不再创建 memory 目录。
- 修改 `src/transbridge/ui/tools/smart_assistant/chat_widget.py`：移除记忆字段和关闭参数，更新阶段说明。
- 修改 `src/transbridge/ui/tools/smart_assistant/lifecycle_binding.py`：移除旧记忆关闭步骤。
- 修改 `src/transbridge/ui/tools/smart_assistant/session_binding.py`：移除对话截取写入、检索、待注入记忆状态及清理方法；保留会话保存/恢复及轮次协调。
- 删除 `tests/smart_assistant/test_memory.py`：退役被删除实现的专属测试。
- 修改 `tests/ui/tools/smart_assistant/test_chat_bindings.py`：移除记忆假对象与构造参数，保留轮次和关闭行为回归。
- 修改 `tests/ui/tools/smart_assistant/test_v2_chat_recovery.py`：初始化测试改为验证不创建旧 memory 目录、不加载模型配置或创建客户端。
- 修改 `docs/requirements.md`：替换 FR7.13.3 旧要求，删除相关线程/LRU、测试及 ChatWidget 职责要求，标记 FR10.5 退役。
- 修改 `docs/adr/009-agent-file-memory-reflexion.md`：将长期记忆决策标记为已退役历史，保留其他决策。
- 修改 `docs/changelogs/INDEX.md`：登记本记录。

## 验证

- `uv run --no-cache pytest tests/ui/tools/smart_assistant/test_chat_bindings.py tests/ui/tools/smart_assistant/test_v2_chat_recovery.py tests/smart_assistant/test_conversation_orchestrator_lifecycle.py tests/smart_assistant/test_context_summary.py tests/application/assistant_context -q`：91 passed，3 条 SWIG 弃用警告。
- `uv run --no-cache ruff check src tests`：首次发现删除接线后遗留的导入空行；修复后复验通过。
- `uv run --no-cache ruff format --check src tests`：1353 files already formatted。
- `git diff --check`：通过。
- 搜索 src/tests/scripts 中旧记忆包、写入和召回接线：无残留引用。
- 使用现有 uv 环境，在沙箱外执行验证以访问既有 Python 解释器；没有更新依赖或锁文件。
- 未运行全库 pytest 或真实模型请求：本次回归集中验证受影响聊天链路以及保留的上下文和摘要能力。

## 兼容与后续

旧记忆包及构造参数不再提供兼容入口。已有用户记忆文件不读取、不迁移、不删除；会话历史、请求上下文、摘要压缩及独立的 translation_memory 词典系统保留。共享 embedding/向量基础设施仍由其他功能使用，未移除依赖。

本次删除无未完成项。新长期记忆系统的需求与实现留待后续设计。
