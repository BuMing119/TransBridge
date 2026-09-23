# 清理旧执行旁路与上下文策略

- 日期：2026-09-23。
- Epic：assistant-task-context-consistency；Story：S08。
- 计划：[综合计划](../../../../plans/assistant-task-context-consistency/plan.md)。
- 授权：用户审阅保留/删除/接入建议后调用 bm-pilot 推进本地实施；不提交或推送 Git。

## 行为与边界

助手写操作缺少撤销服务或工作轮次时在执行前失败，不继续无凭证写入，不退回整份配置保存。真实新输入可通过既有 AMEND/RESUME 路由建立后续轮次；旧记录仍可读取，不为旧操作伪造归属。已声明不能安全撤销的领域操作仍允许执行并登记真实归属。独立非助手工具调用保持原领域命令能力；共享工具 MCP 兼容验证涉及注入上下文适配器，不表示主 MCP 入口新增写能力。

旧上下文组装器退出生产目录，新流程直接使用抽出的协议能力。旧摘要不再生成或刷新，旧格式解析、读取验证与迁移保留；内部确定性重建用于精确核验旧材料，迁移入口原有来源/权限规则不变。路由状态 reducer 由新结果提交用例复用，旧直接事务入口删除。

## 本轮文件与符号变更

以下仅记录相对本轮开始状态的清理，不将工作区既有 S02～S07 或聊天路由改动归入本次。

### 助手写入

- 修改 `src/transbridge/smart_assistant/request_execution.py`：`before` 在步骤/effect 创建前检查必需服务与轮次，去掉 `undo is None` 时不登记归属的分支；继续使用既有原子 effect/轮次登记。
- 新增 `src/transbridge/smart_assistant/tools/undo_admission.py`：统一解析助手捕获目标；助手缺 gate、服务、轮次明确失败；真正独立调用返回独立执行分支。
- 修改 `src/transbridge/smart_assistant/tools/undo_capture.py`：译文/Variant 命令使用统一解析，删除 gate 已存在但缺服务时直接 mutation 的旁路。
- 修改 `src/transbridge/smart_assistant/tools/binding_undo_capture.py`：绑定命令使用相同严格边界。
- 修改 `src/transbridge/smart_assistant/tools/config_undo_capture.py`：助手缺服务/轮次不能退回 `save_to_file`；独立工具原保存方式保留。
- 修改 `src/transbridge/smart_assistant/tools/file_undo_capture.py`：统一助手身份解析，删除空轮次直接发布文件分支，保留文件授权及备份根检查。
- 修改 `src/transbridge/application/assistant_requests/round_undo.py`：登记和 Variant 捕获拒绝空轮次，移除旧登记静默跳过。
- 修改 `src/transbridge/application/assistant_requests/binding_undo_capture.py`：移除空轮次直接 mutation；前置归属校验继续负责拒绝无效来源。
- 修改 `tests/smart_assistant/test_request_execution.py`：fixture 使用真实接纳输入和应用状态，版本选择测试不再覆盖输入归属。
- 修改 `tests/smart_assistant/test_undo_effect_registration.py`：缺轮次、缺服务零写入/零 effect；旧任务只读仍可用；保留登记失败回滚验证。
- 修改 `tests/smart_assistant/test_undo_capture.py`：独立调用测试不再将缺服务助手视作兼容成功。
- 新增 `tests/smart_assistant/test_undo_admission.py`：四类捕获适配器缺服务/轮次/gate 均不写入，独立调用仍写入。

### 上下文与摘要

- 删除 `src/transbridge/smart_assistant/request_context_assembler.py`：移除旧滚动选材、预算裁剪及摘要拼装策略。
- 新增 `src/transbridge/application/assistant_context/protocol.py`：抽出消息去重、冲突身份检查、迟到结果整理及工具协议组完整性检查。
- 修改 `src/transbridge/application/assistant_context/projection.py`：直接使用新协议模块，不依赖旧类私有方法。
- 修改 `src/transbridge/smart_assistant/request_model_input.py`：归属投影直接导入规范 history_scope 模块。
- 修改 `src/transbridge/application/assistant_requests/summary_service.py`：删除 refresh 写入口，保留历史读取和旧摘要验证。
- 修改 `src/transbridge/application/assistant_requests/summaries.py`：`plan_summary` 改为内部 `_reconstruct_legacy_summary`，仅供旧格式验证；不减弱验证规则。
- 修改 `tests/smart_assistant/test_request_context_budget.py`：针对当前追加投影、预算失败、结果引用和协议整理测试，移除旧组装策略断言。
- 修改 `tests/application/assistant_requests/test_summaries.py`：明确为旧格式重建验证测试。
- 修改 `tests/application/assistant_requests/test_summary_service.py`：验证旧摘要重开只读、缺失不生成、陈旧不刷新、损坏不修补。
- 修改 `tests/application/assistant_context/test_migration.py`：测试准备旧格式使用内部重建，不启用旧生成入口。
- 修改 `scripts/evaluate_assistant_context.py`：去掉旧组装基线，测当前追加投影与存储恢复。
- 修改 `scripts/benchmark_assistant_requests.py`：测当前投影和只读材料准备，不再调用旧摘要刷新。

