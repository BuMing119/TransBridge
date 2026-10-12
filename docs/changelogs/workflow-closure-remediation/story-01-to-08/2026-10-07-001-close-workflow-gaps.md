# 关闭取消、版本事务、助手重试与 FOMOD 流程缺口

- 日期：2026-10-07。
- Epic / Story：workflow-closure-remediation，S01～S08。
- 计划：[跨模块流程闭环整改](../../../../plans/workflow-closure-remediation/plan.md)。
- 授权：用户在全项目审查和逐项处理方案后调用 `bm-pilot`，执行本地实现、验证及增量记录；未执行 Git commit/push。
- 结果：8 项原始问题已修复；最终组合回归 211 passed、1 skipped，Ruff 全库检查通过。全库常规回归暴露的 3 条旧测试合同也已更新并复验，无未解决失败。

## 归属与兼容

工作区原有 purpose-aware-data-projection 等未提交变更保留。本记录仅涵盖本轮可归属的增量：对有交集的 handler/base/retry executor 曾保存并比较实施前副本；`retry_executor.py` 本轮未改。`tools/types.py` 仅新增内部步骤管理字段，既有业务数据投射改动不计入本批。文档索引仅增加本批条目，不改其他工作线状态。

公开翻译 key/id、文件格式和已有译文保持兼容。请求步骤新增字段可读取缺省字段的旧记录；旧批次缺少只读或副作用证据时保留待核对状态。新增 activation journal 有独立 schema、摘要和参与记录校验；存在未完成新 journal 时，须由理解该协议的版本恢复后再考虑降级。

FOMOD 的运行期装配异常现在通过 JobRef 的 failed 状态与诊断交付；注册前输入校验仍直接拒绝。项目与会话共享 root OS 写租约，会明确拒绝并发写入；会话原有 `SESSION_WRITER_BUSY` 错误码保留。

## S01 / S06：AI 最终提交与连接生命周期

- 修改 `src/transbridge/ui/tools/ai_translator/task_adapter.py`：增加整个 run 的取消绑定、worker 解绑和 `commit_mutation`，复用真实 `commit_permit/try_commit`；终态释放订阅。
- 新增 `src/transbridge/ui/tools/ai_translator/task_runtime_binding.py`：负责 runtime 与 AI run 接线，取消经 Qt queued signal 回到 GUI 线程，提交后同步运行时状态。
- 修改 `src/transbridge/ui/tools/ai_translator/task_run.py`：待预览、异步合并和失败重试阶段保持控制有效；暂停可恢复，取消等待使用者退出；远端 client 在完整任务终结后幂等关闭。
- 修改 `src/transbridge/ui/tools/ai_translator/task_session.py`：唯一正式提交入口传递可选 mutation guard，保留分批应用和新 generation 的持久化对象选择。
- 修改 `src/transbridge/ui/tools/ai_translator/task_completion.py`：先记下成功提交的条目，再同步迟到取消；已应用结果、账本和历史不被取消撤回。
- 修改 `src/transbridge/ui/version_persistence.py`：V2 facade 与 legacy 正式 mutation 都接受可选提交回调；未传回调的旧调用方保持兼容。
- 新增 `tests/ui/tools/test_translation_workflow_closure_audit.py`：审查时的取消、预览重试和连接生命周期复现，6 项转绿。
- 新增 `tests/ui/tools/test_ai_runtime_commit_boundary.py`：10 项真实 runtime/V2 边界，覆盖提交与取消两种顺序、失效 permit、暂停恢复、异常、跨线程通知和连续重试关闭。
- 修改 `tests/ui/tools/test_ai_task_session.py`：测试替身支持真实提交回调，保留既有业务断言。

项目锁先于 runtime 锁取得，runtime 锁只包围权威内存 mutation 与 generation 更新；投影通知在其外部。网络、合并准备和落盘不进入 runtime 提交锁。

## S02 / S03：版本目录的条件发布与恢复

