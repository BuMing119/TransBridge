# 统一运行流程与旧工程导入 QA

日期：2026-10-26。关联：[计划](../../plans/task-result-lifecycle/plan.md)、[ADR-049](../adr/049-task-result-lifecycle.md)。

## 范围与证据边界

本轮删除旧运行时，把旧数据读取留在导入和恢复边界。基于前两轮未提交修改继续实施，不重复认领前两轮全部改动，不包含用户已有的版本号修改。未读取 `.transbridge/`，未访问真实 ParaTranz/LLM，未提交或发布。测试使用合成工程、临时目录和 offscreen Qt。

实现分别由工程导入、AI、上传下载 Agent 负责；主会话整合工程壳层、助手命令和文档。另开独立 Agent 审查对应调用路径，发现后交实现者修复再复验。

## 审查发现与处理

1. 删除旧 `ProjectHandle` / `VariantStore` 后，工作台、对话编辑、字典和助手还有直接修改集合的回退。统一改为当前工程命令成功后发布投影；写入失败不能先改可见内容。测试改用真实 lifecycle、aggregate、command 和 projection，不保留测试专用生产分支。
2. 旧历史只保存部分词条时，列表可见却不能恢复。已用验证过的来源补齐缺失项，保留历史明确保存的空译文；ZIP 和目录导入后实际恢复均有回归。
3. AI 启动前临时 ID 与 TaskRuntime 分配 ID 不同，覆盖前证据无法由历史任务找到。通过 `bind_run_identity` 在捕获恢复记录前统一账本和证据 ID；独立真实 Session/Runtime 复现已通过。
4. 当前工程记录共用父目录，任务窗口及历史原先按目录判断归属。现按捕获的工程身份筛选，避免工程 B 唤起 A 的任务；下载已有包含工程、版本和账号的完整 target 比较。
5. 助手 editor 重新捕获实时 AppContext 会绕过 ExecutionContext 的原始目标。现使用冻结身份、集合和修订；只在自己的命令成功后推进执行目标。新增切换工程、替换集合和连续成功修改回归。
6. 正常旧工程包含 `ai-task-history` / `ai-task-recovery` 时被导入器拒绝。现统一保留原字节及原路径索引，由只读历史适配器展示和导出。进一步补齐真实 writer 生成的 `ai-proofread-resume`、`ai-translation-resume`、`download-results`；SQLite/WAL 等作为不执行的二进制附件保留。单个坏任务产生只读诊断，其余正常工程和成果继续导入。当前工程包再次导出/导入也保留这些附件，未自动迁移旧任务的写入权限。
7. 旧下载 `publish_download` / `rebased_candidates` 只有旧测试消费，已删除；测试使用真实公共三方合入。

## 已运行验证

所有 pytest 命令使用 `uv run --offline --no-sync pytest … -q`，临时目录由 `--basetemp` 指向本任务目录。不同矩阵有重叠，不合计为独立测试总数。

