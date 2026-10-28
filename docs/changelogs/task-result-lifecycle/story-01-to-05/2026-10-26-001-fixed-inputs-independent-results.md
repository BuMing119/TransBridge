# 固定输入、独立成果、局部合入与保存成本

- 日期：2026-10-26。
- Epic：task-result-lifecycle；Story：S01～S05。
- 计划：[实施计划](../../../../plans/task-result-lifecycle/plan.md)；架构：[ADR-049](../../../adr/049-task-result-lifecycle.md)；验证：[QA 报告](../../../test-reports/task-result-lifecycle-qa-2026-10-26.md)。
- 基线：`2058215e309f35b377737687616a3569d222ce24`。记录仅归属本任务；用户已有 `pyproject.toml` / `uv.lock` 的 0.2.1.8 版本修改及 `.transbridge/` 数据排除。

## 用户可见变化

上传和 AI 在启动后使用固定输入，后续编辑或修改设置不再使整个任务失效。成功成果独立保留；合入只处理涉及词条的变化。设置提供 AI、下载各自的冲突偏好，默认保留当前内容；采用候选或暂缓也可选择。无冲突项直接处理，原文/上下文变化或删除的条目保持待处理。

AI 可从结果窗口处理保存的候选并恢复实际覆盖前内容。下载可重开或另存已有成果，包括来源内后续文件失败前下载成功的部分；查看和另存不要求重新访问云端。任务的部分应用、重试和普通保存不再反复创建整项目正式历史。退出可返回工作台，再次退出不受第一次迟到回调影响。

## S01：公共合入与设置

以下路径相对 `src/transbridge/`：

- 新增 `application/translation/result_merge.py`：纯三方比较、关联的 translation/stage、结构保护、三种偏好、显式审阅快照和逐条回执；不依赖 Qt 或存储。
- 新增 `application/projects/result_merge.py`：读取最新权威目标，复用稀疏命令及 CAS，有界重试；提交前证据保护；授权未执行不能报成功；失败原回执透传保留计数及真实原因。
- 新增 `application/projects/result_sources.py`：只读取成果目标所属来源；安装基线身份保有强引用，避免对象 ID 重用或全量基线 hash；无法读取的来源局部待处理。
- 修改 `application/projects/gui_facade.py`：委派公共合入服务，暴露成果目录及仅绑定目标身份的普通保存选项。
- 新增 `config/task_results.py`：通过现有 ConfigRepository 的独立 section 保存 AI/下载偏好；未知值保守回落。
- 新增 `ui/settings/task_results_page.py`：设置加载、修改与保存；取消不写盘，加载/保存错误明确展示。
- 修改 `ui/settings/sections.py`、`ui/settings/center_layout.py`、`ui/settings_dialog.py`：注册“结果与冲突”页并接入保存流程。
- 修改 `bootstrap/composition.py`、`ui/app.py`、`ui/context.py`、`ui/shell/tool_windows.py`：注入同一偏好仓库和启动时读取入口，不扩大 LLMConfig 的职责。

## S02：AI 固定输入与成果生命周期

以下 UI 路径相对 `src/transbridge/ui/tools/ai_translator/`：

- 修改 `task_consistency.py`：长期设置变化不再否定已经固定的执行规格。
- 修改 `task_session.py`、`task_session_preparation.py`：固定捕获后只保持目标身份边界，应用选中候选，不反复捕获全项目；按实际已应用值识别保存事实。
- 新增 `task_session_merge.py`：冻结稀疏候选、后台公共合入、实际回执接收；并发编辑后不以旧全局版本拒绝整个结果。
- 修改 `task_apply_preparation.py`：删除无调用的整集合深拷贝、全量重建及后置 rebaseline，保留 Qt 交付职责。
- 修改 `task_draft.py`、`task_entry_results.py`：保留固定输入与逐条结果，区分应用、保留及待处理；成功但有疑问的结果可保留。
- 修改 `task_result_flow.py`、`task_completion.py`、`task_run.py`：先交付结果账本，再做耐久保存和应用；失败可重试保存；真实成功写入不被后置全局检查抹成失败。
- 修改 `task_recovery_binding.py`、`task_recovery_persistence.py`：结果与决定独立落盘，复用同份载荷。
- 新增 `task_recovery_inputs.py`；修改 `task_result_recovery.py`、`task_source_selection.py`：恢复任务保存的所选范围和 entry 封套；当前工作台编辑不替换原输入；损坏身份、重复范围仍拒绝，结构变化交局部合入处理。
- 修改 `task_recovery_prompt.py`、`task_registry.py`：终结任务成果可访问，恢复启动保留互斥，持久化错误参与退出保护；显式接续转交已经停止且写入完成的旧执行所有权。
- 新增 `task_merge_review.py`、`task_merge_dialog.py`、`task_legacy_review.py`：持久成果审阅与选项应用，恢复实际覆盖前译文；旧工程的证据 I/O 在后台完成。
- 修改 `task_progress.py`、`task_history_dialog.py`、`task_result_dialog.py`：从正在显示的结果及历史记录进入已保存成果处理，不只提供不可达的后台服务。

