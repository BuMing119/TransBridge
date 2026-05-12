# Story 07: 测试补充

**所属方案**: `plans/smart-assistant-qa-fix/plan.md`
**技术模块**: `tests/`（测试）、`smart_assistant/`（被测模块）
**状态**: 已确认
**创建日期**: 2026-05-12
**覆盖问题**: C2（测试覆盖几乎为零 — 仅 1 个测试文件覆盖 3 个 Epic 的 ~50 源文件）

## 前置依赖

### 上游 Story
- **Story-01~06**: 全部修复完成后，本 Story 的测试验证修复的正确性
- 测试用例需与修复后的行为一致（如 Story-01 后 ReAct 走 guardrails）

### 引用的架构决策
- **ADR-008**（代码分层）: 测试文件位于 `tests/`，引用 backend 包
- **ADR-012 §1**（安全护栏）: 测试需覆盖 guardrail 激活/绕过场景

## 验收标准

- [ ] `ChatWorker` 测试：流式响应 / cancel / 错误处理 / token usage 统计
- [ ] `ConversationManager` 测试：max_turns 裁剪（含 observation 消息）/ 上下文长度限制
- [ ] `ExecutionEngine.execute_graph()` 测试：DAG 拓扑排序 / 层级并行 / checkpoint 暂停恢复 / 重试
- [ ] `RetryHandler` 测试：可重试错误 vs 不可重试错误 / 参数调整 / MAX_RETRIES（若 M1 实例化）
- [ ] `MemoryStore` / `MemoryRetriever` 测试：添加 / 语义搜索 / 精确搜索 / LRU 淘汰
- [ ] `ObservabilityCollector` 测试：token 统计 / 追踪持久化 / 过期清理
- [ ] `MarkdownRenderer` 测试：12 种格式渲染 / 容错降级 / 链接点击
- [ ] `ContextBuilder` 测试：系统提示词构建 / 上传文件摘要注入 / 工具 schema 注入
- [ ] MCP 模块测试：tools/list / tools/call / auth 拒绝 / 错误处理
- [ ] 5 个知识缺口验证测试

## 实现步骤

### 步骤 1: test_chat_worker.py

**涉及文件**: `tests/test_chat_worker.py`（新建）

**覆盖场景**:
- LLM 流式响应 → 验证 `chunk` 信号发射顺序和内容
- `cancel()` 中断 → 验证 worker 停止且无残留信号
- API 错误 → 验证 `error` 信号发射 + 错误信息正确
- Token usage 统计 → 验证 `usage` 信号数据正确

**Mock 策略**: Mock `LLMClient.completions.create(stream=True)` 返回预定义 chunk 序列

---

### 步骤 2: test_conversation_manager.py

**涉及文件**: `tests/test_conversation_manager.py`（新建）

**覆盖场景**:
- 正常对话 10 轮（< max_turns=20）→ 不裁剪
- 对话 25 轮（> max_turns=20）→ 保留最后 20 轮
- 含 observation 消息的对话 → 裁剪时一并移除旧的 observation
- 含 plan_result 消息 → 同上
- 空对话 → 不抛异常

---

### 步骤 3: test_execution_engine.py

**涉及文件**: `tests/test_execution_engine.py`（新建）

**覆盖场景**:
- 线性 DAG [A→B→C] → 验证拓扑排序正确 (A,B,C)
- 分支 DAG [A→B, A→C, B+C→D] → 验证 B/C 并行
- checkpoint 暂停 → 验证暂停后可从 checkpoint 恢复
- 步骤失败 → 验证失败传播 + 重试触发
- cancel → 验证引擎停止且 `all_finished` 发射剩余结果

---

### 步骤 4: test_memory.py

**涉及文件**: `tests/test_memory.py`（新建）

**覆盖场景**:
- `MemoryStore.add()` + `get()` → 精确检索
- `MemoryRetriever.search("翻译 Dragonborn")` → 语义检索返回相关条目
- LRU 淘汰 → 超过 max_entries 后最旧条目被淘汰
- 空存储 → 搜索返回空列表

