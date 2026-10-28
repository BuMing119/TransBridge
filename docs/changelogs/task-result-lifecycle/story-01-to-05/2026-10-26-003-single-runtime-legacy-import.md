# 统一运行流程，旧数据集中在导入入口

- 日期：2026-10-26
- Epic / Story：task-result-lifecycle / S01–S05
- 前序：[002 独立路径审查](2026-10-26-002-independent-path-audit.md)
- 计划：[plan.md](../../../../plans/task-result-lifecycle/plan.md)
- 验证：[本轮 QA](../../../test-reports/task-result-lifecycle-unification-2026-10-26.md)

## 范围

用户授权直接删除废弃流程，不为旧测试保留第二套业务逻辑，补齐旧工程目录导入，并要求不同实现 Agent 协作及独立审查。本增量记录前两轮之后的统一清理，不重复认领前两轮全部未提交代码，不包含用户已有 `pyproject.toml` / `uv.lock` 版本号修改。未读取 `.transbridge/`，没有提交、发布或真实云端调用。

## 工程、版本与保存

- 删除 `persistence/project.py`、`persistence/variant_store.py`、`application/projects/legacy.py`；移除 `persistence/v2/baselines.py` 的旧身份注册表及 `v2/variant.py` 的未使用旧投影构造器。相应包导出同步删除。
- 修改 `application/projects/gui_facade.py` 和 bootstrap 装配：只接当前 lifecycle、投影和仓储，不再注入旧身份适配器。
- 修改 `ui/context.py`：工程、版本和 dirty 来自权威投影；移除可变旧工程/旧存储属性及空的 `mark_dirty` 接口。`persistence/workspace.py` 保留界面设置与未知旧字段，不再充当旧工程目录的运行时注册表。
- 修改 `ui/main_window.py`、`coordinators/project_coordinator.py`、`variant_coordinator.py`、`parse_coordinator.py`、`workbench/_project_bar.py`、`shell/start_center_controller.py`：统一当前工程打开、编辑、保存和展示；移除旧目录直接写入分支。
- 将 `ui/coordinators/v2_project_transfer.py` 的当前实现移入规范入口 `project_transfer_coordinator.py`，删除旧包装层及旧文件。手动历史仍走当前工程服务。
- 修改 `ui/shell/project_save.py`、自动保存和 `save_recovery.py`：并发保存共用当前协调器，保存完成只确认原目标，期间新编辑继续保持 dirty；删除前轮临时引入的旧工程保存适配。
- 修改 `ui/dialogue/editing.py`、`workbench/inline_edit.py`、`translation_reset.py`、`translation_stage_action.py`、`widget.py`、标签操作及 `tools/dictionary_panel.py`：删除直接修改集合的回退，先提交当前工程命令再发布视图，失败保留原内容。

## 上传下载

- 修改 `ui/operations/facade.py`、`production.py`、`production_support.py`、`categorized_upload_bridge.py`：默认入口、显式云端目标与自定义窗口工厂均接同一生产任务；`download_context.py` 只负责显式目标覆盖。
- 删除 `ui/operations/paratranz_sync.py`、`paratranz_dialog.py`、`download_session.py`、`sync_preparation.py`、`sync_preflight_presentation.py`、`sync_result.py`、`ordinary_upload_scope.py`、`categorized_download_scope.py`，以及前轮未提交的旧工程下载执行适配。
- 删除 `paratranz/ordinary_upload.py`、旧 `categorized_upload.py`、`categorized_response_diagnostics.py`、`workflow/uploader.py`；仍使用的文件传输操作归入 `paratranz/file_transport.py`。
- 清理 `application/sync`、operations coordinator/mapper 和审阅模型中无生产调用者的 `publish_download`、`BatchDownloadCandidates`、`rebased_candidates`。保留真实消费者使用的规划、内容匹配和旧云端文件名解析，公共合入只有一套。

## AI 与助手

