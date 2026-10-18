# 术语流程边界回归修复

- 日期：2026-10-18。
- Epic：terminology-ui-remediation；Story：S09～S13 后续回归修复。
- 计划：[术语界面与操作流程整改](../../../../plans/terminology-ui-remediation/plan.md)。
- 前序：[2026-10-16 术语操作流程与功能补齐](2026-10-16-001-terminology-workflow-completion.md)。
- 状态：本轮修复、独立交叉审查、627 项分组回归及静态检查完成；最终全仓复验 6715 项通过、10 项跳过，零失败。
- 归属：本次在同一聊天前序成果上继续审查和修复。工作区仍包含 10 月 16 日未提交的功能增量，本记录只说明 10 月 18 日新增的修复和验证，不重新归属或覆盖前序历史。

## 七项使用流程修复

### 1. 保存期间切换工程或翻译版本

原窗口的保存尚未完成时切换上下文，旧关闭流程可能留下禁用窗口，启动器再次打开时又复用旧窗口。现在原窗口只完成已接受的原版本保存，当前版本可以打开新的工作台；失败保留原因和输入，旧上下文不能再次提交修改。

- 修改 `src/transbridge/ui/tools/terminology/context_binding.py`：用 `invalidated` 信号交给保存协调器处理上下文失效；关闭工程时将空身份规范为 `(None, None)`，避免继续认定旧上下文有效。
- 修改 `src/transbridge/ui/tools/terminology/launcher.py`：`open` 只复用当前上下文窗口；保留尚在收尾的旧窗口，收到关闭信号后释放。应用关闭也等待已接受的保存，不提前销毁执行中的窗口。
- 修改 `src/transbridge/ui/tools/terminology/window.py`：统一检查当前上下文，失效时通知子编辑表单；拒绝失效窗口的新命令，并把拒绝原因传回编辑回调。
- 修改前序新增的 `src/transbridge/ui/tools/terminology/save_close.py`：区分普通关闭与上下文失效，原版本保存完成后收尾；失败和未保存表单保持可见，关闭需明确处理输入。
- 修改前序新增的 `src/transbridge/ui/tools/terminology/term_edit_dialog.py`：上下文失效后保留可复制的只读输入，禁止再保存；成功完成原保存时正常收尾，失败时保留原错误及恢复提示。
- 新增 `tests/ui/tools/terminology/test_context_save_switching.py`：切换工程、版本、关闭工程，保存成功/失败以及启动器关闭的真实 Qt 生命周期。
- 新增 `tests/ui/tools/terminology/test_term_edit_context_change.py`：表单保存中及尚未保存时切换上下文，验证输入保留、旧保存完成、新窗口可用和明确放弃。
- 修改 `tests/ui/tools/terminology/test_context_isolation.py`：对齐失效窗口的收尾契约。

### 2. 停止按钮绑定当前显示任务

多个任务重叠时，界面显示的进度与最近提交任务可能不同。原停止按钮按提交顺序选择任务，可能取消另一项任务。

- 修改 `src/transbridge/ui/tools/terminology/window.py`：记录当前显示的 `run_id`；`_cancel_displayed` 只取消该任务。进度先于提交确认到达时暂不允许取消；终态及迟到事件不回退到其他任务。
- 新增 `tests/ui/tools/terminology/test_task_cancellation_routing.py`：覆盖显示顺序与提交顺序不同、确认到达较晚、任务结束后迟到事件三个场景。

### 3. 待复核入口清除无关筛选

带着原有搜索词或插件范围进入“待复核”，可能出现计数不为零而列表没有结果的情况。

- 修改 `src/transbridge/ui/tools/terminology/term_filters.py`：`show_review_required` 在信号阻塞期间清空搜索与范围，再设置待复核状态并统一应用，避免中间筛选请求。
- 扩充前序新增的 `tests/ui/tools/terminology/test_workflow_routes.py`：分别保留旧搜索、旧范围及组合条件进入待复核，确认实际待办出现。

### 4. 方案映射按原名和范围定位

项目级和插件级映射可以具有同一原名，单凭显示原名定位会编辑到另一范围。现在从生效词条、方案映射及未映射投影进入管理器时均携带精确范围；找不到或不唯一时给出说明，不猜测另一行。

