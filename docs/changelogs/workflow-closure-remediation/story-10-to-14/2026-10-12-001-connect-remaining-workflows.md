# 补齐草稿关闭、解析迁移、下载结果和任务中心流程

- 日期：2026-10-12。
- Epic / Story：workflow-closure-remediation，S10～S14。
- 计划：[跨模块流程闭环整改](../../../../plans/workflow-closure-remediation/plan.md)。
- 授权：用户在流程审查后调用 bm-pilot，要求修复本轮问题。
- 归属：以本轮启动时的 Git 状态、差异及实际补丁核对，只记录下列变更。保留此前的工作台交互、写回、数据保真等未提交改动；没有 commit/push。

## 行为与逐文件变更

### S10：失败草稿与关闭生命周期

- 修改 `src/transbridge/ui/workbench/inline_edit.py`：增加 `confirm_close`，关闭前展示失败草稿数量，默认取消；明确放弃只删除确认前的草稿对象，确认期间的新草稿不会被误删。此文件原已存在于工作区，本轮没有把行内编辑功能整体计作新增。
- 修改 `src/transbridge/ui/workbench/step2.py`、`widget.py`：各提供关闭保护的薄代理。
- 修改 `src/transbridge/ui/shell/window_lifecycle.py`：关闭开始和最终保存后检查草稿，取消关闭统一恢复输入、自动保存和 AI 状态；模态确认前后重查脏状态、API/工程打开/前台/AI 任务，晚到工作排空后才能释放资源。过期回调不能重新开始已取消的关闭。
- 新增 `tests/ui/test_inline_edit_shutdown.py`：覆盖保留、放弃、重试成功、最终保存期间新增草稿、确认期间替换草稿、工程变脏和后台任务启动。
- 修改 `tests/contracts/projects/test_authoritative_mutation_paths.py`：扫描行内编辑控制器，将精确允许写点从已迁移的 Step2 方法更新到 `InlineEditController._commit`，不放宽权威提交边界。

### S11：解析附带迁移

- 新增 `src/transbridge/ui/coordinators/parse_migration.py`：无 Qt 的迁移准备职责，复用严格应用层适配器；EET、XT、已翻译插件、Strings 逐来源创建独立候选，失败来源不污染成功部分，记录类型、路径和原始诊断。
- 修改 `src/transbridge/ui/coordinators/parse_coordinator.py`：单插件、批量插件和 EET 解析共用准备逻辑；批量应用所选迁移输入，记录实际成功来源。部分失败明确提示并可定位原集合重新打开迁移配置，过期或移除集合拒绝重试；保留原权威提交和修订校验。
- 修改 `src/transbridge/application/io/legacy_migration.py`：共享 XML 迁移入口用注册适配器核验实际 EET/XT 格式，拒绝选错类型和未知根；合法空文件、无匹配、UTF-8 BOM、UTF-16 仍可使用。
- 新增 `tests/ui/test_parse_migrations.py`；修改 `tests/contracts/io/test_migration_import.py`：真实文件经迁移、权威导入、水合后验证译文，覆盖四类来源、故障隔离、格式错误、编码和重试身份。

### S12：下载失败与部分完成

- 修改 `src/transbridge/paratranz/workflow/downloader.py`：`DownloadResult` 追加成功读取文件数和逐文件失败记录，保留有效结果；远端读取异常、非列表响应及所选文件消失均明确返回失败证据。
- 修改 `src/transbridge/ui/workbench/cards/download_card.py`：单次和批量下载展示真实失败原因，批量分别统计成功、部分完成、失败；仅有合并结果时提交权威候选，全失败和正常空结果不做空写入。
- 修改 `tests/paratranz/test_workflow.py`、`tests/ui/test_authoritative_batch_download.py`，新增 `tests/ui/test_download_failure_results.py`：覆盖正常空文件、无匹配、全失败、部分成功、远端文件消失，以及实际下载到 UI/权威提交的完整链路。

### S13：任务中心与功能结果接线