- 新增 `src/transbridge/application/projects/catalog_commands.py`：创建、复制、删除只准备 detached candidate，目录写入统一交给 lifecycle activation；预检同时核对活动记录的 revision 和内容。
- 修改 `src/transbridge/application/projects/gui_facade.py`：移除提前保存 Variant/Project 的旁路，委托目录命令；向正式词条提交边界传递可选 AI mutation guard。
- 修改 `src/transbridge/application/projects/lifecycle.py`：目录候选接入现有 prepared token/activation；正式 mutation 的 guard 与事件通知分开。
- 修改 `src/transbridge/application/projects/models.py`：候选保存目标 Project/Variant 的预期 revision、摘要和删除参与项，避免以旧活动版本代替目标前置条件。
- 修改 `src/transbridge/persistence/project_lifecycle_loader.py`：捕获候选来源摘要和 Variant 源版本；删除无调用的旧加载 helper。
- 新增 `src/transbridge/persistence/v2/project_activation_journal.py`：记录精确前像、目标内容、摘要和 prepared/committed 状态；发布 Project、Variant、活动指针；恢复前先核验所有参与者，未知内容冲突时保留日志并阻止后续写。
- 新增 `src/transbridge/persistence/v2/project_record_guard.py`：普通保存和 activation 共享记录版本/摘要及前像验证原语。
- 新增 `src/transbridge/persistence/v2/project_write_lock.py`：同 root 共享、线程可重入的 OS 写租约与恢复边界；保留 `acquire/release`，覆盖读取后嵌套写，以及恢复异常被捕获后的再次写入保护。
- 修改 `src/transbridge/persistence/v2/session_write_lease.py`：从现有 Session 租约提取共用 OS 文件租约能力。
- 修改 `src/transbridge/persistence/v2/repository.py`：Project/Variant 读写、迁移及条件保存接入共享边界；保留 Session 锁顺序与错误码。
- 修改 `src/transbridge/persistence/v2/project_save_journal.py`：复用记录校验原语，将普通保存恢复注册到共享写边界。
- 修改 `src/transbridge/persistence/source_update_store.py`：将来源更新日志恢复注册到同一边界，避免后续正式写绕过未完成事务。
- 修改 `src/transbridge/persistence/v2/lifecycle_transactions.py`：activation 委托新协议，baseline 注册/移除在 committed 标记前参与；提交失败恢复其精确内存前像。
- 修改 `src/transbridge/persistence/v2/baselines.py`：提供事务所需的注册前像捕获和恢复。
- 修改 `src/transbridge/bootstrap/persistence.py`：启动先恢复 Project 发布协议，再读取和修复 catalog。
- 修改 `src/transbridge/persistence/project_catalog_repair.py`：catalog 修复复用 root 排他边界。
- 新增 `tests/persistence/v2/test_workflow_closure_audit.py`：5 项原始审查场景转绿，故障注入随 durable 发布合同调整。
- 新增 `tests/persistence/v2/test_project_activation_transactions.py`：44 项场景，覆盖参与记录发布前后故障、模拟进程中断、重复恢复、staging/readback、baseline 回滚、清理失败、回滚失败、同 revision 不同内容、损坏日志、活动/非活动版本删除、原不存在指针、真实子进程互斥和嵌套恢复。
- 修改 `tests/persistence/v2/test_source_update_store.py`：第三方篡改使用绕过协议的 `fs.seed`；正式 repository 写如今会先恢复日志，不能继续用它模拟外部违规写。

committed 标记前恢复旧状态，之后恢复新状态。提交后的日志清理失败保留业务成功并记录警告，后续写在恢复清理成功前被阻止。baseline 属于进程内投影，重启从来源描述重新构建。