- 修改前序新增的 `src/transbridge/ui/tools/terminology/workflow_controller.py`：从 `TermDecision`、`ProfileMappingRow`、`ProfileTermMapping` 及未映射行取得 `TermScope`，传递所选行的身份。
- 修改 `src/transbridge/ui/tools/terminology/context_binding.py` 与 `src/transbridge/ui/workbench/terminology_profile_controller.py`：`open_manager` 贯通可选 `scope`，保留旧无参调用；已有管理器和新建管理器采用相同定位参数。
- 修改 `src/transbridge/ui/tools/terminology_profiles/manager_dialog.py`、`mapping_editor.py`、`mapping_model.py`：搜索完成后按精确范围定位；歧义、缺失及筛选隐藏均明确提示。原名比较使用领域 `normalize_term`，包括 Unicode 和空白规范化后的等价身份。
- 扩充 `tests/ui/tools/terminology/test_workflow_routes.py`：覆盖项目/插件范围和四种实际行类型。
- 新增 `tests/ui/tools/test_profile_mapping_search.py`：覆盖新旧管理器、精确范围、缺失/歧义及规范化名称，不把未映射项跳到同名其他范围。

### 5. 搜索框回车不触发新建方案

方案管理器中的按钮曾参与对话框自动默认按钮行为，搜索时按回车可能弹出新建对话框。

- 修改前序新增的 `src/transbridge/ui/tools/terminology_profiles/manager_layout.py`：统一为操作按钮设置 `setAutoDefault(False)`，搜索输入不再意外执行创建等动作。
- 在 `tests/ui/tools/test_profile_mapping_search.py` 中通过实际键盘事件验证回车不会触发任何创建对话框。

### 6. 大量方案映射的搜索移出 Qt 主线程

同步筛选大量映射会阻塞输入。现在复用既有异步行筛选器，对不可变快照构建索引；主线程只应用当前查询对应的行映射和状态。

- 修改前序新增的 `src/transbridge/ui/tools/terminology_profiles/mapping_filter.py`：使用 `AsyncRowFilter` 与 `FilteredRowsModel`；原名、当前译名、方案译名和修改状态从快照建立索引，查询与原名定位共用领域规范化。
- 修改 `src/transbridge/ui/tools/terminology_profiles/mapping_model.py`：提供搜索快照及精确原名/范围查找，避免工作线程访问 Qt 模型。
- 修改 `src/transbridge/ui/tools/terminology_profiles/mapping_editor.py`：保持筛选行与源行映射，搜索前提交正在输入的单元格；搜索期间暂停编辑，结果替换后恢复可用选择；多选移除冻结源行，关闭时清理异步搜索。
- 修改 `src/transbridge/ui/tools/terminology_profiles/mapping_delegate.py`：`commit_pending(close=True)` 在查询变更前提交并关闭活动编辑器，防止编辑器继续绑定旧视图索引。
- 修改 `src/transbridge/ui/tools/terminology_profiles/manager_dialog.py` 与前序新增的 `manager_layout.py`：显示搜索状态，实际关闭时才清理搜索；拒绝关闭后仍可继续编辑和搜索。
- `tests/ui/tools/test_profile_mapping_search.py` 覆盖 5 万行后台索引和 Qt 事件响应、过期结果淘汰、筛选后编辑/删除/插入、活动输入提交、拒绝关闭与最终关闭。
- 独立只读复核额外检查了多选删除确认期间搜索结果完成的情况：删除对象仍是确认前冻结的源行，未误删新结果中的其他映射。

### 7. 词条允许改回自身历史原名或范围

原名或范围变化会生成新身份并停用旧身份，之后改回自身旧身份曾被当作独立词条碰撞拒绝。

- 修改前序新增的 `src/transbridge/application/terminology/term_edit.py`：`replace_identity` 仅允许恢复当前词条替换链中的已停用历史身份，重接当前替换链以避免环，并保留最新编辑字段。独立词条、活动身份和分叉碰撞继续拒绝。
- 修改 `src/transbridge/application/terminology/decisions.py`：完整编辑和旧单字段原名/范围命令共用身份恢复逻辑；不可变发布版本和人工动作历史不改写。
- 扩充 `tests/application/terminology/test_term_edit.py`：完整/单字段入口、原名/范围恢复、中间身份恢复、不成环及独立停用词条仍拒绝。
- 扩充 `tests/integration/bootstrap/test_terminology_editing.py`：发布后恢复自身身份，验证历史快照不变且后续编辑字段保留。

## 后端异译保护补齐

审查发现普通新增及部分编辑入口可绕过未解决异译的审核约束。主窗口发布按钮原本会因未解决异译禁用，因此不把此问题描述为主界面可直接发布；缺口存在于应用命令以及旧草稿的直接发布路径。

