# 插件拖放的工程选择与安全加入

- 日期：2026-10-23
- Epic / Story：`plugin-drop-project-choice` / S01、S02
- 计划：[plan](../../../../plans/plugin-drop-project-choice/plan.md)
- 需求：[FR26.11](../../../requirements.md)
- 类型：拖放缺陷修复、上下文交互扩展、保存与恢复回归

## 原因与用户可见行为

主窗口原先只过滤自身的拖放事件且未处理 DragMove。表格视口、搜索框及文本编辑器接收文件拖放时，可能阻断后续 Drop 或将路径当成文本，表现为重复拖入插件没有弹窗。

现在主窗口及其子控件统一识别文件拖放。没有项目时直接打开预填的新建草稿；已有项目时展示当前项目名称、插件路径和“加入当前项目 / 新建项目 / 取消”。同一路径已登记时说明原因并禁用加入，仍允许新建。相同内容复制到其他路径时，由后台来源命令拒绝重复身份。两个项目可独立使用同一插件；工程名称冲突沿用原有可见错误提示。

加入操作在后台执行，保留现有来源、译文、工程版本及未保存修改。选择新建且当前内容未保存时，提供保存/取消，保存失败不打开新草稿。选择期间目标变化时拒绝过期操作。普通文字拖放、其他窗口和非插件确认流程保留。

完整失败恢复测试还发现：已加入插件但界面安装失败后，重开工程先读旧来源、再保存新增来源，会触发 `VARIANT_SOURCE_IDENTITY_MISMATCH`。修复后，用户明确选择保存时，先在应用层锁内校验工程、版本及修订并保存，再读取来源。保存失败或目标变化不继续打开；已验证恢复后两个插件和原有未保存译文均完整。

## 逐文件变化

- **修改 `src/transbridge/ui/drop_binding.py`**：`SafeDropBinding` 在应用事件源观察 DragEnter / DragMove / Drop，接收者限定为目标窗口同一顶层窗口内的子控件；只截获本地文件 URL，关闭时移除过滤器。
- **修改 `src/transbridge/ui/shell/intent_composition.py`**：将完整拖放所有权和清理职责交给 `ShellDropController`，移除旧 `_on_drop_resolution`；从 509 行降至 488 行。
- **新增 `src/transbridge/ui/shell/drop_controller.py`**：按工程上下文处理插件目的地，核验后台状态、规范化路径及选择前后身份；保留非插件复核；协调新建前保存、取消及关闭。
- **新增 `src/transbridge/ui/shell/plugin_drop_dialog.py`**：提供目标选择、重复原因、明确按钮、默认焦点及可访问名称；项目名和路径按纯文本显示。
- **新增 `src/transbridge/ui/coordinators/source_add_operation.py`**：复用权威 `add_source` 命令的版本/修订约束，在 worker 中准备新增来源并核验全部词条；统一安装旧、新来源视图。提交后界面失败进入禁止编辑状态，并说明恢复操作。
- **修改 `src/transbridge/ui/coordinators/project_open_operation.py`**：`run` 在用户选择 SAVE 时先执行带目标签名的保存，成功后才读取目标来源；保存失败走现有失败和清理路径。
- **修改 `src/transbridge/application/projects/gui_facade.py`**：`save` 增加可选 `expected_active_signature` 并传递至生命周期服务；省略参数的既有调用保持行为。
- **修改 `src/transbridge/application/projects/lifecycle.py`**：`save_active` 在现有锁内、写入前核验可选目标签名，变化返回 `ACTIVE_SAVE_TARGET_CHANGED`。该模块已有大体量，本次只补原保存职责内的局部正确性检查；未新增生命周期能力。后续功能扩展前须按事务职责拆分，避免本次附带大范围结构变更。
- **修改 `tests/ui/test_safe_drop_binding.py`**：真实 Qt 事件覆盖窗口、标签、表格、两类编辑器、重复拖入、独立窗口/子弹窗隔离与普通文字拖放。
- **新增 `tests/ui/test_project_creation_reuse.py`**：真实主窗口和临时运行时验证同插件跨项目独立建项、名称冲突、源文件及原译文不变；覆盖窗口、表格、搜索框重复拖入后选择新建。
- **新增 `tests/ui/test_plugin_drop_workflow.py`**：13 项场景覆盖无项目草稿、加入并保存重开、旧未保存译文保留、重复路径/内容、保存/取消/失败、过期目标、无效插件、非插件复核和界面失败后的完整恢复。
- **修改 `tests/ui/test_project_open_operation.py`**：补充打开前保存失败时不读取来源、不激活目标并释放工作台状态的回归。
- **修改 `tests/application/projects/test_lifecycle.py`**：补充 5 种过期签名拒绝且零写入，以及匹配签名成功保存的回归。
- **修改 `docs/requirements.md`**：仅本任务的 FR26.11 段落，明确有/无项目、重复来源、新建保存和过期选择语义；该文件其他未提交需求变化不计入本记录。
- **新增 `plans/plugin-drop-project-choice/plan.md`**：记录 S01/S02、文件边界、恢复测试发现、验收与验证例外；方案标签保留草稿，实际实现与验证状态另记。
- **修改 `plans/INDEX.md`、`docs/changelogs/INDEX.md`**：仅增加/更新本工作线的计划、结果及本记录链接；已有其他工作线记录保持原样。

