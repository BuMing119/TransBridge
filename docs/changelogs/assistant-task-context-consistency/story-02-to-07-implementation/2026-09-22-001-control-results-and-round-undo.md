# 任务结果一致性、取消收尾与可选本轮撤销

- 日期：2026-09-22。
- Epic：assistant-task-context-consistency；Story：S02～S07。
- 计划：[综合计划](../../../../plans/assistant-task-context-consistency/plan.md)。
- 继承：[决策材料第一版](../../assistant-context-compaction/story-11-to-13-decision-context/2026-09-22-001-decision-context.md)。
- 用户授权：bm-pilot 本地规划、实现、验证；保留完成后的模型解释调用；取消默认保留，用户另点撤销；扩大本地写入撤销，远端无安全逆操作仍允许执行并说明范围。

## 行为

控制查询、回答和路由在应用后台提交唯一持久工具回执，GUI 接收已保存事实。取消先停推进并保留正式提交，临时投影仍清理；清理失败不再被 cancelled 掩盖。停止收尾后提供可选撤销，点击时重新预检，部分范围需要用户确认。撤销不恢复 DAG checkpoint，不重放工具或倒退请求修订。

既有条目译文/阶段/external_refs、标签及标签库、明确配置字段、授权打包/写回文件、本地 ParaTranz 项目绑定已有实际接线。新增/删除条目、来源结构、解包目录、词典整库、术语联合状态和远端效果没有安全自动逆操作，执行前及撤销范围内明确显示。

## 文件与符号级改动

### 取消与投影

- 修改 `src/transbridge/application/tasks/controls.py`、`__init__.py`：定义及导出 TaskCleanupFailed。
- 修改 `src/transbridge/application/tasks/runtime.py`：只对显式清理失败允许 cancelling 转 failed，保留诊断；普通取消异常不改为失败。
- 新增 `src/transbridge/smart_assistant/tools/projection_restore.py`：核对原集合/版本，用最新权威条目恢复临时投影；独立撤掉本次清理回调的 gate，不重新授权业务写入。
- 修改 `tools/types.py`、`tool_translator.py`：薄接线及清理失败原始原因传播；不以旧初始值填补缺失权威状态。
- 新增 `tests/smart_assistant/test_projection_restore.py`；修改 `test_runtime_worker_controls.py`、`test_authoritative_entry_commit.py`：真实 worker、版本切换、缺失权威及清理失败回归。

### 结果提交与后台接线

- 新增 `application/assistant_requests/control_results.py`、`control_operations.py`：持久父消息/请求/租约校验、回执幂等、取消终结与重开核对。
- 新增 `application/assistant_requests/routing_results.py`；修改 `routing_commit.py`：提取纯路由变更，使路由状态、直接回答和工具回执同事务提交；完整捕获历史保留前一请求收尾。
- 修改 `scheduler.py`：窄 serialized 接口；修改 `transactions.py`：支持凭证附件 pinning，并区分业务拒绝与事务实际失败原因。
- 修改 `service.py`：artifact_refs、related_change 的薄事务接口，效果准备和轮次登记同事务；已撤销轮次无新输入不能 resume。
- 修改 `smart_assistant/conversation_manager.py`：预留应用拥有的控制调用，原回执投影及已保存历史合并，避免竞争取消/孤立工具结果。
- 新增 `ui/tools/smart_assistant/request_background.py`、`request_control_binding.py`、`request_turn_selection.py`、`request_routing_binding.py`；修改 `request_binding.py`、`request_answer_binding.py`、`request_history_binding.py`、`request_state_binding.py`、`request_management_binding.py`、`request_view_refresh.py`：查询、回答、路由及相交入口后台化，generation/Event 隔离迟到续跑，关闭保留 drain。
- 删除 `application/assistant_context/state_queries.py` 中已被后台提交合同替代的 GUI 页重验入口；相应查询测试继续验证分页 digest。
- 新增/更新控制结果、routing_results、回执投影、会话合并、Qt 生命周期/历史/状态/列表/timeline 测试。慢查询和慢撤销持锁时实测 Qt 心跳，不以“在线程里”代替响应验证。

### 轮次与领域逆操作