- 新增 `src/transbridge/application/terminology/review_guards.py`：统一人工编辑与有效版本成员校验。显式异译决定需匹配操作、状态、原名、工程/版本和已固定证据；普通新增、完整编辑、旧单字段编辑及启用不能把未审核异译变为有效词条。
- 修改 `src/transbridge/application/terminology/decisions.py`：决策服务接收固定冲突集，每个阶段统一执行审核保护；批量失败不保存部分修改。
- 修改前序新增的 `src/transbridge/application/terminology/batch_edits.py`：复用统一冲突判断，保留停用等合法操作。
- 修改 `src/transbridge/bootstrap/terminology_review.py`：把当前固定整理结果的冲突传入决策服务，统一新增、完整编辑、旧命令和批量入口；保留期望草稿、版本和来源的并发保护。
- 修改 `src/transbridge/application/terminology/versions.py`：发布及历史恢复物化时拒绝未解决异译对应的有效词条，覆盖项目和插件范围，防止旧草稿绕过新编辑校验。
- 修改 `tests/application/terminology/test_conflicts.py`，扩充 `tests/application/terminology/test_term_edit.py` 与 `tests/integration/bootstrap/test_terminology_editing.py`：覆盖合法显式审核、非法新增/编辑/直接发布、原子失败、范围和旧草稿。

保留既有部分发布合同：未解决异译可作为待办保留在版本记录中，无关的已确认词条仍可发布；相关词条被停用时也不阻断其他有效内容。修复没有把所有未解决异译一律升级为应用层整体发布禁令。

## 随后交叉审查补正

- 修改 `src/transbridge/application/terminology/decisions.py`：`apply_many` 不再按动作数量切取元组尾部。`TerminologyDraft` 按动作 ID 排序，而生产 UUID 不保证递增，因此改为按原动作 ID 集合的差集提取新动作，使用草稿确定性顺序。单命令仍使用 `manual_action`，多命令使用 `manual_actions`，保留既有摘要契约。
- 修改 `tests/application/terminology/test_decisions.py`：新增非递增 ID 的单项和批量回归，并核验与已有动作重复、批内重复 ID 均拒绝保存。非递增 ID 回归在修复前失败。
- 修改 `src/transbridge/ui/tools/terminology/draft_write_queue.py`：显式撤销尚未执行的保存时，向该操作的失败回调发送 `DraftWriteDiscarded`，不计为写入失败，不中断正在执行的提交。
- 修改前序新增的 `src/transbridge/ui/tools/terminology/term_edit_dialog.py`：收到撤销通知后退出保存中状态并结束该表单，避免窗口一直等待已经从队列移除的操作；实际保存失败继续保留输入。
- 在 `tests/ui/tools/terminology/test_term_edit_context_change.py` 新增“一个保存正在执行、另一表单保存排队后被显式放弃”的回归，验证仅首项真正写入、表单退出、没有多余失败计数或再次确认。

## 已完成的验证

以下是本聊天主会话最后一轮分组运行证据，三组目标互不重复：**358 + 184 + 85 = 627 项全部通过**。它们与前序记录及更早的聚焦复验存在重叠，不把这些运行次数相加作为新增用例数。

命令使用现有 uv 环境及 `--offline`，通过 `python -m pytest` 执行。各组同时指定本任务独占的 `UV_CACHE_DIR`、`--basetemp` 和 `--junitxml` 输出路径；下列命令保留实际测试目标，省略现已清理的临时路径参数。Qt 运行使用无界面平台。

1. 领域、持久化及生产接线：**358 passed**。

   ```powershell
   $taskBootstrapTests = Get-ChildItem -LiteralPath 'tests/integration/bootstrap' -Filter 'test_terminology*.py' | Select-Object -ExpandProperty FullName
   uv run --offline python -m pytest tests/application/terminology tests/persistence/terminology $taskBootstrapTests -q
   ```

   bootstrap 匹配范围为 `test_terminology_browsing.py`、`test_terminology_build_review.py`、`test_terminology_conflict_review.py`、`test_terminology_editing.py`、`test_terminology_presenter_build.py`、`test_terminology_publication_review.py`、`test_terminology_save_snapshots.py`、`test_terminology_task_wiring.py`。

2. 术语工作台 Qt：**184 passed**。

   ```powershell
   uv run --offline python -m pytest tests/ui/tools/terminology -q
   ```

3. 方案编辑及工作台联动：**85 passed**。

   ```powershell
   uv run --offline python -m pytest tests/ui/tools/test_profile_mapping_search.py tests/ui/tools/test_profile_manager_workflows.py tests/ui/tools/test_terminology_profile_manager.py tests/ui/tools/test_terminology_profile_mapping_model.py tests/ui/tools/test_terminology_profile_edit_protection.py tests/ui/workbench/test_terminology_profile_switching.py tests/ui/workbench/test_terminology_preview_async.py tests/ui/workbench/test_terminology_projection_consistency.py tests/ui/workbench/test_profile_refresh_async.py -q
   ```

- `uv run --offline ruff check src tests`：通过。
- `uv run --offline ruff format --check src tests`：1812 个文件通过。
- `git -c core.safecrlf=false diff --check`：通过。
- 独立审查覆盖原版本保存收尾、取消任务绑定、身份恢复与异译保护，以及方案筛选后的源行一致性、未保存编辑和关闭清理。针对 Windows 沙箱内测试临时目录权限失败，使用获准的本地权限环境复验；不将权限失败运行记录为通过。