## 验证

以下命令均使用仓库既有 Python 3.12 环境和锁定依赖。`--cache-dir` 将缓存限定在本任务临时目录，`--no-sync` 避免无关环境变动。

恢复、打开、生命周期及保存事务专项：

```powershell
uv run --cache-dir .tmp-plugin-drop-choice-qa/uv-cache --no-sync pytest tests/ui/test_plugin_drop_workflow.py tests/ui/test_project_open_operation.py tests/ui/test_project_open_guards.py tests/application/projects/test_lifecycle.py tests/persistence/v2/test_project_open_records.py tests/persistence/v2/test_project_open_activation.py tests/persistence/v2/test_lifecycle_save_transactions.py -q --basetemp .tmp-plugin-drop-choice-qa/recovery-regression
```

结果：退出码 0，**98 passed**，包含拖放流程全部 13 项。恢复测试在修正保存顺序前明确复现来源身份不匹配，修正后通过。

最终扩大回归：

```powershell
uv run --cache-dir .tmp-plugin-drop-choice-qa/uv-cache --no-sync pytest tests/ui/test_safe_drop_binding.py tests/ui/test_safe_drop_router.py tests/ui/test_project_creation_reuse.py tests/ui/test_project_creation_transition.py tests/ui/test_start_center_guided_project.py tests/ui/test_start_center_multi_plugin.py tests/ui/test_start_center_draft_focus.py tests/ui/test_main_window_shell.py tests/ui/test_plugin_drop_workflow.py tests/ui/test_accessibility_contracts.py tests/ui/test_project_open_operation.py tests/ui/test_project_open_guards.py tests/application/projects tests/persistence/v2/test_project_open_records.py tests/persistence/v2/test_project_open_activation.py tests/persistence/v2/test_lifecycle_save_transactions.py -q --basetemp .tmp-plugin-drop-choice-qa/final-regression
```

结果：退出码 1，**324 passed、1 skipped、1 failed**，耗时 32.99 秒。跳过项为环境不能创建符号链接；3 条警告来自 Swig 类型的弃用提示。失败项：

`tests/application/projects/test_source_update.py::test_reordered_source_projects_into_context_only_remote_plan`

该测试预期两个 `context_order_changed`，实际为三个 `upload_association_confirmed`。在本次保存顺序修复前，以下独立命令已得到相同失败：

```powershell
uv run --cache-dir .tmp-plugin-drop-choice-qa/uv-cache --no-sync pytest tests/application/projects/test_source_update.py::test_reordered_source_projects_into_context_only_remote_plan -q --basetemp .tmp-plugin-drop-choice-qa/sync-failure-isolation
```

该断言属于 ParaTranz 同步计划，与新增拖放流程无关；同步实现与相关测试未由本任务修改。工作区存在独立的同步工作线，不将其改动归入此记录，也不宣称扩大回归全绿。

静态检查：

```powershell
uv run --cache-dir .tmp-plugin-drop-choice-qa/uv-cache --no-sync ruff check src tests
uv run --cache-dir .tmp-plugin-drop-choice-qa/uv-cache --no-sync ruff format --check src tests
```

两项退出码均为 0；1962 个 Python 文件格式合格。发现的测试 fixture 导入与排序问题已在本任务文件中修正。

## 兼容与剩余项

- 无数据迁移；插件源文件、项目持久化格式与现有来源命令不变。保存接口只增加可选参数。
- 用户选择重开前保存时，保存现在发生在目标来源读取之前；即使之后目标读取失败，已明确要求保存的当前工程修改仍会留在磁盘。
- 未运行全仓 pytest、Windows 文件管理器原生拖放及打包版手测；自动化使用真实 Qt 控件和事件。
- 范围外遗留：上述 ParaTranz 同步计划断言失败，需由对应工作线处理。
- 未提交或推送 Git；工作区其他同步、术语、ADR 及其增量记录不属于本任务。