职责审查：GUI facade 从 544 行降到 410 行，目录职责完整提取；新 activation 协议约 345 行、root lock 91 行，普通保存协议缩到 684 行。既有 lifecycle 为 1072 行，本轮仅增加边界薄接线；为避免暴露锁或复制终态/事件规则未做整类拆分。其职责集中风险保留，下次新增生命周期操作前须先提取 transition 状态机。repository 约 595 行，协议与锁主体均已抽离，本轮只接入现有读写责任。

## S04 / S05：逻辑步骤、attempt 与最终回执

- 新增 `src/transbridge/application/assistant_requests/step_execution.py`：稳定逻辑步骤、最多 4 次只读 attempt、一次最终回执、未启动步骤中断、回执持久化失败保留与重交，以及保守恢复规则。
- 新增 `src/transbridge/smart_assistant/step_execution.py`：将共同护栏和既有 retry executor 接入逻辑步骤；批次接纳失败也结算剩余未启动步骤。
- 修改 `src/transbridge/application/assistant_requests/models.py`：增加 `StepAttempt`、`StepReceipt`、持久化权限证据和兼容缺省字段的读取。
- 修改 `src/transbridge/application/assistant_requests/dispatch.py`：所有步骤完成后仍等待真实 effect/job 终态，再派生 dispatch 状态。
- 修改 `src/transbridge/application/assistant_requests/recovery.py`：保留活跃 job；仅有明确只读证据且无未决副作用的中断批次可自动关闭，旧记录不假造成功。
- 修改 `src/transbridge/application/assistant_requests/scheduler.py`：释放许可支持非阻塞尝试，保留原默认阻塞合同。
- 修改 `src/transbridge/smart_assistant/request_execution.py`：程序签发稳定 step handle，每次 attempt 重新验证 turn、权限和 scope；回执补记失败阻止新业务。
- 修改 `src/transbridge/smart_assistant/tool_execution_handler.py`：前置拒绝、未知/不可用工具、后台批次取消和 retry 均进入统一步骤协调。
- 修改 `src/transbridge/smart_assistant/tools/base.py`：共同工具边界接入步骤管理，护栏拒绝也有收尾。
- 修改 `src/transbridge/smart_assistant/tools/types.py`：仅新增内部 `assistant_step_managed` 字段，防止嵌套重复管理；模型参数不能签发身份。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_binding.py`：释放轮次时结算未启动步骤；非阻塞释放许可，忙锁时后台完成；回执写盘失败在 GUI 撤销卡片权限、显示诊断并清空等待队列。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_confirmation_binding.py`：保留忙锁期间的点击，许可释放事件后重新验证交付。
- 修改 `src/transbridge/ui/tools/smart_assistant/confirmation_view.py`：支持延迟确认交付，保留 generation/卡片失效检查。
- 新增 `tests/smart_assistant/test_workflow_closure_audit.py`：从 3 项审查复现扩到 28 项，覆盖重复步骤、参数修正、重试耗尽、拒绝、取消、旧 lease、scope、写工具不 retry、补交回执、两步批次及历史恢复。
- 修改 `tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py`：确定性覆盖后台释放前立即忽略、真实忙锁点击、忽略/批准与回执写盘失败组合，验证 GUI 不阻塞且无未授权业务调用。

回执补交只保存结果，不重新执行工具。用户停止不撤回已成功提交的效果；返回 JobRef 仅代表提交，实际完成仍由 TaskRuntime 事件驱动。

## S07 / S08：FOMOD 复用身份与任务机制

- 修改 `src/transbridge/fomod/pipeline.py`：以完整 `EntryKey` 筛选，再复用 `ai_entry_key()` 传给 AI，移除裸 key 反查旁路；保持 StagePolicy 与 TM/迁移优先级。
- 修改 `src/transbridge/application/fomod/runtime.py`：工作目录、spec 和 engine 装配移入 `schedule` workload，装配前后检查取消；失败由 runtime 产生终态，清理本 run 创建的空目录；有界保存结果报告，避免保留 engine 引用。
- 新增 `tests/application/fomod/test_workflow_closure_audit.py`：真实插件重复 key/不同原文的离线 AI 写回、普通 key 对照，以及 namespace、原文空白、选择范围和 hidden/locked 筛选。
- 修改 `tests/application/fomod/test_operation_task_entrypoint.py`：覆盖目录/spec/engine/backend.start 故障，装配前和装配中取消，单次终态及无输出发布。

