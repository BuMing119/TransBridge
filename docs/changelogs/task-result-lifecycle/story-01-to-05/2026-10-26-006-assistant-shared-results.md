# 助手长任务统一固定输入与成果生命周期

- 日期：2026-10-26
- Epic / Story：task-result-lifecycle / S01–S05，第五轮补齐
- 前序：[005 工程包自动升级](2026-10-26-005-transparent-package-upgrade.md)
- 计划：[plan.md](../../../../plans/task-result-lifecycle/plan.md)

## 问题与结果

助手翻译、润色及后处理仍在任务结束时发布整份活动集合，依赖启动修订号；取消时恢复旧状态。逐条上传每次请求检查整个工程，关联回写携带旧译文。现在后台执行固定输入，成果独立保存，合入复用同一逐键三方比较；上传确认与本地关联分开处理。删除助手旧整表发布和回滚链，不保留测试专用业务回退。

## 可归属变更

- `application/translation/task_draft.py`、`task_entry_results.py`、`task_result_recovery.py`、`task_result_evidence.py`、`task_result_index.py`、`task_recovery_inputs.py`、`task_config_snapshot.py`：从同名 UI 模块整体迁移。原 UI 算法文件删除，生产与测试改为公共导入。`task_scope.py` 提取 `SourceTask`；`task_summary.py` 提取成果汇总，UI `task_run_presentation.py` 仅保留界面呈现。配置签名亦集中于 `task_config_snapshot`，删除 GUI 旧签名入口。
- `task_entry_results.py`、`task_scope.py`、`task_draft.py`、`task_recovery.py`、`task_recovery_results.py`、`task_result_recovery.py`、`task_recovery_inputs.py`：显式支持 postprocess 动作。按实际阶段完成及诊断接纳成果，失败前缀和未处理默认候选不能冒充成功；逐阶段建索引避免逐词条反复扫描全部候选。
- `smart_assistant/tools/translation_task_run.py`：捕获配置、输入、术语与远端身份，后台准备及执行独立集合。`translation_task_results.py`：复用恢复清单与成果存储，记录失败/取消及部分完成，保留保存失败的内存材料。`translation_task_merge.py`：复用公共合入、覆盖证据、runtime/effect 许可和撤销，分别报告实际提交与回执失败，成功后确认本请求的新修订号。
- `tool_translator.py`：改为薄控制入口，新增 `get_task_result` / `merge_task_result`；`translation_settings.py` 承接完整配置职责。成果查询与再次合入不调用模型、不依赖 TaskManager 内存。助手清单标明 results-only，GUI 恢复入口不会误重新执行助手任务。
- `tool_proofreader.py`、`postprocess_task_run.py`、`_postprocess_tool_runtime.py`：后处理接同一成果生命周期。临时阶段检查点在独立任务内存中保留，正式成果统一耐久保存，避免旧检查点磁盘错误使模型成果不可达。最新质量报告按调用者、会话和工程版本隔离。`agents/agent_registry.py` 补齐校对 Agent 的成果查询与合入能力。
- `application/sync/entry_upload_results.py`、`tools/_entry_upload.py`、`_entry_upload_associations.py`、`_entry_upload_recovery.py`、`tool_paratranz.py`：固定上传请求，发送前记录 in-flight，确认后保留 remote ID。最新本地关联批量 CAS，只更新原来源的引用，保留新译文、阶段和其他引用。切换可见插件不误换目标；未知远端副作用不能靠切换会话或本地版本重发。读取恢复仍按所有权授权，未授权回执只产生不泄露内容的防重提示。内存回执可以通过同一本地恢复工具补保存，不再请求远端。
- `tools/types.py`、`request_scope.py`、`_project_tool_mutations.py`：删除无调用的整表状态捕获/发布/回滚及其每次全量复制；删除 `projection_restore.py`。保留即时命令现有权限与 CAS。新增执行上下文不遍历全量词条的回归。
- 新增 `test_translation_task_lifecycle.py`、`test_postprocess_task_lifecycle.py`、`test_upload_result_lifecycle.py`。旧后处理测试补真实工程/成果目录契约；删除旧回滚专属测试，撤销覆盖改测实际权威命令。`test_authoritative_mutation_paths.py` 移除已删除写入路径的白名单。校对测试分别覆盖完整 review 与缺失 review 的既有待复核规则，不修改业务质量策略。
- 更新 FR19.15.9、ADR-049、开发说明、计划和直接相关索引。用户已有 writer、task_manager、input_validator、版本号及其余工作区改动不归本增量。

## 验证与审查

- 最终 `uv run --offline --no-sync pytest tests/smart_assistant -q --basetemp=.tmp-rs2 --tb=short`：**1037 passed**，11 条依赖/旧集合接口弃用警告。使用专属 TEMP/TMP、UV_CACHE_DIR 和 offscreen Qt。首轮沙箱硬链接/临时目录权限失败及一次 Windows replace 失败已在隔离目录复验；不据此放宽持久化保护。
- 公共核心搬迁聚焦矩阵 **183 passed**；签名与 GUI 恢复绑定最后聚焦矩阵 **53 passed**。扩大 GUI 矩阵有一项 Windows SQLite 长路径失败，短路径单项复验通过。各组重叠，不相加计数。
- 本轮真实权威命令/撤销专项 **7 passed**，原文身份 UI 回归 **6 passed**，权威写入边界契约 **4 passed**。
- `uv run --offline --no-sync ruff check src tests`、`uv run --offline --no-sync ruff format --check src tests`：通过，**2213** 个 Python 文件。`git -c core.safecrlf=false diff --check`：通过。
- 全新独立 Agent 检查真实入口、工具能力、取消、作用后错误、跨会话防重、来源切换及后处理阶段失败；发现的问题均补修并复核，最终未发现新的阻断问题。

## 真实边界

未运行全仓所有测试、真实模型/云端或用户桌面验收；未读取用户 `.transbridge` 数据，未提交或发布。磁盘尚未保存的内存成果依赖本进程存活，工具明确报告并支持重试；已保存成果可在重启后访问。未知远端结果仍需要核对，不能凭模型传入一句说明就断言“未上传”并解锁重发。上述边界不代表第二套旧执行逻辑。