- 修改 `src/transbridge/application/tasks/actions.py`：允许功能 navigator 提供“重新准备”意图，明确区分准备与提交新任务；执行时重新读取历史并校验助手请求归属，不伪造 JobRef；增加对称注销。
- 新增 `src/transbridge/ui/shell/task_navigation.py`：桌面功能绑定，核验结果、当前工程/版本及历史修订，打开真实回执/诊断、指定 AI 报告或原功能配置窗口；意图交付时再次核验权限。关闭时注销并关闭所有结果窗口。
- 修改 `src/transbridge/ui/shell/intent_composition.py`：在真实桌面启动/关闭装配导航，重试引导打开任务中心；提供 AI 校对恢复记录入口。
- 修改 `src/transbridge/ui/shell/task_center.py`：终态事件刷新历史，当前终态任务与历史共用同一动作校验；准备动作显示“重新准备…”，诊断按钮准确命名，AI 恢复进入现有任务记录窗口。
- 修改 `src/transbridge/ui/operations/facade.py`：仅增加授权后的 `read_task_result` 公共读取口，复用原结果读取器。
- 修改 `src/transbridge/ui/operations/production.py`、`paratranz_sync.py`、`src/transbridge/application/sync/task_adapter.py`：同步结果在终态前交付到与 FOMOD 共用的有界回执缓存；同步进度携带真实操作结局，窗口关闭后仍可在本次进程中查看。
- 修改 `src/transbridge/ui/operations/production_support.py`：从当前 UI 工程/版本生成操作身份，避免沿用启动时的旧上下文。
- 修改 `src/transbridge/application/translation/task_history.py`：增加按 run ID 的报告存在性查询。
- 修改 `src/transbridge/ui/tools/ai_translator/task_registry.py`、`task_history_dialog.py`：接收目标 run ID，异步定位并打开指定报告；缺失时给出诊断，不自动打开另一条历史。关闭撤销自动打开并使已发出的报告读取回调失效，重开窗口不恢复旧请求；工作线程正常排空。
- 新增 `tests/ui/test_desktop_task_navigation.py`、`tests/contracts/tasks/test_task_retry_preparation.py`：真实运行时与 Shell 装配、当前/历史按钮路径、准备不执行、跨工程/版本、意图交付前撤权、结果丢失、注销及持久化助手请求归属。
- 修改 `tests/ui/operations/test_paratranz_download_authority.py`、`test_production_support_authority.py`、`tests/ui/tools/test_ai_task_history_ui.py`：验证真实同步回执、当前身份、指定报告和丢失报告。

### S14：稳定验证与记录

- 修改 `tests/ui/test_main_window_shell.py`：固定离屏平台不可靠的系统光标输入，仍通过真实 Qt 定时器验证菜单折叠，使用有界条件等待。
- 修改 `tests/ui/test_accessibility_contracts.py`：任务中心用例使用真实 `TaskRuntime` 的活动投影，替换缺少 `is_terminal` 字段的手工夹具，继续验证 Escape 不停止任务及停止按钮的恢复说明；不更改生产终态契约。
- 更新本 Epic 的 plan、`plans/INDEX.md` 及 `docs/changelogs/INDEX.md`，保留历史增量。

## 验证证据

所有命令在仓库根目录运行，使用既有 uv 环境和锁文件。

```text
uv run --no-sync pytest tests/paratranz/test_workflow.py tests/ui/test_authoritative_batch_download.py tests/ui/test_download_failure_results.py -q --basetemp=.tmp-workflow-fix-20261012/download/pytest-2
uv run --no-sync pytest tests/contracts/io/test_migration_import.py tests/ui/test_parse_migrations.py tests/ui/test_legacy_migration_safety.py -q --basetemp=.tmp-workflow-fix-20261012/xml-format/pytest-1
uv run --no-sync pytest tests/ui/test_inline_edit_shutdown.py tests/ui/test_ai_task_shutdown.py tests/ui/test_worker_shutdown_registry.py -q --basetemp=.tmp-workflow-fix-20261012/close-review/pytest-1
uv run --no-sync pytest tests/ui/operations tests/ui/tools/test_ai_task_history_ui.py tests/ui/test_task_center.py tests/ui/test_main_window_shell.py tests/contracts/tasks -q --basetemp=.tmp-workflow-fix-20261012/task-ext-2
uv run --no-sync pytest tests/ui/test_desktop_task_navigation.py tests/contracts/tasks/test_task_retry_preparation.py -q --basetemp=.tmp-workflow-fix-20261012/task-final-scope
```

以上分组依次为 **41、61、31、163、15 passed**，分组存在交集，不相加作为覆盖数。较早的草稿/AST 组合 35 passed，解析/水合/通知组合 47 passed。下载首次测试因 basetemp 父目录不存在出现夹具错误，创建专属目录后通过；未改变产品代码来绕过夹具错误。

