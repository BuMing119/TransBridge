# 聊天与工作请求分流

- 日期：2026-09-13
- Epic：assistant-conversation-routing；Story：S01/S02。
- 依据：[Plan](../../../../plans/assistant-conversation-routing/plan.md)、[FR30](../../../requirements.md#fr30智能助手用户请求生命周期与长会话上下文)、[ADR-040](../../../adr/040-assistant-user-request-lifecycle.md)。
- 授权：用户展示“你好”被创建为任务且回答后报 ANSWER_INCOMPLETE 的截图，讨论聊天与工作分流后调用 bm-pilot 实施。开始时工作区无未提交变更；本记录仅涵盖此次实现。

## 行为

寒暄、感谢及无需工具或进度跟踪的普通问题可在入口同一次模型调用中直接回复，不创建 UserRequest、不要求回答覆盖声明。需要工具、多步骤或明确跟踪的工作仍使用原请求及执行证据规则。混合输入支持回复与工作指令共存。

回复正文、稳定消息身份和输入消费位置在同一个 Session 事务中保存；保存失败可重试，重放不会重复回复。空工作列表与工作按钮隐藏，会话过程入口始终保留。入口最近对话限量投影，终态只选择有限的近期/标题相关/显式 ID 候选，不逐轮发送全部已完成事项。

## 文件与符号

- 修改 `src/transbridge/application/assistant_requests/routing.py`：`parse_proposal` 支持严格 RESPOND 字段与非空正文；`apply_proposal` 只生成无 request_id 的已应用回执，不能用直接回复改变工作状态。
- 新增 `src/transbridge/application/assistant_requests/routing_commit.py`：`commit_routing` 负责 Session 原子提交，`direct_replies` 从已保存提案及回执派生稳定消息；修改 `service.py`，将原 `apply_routing` 完整用例移至新模块，保留公开方法。
- 修改 `src/transbridge/smart_assistant/request_protocol.py`：路由工具 schema 增加 RESPOND 和 response；沿用既有阶段隔离及截断控制拒绝。
- 修改 `src/transbridge/smart_assistant/request_router.py`：提示模型区分直接聊天、跟踪工作和已有工作操作；感谢不恢复旧请求，遗漏目标先澄清。使用新上下文投影。
- 新增 `src/transbridge/smart_assistant/routing_context.py`：`request_candidates` 过滤会话/scope，并选择活动及有限终态目标；`recent_conversation` 限量保留聊天及问答正文，不回放工具调用。最近 12 条、每条最多 2000 字符、总共最多 12000 字符；终态取最近三项、词项相关至多三项及显式 ID 引用。
- 修改 `src/transbridge/smart_assistant/conversation_manager.py`：`add_assistant` 支持可选稳定 message_id，原调用保持兼容。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_binding.py`：向路由提供近期历史，展示已持久化直接回复并闭合控制回执。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_list_view.py`：工作列表为空时隐藏标题、列表和工作操作按钮；保留会话过程及停止生成。
- 修改 `src/transbridge/smart_assistant/conversation_orchestrator.py`：在原响应显示分支移除路由阶段说明气泡，实际回复由已接纳 RESPOND 展示。仅局部修改 4 行；该文件 651 行，后续责任拆分条件记录于 Plan。
- 修改 `src/transbridge/smart_assistant/request_evaluation.py`：语料 history 经过生产近期聊天投影，评测输入与运行路径一致。
- 修改 `tests/application/assistant_requests/test_routing.py`：覆盖直接回复、字段约束、重放、混合工作及普通文本不能完成/恢复执行请求。
- 修改 `tests/application/assistant_requests/test_request_repository.py`：真实临时 Session 验证原子保存、附件历史、重试去重、无效提案及存储失败恢复。
- 新增 `tests/smart_assistant/test_routing_conversation.py`：覆盖有限终态、中文标题/显式 ID、会话过滤、近期聊天、问答正文与工具协议分离、截断。
- 修改 `tests/smart_assistant/test_request_protocol.py`：截断/错误/取消的直接回复不能提交。
- 修改 `tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py`：三个真实 Qt 离线客户端场景验证一次调用、零请求、无覆盖报错、落盘、空工作控件及后续对话背景。
- 修改 `tests/fixtures/assistant_request_routing/corpus.json`、`synthetic_captures.json`、`README.md`：普通引用解释改为 RESPOND，跟踪分析场景明确要求建任务；增加问候、已完成后的感谢、普通问题、问候与工作四项。对合成记录重建输入摘要，全部保留 synthetic 标记；未修改或伪造真实采集记录。
- 修改 `tests/smart_assistant/test_request_evaluation.py`：按语料实际数量核对结果，保持合成验证与真实模型验收分开。
- 修改 `docs/requirements.md` 的 FR30.2/7/10/13 及 `docs/adr/040-assistant-user-request-lifecycle.md` 的入口契约；新增当前 Plan，并最小更新 `plans/INDEX.md` 与 changelog 索引。

## 验证

- `uv run pytest tests/application/assistant_requests/test_routing.py tests/application/assistant_requests/test_request_repository.py tests/smart_assistant/test_routing_conversation.py -q`：初次聚焦 43 项通过。
- Qt 初次新增测试因离线客户端签名缺少第四位置参数失败，修正测试替身后直接对话 3 项通过。
- 首次扩大回归 1,108 通过、23 失败：旧合成摘要与新协议不匹配，以及隐藏整个工作区导致过程入口不可达；均已修正。重建语料时修正两个场景预期落点后，语料模块 36 项通过。
- `uv run pytest tests/application/assistant_requests tests/application/assistant_context tests/smart_assistant tests/ui/tools/smart_assistant -q`：最终相关回归 **1,137 passed**，34 条弃用警告，退出码 0。
- 最后补充覆盖声明所附回答正文的近期投影；`uv run pytest tests/smart_assistant/test_routing_conversation.py tests/smart_assistant/test_request_evaluation.py tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py -q`：**78 passed**，退出码 0。
- `uv run ruff check src tests`：最终通过。
- `uv run ruff format --check src tests`：最终 1,359 文件均已格式化。
- `git diff --check`：通过；无提交、推送、依赖或锁文件变更。
- 沙箱内 uv 缓存与解释器访问失败后，经工具批准使用既有 uv 环境运行；未安装新依赖。本次 `.tmp-chat-routing-uv` 临时缓存于收尾核对路径后删除。

## 兼容与限制

旧请求和历史继续保留，包括用户原有问候请求；不自动删除、完成或恢复它们。v1 指令新增 RESPOND，旧存档可读；含新指令的存档不可用旧路由解析器处理，回退需保留备份。

真实模型语义评测未执行；离线协议、状态及 Qt 回归不能证明所配置模型必定正确分类所有自然语言。历史候选使用本地词项匹配，目标不明时需澄清标题或 ID，没有实现全历史语义搜索。未跑整个仓库测试，验证覆盖本次相关请求、上下文及助手模块。
