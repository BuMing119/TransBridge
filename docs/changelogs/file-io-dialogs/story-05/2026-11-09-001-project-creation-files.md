# 新建工程页统一文件来源与批量译文匹配

- 日期：2026-11-09
- Epic / Story：[file-io-dialogs / S05](../../../../plans/file-io-dialogs/plan.md)
- 需求：用户确认调整新建工程页，后续要求一次框选多个译文文件自动匹配。
- 归属：本记录仅覆盖本轮建项 UI、初始译文准备及相关测试/文档；开始时已有的工程存储重构和后续其他性能改动不计入。共享文件已与任务开始副本核对，不改写既有增量。

## 用户可见行为

开始中心“选择来源文件”支持插件、EET、SST、两种 XML 及 DSD/ParaTranz JSON。来源列表固定高度，支持多选、添加、移除；多个来源默认名为“新翻译工程”，增删文件保留用户自定义名称和选项。

已有译文区始终占位，未勾选时禁用并淡化；浏览可一次选择多个文件，允许混合格式，选择框显示数量，悬停可查看完整路径，清空按钮显式清除。取消文件选择或关闭该选项保留原选择。Strings 使用单个目录。

每个主来源按已有身份匹配能力自动寻找译文；包含原文的格式额外严格核对原文，已汉化插件沿用定位匹配。默认保留已有译文、只填空值，可明确替换；受保护条目保留。相同结果只应用一次，不同译文或状态冲突时拒绝创建并列出冲突文件，选择顺序不会决定覆盖。无匹配不修改，有效但未产生更新时反馈原因，损坏文件不会被当成无匹配跳过。译文文件不登记成额外可编辑来源。

直接展示“跳过空原文”，移除高级解析折叠层。页头和“创建工程”按钮固定，小窗口/大字体只滚动中间表单，宽屏表单限制 1040px。创建仍复用后台 prepare/commit，一次完整发布整个工程；忙碌时阻止重复提交、修改和返回（包括 Esc）。

## 文件与职责

- 新增 `src/transbridge/ui/shell/project_creation_form.py`：独立建项表单，固定列表/错误区/页脚，来源增删和批量译文选择状态，保持焦点与无障碍提示。
- 修改 `src/transbridge/ui/shell/start_center.py`：委托上述表单，保留已有信号和兼容控件名称；旧表单代码移出，减少主 View 职责。
- 修改 `src/transbridge/ui/shell/start_center_controller.py`：多格式文件选择、增删来源、单目录/多译文选择及完成说明；追加混合主来源时恢复自动格式识别，避免新文件筛选器误作用于旧文件。
- 修改 `src/transbridge/ui/shell/start_center_landing.py`：入口改为“选择来源文件”，更新格式说明及空工程文案。
- 新增 `src/transbridge/ui/workbench/file_source_formats.py`，修改 `open_file_dialog.py`：共享来源格式列表和文件筛选器；保留打开文件窗口既有行为及兼容常量名。本轮多译文选择接入建项页，未扩展独立导入译文窗口的文件选择。
- 修改 `src/transbridge/ui/coordinators/guided_project_coordinator.py`：来源格式、批量译文路径及策略状态；默认名称、去重、增删时保留用户设置；准备选项、完成说明及取消/忙碌保护。`translation_path` 保留为首项兼容访问器，旧 `set_migration_sources` 程序接口保留。
- 新增 `src/transbridge/application/io/initial_translation.py`：各译文独立对同一目标匹配，汇总候选、检测跨文件冲突并一次生成初始状态，复用现有导入算法。即使一个候选与当前译文相同，显式替换时也不会遮蔽另一个不同候选。
- 修改 `src/transbridge/application/io/translation_import.py`、`migration_import.py`：增加默认关闭的原文核对选项，建项批量匹配启用；其余调用保持原策略。
- 修改 `src/transbridge/persistence/project_provisioning.py`：准备主来源时应用 `initial_translation`，新表单要求完整解析；准备动作选项在解析前取出，不存为后续 parser settings，避免工程重开时自动重读外部译文。本轮保留任务开始时已有的 SST facts 提取改动。
- 新增 `tests/contracts/io/test_initial_translation_batch.py`（8 项）、`tests/persistence/v2/test_project_creation_file_options.py`（36 项）、`tests/ui/test_guided_file_options.py`（4 项）、`test_project_creation_file_workflow.py`（3 项）、`test_project_creation_form.py`（12 项）：覆盖真实格式、保存恢复、混合批量、原文核对、冲突、损坏、Strings、原子失败、Qt worker/工作台安装及固定几何。
- 修改 `tests/ui/test_start_center_guided_project.py`、`test_start_center_multi_plugin.py`、`test_start_center_responsive_layout.py`、`test_project_creation_reuse.py`：同步新入口、默认名称、固定列表和多译文选择断言；无关存储断言保持原样。
- 修改 `docs/requirements.md` 的 FR26.2/FR26.3/FR28.1/NFR1.6、`docs/dev/ui.md`、本 Epic 计划及两个索引：移除旧单插件/首文件命名/折叠设置要求，记录当前建项流程和兼容边界。