### 路由

- 修改 `src/transbridge/application/assistant_requests/service.py`：删除 `apply_routing` 薄入口，其他已有变化保留。
- 修改 `src/transbridge/application/assistant_requests/routing_commit.py`：删除 `commit_routing`，保留新结果提交用例使用的 reducer 与回复投影。
- 新增 `tests/routing_fixtures.py`：明确的 reducer/持久化测试数据辅助；不模拟模型执行资格。
- 修改 `tests/application/assistant_requests/test_request_repository.py`：路由 fixture 调用收拢；旧组装断言改为当前投影协议验证。
- 修改 `tests/application/assistant_requests/test_journal.py`：使用测试路由 fixture，保留日志审计验证。
- 修改 `tests/application/assistant_requests/test_work_round_provenance.py`：使用 fixture，补旧空轮次请求经真实 RESUME 输入获得新归属。
- 修改 `tests/application/assistant_context/test_history_queries.py`、`test_context_review.py`：路由数据准备使用 fixture。
- 修改 `tests/application/assistant_requests/test_routing_results.py`：实际新用例的回复/回执原子提交、重放及无效回复拒绝测试。

### 文档

- 修改 `docs/requirements.md`：FR30.24 明确助手写入前置条件，更新旧摘要保留范围。
- 修改 `docs/adr/040-assistant-user-request-lifecycle.md`：删除旧逐轮选材与摘要刷新描述，以 ADR-041 的当前合同为准；核心路由入口改为结果提交用例。
- 修改 `docs/adr/043-assistant-turn-results-and-undo.md`：明确旧记录可读、缺轮次不能新增助手写入、独立调用边界。
- 修改 `plans/assistant-task-context-consistency/plan.md`：新增并完成 S08 与验证证据。
- 修改 `plans/INDEX.md`、`docs/changelogs/INDEX.md`：仅更新本工作线索引。

## 验证

沿用既有 uv 环境，最终测试使用 `uv run --no-sync`，未安装依赖、未调用真实模型或远端业务写接口。

1. `uv run --no-sync pytest tests/application/assistant_context tests/application/assistant_requests tests/smart_assistant tests/ui/tools/smart_assistant tests/infra/test_assistant_prompt_cache.py tests/application/projects/test_assistant_undo.py tests/application/projects/test_assistant_binding_undo.py tests/application/test_file_undo.py -q --tb=short`：1351 passed、1 skipped、65 warnings，退出 0。
2. `uv run --no-sync pytest tests/contracts/test_task_runtime.py tests/contracts/test_task_runtime_backends.py tests/contracts/tasks tests/integration/bootstrap/test_task_runtime_wiring.py tests/ui/tools/test_unified_task_runtime.py tests/integration/entrypoints/test_mcp_stdio.py -q --tb=short`：89 passed、3 warnings，退出 0。与上项合计 1440 passed / 1 skipped。
3. `uv run --no-sync pytest tests/application/test_file_undo.py::test_symlink_parent_is_rejected -q -rs`：确认跳过原因是 Windows 符号链接权限，退出 0。
4. `uv run --no-sync ruff check src tests`、`uv run --no-sync ruff format --check src tests`：通过，1409 文件格式检查，退出 0。
5. `git -c core.safecrlf=false diff --check`：通过。
6. `uv run --no-sync python scripts/evaluate_assistant_context.py --messages 50`：通过，prefix_preserved 为 true，恢复消息一致。
7. `uv run --no-sync python scripts/benchmark_assistant_requests.py --messages 40 --requests 2 --samples 20`：小规模脚本冒烟通过，不作为供应商缓存或真实场景性能结论。

前置定向测试：写入前置条件组 36 passed，路由组 81 passed，上下文组 144 passed。初次受限运行因 uv 缓存/pytest 临时目录权限失败，授权环境复测；写入组初次有效运行 70 passed / 1 failed，失败来自版本选择测试覆盖了真实输入归属，修正 fixture 后通过。联合命令首次误指定不存在的 `tests/application/tasks`，未执行测试，已改用上述实际目录。没有通过放宽产品合同解决测试失败。

独立只读复审未发现新增阻断问题。本轮自建临时目录经路径和归属核验后清理；未修改依赖、锁文件或构建配置，未提交 Git。

## 保留限制

真实模型语义、供应商缓存命中及费用没有重新测量。旧数据迁移和旧摘要读取校验仍需保留，未进行用户数据迁移写入。非助手调用保持原能力；Variant/绑定没有活动目标时仍由普通命令报告缺目标，不将其混同为缺撤销服务后直接执行的旁路。既有 GUI 同步读取和跨域撤销限制不属于本次清理范围。
