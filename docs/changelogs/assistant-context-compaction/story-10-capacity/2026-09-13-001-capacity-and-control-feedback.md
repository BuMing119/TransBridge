# 001：助手容量估算与内部控制回合提示修复

- 日期：2026-09-13。
- Epic / Story：assistant-context-compaction / S10。
- 授权：用户在截图诊断后调用 bm-pilot，授权本地实现、回归与增量记录。
- 关联：[计划](../../../../plans/assistant-context-compaction/plan.md)、[ADR-041](../../../adr/041-assistant-context-compaction.md)。

## 用户可见结果

有效的纯路由/查询控制回合不再显示“模型未返回可显示内容”；真正空响应仍提示重试。
助手容量 0 表示自动。对已核验的 DeepSeek 官方兼容端点与 deepseek-v4-flash/pro 精确模型 ID，
自动使用 1,000,000；其他服务明确回退 32K。已有正数配置原样保留，设置显示容量来源并提供
“使用自动容量”按钮。代理服务需按实际规格手动填写，不通过模型名称推断服务能力。

估算不再使用 UTF-8 字节上界：已有模型 tokenizer 可用时加 25% 余量；否则采用字符/词段估算
加 25% 余量。不加载或下载编码。超限提示列出消息、工具定义、输出预留和协议余量，明确是本地
估算而非服务端实际用量。完整历史、摘要链、工具加载与执行授权语义不变。

## 逐文件变更

- 新增 `src/transbridge/smart_assistant/context_capacity.py`：容量解析与来源说明，精确匹配官方端点/模型，手动值优先。
- 新增 `src/transbridge/smart_assistant/context_estimation.py`：已加载 tokenizer 选择及离线文本估算；用户特殊 token 文本作为普通内容计数。
- 修改 `src/transbridge/smart_assistant/context_budget.py`：统一配置预算工厂，改用新估算器，增加可解释的预算组成。
- 修改 `src/transbridge/config/llm.py`：缺省容量改为自动 0，既有正数配置读取与持久化保留。
- 修改 `src/transbridge/smart_assistant/conversation_orchestrator.py`：调用预算工厂，传递自动配置，空正文但含有效工具调用时不误报。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_binding.py`：路由和执行共享配置预算工厂，同步与延迟准备保持兼容。
- 修改 `src/transbridge/application/assistant_context/compaction.py`：硬超限、关闭摘要和摘要空间不足提示包含当前预算明细。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_context_status.py`：去掉重复句号，提示核对容量并在调整后继续。
- 修改 `src/transbridge/ui/settings/ai_service_page.py`：自动容量、来源和旧手动上限提示随模型/端点更新，支持显式切回自动。
- 新增 `tests/smart_assistant/test_context_capacity.py`：官方模型/代理隔离、手动覆盖、离线计数、禁止加载编码、较大输入和全量工具预算。
- 修改 `tests/smart_assistant/test_conversation_orchestrator_lifecycle.py`：复现纯控制回合误报，并保留真正空响应提示验证。
- 修改 `tests/smart_assistant/test_request_context_budget.py`：默认估算标签及中文材料计数验证。
- 修改 `tests/application/assistant_context/test_budget_policy.py`：更新默认离线估算标签断言。
- 修改 `tests/application/assistant_context/test_compaction.py`：超限明细及必需输入不被改写验证。
- 修改 `tests/config/test_assistant_context_settings.py`：缺省自动及 0/旧 32K/大窗口持久化回归。
- 修改 `tests/ui/test_ui_settings_dialog.py`：已有手动容量提示、显式自动切换和代理回退显示。
- 修改 `tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py`：摘要语料固定使用 20,000 测试窗口，保持生成/失败/取消覆盖；新增大输入贯穿真实 Qt 路由和执行的离线测试。
- 修改 `docs/adr/041-assistant-context-compaction.md`：在原预算条款更新容量与估算事实、局限及官方依据。
- 修改 `plans/assistant-context-compaction/plan.md`：记录 S10 边界、步骤、验证与完成状态；S09 外部验收继续待办。
- 修改 `plans/INDEX.md`、`docs/changelogs/INDEX.md`：只更新本 Epic 的状态与本记录链接。

现有 orchestrator 和配置 facade 只做局部条件/默认值/接线修正，新容量和估算职责位于独立模块。
工作区同期出现的长期记忆 ADR-042、assistant-durable-memory 计划及其索引内容属于其他工作，未修改或归入本记录。

## 验证证据

- 首轮聚焦 7 个测试文件：97 项通过。
- 扩大回归最初 1,134 通过、6 失败；失败均为旧摘要夹具未再达到压缩水位。改用显式 20,000 窗口后，请求生命周期 34 项通过。
- 最终命令：`uv run pytest tests/smart_assistant tests/application/assistant_context tests/application/assistant_requests tests/ui/tools/smart_assistant tests/config/test_assistant_context_settings.py tests/ui/test_ui_settings_dialog.py -q`：**1,141 passed**，34 条既有弃用警告，36.66 秒。
- `uv run ruff check src tests`：通过。
- `uv run ruff format --check src tests`：通过，1,356 个文件格式符合要求。
- `git diff --check`：通过；Git 另有 CRLF 转换提示。

## 兼容与遗留

未调用真实付费模型、未修改用户运行配置、未提交或推送代码；未跑不相关的全仓库测试。
已有 32K 配置不会被静默改大，升级后可在设置 → AI 服务点击“使用自动容量”；代理端点手动配置。
离线估算不是严格上界，特殊内容或供应商编码可能低估；服务端仍可能拒绝超限请求。
真实 tokenizer 误差校准、真实模型收益与重复性能样本仍属于 S09，未以离线测试替代验收。
本轮没有创建需要交付后清除的临时脚本/截图目录。