- 主会话核心矩阵 **880 passed**：`tests/persistence tests/application/projects tests/application/projections`，以及 `test_project_creation_reuse`、`test_v2_variant_hydration`、`test_cancel_application_close`、`test_project_open_guards`、`test_background_gui_operations`、`test_save_completion_state`、`test_save_recovery`、`test_main_window_coordinators`、`test_app_context_projection`、`test_v2_project_transfer`。
- 最后保存/投影补测 **15 passed**：`tests/ui/test_save_completion_state.py tests/integration/gui/test_app_context_projection.py`。包括旧保存完成不能确认另一个 clean 工程。
- 主会话 `tests/ui/operations tests/contracts/paratranz`：**507 passed、10 failed、1 skipped**。9 项失败是 localhost 套接字被沙箱拒绝，1 项为 Qt 定时器时序断言。对相关两文件重新运行（允许本机服务）：`test_terms_api_contract.py test_batch_download_dialog.py` **49 passed、1 skipped**，全部上述失败取得通过证据。
- 上传下载 Agent 最终清理后聚焦 **94 passed**；独立上传下载审查 **161 passed**。
- 编辑器迁移矩阵 **190 passed**；后续失败保真与删除来源确认补测通过。助手 UI 相关矩阵 **65 passed**。
- 助手完整矩阵先由 Agent 得到 **972 passed、1 failed**；主会话增加目标绑定回归后运行 **971 passed、2 failed、2 errors**。其中三个额外失败/错误涉及临时持久化权限；允许临时目录原子写入后 `test_request_execution.py test_undo_effect_registration.py` **24 passed**。剩余校对阶段断言见下一节。
- 最新助手目标/导入聚焦 **102 passed**：`tests/smart_assistant/test_agent_tool_integration.py tests/smart_assistant/tools/test_source_import_authority.py`。
- 独立 AI 审查首组 **59 passed**，存储补审 **41 passed**；助手冻结目标修复独立复验 **117 passed**。
- AI 最后一次 770 项矩阵得到 **764 passed、6 failed、5 teardown errors**；4 个失败及 teardown 的旧测试接口假设修正后，恢复矩阵 **73 passed**、进度/历史/能力矩阵 **48 passed**。该 770 项集合累计取得 768 项通过证据，剩余两项布局断言；不是一次完整矩阵全绿。真实启动→捕获→Runtime ID→覆盖→重开恢复路径 **16 passed**，同目录工程隔离 **2 passed**。
- 旧成果附件新增测试 **10 passed**：`tests/persistence/v2/test_legacy_project_task_assets.py`，包含真实历史/成果 writer、目录/ZIP、只读身份、原字节保留、当前 ZIP 往返、副本导入和发布异常回滚。
- 最终独立附件/旧工程复验 **49 passed**；三项真实 writer / 损坏记录复现均关闭。首次长临时路径触发 SQLite 路径限制，改为短的专属测试目录后全部通过。
- 随后补齐版本名恰好为 `ai-task-history` 的边界：已登记版本的手动历史优先按版本解码，不能误作任务附件。主会话重跑 `test_legacy_project_task_assets.py test_legacy_project_directory.py test_legacy_project_archive.py` **50 passed**。
- 附件/当前包/历史 UI 组合先得到 **75 passed、14 failed**，失败均在创建测试恢复记录时被沙箱禁止硬链接。允许临时目录原子写入后，完整 `tests/ui/tools/test_task_recovery_discovery.py` **19 passed**。
- 最终全库 `uv run --offline --no-sync ruff check src tests`、`uv run --offline --no-sync ruff format --check src tests` 通过（2189 个 Python 文件）；`git diff --check` 通过。
- `uv run --offline --no-sync pytest --collect-only -q` 最终收集 **9361 项**，无已删除模块的导入错误；同名边界补测见[收尾增量](../changelogs/task-result-lifecycle/story-01-to-05/2026-10-26-004-import-name-boundary.md)。

## 扩大验证限制

- `test_proofread_preserves_text_and_applies_questionable_stage[False-1]` 期望 stage 1，当前纯校对结果是 2。`proofread_pipeline.py`、`_polish_execution.py` 与 HEAD 一致，保留失败，没有为本轮改断言或修改不相关产品规则。
- bootstrap 扩大矩阵命令为 `uv run --no-sync pytest tests/application/projects tests/integration/bootstrap -q --ignore=tests/application/projects/test_source_update.py`，结果 **316 passed、2 failed**：`tests/integration/bootstrap/test_composition.py::test_build_runtime_is_headless_in_fresh_process` 和 `tests/integration/bootstrap/test_terminology_task_wiring.py::test_production_runtime_closes_build_decision_publish_history_restore_and_render_loop`。前者仍经与 HEAD 相同的 `infra/__init__.py` 加载 Qt，后者任务失败尚未定位，不归为通过。
- AI 剩余布局用例为 `tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size1-14]` 和 `[size2-10]`；对应 `single_task_view.py`、`view_state.py`、`scope_view.py` 与 HEAD 无差异。
- 扩大合同验证还发现 `tests/contracts/translation/test_workload_commit.py::test_stream_candidate_is_only_accepted_after_durable_checkpoint_and_retries` 失败，其函数 AST 与 HEAD 一致，`src/transbridge/ai_translator/` 与 HEAD 无差异。未对独立 HEAD checkout 重跑，因此仅证明相关实现未改，不宣称已实测为 HEAD 既有失败。
- 不宣称全套测试或真实桌面用户旅程全部通过，不以单元测试推导实际提速倍数。当前保存事务保留必要的原子性、身份和写后验证；用户手动历史保留。

## 审批限制

自动审批拒绝删除 `SyncReviewDialog.submit_on_accept` 可选参数，理由是可能改变确认后的立即下载/保存行为。本轮保留该参数；生产入口仍使用现行流程，这不构成第二套旧下载执行器。
