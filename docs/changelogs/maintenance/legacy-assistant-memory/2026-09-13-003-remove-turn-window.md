# 003：移除旧固定 20 轮上下文窗口

- 日期：2026-09-13
- Epic / Story：maintenance / legacy-assistant-memory
- 来源：用户要求通过 bm-pilot 完整清理旧 20 轮路径，而非保留实际调用与兼容回退。

## 完成步骤与行为

已完成调用链核查、实现删除、回归验证、文档状态同步和增量记录。聊天历史不再提供固定轮次窗口；系统提示词查找使用完整历史，轮次准备直接取得完整 transcript，模型输入预算与压缩仍由现有请求上下文系统负责。存储格式不变，无用户数据迁移或删除。

## 文件变更

- `src/transbridge/smart_assistant/conversation_manager.py`：删除 max_turns、get_messages、窗口缓存、轮次索引及仅用于重建索引的旧观察消息识别；保留完整历史、稳定 ID、工具命名空间恢复和未完成工具调用闭合。
- `src/transbridge/smart_assistant/conversation_orchestrator.py`：系统提示词查找改用 get_history；模型轮次使用 get_transcript，移除旧接口回退。
- `src/transbridge/ui/tools/smart_assistant/chat_widget.py`：删除 max_turns=20 构造参数。
- `tests/smart_assistant/test_conversation_manager.py`：撤销窗口截断测试；验证长对话及工具结果完整保留、重启恢复、稳定 ID 和副本隔离。
- `tests/smart_assistant/test_conversation_orchestrator_lifecycle.py`：测试替身提供完整历史/transcript 接口；新增 25 轮模型准备仍包含第一条用户消息及 ID 的回归。
- `tests/smart_assistant/test_session_manager.py`：改用完整历史，轮次索引断言改为恢复消息顺序断言。
- `tests/ui/tools/smart_assistant/test_submission_lifecycle.py`：提交和清空行为测试使用完整历史。
- `tests/application/assistant_requests/test_request_repository.py`：长会话恢复不再期待一个缩短的窗口视图，验证完整消息数量。
- `docs/requirements.md`：FR7.14.6 取消当前会话 20 轮上限；FR7.15.5 废除固定轮次裁剪，明确完整历史及请求预算/摘要职责。
- `plans/llm-chat/plan.md`：旧固定窗口范围及 S02 验收标为废除。
- `plans/llm-chat/stories/story-02-core-backend.md`：旧窗口接口设计标为已废除历史，更新当前数据流。
- `plans/llm-chat/stories/story-03-loop-control-cards.md`、`story-10-toolresult-observation.md`：数据流改为 get_transcript。
- `plans/smart-assistant-qa-fix/plan.md`、`stories/story-05-thread-resource.md`、`stories/story-07-testing.md`：旧 M10/固定轮次裁剪和专属测试要求废除，保留其他功能状态。
- `plans/assistant-user-request-lifecycle/plan.md`：取消 20 轮兼容投影的现行说明。
- `docs/adr/041-assistant-context-compaction.md`：更新现行调用链，不再把旧窗口描述为保留接口。
- `docs/INDEX.md`、`plans/INDEX.md`：同步 llm-chat 的部分废除状态。
- `docs/changelogs/INDEX.md`：登记本增量；既有记录不变。

## 验证

- `uv run --no-cache pytest tests/smart_assistant/test_conversation_manager.py tests/smart_assistant/test_session_manager.py tests/smart_assistant/test_conversation_orchestrator_lifecycle.py tests/ui/tools/smart_assistant/test_submission_lifecycle.py tests/application/assistant_requests/test_request_repository.py -q`：83 passed。
- `uv run --no-cache pytest tests/smart_assistant tests/ui/tools/smart_assistant tests/application/assistant_context -q`：945 passed，34 条依赖或既有兼容 API 弃用警告。
- 新增轮次准备回归后，`uv run --no-cache pytest tests/smart_assistant/test_conversation_orchestrator_lifecycle.py -q`：8 passed。
- `uv run --no-cache ruff check src tests`：通过。
- `uv run --no-cache ruff format --check src tests`：首次发现一个测试表达式格式问题，格式化该文件后复验通过（1353 files already formatted）。
- `git diff --check`：通过。搜索 src/tests 的旧接口、参数、索引和缓存符号：无残留。
- 未运行仓库全部 pytest 或真实模型 API 请求；本次覆盖受影响助手路径与保留的上下文/压缩功能。

## 兼容与遗留

get_messages 和 max_turns 构造参数已删除，不提供兼容别名。仓库内调用方和测试已更新；外部直接调用者应改用 get_history 或 get_transcript。上下文预算并未取消，完整历史也不等于每次将全部历史无条件发送给模型。本次无未完成项，未提交 Git。