其他生产路径相对 `src/transbridge/`：

- 新增 `application/translation/task_merge_evidence.py`：局部证据内容寻址复用，已有文件验证，提交尝试与实际 applied 回执区分，实际回执失败可重试；循环覆盖按成功事件排序。
- 修改 `application/translation/task_history.py`：同锁保护载荷/元数据引用；发布成功后只清理本任务被取代的报告载荷，保留手动历史和未处理成果。
- 修改 `application/translation/task_recovery.py`：跨 store 实例读改写串行，保持 lease 与原子创建。
- 修改 `application/translation/task_recovery_results.py`：相同结果不重写，保留更新的用户决定。
- 修改 `ui/version_persistence.py`：冻结公共合入与保存端口，接收实际回执；普通任务保存绑定工程/版本身份并保存最新内容，不建立重复正式历史；旧工程元数据并发编辑仍保持 dirty。

## S03：上传与下载

以下路径相对 `src/transbridge/`：

- 修改 `application/projects/categorized_upload.py`、`ui/operations/categorized_upload_bridge.py`：捕获完成后不因无关修订或长期配置变化中止固定上传；保留目标身份与取消保护。
- 修改 `paratranz/batch_download_snapshot.py`：逐来源交付成果；同来源后续文件失败/403/取消时先交付成功文件及不完整原因，不冒充完整读取。
- 修改 `application/sync/batch_download.py`：隔离失败来源，让完整成果继续进入匹配与局部合入。
- 新增 `application/sync/download_results.py`：独立来源成果、base、远端内容、尝试证据及实际回执；可重开和独立导出；恢复只消费已确认覆盖，内容复用不打乱重复恢复顺序。
- 修改 `ui/operations/batch_download_session.py`：固定本轮输入和偏好；耐久与内存失败回退；重试复用成功来源；移除全局修订门禁及重复完整历史。
- 新增 `ui/operations/batch_download_results.py`：适配公共三方比较和真实回执，只清理实际应用项对应的旧云端关联；已解决的远端复核不再冒充待处理。
- 新增 `ui/operations/batch_download_access.py`：重开已有成果、显示不完整来源、构造受保护的反向恢复候选。
- 新增 `ui/operations/batch_download_recovery.py`；修改 `batch_download_dialog.py`、`batch_download_view.py`：后台重开、另存、恢复入口与结果展示，关闭窗口后可再次使用。

## S04：快照、保存和退出

以下路径相对 `src/transbridge/`：

- 修改 `persistence/v2/variant.py`：未变化 aggregate 返回既有 immutable snapshot；提交成功才更换；已冻结 JSON 不重复冻结。
- 修改 `application/projects/source_hydration_cache.py`：已安装且已验证的来源可复用文本读模型；插件导出继续验证实际文件，不复用写回模板。
- 修改 `application/projects/durable_variant.py`：内容 no-op 不制造新 revision，dirty 内容仍可耐久保存。
- 修改 `application/projects/lifecycle.py`：保持身份/CAS 在原提交边界核验，处理 no-op 保存和证据回调，普通保存允许同目标的新修订。
- 新增 `ui/shell/close_progress.py`；修改 `close_input_guard.py`：关闭期间提供可用的“返回工作台”，不被其他窗口禁用连带锁死。
- 修改 `ui/shell/window_lifecycle.py`：退出 attempt 隔离旧定时器、保存回调和嵌套确认；仍等待线程及必要原子写入安全结束。
- 新增 `scripts/benchmark_variant_snapshot.py`：在内存中与确切 Git 基线对照，使用合成数据测量重复 snapshot 构造，不读取用户工程。

## S05：测试、文档和结果

新增回归文件：