## 更新过期测试合同

- 修改 `tests/quality/test_success_chains.py`：受控 HTTP 夹具从旧请求/响应结构迁到既有 Responses 结构化输出合同，验证路径、schema、store 和业务结果。
- 修改 `tests/integration/publish/test_atomic_publish.py`：旧失败断言落后于已提交 `a7d81ae` 的插件身份修复；改为真实插件成功往返和完整保真断言。另构造真实 EET locator 冲突，产生 PARTIAL 解析，保留失败关闭、旧目标和源文件字节不变的检查。未改生产发布逻辑。
- 修改 `tests/contracts/projects/test_authoritative_mutation_paths.py`：旧源码合同仍引用 `mark_completed` 和 `self._persistence.commit_translation`，HEAD 已采用 `_commit_entries` 与按 generation 选择的 persistence；更新精确白名单及新 mutation guard 传递断言，不扩大可直接写投影的位置集合。
- 新增并更新 `plans/workflow-closure-remediation/plan.md`：保存 8 项验收、实际实施决策和规模例外；只为本批更新 `plans/INDEX.md`、`docs/changelogs/INDEX.md`、`docs/INDEX.md`。

## 验证证据

审查起点：4 个新增复现文件共 17 项，13 failed、4 passed。以下命令均在仓库根目录、现有 uv/Python 3.12 环境执行；结果不能相加作为去重测试总数。

### 全项目常规离线回归

```powershell
uv run pytest tests -q -m "not llm and not integration and not slow" --ignore=tests/performance -p no:cacheprovider --basetemp .tmp-pilot-closure-full
```

结果：5535 passed、7 skipped、22 deselected、3 failed，退出码 1，406.08 秒。3 个失败为上述过期插件发布断言及 2 个 AI 权威写入源码合同。它们已修正，全部纳入下一组最终复验。未把这次命令描述为全绿，也未在最后局部修正后重复执行整个集合。

### 最终代码组合复验

```powershell
uv run pytest tests/application/fomod tests/persistence/v2/test_project_activation_transactions.py tests/persistence/v2/test_workflow_closure_audit.py tests/smart_assistant/test_workflow_closure_audit.py tests/ui/tools/test_ai_runtime_commit_boundary.py tests/ui/tools/test_translation_workflow_closure_audit.py tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py tests/ui/tools/smart_assistant/test_confirmation_lifecycle.py tests/integration/publish/test_atomic_publish.py tests/quality/test_success_chains.py tests/contracts/projects/test_authoritative_mutation_paths.py -q -ra -p no:cacheprovider --basetemp .tmp-pilot-closure-final --disable-warnings
```

结果：211 passed、1 skipped、3 warnings，退出码 0，116.98 秒。跳过项为真实符号链接创建，Windows 返回 WinError 1314；junction 与实际子进程写租约场景有执行。包含最后的忙锁确认/回执故障组合和全部 3 条过期合同修正。

### 相关分组回归

```powershell
uv run --cache-dir .tmp-workflow-audit-uv pytest tests/persistence tests/application/projects tests/application/sessions tests/ui/test_v2_variant_hydration.py tests/ui/test_parse_hydration_authority.py tests/ui/test_legacy_migration_safety.py tests/ui/test_workbench_translation_import.py -q -p no:cacheprovider --basetemp .tmp-pilot-closure-project
```

636 passed，退出码 0。删除无调用 loader helper 后，其对应加载测试另有 4 passed。

```powershell
uv run --cache-dir .tmp-pilot-closure-assistant-uv pytest tests/smart_assistant tests/application/assistant_requests tests/ui/tools/smart_assistant -m 'not llm and not integration' -q -p no:cacheprovider --basetemp .tmp-pilot-closure-assistant
```

