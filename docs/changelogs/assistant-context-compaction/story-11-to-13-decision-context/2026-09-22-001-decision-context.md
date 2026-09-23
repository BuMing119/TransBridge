# 任务决策材料、版本与详情查询第一版

- 日期：2026-09-22。
- Epic：assistant-context-compaction；Story：S11～S13。
- 关联：[实施计划](../../../../plans/assistant-context-compaction/plan.md)、[ADR-041](../../../adr/041-assistant-context-compaction.md)。
- 状态：S11、S13 本地实现与离线验证完成；S12 功能已实现，但大记录交付性能设计待用户选择，未完整验收。

## 行为与范围

执行恢复仍由代码与 checkpoint 负责。模型输入改为下一次决策所需的目标、约束、活跃事项、依赖状态及未结算操作，完成记录详情通过受当前 admission 约束的工具查询。原始选择范围仍保留；活跃工作集与不可变摘要链仍可能触发容量等待，不承诺上下文永久有界。

普通追加保留旧消息字节；实质材料变化才追加。新消息使用独立单调 state_seq，最高序号生效，压缩后消息位置和请求 revision 不再被用作新旧依据。旧无序号格式只读识别，首次新材料明确取代其权威性。用户明确要求保留完成后的模型结果解释回合，本次不修改该调用规则，也不增加循环重试或静默恢复兜底。

## 逐文件变更

以下生产路径均相对 `src/transbridge/`，仅记录本任务的差异：

- 修改 `application/assistant_context/state_projection.py`：以 decision_context 替代完整 required_state 投影，完成项使用数量汇总，隐藏已结算明细与内部进度，按 admission 提供回答协议及详情查询入口。
- 修改 `application/assistant_context/models.py`：加入新旧状态材料解析、state_seq 验证及自描述权威规则；消息和分组使用独立身份，避免 A→B→A 的相同内容复用身份。
- 修改 `application/assistant_context/projection.py`：按实质 digest 追加，序号在配置重建时继续递增；旧格式首次升级追加新版材料。
- 修改 `application/assistant_context/budget_policy.py`：按最大 state_seq 选择当前材料，旧格式仅走明确兼容分支。
- 修改 `application/assistant_context/compaction.py`：通过统一解析取得当前材料，兼容新旧包装；摘要链保留策略不变。
- 新增 `application/assistant_context/state_queries.py`：校验会话、范围与 admission，按 section 和字符页查询，分页 digest 不一致显式报错，拒绝过期交付。
- 新增 `smart_assistant/state_query_protocol.py`：定义 read_request_state 工具 schema 和严格参数解析。
- 新增 `ui/tools/smart_assistant/request_state_binding.py`：后台查询、交付资格复验、保存工具回执及继续回合；当前前台重验全 section 的性能风险见遗留项。
- 修改 `smart_assistant/context_runtime.py`：调用 decision_context。
- 修改 `smart_assistant/native_tools.py`：注册状态详情查询定义。
- 修改 `smart_assistant/request_protocol.py`：加入查询控制工具及参数解析；保留工作区原有回应协议修改。
- 修改 `ui/tools/smart_assistant/request_binding.py`：接入 decision_context 与查询交付；保留工作区原有路由修改。
- 修改 `infra/assistant_prompt_cache.py`：已识别官方 OpenAI 助手模型统一使用稳定 key 和自动前缀缓存，移除改写 system 内容的显式缓存分支；翻译缓存和未知端点策略不变。

测试与文档：

- 新增 `tests/application/assistant_context/test_decision_context.py`：完成详情增长、去重、依赖与未知结果、序号、重建、旧格式、损坏状态和 A→B→A 回归。
- 新增 `tests/application/assistant_context/test_state_queries.py`、`tests/smart_assistant/test_state_query_protocol.py`、`tests/ui/tools/smart_assistant/test_request_state_binding.py`：参数、范围、分页、过期与取消交付回归。
- 修改 `tests/application/assistant_context/test_compaction.py`：覆盖压缩后的序号权威性；按预算推导无收益测试夹具，生产预算未放宽。
- 修改 `tests/infra/test_assistant_prompt_cache.py`：验证官方模型自动缓存保留原消息及稳定 key。
- 修改 `tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py`：仅本任务涉及的两个模拟模型分支改为解析新版决策材料；该文件其他工作区差异不属于本记录。
- 修改 `docs/adr/041-assistant-context-compaction.md`、`docs/requirements.md` 的 FR30、`plans/assistant-context-compaction/plan.md`、其 `stories/story-05-budgeted-compaction.md`：同步实际合同、兼容边界及待决策项。
- 修改 `plans/INDEX.md` 对应 Epic 行、`docs/changelogs/INDEX.md`：登记当前状态和本增量。

已有 assistant-conversation-routing、ADR-040、路由服务与路由测试等差异不属于本次增量，未回退或归入本记录。

## 验证

使用已有 uv 环境，未安装依赖、未调用付费模型。先进行相关模块局部验证，再执行以下联合回归：

```powershell
uv run --offline --no-sync --no-cache pytest tests/application/assistant_context tests/application/assistant_requests tests/smart_assistant tests/ui/tools/smart_assistant tests/infra/test_assistant_prompt_cache.py tests/infra/test_llm_client_prompt_cache.py tests/infra/test_openai_tool_calling.py tests/infra/test_anthropic_tool_calling.py tests/persistence/test_assistant_attachment_cleanup.py tests/persistence/v2/test_assistant_session_migration.py -q -p no:cacheprovider --basetemp .tmp-context-decision-qa --tb=short
uv run --offline --no-sync --no-cache ruff check src tests
uv run --offline --no-sync --no-cache ruff format --check src tests
```

结果：1,263 passed，34 warnings，34.02 秒；Ruff 检查通过，1,366 个文件格式检查通过。警告为 SWIG 与现有直接修改消息接口的弃用警告。未运行全仓库测试、真实模型语义和服务端缓存收益评估，未提交 Git。

## 遗留与兼容边界

- S12：交付时 validate_state_page 会在 GUI 线程重新读取、序列化整个 section。分页限制返回量，不能限制此次校验工作量，大 evidence/effects 可能阻塞 UI。删除复验会削弱过期保护；后台提交又涉及取消与前台工具回执的顺序。已向用户提出“扩展后台提交与前台回执协议”（推荐）或“接受首版同步校验限制”两种选择，等待决定；没有用截断、缓存或重试掩盖此设计问题。功能测试通过不代表 UI 响应验收完成。
- S09：缓存前缀稳定性由本地测试验证，真实命中率、总成本和语义收益仍待真实模型评估。
- 新状态包装不保证旧程序可解读；降级需要升级前完整备份。工具 schema 变化可引发一次上下文配置重建和缓存前缀失效。
