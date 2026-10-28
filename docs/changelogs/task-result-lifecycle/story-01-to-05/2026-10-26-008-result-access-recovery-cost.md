# 成果访问、保存与恢复成本补修

- 日期：2026-10-26
- Epic / Story：task-result-lifecycle / S01–S05，第七轮
- 依据：本轮六处独立审查发现、当前实现差异及实际测试；不把工作区其他已有改动归入本轮。
- 计划：[plan](../../../../plans/task-result-lifecycle/plan.md)；契约：[ADR-049](../../../adr/049-task-result-lifecycle.md)。

## 用户可见结果

查询已保存的 AI 成果不再要求原选区仍存在；一个坏下载记录不会挡住其他成果。合法词条元数据不再因字段叫 token、secret 或 headers 被误当凭据。保存、退出和恢复列表减少整份数据扫描；任务状态更新复用固定输入和已保存报告，不因状态变化重复保存整份成果。冲突仍在真正写回时按相关条目处理，真实来源定位变化仍须重新审阅。

## 本轮文件与根因修改

1. 助手访问：`smart_assistant/tool_registry.py` 增加显式只读 target scope，禁止修改工具使用此豁免；`request_scope.py`、`request_execution.py` 将直接和 prepared-step 查询统一接入；`tools/tool_translator.py` 为 get_task_result 声明范围。保留请求许可、目标身份、成果归属和修改工具选区保护。
2. 下载成果：`application/sync/download_results.py` 对每份记录分别解析并保留诊断，原坏记录不删除；`ui/operations/batch_download_recovery.py` 展示健康记录及诊断。`batch_download_results.py`、`batch_download_session.py` 用与权威合入相同的来源定位签名，且只计算候选词条，修复正常审阅被误判过期。测试覆盖 metadata/string_id/fingerprint 真实变化仍拒绝旧决定、保持本地、接受成果及审阅后编辑。
3. 恢复存储：`application/translation/task_recovery.py` 区分业务词条封套与执行配置；新增 `task_recovery_storage.py` 分离不可变输入、小摘要和可变成果，新增 `task_recovery_transaction.py` 串行化跨进程发布/清理。普通 metadata 事务不读取固定输入，无变化不写；成功保存的相同 ledger 复用证据。范围缓存绑定完整输入引用与已验证 EntryKey，更新回执不重复打开原文。短文件名配合完整摘要及任务归属校验，避免新增深路径导致 Windows 工程包往返失败。可变状态只保留当前引用代次。
4. 恢复消费者：`task_result_recovery.py`、`legacy_task_recovery.py` 与 UI 的 `task_recovery_binding.py`、`task_recovery_persistence.py`、`task_recovery_prompt.py`、`task_registry.py` 改用小状态事务和摘要发现；助手 `entry_task_results.py`、`translation_task_results.py`、`translation_task_merge.py`、`_source_import.py`、`translation_task_run.py`、`postprocess_task_run.py` 同步接入。列表先筛目标/状态，实际选择才后台完整加载，交付前复核工程版本。旧内联记录只在读取/更新边界适配，保留原件，不另开执行链。
5. 报告复用：新增 `ui/tools/ai_translator/task_record_persistence.py`，由 `task_record_writer.py` 的串行 worker 使用；`application/translation/task_history.py` 增加绑定 payload 代次的 metadata 更新。同一成果状态变化复用报告，候选或取消语义改变才生成新内容；过期 writer 不能修改别人的新报告，失败仍保留重试内容。
6. 保存一致性：新增 `converter/collection_changes.py`，在 `translation_entry.py` 和 `translation_entry_collection.py` 发布有界变更证据；新增 `ui/projection_consistency.py` 并接入 `ui/project_view_binding.py`。clean 保存不遍历已交付词条；普通修改只检查相关条目；日志溢出回退一次完整集合核验。嵌套可变关联仍检查相关条目。共享词条新增订阅与 setter 使用原子边界，修复独立审查复现的漏通知竞争；保留 Qt 投影交付等待。
7. 关联 QA 修补：`ui/coordinators/project_transfer_coordinator.py` 对已有副本导入确认设置默认 No，没有新增弹窗。迁移 `test_assistant_project_navigation.py`、`test_start_center_guided_project.py`、`test_destructive_confirmation_defaults.py`、`test_workbench_context_layout.py`、`ux/test_current_user_journeys.py`、`test_step2_incremental_rendering.py` 的旧工程/工作区夹具；旧 EET 工具契约测试改用真实权威工程，启动测试注入现行任务偏好服务。历史继续测试等待实际后台加载完成，独立工具测试避免重复注册。