1285 passed，退出码 0；这是最后几项确认/回执边界补强前的广回归。补强后的 103 项、8 项局部复验及最终组合均通过。中途确认忽略竞态和同步释放阻塞 UI 的失败已修复，未以增加固定等待或削弱断言处理。

```powershell
uv run --cache-dir .tmp-workflow-audit-uv pytest tests/ui/tools/test_ai_runtime_commit_boundary.py tests/ui/tools/test_translation_workflow_closure_audit.py tests/ui/tools/test_unified_task_progress.py tests/ui/tools/test_ai_task_async_apply.py tests/ui/tools/test_task_completion_flow.py tests/ui/test_ai_task_shutdown.py tests/ui/tools/test_ai_task_session.py tests/ui/tools/test_unified_task_runtime.py tests/ui/tools/test_ai_task_consistency.py tests/ui/tools/test_proofread_resume_wiring.py tests/ui/tools/test_legacy_ai_worker_lifecycle.py tests/ui/tools/test_ai_translator_story08.py tests/application/projects/test_gui_facade_ai_snapshots.py tests/contracts/test_task_runtime.py tests/contracts/test_task_runtime_backends.py -q -p no:cacheprovider --basetemp .tmp-pilot-closure-ai --disable-warnings
```

207 passed，退出码 0；终态订阅释放最后调整后，相关 60 项复验通过，最终组合再次覆盖新边界。

```powershell
uv run pytest tests/application/fomod tests/test_fomod_pipeline.py tests/test_fomod_typed_pipeline.py tests/test_fomod_staging_publish.py tests/test_fomod_tm_provenance.py tests/test_fomod_xml_resource_fidelity.py tests/ui/test_fomod_journey.py tests/ui/operations/test_production_facade.py tests/contracts/io/test_original_match_migration.py tests/contracts/io/test_localized_strings_adapter.py -q -p no:cacheprovider --basetemp .tmp-pilot-closure-fomod-existing
uv run pytest tests/quality/test_success_chains.py tests/application/translation tests/ai_translator/post_processor -q -p no:cacheprovider --basetemp .tmp-pilot-closure-translation-regression
```

依次为 128 passed、387 passed，退出码均为 0。

```powershell
uv run --cache-dir .tmp-workflow-audit-uv pytest tests/integration/publish tests/contracts/io/test_legacy_format_adapters.py tests/contracts/io/test_hydrated_write_operation.py tests/contracts/io/test_localized_hydrated_publication.py tests/parser/test_original_match_plugin.py tests/parser/test_plugin_parser.py tests/parser/test_plugin_parser_integration.py tests/writer/test_plugin_writer.py -q -p no:cacheprovider --basetemp .tmp-pilot-closure-publish-regression-related --disable-warnings
```

91 passed、1 skipped，退出码 0；旧插件合同修正后执行。

### 静态检查与清理

```powershell
uv run ruff check src tests
uv run ruff format --check src tests
git diff --check
```

全部通过；最终 Ruff 格式检查覆盖 1563 个文件。Git 使用仓库正常换行配置核验，不修改用户换行设置。所有本轮 `.tmp-pilot-closure-*` 和重新使用的 `.tmp-workflow-audit-uv` 已清理；先核对绝对路径和归属，并单独移除测试创建的两条 junction 本身，未遍历其目标。保留用户原有文件和未提交改动。

## 遗留与未运行项

本批 8 项缺陷无未完成项。未执行真实 LLM/ParaTranz 写入、正式性能基准、重新构建安装包或人工桌面验收；全库常规回归排除了 `llm/integration/slow` 标记及 `tests/performance`，相关分组另有离线集成和慢测试覆盖。Windows 文件租约有真实子进程证据，POSIX `flock` 本机未执行。任意外部程序绕过锁改文件时，采取摘要冲突停写与保留恢复材料，不承诺自动合并未知内容。
