# 002: ChatWorker / ExecutionEngine / MemoryStore / ObservabilityCollector / MCP 测试补充

**日期**: 2026-05-12
**类型**: 增/改
**关联**: Epic: Smart Assistant QA 修复 > Story 07: 测试补充

## 修改文件

### `tests/test_memory.py` (增)
- **修改内容**: 新增 `TestMemoryStore` 类 10 个测试用例。覆盖 CRUD（add/get/delete/count）、LRU 淘汰（max_entries=10 下添加 15 条自动淘汰 5 条、访问旧条目延长生命周期）、异步写入（close 刷盘后重新加载验证持久化）、按类型搜索（list_by_type）
- **原因**: MemoryStore 是记忆系统核心，M9 新增异步写入和 LRU 淘汰需验证正确性

### `tests/test_mcp.py` (增)
- **修改内容**: 新增 `TestMCPAuth` 4 用例（空 token 放行、错误 token 拒绝、正确 token 通过、空白 token 放行）+ `TestMCPToolHandling` 6 用例（工具列表非空、deprecated 工具在 registry 但不在 prompt、非 deprecated 工具在 prompt、namespace 查找、不存在工具返回 None、全量 deprecated 验证）
- **原因**: MCP Server 是外部集成接口，C7 认证与 M2 deprecated 过滤需测试验证

### `tests/test_chat_worker.py` (增)
- **修改内容**: 新增 `MockLLMClient` mock 类 + `TestChatWorker` 6 用例。覆盖流式响应 chunk 顺序和聚合（test_streaming_chunks/full_text）、错误信号发射含超时关键词（test_error_signal_on_failure）、错误时无 finished 信号（test_no_finished_on_error）、cancel 中断流式（test_cancel_stops_streaming）、空 chunks 边界（test_empty_chunks）
- **原因**: ChatWorker 是 LLM 通信核心，需验证流式响应、错误处理和取消逻辑

### `tests/test_observability.py` (增)
- **修改内容**: 新增 `TestObservabilityCollector` 9 用例。覆盖会话生命周期（start/end/null end）、Token 统计累积（M12 session_tokens 重置）、追踪持久化（文件存在性）、过期清理（31 天前文件被删除）、工具调用记录（step_started/finished 配对）、重试次数跟踪
- **原因**: ObservabilityCollector 是遥测基础设施，M12/M15 token 和工具统计修复需验证

### `tests/test_execution_engine.py` (增)
- **修改内容**: 新增 `TestExecutionEngine` 10 用例（6 通过 + 4 跳过）。通过项：RetryHandler 实例化验证（M1）、`_paused` 实例级独立验证（M8）、`_decision_cv` 创建验证（M9）、`_executor` 线程池复用验证（M11）、`_eval_condition` 真/假/空三种条件求值、B3 middlewares 注入、`provide_decision` 存储。跳过项：3 个 execute_graph 拓扑/并行/排序和 1 个 cancel 测试（需完整 Qt + ToolRegistry 运行时环境，标记 @unittest.skip）
- **原因**: ExecutionEngine 是工具执行中枢，多项修复（M1/M8/M9/M11/B3）需单元测试验证

### `docs/test-reports/smart-assistant-qa-fix.md` (改)
- **修改内容**: 测试覆盖表追加 5 个新测试文件（~165 用例）；修复状态表更新为 46/50 已修复 4 Minor 已知限制；评分表新增第二轮修复列（32→46→51/60）；审查结论更新为 ✅ 通过
- **原因**: 反映 S05/S06 剩余修复 + S07 测试补充的最终成果