- 删除 `ui/tools/ai_translator/` 下 `_translation_worker.py`、`_polish_worker.py`、`_mixed_worker.py`、`_translation_progress_window.py`、`workflow_progress.py`、`workflow_progress_runtime.py`、`versioned_run.py`、`version_snapshot.py`、`polish_runtime.py`、`run_view.py`；清理 `run_controller.py` 的废弃启动接口。
- 修改 `task_runtime_binding.py`、`task_launch.py`、`task_session.py`、`task_consistency.py`、`task_result_flow.py`、`task_result_recovery.py` 等：要求真实 TaskRuntime 和冻结请求，消费实际合入回执，使用当前异步重试。删除仅为测试替身存在的同步回退、虚假回执和旧快照状态。
- 修改 `ui/version_persistence.py`，删除 `version_snapshot_inputs.py`：只合入当前工程并保存，不再保留 VariantStore 或任务前后自动正式历史写入。
- 修改 `task_launch.py` / `task_session.py`：TaskRuntime 接收任务后统一账本、恢复记录与覆盖前证据的 run ID，修复真实启动后无法找到覆盖前译文的问题。
- 修改 `task_registry.py`、`task_history_dialog.py` 和恢复发现：按工程身份隔离历史及活动窗口，避免共享父目录引起跨工程混入；未知归属只读保留。统一 mixed 暂停能力并消除进度同步时重复发出暂停控制。
- 修改 `smart_assistant/tools/_project_tool_mutations.py`、`_source_import.py`、`tool_editor.py`、`types.py`、`projection_restore.py`：删除直接写集合的兼容回退；使用当前命令和最新权威投影恢复。ExecutionContext 的目标保持冻结，只有自己的成功命令才能推进目标，切换工程不能重新绑定后误写。
- 修改 `smart_assistant/tools/tool_default.py` 及相关 UI：工程列表与名称来自当前 catalog/投影。

## 旧工程与成果入口

- 新增 `persistence/legacy_project_records.py`：目录、`project.json` 与 ZIP 共用只读记录接口。目录不重打包、不修改源文件，拒绝危险路径、链接及超限输入。
- 修改 `persistence/legacy_project_archive.py`、`project_archive.py`：共用迁移草稿和发布/普通异常回滚，保留所有版本及历史；验证过的基线补齐稀疏历史，显式空译文仍覆盖默认内容。
- 新增 `persistence/legacy_project_tasks.py`：集中保留旧 AI 历史、恢复成果、翻译/校对检查点和下载结果，按原路径摘要存放不执行的二进制附件及索引。单条坏成果保留原字节和诊断，不阻断其他正常内容。
- 新增 `application/translation/imported_task_history.py`：把导入的可解析成果提供给当前历史窗口查看、导出；不把历史 ID 自动映射成当前可写身份，不启动旧任务执行器。原始附件随当前工程包往返和副本导入保留。

## 测试与文档

- 新增 `tests/ui/fixtures/current_editing.py`、`project_runtime.py` 等当前服务夹具；迁移旧测试到真实命令、投影与 TaskRuntime。删除只验证废弃模块的测试，没有反向恢复旧生产分支。
- 新增 `test_legacy_project_directory.py`、`test_legacy_project_task_assets.py`；扩展稀疏历史恢复、原字节保留、失败回滚、工程隔离、真实启动恢复、编辑失败及冻结目标回归。
- 同步 `docs/requirements.md`、`ADR-049`、计划、AI/上传说明及 `docs/dev/ui.md` / `INDEX.md`；删除开发说明中已失效的旧 worker/进度窗口结构，前两轮增量记录保持不变。
- 主会话核心回归 **880 passed**；保存/投影补测 **15 passed**；助手目标/导入 **102 passed**，独立复验 **117 passed**；上传下载相关失败定向重跑 **49 passed、1 skipped**；恢复发现 **19 passed**。独立最终旧成果/归档复验 **49 passed**。这些集合有重叠，不相加。
- AI 770 项集合累计取得 768 项通过证据，尚有两项布局断言；扩大测试的校对、候选检查点与 bootstrap 限制详见 QA，不宣称全套全绿。
- 最终执行 `uv run --offline --no-sync ruff check src tests`、`uv run --offline --no-sync ruff format --check src tests`、`git diff --check` 均通过；`uv run --offline --no-sync pytest --collect-only -q` 收集 **9360** 项。

## 保留项

自动审批拒绝删除 `SyncReviewDialog.submit_on_accept` 可选参数，理由是可能改变确认后的立即下载/保存行为，故保留该参数；旧执行链仍已删除。旧身份无法验证的成果只读保留，恢复执行不在本轮迁移授权范围内。没有真实云端、LLM 或用户桌面验收证据。