- `tests/application/translation/test_result_merge.py`、`tests/application/projects/test_project_result_merge.py`：纯比较、CAS/授权、身份、错误回执和耐久边界。
- `tests/config/test_task_result_preferences.py`、`tests/ui/test_task_result_settings.py`：缺省、损坏配置、取消和保存语义。
- `tests/ui/test_cancel_application_close.py`：迟到回调、可用返回按钮与嵌套取消/再次退出。
- `tests/ui/tools/test_ai_result_lifecycle.py`：真实 V2/旧工程结果处理、失败不丢成果、原文保护、错误证据与循环覆盖恢复。
- `tests/ui/operations/test_batch_download_results.py`：耐久重开、部分来源失败、内存另存和实际覆盖回执。

更新现有回归：

- 项目/存储：`test_categorized_upload.py`、`test_durable_variant.py`、`test_file_source_performance.py`、`test_variant_materialization.py`；任务记录：`test_task_history.py`、`test_task_recovery.py`。
- ParaTranz：contracts 的 `test_batch_download.py`、paratranz 的 `test_batch_download_snapshot.py`；operations 的 `test_batch_download_autosave.py`、`test_batch_download_dialog.py`、`test_batch_download_session.py`、`test_categorized_upload_bridge.py`、`test_categorized_upload_checkpoints.py`、`test_download_automatic_recovery.py`、`test_download_autosave_boundaries.py`、`test_download_workbench_projection.py`、`test_unified_download_service.py`。
- AI tools：`test_ai_partial_task.py`、`test_ai_result_recovery.py`、`test_ai_session_preparation.py`、`test_ai_task_async_apply.py`、`test_ai_task_consistency.py`、`test_ai_task_session.py`、`test_full_task_recovery_ui.py`、`test_mixed_completion_integrity.py`、`test_task_recovery_binding.py`、`test_task_recovery_discovery.py`、`test_translation_workflow_closure_audit.py`。
- UI 生命周期/设置：`test_ai_task_shutdown.py`、`test_settings_connection_feedback.py`、`test_settings_keyboard_navigation.py`、`test_ui_settings_dialog.py`。共享 Qt 测试显式等待 worker release，并保有 QApplication，避免测试模块切换时销毁仍被使用的对象。

文档：更新 `docs/requirements.md` 的相关旧约束并新增 FR19.15；新增 ADR-049，ADR-037/047 标明局部取代关系；更新 `docs/dev/ai_translator.md`、`docs/dev/paratranz-categorized-upload.md` 的实际使用行为；新增实施计划、S01 合入契约及 QA 报告，最小更新相关索引。

实际验证：核心联合 **227 passed**；下载 operations **550 个不同用例全部取得通过证据**（分文件，部分改可写 TEMP 后复验）；AI 合同 **98 passed**，恢复/真实结果路径 **51 passed**，退出/备份 **41 passed**。数字有交叠，不相加为独立总数。全库 `ruff check src tests`、`ruff format --check src tests`、基准脚本检查及 `git diff --check` 通过。精确命令、扩大矩阵及失败处置见 QA。

性能：10,000 条合成数据连续 100 次未变化读取，5 轮中新建 snapshot 数从每轮 100 降为 0；中位从 0.4437518 秒降为 0.00000380003 秒。仅代表此读取路径，不宣称全软件同等提速。

## 遗留与兼容

扩大测试中仍有在原始 HEAD 复现的来源排序断言、workflow mock 参数不匹配、Windows 原子替换权限错误，以及既有字号布局断言。长路径 SQLite 问题用短测试目录验证；一次扩大 Qt 单进程检查原生崩溃，分文件通过，未宣称修复无关的全仓稳定性。具体证据及限制见 QA。

没有破坏性工程格式迁移，不删除手动历史或未处理成果；普通编辑推进修订计数并自动保存当前内容，不因此创建一串正式版本。新增成果记录在回退前需保留。未操作真实用户工程、真实云端或付费模型，未打包、提交或推送。

## 最终收口验证

显式继续完成/取消任务的同进程 lease 转交已有真实 UI 回归，生命周期文件 15 项通过。随后 316 项联合为 315 通过、1 项末尾结果按钮断言失败；该节点和完整 action_flow 文件独立复验分别 1/1、12/12 通过。失败原因未证实，仅在 `tests/ui/tools/test_ai_task_action_flow.py` 增加按钮/运行/记录状态的断言诊断，未加 sleep 或弱化断言。此限制已纳入 QA，不将联合运行宣称全绿。
