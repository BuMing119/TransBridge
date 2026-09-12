# S05：按预算生成可验证摘要候选

- 状态：本地实现与离线验收完成；真实语义质量待 S09。
- 所属：[实施计划 S05](../plan.md#s05)。
- 契约：[ADR-041](../../../docs/adr/041-assistant-context-compaction.md)。
- 下游：[S06 原子激活](story-06-atomic-activation.md)。

## 1. 实际接口与职责

`application/assistant_context/budget_policy.py` 的 BudgetPolicy 决定 append、compact 或 capacity_wait；复用 ContextBudget 联合计算消息、工具、输出预留和协议余量。默认 H=0.80B、L=0.45B，新段目标上限 min(2048, 0.15B)，包装和来源开销计入预算。

`compaction.py` 的 `compact(epoch, budget, tools, required_state, summarizer, enabled=True)` 返回新的 ContextEpoch 或 PreparationWait。它只构建候选，不发布 head、不更新目标、计划或执行状态。`validate_summary` 检查结构及来源，`validate_successor` 校验旧摘要链精确继承。

`ports.py` 的 SummaryGenerator 接收选中的 FrozenContextItem、输出限额和可选只读旧摘要背景。`smart_assistant/context_summary.py` 的 SemanticSummaryGenerator 通过 IsolatedSummaryClient 调用现有服务，无业务工具，purpose 为 summary，用量绑定原会话。真实客户端按需独立创建，取消不关闭共享翻译连接。

## 2. 选择与生成流程

1. 检查当前状态快照与权威 RequiredState 一致。
2. 未达高水位且硬预算允许时原样返回，不按轮数整理。
3. 固定全部旧摘要、当前状态、最新用户输入及必要完整工具组；旧摘要或必需材料超限直接等待。
4. 只选择尚未被摘要覆盖的旧原文完整组，不截断调用与结果。用实际摘要提示形状先核验生成输入预算。
5. 摘要返回 discussion_context、decisions_with_sources、unresolved_questions、suggested_next_steps 四字段 JSON；决定只能引用本次 new_sources 中的准确 ID。
6. 程序分配新 summary_id 和覆盖范围，原样继承所有旧段；将当前状态放到摘要之后，再保留原文和后续事件。
7. 核验最终完整请求预算，合格才交给 S06 发布。

来源检查能证明可追溯，不能证明语义忠实。否定、条件例外、决定被推翻及同名任务的语料留给 S09 人工标注评估。

## 3. 连续压缩与失败边界

第一次形成 S1；第二次只整理后续尚未摘要原文形成 S2，实际输入继续包含 S1+S2；第三次为 S1+S2+S3。不递归总结、覆盖或自动删除旧摘要。旧决定被修改时通过后续事件与新段表达，当前权限及状态始终来自权威系统。

正常整理最多一次生成加一次结构修复；超大首次历史最多三次生成/修复调用，可形成多个独立段。供应商明确参数降级可能增加一次单独记账的 wire attempt，成本按所有 attempt 统计。

达到调用限额或后续失败时，已验证分块通过 PreparationWait.candidate_epoch 返回；runtime 将其保存为未发布候选，绑定原 revision/head/归属与配置。只有显式继续并重新核验才能复用，不自动无限重试。全部旧摘要链本身已经超限时，重试也不会自动删段或再次调用模型。

## 4. 验证与落点

实现文件：`models.py`、`ports.py`、`budget_policy.py`、`compaction.py`、`smart_assistant/context_summary.py`。配置开关在 LLMConfig，准备/等待与发布在 S06。

已运行的测试覆盖高低水位、小窗口、巨大输入、完整工具组、至少三次压缩与附件重开、旧链原文/顺序不变、新来源覆盖不重复、结构错误修复上限、取消、容量等待、旧候选失效及 usage。

```powershell
uv run --offline --no-sync --no-cache pytest tests/application/assistant_context/test_budget_policy.py tests/application/assistant_context/test_compaction.py tests/application/assistant_context/test_long_sessions.py tests/smart_assistant/test_context_summary.py -q
```

综合证据见[验证报告](../../../docs/test-reports/assistant-context-compaction.md)。fake 摘要用于协议与状态验证，不能据此声明真实模型语义质量或成本改善。