**注意**: FAISS 测试需要临时目录，teardown 清理

---

### 步骤 5: test_observability.py

**涉及文件**: `tests/test_observability.py`（新建）

**覆盖场景**:
- 对话追踪 → `start_conversation` + `on_step_finished` × 3 → 验证 trace 含 3 条记录
- Token 统计 → 累计 input/output → 验证 `_session_token_total` 正确
- 持久化 → `flush()` → 验证 JSON 文件内容正确
- 过期清理 → 创建 31 天前的文件 → `cleanup_expired()` → 验证已删除

---

### 步骤 6: test_markdown_renderer.py

**涉及文件**: `tests/test_markdown_renderer.py`（新建）

**覆盖场景**:
- 12 种格式渲染：heading/code block/table/list/link/bold/italic/inline_code/hr/quote/image/plain
- 容错降级：未闭合标签 → 渲染为纯文本不崩溃
- 空输入 → 返回空 QWidget
- 代码块语言标注 → 验证语言名称显示

**注意**: MarkdownRenderer 返回 QWidget，测试无需 QApplication（可 headless）

---

### 步骤 7: test_context_builder.py

**涉及文件**: `tests/test_context_builder.py`（新建）

**覆盖场景**:
- 基本 prompt 构建 → 验证 system prompt 包含工具列表
- uploaded_docs 摘要注入 → 验证注入文件名+字符数，不含 raw_text
- Agent spec 注入 → 验证 agent role 出现在 prompt 中
- 无上传文件 → uploaded_docs 段不出现

---

### 步骤 8: MCP 测试扩展 + 知识缺口验证

**涉及文件**: `tests/test_agent_tool_integration.py`（扩展）

**新增测试**:
- MCP `tools/list` → 验证返回的 tools 含 name/description/inputSchema
- MCP `tools/call` → 调用 read 级工具，验证结果
- MCP auth token → 错误 token 拒绝，正确 token 通过
- MCP 错误处理 → `jsonrpc` 解析错误返回 -32700
- 5 个知识缺口验证（从 `docs/smart-assistant-knowledge-gaps.md` 提取）

---

### 步骤 9: 运行全量测试

- 运行 `pytest tests/ -v` 确认全部通过
- 确认测试覆盖核心路径 ≥ 80%（关键路径 100%）
- 生成覆盖率报告

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `tests/test_chat_worker.py` | 新建 | ChatWorker 流式/取消/错误测试 |
| `tests/test_conversation_manager.py` | 新建 | 对话裁剪含 observation 测试 |
| `tests/test_execution_engine.py` | 新建 | DAG/并行/checkpoint/重试测试 |
| `tests/test_memory.py` | 新建 | MemoryStore + Retriever + LRU 测试 |
| `tests/test_observability.py` | 新建 | 追踪/统计/持久化/清理测试 |
| `tests/test_markdown_renderer.py` | 新建 | 12 格式渲染 + 容错测试 |
| `tests/test_context_builder.py` | 新建 | prompt 构建 + 注入验证测试 |
| `tests/test_agent_tool_integration.py` | 修改 | 新增 MCP 协议 + 知识缺口测试 |

## 风险与注意事项

- **风险**: 测试依赖 Mock 对象（LLMClient/AppContext等），Mock 不准确会导致假阳性 → **缓解**: 优先使用真实对象（如 MemoryStore 用临时文件），仅对网络/UI 依赖使用 Mock
- **风险**: MarkdownRenderer 测试需要 QApplication → **缓解**: pytest-qt 的 `qtbot` fixture 自动管理
- **注意**: 测试应在 Story-01~06 全部修复完成后执行，确保测试的是修复后的行为
- **注意**: 知识缺口验证测试应标记为 `pytest.mark.skip`（若缺口尚未修复）或 `pytest.mark.xfail`（已知问题）
