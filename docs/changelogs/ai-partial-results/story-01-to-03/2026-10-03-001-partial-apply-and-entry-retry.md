# 部分成功应用与失败条目重试

日期：2026-10-03。Epic：ai-partial-results；Story：01～03。
计划：[plan](../../../../plans/ai-partial-results/plan.md)。

## 行为

- 正常结束但存在失败时，由用户点击“应用成功结果”，仅将通过全部启用检查、经确认的结果应用到项目。失败条目保留原译文。
- “重试失败条目”只处理逐条执行记录中的失败集合；成功、已应用、预览中未采纳的条目保持原状。重试成功后仍需显式应用。
- 取消丢弃尚未应用的结果，不回滚此前明确应用的结果；已应用结果仍可保存项目。
- 应用和重试前检查项目输入、版本、执行配置、项目术语版本及本地术语文件；重试加载术语时跨阶段核对实际内容。本任务实际生成并落盘的动态术语允许继续使用，外部动态术语文件修改会阻止操作。
- 任务记录保存逐条执行、采纳、应用状态和尝试次数；结果窗口可筛选“已应用”“未采纳”“失败”。不依赖导出 Excel。

## 文件增量

下列为本次任务可归属的符号级改动；同文件中的既有任务生命周期、取消修复、术语并发及结构化输出修改不计入本次增量。

- 新增 `src/transbridge/ai_translator/translation_entry_outcomes.py`：`TranslationEntryOutcome` 与结果构建函数，根据候选接受、实际提交和完整后处理阶段证据确定逐条状态，避免旧译文被误判成功。
- 修改 `src/transbridge/ai_translator/translator.py`：向 `TranslationResult` 增加逐条结果；记录开始/完成集合；后处理仅接收实际翻译成功条目；增加严格目标范围及术语加载、自产术语更新观察接口。
- 新增 `src/transbridge/ai_translator/post_processor/strict_execution_evidence.py`：独立跟踪启用阶段的必需/完成/失败集合，阻止后续规则或模型裁决覆盖前序执行失败。
- 修改 `src/transbridge/ai_translator/post_processor/post_processor.py`：接入阶段完成证据，输出 `processing_statuses` 和原因。
- 修改 `src/transbridge/ai_translator/post_processor/proofread_pipeline.py`：严格校对仅在全部阶段完成且最终通过时提供 `completed`；失败证据进入逐条结果。
- 修改 `src/transbridge/ai_translator/post_processor/base.py`：Issue 增加默认关闭的 `execution_failed` 字段。
- 修改 `src/transbridge/ai_translator/post_processor/quality_gate.py`：调用、解析、缺键、重复或非法裁决带上结构化执行失败标记，避免不确定结果随后被错误放行。
- 修改 `src/transbridge/application/translation/postprocess_stages.py`：CheckerStage 保留执行失败分类；补齐 `_EntryView.id`，让检查器正确关联逐条结果。该文件既有 HTTP 协议调整不属于本次工作。
- 新增 `src/transbridge/ui/tools/ai_translator/task_draft.py`：隔离草稿、保存不可变输入、按键恢复失败输入并保留完整来源上下文。
- 修改 `src/transbridge/ui/tools/ai_translator/task_session.py`：按 EntryKey 原子应用、幂等记录、更新自身版本基线；部分应用后可保存、继续重试及再次应用；每次新应用使用独立保存后快照。
- 新增 `src/transbridge/ui/tools/ai_translator/task_consistency.py`：冻结请求和用户配置指纹，检查术语版本、文件签名和跨阶段内容基线，单独认可本任务实际落盘的动态术语。
- 新增 `src/transbridge/ui/tools/ai_translator/task_entry_results.py`：分开记录执行状态、用户决定和应用状态；仅合并本轮目标，生成逐条历史快照。
- 修改 `src/transbridge/ui/tools/ai_translator/source_execution.py`：传递严格目标范围、术语观察和独立尝试编号；支持账本生成的来源报告快照。
- 修改 `src/transbridge/ui/tools/ai_translator/proofread_composition.py`：术语加载后、模型请求前执行一致性观察。
- 修改 `src/transbridge/ui/tools/ai_translator/task_worker.py`：传递一致性检查器和尝试编号，兼容原有执行器。
- 修改 `src/transbridge/ui/tools/ai_translator/task_runtime.py`：任务启动时捕获配置与术语约束。
- 扩展 `src/transbridge/ui/tools/ai_translator/task_run.py`：只重试失败键、保留成功与未采纳结果；部分结果显式应用；前后校验预览状态；拒绝旧线程晚到信号；允许部分应用后的保存。
- 修改 `src/transbridge/ui/tools/ai_translator/task_progress.py`：显示“应用成功结果”“重试失败条目”，预览只呈现通过检查的待决定条目。
- 扩展 `src/transbridge/ui/tools/ai_translator/task_record_writer.py`：冻结账本后在后台构建历史快照，应用状态以实际 applied_keys 为准。
- 扩展 `src/transbridge/ui/tools/ai_translator/task_history_dialog.py`：显示部分应用状态，避免全拒绝任务被标为已应用。
- 扩展 `src/transbridge/ui/tools/ai_translator/task_result_dialog.py`：新增“已应用”、统一“未采纳”结果标签及筛选。
- 新增 `tests/ai_translator/test_translation_entry_outcomes.py`：空结果、旧译文、未变文本、后处理证据、取消和跨来源键回归。
- 修改 `tests/ai_translator/test_translator_term_conflicts.py`：严格目标范围与动态术语写入观察回归。
- 新增 `tests/ai_translator/post_processor/test_strict_execution_evidence.py`：阶段缺键、异常、零置信度和质量检查失败不能被后续裁决覆盖。
- 扩展 `tests/ui/tools/test_ai_task_session.py`：部分提交、保存代次、取消后保留已应用、输入变更及新旧项目持久化回归。
- 新增 `tests/ui/tools/test_ai_partial_task.py`：真实 TaskSession 与 Qt 窗口集成，覆盖选择性应用、失败重试、未采纳、取消、晚到信号及版本约束。
- 新增 `tests/ui/tools/test_ai_task_consistency.py`：冻结配置、术语版本、跨阶段内容、自产动态术语和外部文件修改回归。
- 更新 `tests/ui/tools/test_unified_task_progress.py`、`tests/ui/tools/test_mixed_completion_integrity.py`：使用明确逐条证据，验证显式应用与预览语义。
- 扩展 `tests/ui/tools/test_ai_task_history_ui.py`：任务完成但没有已应用条目时，历史记录仍为未应用。
- 新增 `plans/ai-partial-results/plan.md`；更新 `plans/ai-task-lifecycle-history/plan.md` 的阶段边界，以及 `plans/INDEX.md`、`docs/changelogs/INDEX.md` 的对应索引。