## 验证

最终联合命令：**240 passed / 7 deselected**，15.49s。

```powershell
$creationUiTests = @(rg --files tests/ui -g 'test_start_center*.py')
uv run --offline --no-sync pytest @creationUiTests tests/ui/test_project_creation_form.py tests/ui/test_open_file_dialog.py tests/ui/test_guided_file_options.py tests/ui/test_project_creation_file_workflow.py tests/persistence/v2/test_project_creation_file_options.py tests/application/projects/test_provisioning.py tests/contracts/io/test_initial_translation_batch.py tests/contracts/io/test_translation_import_policy.py tests/contracts/io/test_migration_import.py tests/contracts/io/test_original_match_migration.py tests/ui/test_project_creation_reuse.py tests/ui/test_project_creation_transition.py tests/persistence/v2/test_project_provisioning_import_formats.py -q --basetemp=.tmp-create-file-final-multi --tb=short -k "not test_choose_multiple_plugins_creates_one_project_and_loads_every_collection and not test_reusing_active_plugin_opens_draft_and_creates_independent_project and not test_independent_migration_sources_are_included_in_the_new_workbench" --deselect "tests/ui/test_project_creation_transition.py::test_new_project_replaces_all_old_slots_and_remains_saveable[1]" --deselect "tests/ui/test_project_creation_transition.py::test_new_project_replaces_all_old_slots_and_remains_saveable[2]"
```

- `uv run --offline --no-sync ruff check src tests`：All checks passed。
- `uv run --offline --no-sync ruff format --check src tests`：2529 files already formatted。
- `git diff --check`：通过。
- 真实 Qt 离屏截图：`out/file-io-dialogs-20261109/create-project.png`、`create-project-with-translations.png`。980×780，主来源和译文均可显示 80 个文件，开关译文后按钮坐标仍为 `(878, 730)`，已查看；不操作桌面。
- 早期聚焦 35 passed；Strings/无更新反馈完善后 38 项通过；多选匹配核心最终 44 passed。合成主来源身份重复的测试曾触发既有拒绝保护，调整为两个不同身份的正常样本后通过，未放宽重复来源规则。

### 已有测试失败的归属

旧 persistence v2 建项组扩大运行出现 **20 failed / 63 passed**（含本轮 38 项）。在不覆盖工作区的前提下，用 `importlib.util.spec_from_file_location` 加载本轮开始的 `transbridge.persistence.project_provisioning` 副本，再执行下面四个旧模块，得到完全相同的 **20 failed / 25 passed**：

```powershell
pytest tests/persistence/v2/test_project_provisioning.py tests/persistence/v2/test_project_provisioning_multiple_sources.py tests/persistence/v2/test_project_provisioning_import_formats.py tests/persistence/v2/test_project_provisioning_xml.py -q --tb=no
```

失败涉及旧 JSON 活动指针/catalog/fault 注入、原始文件路径以及旧恢复对象 `baselines`，与工作区已有存储重构不一致。

另用任务开始的 guided coordinator、start-center View/controller 和 preparer 副本复跑旧 UI：

- `test_start_center_multi_plugin::test_choose_multiple_plugins_creates_one_project_and_loads_every_collection`：同样失败于槽位 source ID 与原文件路径断言。
- `test_project_creation_reuse.py`、`test_project_creation_transition.py`：**6 failed / 8 passed**，同样失败于 `esp_path` 和槽位键/顺序断言。

这些失败未归到本轮，也未记作通过。最终联合排除其中实际纳入的 7 项；旧 persistence 组其余 20 项仍保留为已知未解决测试差异。新建项功能另有真实当前存储/Qt 工作流测试覆盖。

## 边界与清理

无本轮新增 schema、依赖或哈希协议；保留既有建项事务、旧 migration_sources 及内部 intent 模式值。不提交 Git，不改用户翻译样本。未运行全仓库 pytest、发行构建、真实桌面/外部翻译器人工验收或真实大语料性能基准。多译文匹配运行于后台，规模性能未在本轮作承诺。

本轮临时基线、测试目录和截图脚本在收尾清理，保留截图；不清理其他任务已有目录。