新增专项回归：`test_recovery_storage_cost.py`、`test_recovery_process_transaction.py`、`test_recovery_lazy_loading.py`、`test_task_record_writer_cost.py`、`test_projection_consistency_cost.py`、`test_task_recovery_archive.py`。其他在相应既有测试文件补充回归，不为旧测试保留第二套生产逻辑。

职责复核：存储编码、跨进程事务、报告复用和投影一致性分别提取独立模块。TaskRecoveryStore 所在文件 453 行、类 17 个方法；新存储 172 行、投影一致性 128 行、集合通知 97 行。本轮未向超长入口增加新的业务职责。

## 验证

使用现有 uv 环境，`--no-sync --offline`，任务专用 TEMP/TMP、UV_CACHE_DIR、basetemp；需 Windows 原子替换/硬链接的回归经正常审批执行。以下计数有交集，不相加冒充全仓测试。

- `uv run --no-sync --offline pytest tests/smart_assistant -q`：1097 passed。
- `uv run --no-sync --offline pytest tests/converter tests/contracts/io tests/integration/gui tests/persistence/v2/test_project_archive.py tests/persistence/v2/test_task_recovery_archive.py -q`：442 passed。
- `uv run --no-sync --offline pytest tests/ui --ignore=tests/ui/tools -q`：1802 passed、1 skipped、5 failed；失败均为两处旧测试夹具。迁移后 `test_workbench_context_layout.py`、`ux/test_current_user_journeys.py`、`test_step2_incremental_rendering.py` 三文件 24 passed。
- `uv run --no-sync --offline pytest tests/application/translation tests/application/projects tests/ui/tools/test_ai_history_result_interactions.py tests/ui/tools/test_ai_result_lifecycle.py tests/ui/tools/test_ai_result_publication.py tests/ui/tools/test_ai_result_recovery.py tests/ui/tools/test_ai_task_async_apply.py tests/ui/tools/test_ai_task_history_ui.py tests/ui/tools/test_translation_task_resume.py tests/ui/tools/test_proofread_resume_wiring.py tests/ui/tools/test_task_background_records.py tests/ui/tools/test_task_recovery_binding.py tests/ui/tools/test_recovery_lazy_loading.py tests/ui/tools/test_task_record_writer_cost.py -q`：707 passed、3 failed。两条历史继续测试修正异步等待；另一条候选日志写入发生 WinError 5。随后完整 `test_ai_result_lifecycle.py` + `test_candidate_checkpoint_journal.py` 复验 39 passed。
- 恢复成本、跨进程事务、后台保存、摘要懒加载、报告复用、助手条目成果及包往返联合 85 passed。保存投影关联 152 passed；独立原并发 probe 和正式回归 13 passed。
- 新 Agent 分别复审访问、恢复存储、投影一致性，发现问题已修复并复验；恢复独立 49 passed，下载定位与确认框追加复审 10 passed。
- 助手 UI `test_request_control_binding.py` + `test_request_lifecycle_panel.py` 独立 53 passed；此前失败发生在 setup 的 Session restore/save_history，底层异常未保留，不能断言具体环境原因，没有增加业务重试。
- `uv run --no-sync --offline ruff check src tests`、`uv run --no-sync --offline ruff format --check src tests`：全部通过，2229 files already formatted。
- `uv run --no-sync --offline pytest --collect-only -q`：9589 tests collected。
- `git -c core.safecrlf=false diff --check`：通过。

## 验证限制与兼容

最初大 UI 矩阵在旧编辑夹具触发模态错误框时中断，夹具已修且定向通过；另一组 2348 项大矩阵在 ThemeService.close 的 Qt 全局样式重置处长时间停滞后中断，相关历史界面 7 项在独立核心组通过。未把中断矩阵视为通过，也未执行全部 9589 项、真实 LLM/ParaTranz 或用户桌面验收。测试存在弃用警告和 Windows 文件操作间歇失败，未通过删除校验或增加重试掩盖。

性能证据为读取/写入/构建次数与有界存储回归，不是用户工程端到端测速，不宣称所有路径常数时间。仍保留实际写入权限、成果完整性、来源定位和冲突检查。

旧 `.transbridge` 自动升级范围不变，不支持未打包目录；新旧恢复数据统一在存储入口读取，原数据保留，不创建额外正式版本。未读取用户 `.transbridge/` 数据，未改依赖、未提交或发布。本轮确认的六处问题无未完成修复；任务自有临时文件在记录完成后清理。