## 全仓验证与既有测试契约修正

用户随后要求全仓验证后提交。通过现有 Python 3.12.12 / uv 环境运行，设置 `QT_QPA_PLATFORM=offscreen`、`PYTHONIOENCODING=utf-8` 和任务独占 `UV_CACHE_DIR`；没有排除慢测试或按标记缩小范围。测试在获准访问自建临时目录的环境执行。

首轮命令：

```powershell
uv run --offline python -m pytest -q -rs --durations=15 --basetemp=.tmp-full-regression-20261018/pytest --junitxml=.tmp-full-regression-20261018/full-results.xml
```

收集 6725 项，结果 **6713 passed、2 failed、10 skipped**，退出码 1，耗时 923.83 秒。本轮没有发生原生 Qt 异常，两个失败分别如下：

1. `tests/ui/tools/smart_assistant/test_submission_lifecycle.py::test_lifecycle_snapshot_reconnects_only_live_same_session_task`：切换会话前保存目录索引，`SessionCatalogRepository._write` 中的 `os.replace` 抛出 Windows 拒绝访问，尚未进入任务恢复断言。该测试和完整存储调用链没有本次修改；读写均关闭句柄，目录仓储内部串行访问，未确定原次失败的系统原因。使用新独占目录复验完整生命周期文件及 `tests/application/sessions/test_gui_session_management.py`，**22 passed**；未修改存储实现或加入重试绕过。
2. `tests/ui/tools/test_layout_stability.py::test_smart_assistant_input_is_one_bounded_composer_card`：旧断言仍要求固定高度和 76 像素发送按钮，而此前提交 `cc290805` 已实现有界增长的 `ComposerEditor` 和 96 像素按钮。修改该测试以验证已提交的实际契约：输入框从最小高度增长到上限、超量内容内部滚动且保留全文、宿主大小稳定、发送按钮尺寸不变且可见、不越界；用 `finally` 清理窗口。没有修改输入框生产代码。与 `tests/ui/tools/smart_assistant/test_composer_design.py` 联合复验 **16 passed**。

两组复验分别使用 `--basetemp=.tmp-full-regression-20261018/catalog-recheck` 和 `layout-recheck`，均通过 `uv run --offline python -m pytest <上述测试文件> -q -rs --junitxml=<对应报告>` 执行。

修正旧测试后再次执行 `uv run --offline ruff check src tests` 和 `uv run --offline ruff format --check src tests`，均通过，格式检查覆盖 1812 个文件。

第二轮完整运行命令：

```powershell
uv run --offline python -m pytest -q -rs --durations=15 --basetemp=.tmp-full-regression-20261018/pytest-final --junitxml=.tmp-full-regression-20261018/full-final-results.xml
```

第二轮收集同样的 **6725 项，6715 passed、10 skipped、0 failed、0 errors**，退出码 0，耗时 986.60 秒。JUnit 实际包含 6725 个唯一 `testcase` 节点，其中 10 个跳过、没有失败或错误节点，与控制台一致；其 `testsuite.tests` 汇总属性为 6740，额外计入五处 `unittest.subTest` 循环的 15 个成功子测试报告，没有重复收集或执行父用例。单进程完整运行，无排除目标或分组替代。首轮的会话文件替换场景和修正后的布局测试均在完整运行中通过；原次 Windows 文件替换错误仍未确定系统原因，本轮未重现。

10 项跳过符合现有用例条件：5 项需要 Windows 符号链接权限，1 项受长路径创建限制，1 项的特殊文件名在 Windows 不合法，1 项没有显式配置专用 ParaTranz 账户，2 项实际安装器测试未显式启用。没有运行真实 LLM 或真实远端写入。输出包含 9783 条弃用警告，主要来自 SWIG 和旧翻译集合接口；本轮没有将它们升级为失败或改动告警配置。

本次全仓运行使用独占 `.tmp-full-regression-20261018/`。结果已转录到本记录；核验目录归属、绝对路径及非重解析点后已清理，日志、缓存和测试数据未纳入提交。预先存在的共享测试目录未删除。

## 兼容与范围

无新增依赖、无数据库迁移，不修改普通项目译文；旧发布版本及人工动作历史保留不可变。新增的校验会拒绝把未审核异译作为有效词条发布，合法部分发布和独立词条碰撞保护仍保留。未运行真实 LLM 或真实远端写入。

本记录保留首轮失败及复验事实，历史增量文件保持原样；更新本计划和两处直接索引。术语实现与回归提交为 `1009f64`，既有输入框测试契约修正为 `042c7b2`，验证文档单独归档，不推送。无未处理的断言失败；Windows 首轮文件替换的系统原因及现有弃用警告保留为验证观察项。