最后补充的慢列表/慢报告关闭回归使用 Event 同步，4 项修复前全部失败，修复后连同导航及 AI 关闭组合 36 passed。FOMOD 无集合重新准备边界也已补对照。

最终变更后的联合复验：

```text
uv run --no-sync pytest tests/ui/test_parse_migrations.py tests/ui/test_parse_hydration_authority.py tests/ui/test_legacy_migration_safety.py tests/contracts/io/test_migration_import.py tests/paratranz/test_workflow.py tests/ui/test_authoritative_batch_download.py tests/ui/test_download_failure_results.py tests/ui/test_inline_edit_recovery.py tests/ui/test_inline_edit_shutdown.py tests/ui/test_ai_task_shutdown.py tests/ui/test_worker_shutdown_registry.py tests/contracts/projects/test_authoritative_mutation_paths.py tests/ui/operations tests/ui/tools/test_ai_task_history_ui.py tests/ui/test_task_center.py tests/ui/test_desktop_task_navigation.py tests/ui/test_main_window_shell.py tests/contracts/tasks -q --basetemp=.tmp-workflow-fix-20261012/final-combined
```

结果：**335 passed、23 warnings，16.51 秒**。警告为 SWIG 及现有兼容写入 API 的弃用提示。

- `uv run --no-sync ruff check src tests`：通过。
- `uv run --no-sync ruff format --check src tests`：1612 个文件通过。
- `git -c core.safecrlf=false diff --check`：通过。
- 对比本轮启动时保存的差异：所有跟踪文件的变化均在本次授权文件集内；未修改锁文件、构建或安装配置。
- 本轮专属 `.tmp-workflow-fix-20261012` 已核验归属并清理，保留用户原有文件和历史增量；没有提交或推送。

全项目离线回归命令：

```text
uv run --no-sync pytest tests -q -m "not llm and not slow" --basetemp=.tmp-workflow-fix-20261012/full
```

结果：**5939 passed、2 failed、10 skipped、13 deselected，672.73 秒**。该轮启动早于最后的 AI 报告关闭/FOMOD 边界补充，最后变更由上面的 335 项联合复验覆盖。两项失败分别为：

- 任务中心无障碍测试的手工 `SimpleNamespace` 没有真实活动状态的 `is_terminal` 字段。替换为真实运行时投影后，`uv run --no-sync pytest tests/ui/test_accessibility_contracts.py tests/ui/test_task_center.py tests/ui/test_desktop_task_navigation.py -q --basetemp=.tmp-workflow-fix-20261012/accessibility-final`：**24 passed，2.04 秒**。
- GUI 基础性能用例的最终生命周期 P95 为 1.4371325 秒，相对门槛为 1.4312706 秒，超出约 5.86 毫秒；其余预算通过。同一时段有其他复验负载，不能据此确定产品性能回归。保持原阈值和实现不变，独立执行 `uv run --no-sync pytest tests/performance/test_ui_foundation_performance.py::test_final_gui_foundation_profile_enforces_all_versioned_budgets -q --basetemp=.tmp-workflow-fix-20261012/perf-recheck`：**1 passed，118.83 秒**。

全量两项失败均已分别复验通过，未再次运行整个全量套件；不将分组结果表述为一次全量全绿。最后仅修改无障碍测试夹具，之后全库 Ruff 检查及 1612 个文件的格式检查再次通过。

## 兼容与验证范围

- 公共文件格式、项目存储格式和旧任务历史格式不变，无迁移步骤。新增结果字段使用默认值；原直接重试提交契约继续要求新 Run ID。
- AI 报告和校对恢复沿用功能侧持久化记录。写回、同步及 FOMOD 结果沿用有界进程缓存，重启或缓存失效后不承诺恢复旧操作回执。
- “重新准备”需要重新核对当前范围、预检和确认，不自动重放历史写入。助手所属任务仍返回原请求处理；未新增通用检查点执行器。
- 解析协调器缩至 499 行，迁移与任务导航独立成专责模块。Step2 超过现有大小阈值，本次仅增加薄代理；下载卡片仅局部修正结果判定。下次扩展职责前应先拆分完整切片。
- 不调用真实 ParaTranz 或 LLM 服务，不将离屏 Qt 回归等同于人工桌面体验检查。