## 验证

以下命令均退出 0：

```powershell
uv run pytest tests/ai_translator tests/application/translation tests/contracts/translation tests/infra/test_limited_llm_client.py tests/integration/translation/test_http_postprocess_chain.py -q --disable-warnings
# 566 passed

$aiTaskTests = @(rg --files tests/ui/tools | Where-Object { $_ -match '(ai_|unified_|polish|report|mixed_completion|proofread_failure_history)' })
uv run pytest @aiTaskTests tests/ui/test_ai_task_shutdown.py tests/ui/test_background_gui_operations.py tests/ui/ux/test_current_user_journeys.py -q --disable-warnings
# 318 passed

uv run ruff check src tests
uv run ruff format --check src tests
git -c core.safecrlf=false diff --check
```

独立 QA 发现并已修复：严格阶段失败被后续通过掩盖，以及同来源不同阶段采用独立术语基线。复核通过。最终两组相关回归共 884 项通过；警告为既有兼容接口弃用及第三方 SWIG 警告。

## 兼容性、责任边界与未运行项

- 记录增加可选条目字段，旧历史继续只读，无批量迁移；重启后可查看记录，不从历史记录恢复执行。
- 没有版本接口的远端术语在各次实际加载时比较内容；应用操作不会主动联网重新拉取远端术语。项目术语版本和本地文件在应用前重新验证。
- 大型既有 translator/post_processor 模块仅增加流程接线，状态判定分别抽为独立模块；未扩大其职责。后续若继续扩展旧执行阶段，应先拆出阶段编排，避免进一步增大主体。
- 未运行真实 LLM、真实远端服务和安装包构建；测试使用本地假服务与 Qt 离屏环境。未提交或推送 Git。
- 功能实现无遗留项；上述真实服务及发行验证不在本次执行范围。