- 修改 `assistant_requests/models.py`、`routing.py`、`reducer.py`、`management.py`、`smart_assistant/request_execution.py`：work_round_id 使用真实输入身份；AMEND/RESUME 同步追加 source_message_ids；禁止模型参数注入；旧空归属不补造；关闭轮次许可不可重新使用。
- 新增 `assistant_requests/round_undo.py`：不可变前像/后像凭证、pending/unresolved/undoing 状态、全域预检、逐域进度、单调 undo_sequence、完成重复调用不重放。
- 新增 `application/projects/assistant_undo.py`；修改 `lifecycle.py`：锁内活动 Variant 快照；局部逆 changeset 与 revision 检查，标签库使用更严格全局修订检查。新增/删除条目不假装覆盖。
- 新增 `application/projects/assistant_binding_undo.py`、`assistant_requests/binding_undo_capture.py`、`tools/binding_undo_capture.py`；修改 `tool_paratranz.py`：只撤回本地 Project 绑定，以正式 CAS 保存，不调用远端恢复。
- 新增 `assistant_requests/config_undo.py`、`config_undo_capture.py`、`tools/config_undo_capture.py`；修改 `config/repository.py` 及 `tool_translator.py`：非敏感字段稀疏写入、expected_revision；不回写工作流加载覆盖值和凭证。旧空 endpoint 不满足当前合同则预检拒绝。
- 新增 `application/io/file_undo.py`、`assistant_requests/file_undo_capture.py`、`tools/file_undo_capture.py`；修改 `tool_archive.py`、`tool_writer.py`：实际授权目标捕获、文件存在性/摘要/身份检查、明确 partial 结果；可信私有备份由 bootstrap 配置。重复覆盖同一文件暂拒绝合并。
- 新增 `tools/undo_capture.py`；修改 `tools/types.py`、`_project_tool_mutations.py`：仅包正式 Variant command；不包整个 GUI 队列或任意 gate mutation。
- 修改 `bootstrap/persistence.py`：共享 RoundUndoService 与文件备份根。
- 新增 `assistant_requests/undo_capabilities.py`、`smart_assistant/undo_notices.py`；修改 `session_controller.py`：在实际步骤分发前显示写入撤销限制，正式 read 工具不提示。
- 新增 `ui/tools/smart_assistant/request_undo_binding.py`；修改 `request_list_view.py` 及管理接线：停止后提示与显式撤销，点击才确认部分范围，后台结果合入当前已保存历史。
- 修改 `application/assistant_context/state_projection.py`：仅提供当前请求最近一次恢复事实，按单调序号而非字典顺序选取；完整凭证不发送模型。
- 新增 `test_round_undo.py`、`test_round_undo_domains.py`、`test_config_undo.py`、`test_file_undo_capture.py`、`test_work_round_provenance.py`、`test_assistant_undo.py`、`test_assistant_binding_undo.py`、`test_file_undo.py`、`test_undo_capture.py`、`test_undo_effect_registration.py`、`test_binding_undo_capture.py`、`test_undo_notices.py`、`test_request_undo_binding.py` 等对应领域测试。

### 文档与既有工作边界

- 新增 ADR-043、综合 plan 与三个复杂 Story 文档；更新 requirements FR30.23～26、ADR-041、旧压缩 plan S12 及直接相关索引。
- `synthetic_captures.json` 仅在本增量刷新新增 work_round_id 后的合成输入摘要；没有采集或伪造真实模型结果。
- 既有聊天/工作路由改动、其需求/ADR-040、语料场景及独立增量保留，不将这些先前改动归为本次实现；混合文件仅以上符号变更计入本记录。未修改锁文件、构建配置，未提交 Git。

## 验证

使用仓库既有 uv 环境；`--offline --no-sync --no-cache`，不安装依赖、不调用真实远端。沙箱对 pytest 自建目录拒绝读取后，同组测试经工具审批在沙箱外运行。

```powershell
uv run --offline --no-sync --no-cache python -B -m pytest tests/application/assistant_context tests/application/assistant_requests tests/application/projects tests/smart_assistant tests/ui/tools/smart_assistant tests/infra/test_assistant_prompt_cache.py tests/application/test_file_undo.py tests/contracts/config/test_unified_repository.py -q -p no:cacheprovider --basetemp .tmp-task-context-final-qa
uv run --offline --no-sync --no-cache ruff check src tests
uv run --offline --no-sync --no-cache ruff format --check src tests
git diff --check
```

- 最终联合回归：1434 passed，1 skipped，65 warnings；42.53 秒。跳过项为 Windows 环境无法创建符号链接的测试，警告为现有弃用接口/SWIG 提示。
- 全仓 Ruff 检查通过，1406 文件格式检查通过；正常仓库配置下 diff 检查通过。
- 首轮联合回归曾 3 failed / 1426 passed / 1 skipped；实际修复路由历史缺收尾、事务失败分类，补全 timeline 测试夹具。随后 87 项聚焦全部通过，再运行上述最终联合回归。
- 本轮各子集结果有重叠，不累加为独立测试总数。多轮顺序、取消后零写、全域预检、部分恢复、最终回执失败、重复调用与恢复后模型事实均有离线回归。

## 真实遗留项

- 真实模型语义、缓存命中/费用、固定硬件多样本 P95 未测试，仍归原 S09。
- 完整原摘要链/原始选择材料仍可能超窗；不保证总上下文永久有界。本地凭证/备份积累需后续保留期限与清理合同。
- 文件共享锁不能隔离外部编辑器；跨领域不是原子事务，未完成恢复必须核对，不自动重试。
- 尚无安全逆操作的范围按前述保留并提示，不声称“整个系统恢复到旧快照”。
- 旧 prepare_model_input、_still_admitted 和部分 clarify 仍有 GUI 权威读取；本次相交控制入口已验证，不宣称所有同步 I/O 已消除。
- 无新跨进程自动 DAG 恢复；旧版本降级不保证识别新增撤销事实。
