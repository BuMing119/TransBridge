# 术语操作流程与功能补齐

- 日期：2026-10-16。
- Epic：terminology-ui-remediation；Story：S09～S13。
- 计划：[术语界面与操作流程整改](../../../../plans/terminology-ui-remediation/plan.md)。
- 状态：实现、分组回归、独立审查和实际 Qt 渲染检查完成。
- 归属：本轮开始时工作区干净；41 个生产 Python 文件、13 个测试文件及直接关联计划/索引。本记录仅覆盖本轮，不修改此前历史记录。

## 用户可见结果

1. 保存中关闭可等待、放弃排队操作或返回编辑；正在执行的保存继续完成，失败保持窗口和原因。后续成功不再覆盖前面失败的摘要。
2. 发布后、放弃草稿后可从已发布内容继续编辑；首次缺少基础术语先引导从来源生成草稿。继续编辑不改变当前生效内容，不伪造整理记录或放宽新鲜度保护。
3. 完整词条表单包含原名、译名、项目/插件范围、别名与备注；失败保留输入。多选确认、停用、启用一次原子提交，未解决异译阻止不合法确认。
4. 方案新建、编辑、重命名有直接入口；可复制已发布项目术语创建方案。发布前显示逐条差异和所有受影响翻译版本，可发布并用于当前版本；纯名称变更也需发布新修订，旧历史名称不变。
5. 方案映射支持三字段搜索、只看修改、多选移除、从主列表定位。工作台按生成草稿、检查编辑、审查发布、继续编辑组织；空态、待办、发布条件和完成对象明确，草稿页直接发布。

## 生产文件增量

### S09～S10：保存、草稿与词条

- 修改 `src/transbridge/application/terminology/decisions.py`：增加完整编辑操作和批量决策事务，单条操作保留既有 `manual_action` 摘要上下文，多条一次提交并完整记录动作。
- 修改 `src/transbridge/application/terminology/drafts.py`：继续编辑的草稿创建可保留已发布冲突决定。
- 新增 `src/transbridge/application/terminology/term_edit.py`：完整字段校验与身份变化；原名/范围变化保留受抑制旧项和可追溯替换关系。
- 新增 `src/transbridge/application/terminology/batch_edits.py`：集中校验确认、停用、启用的选择集与未解决异译条件。
- 修改 `src/transbridge/bootstrap/terminology_review.py`：提供 `prepare_draft`、`edit_term`、`batch_terms`，复验版本、来源和期望草稿身份。
- 修改 `src/transbridge/bootstrap/terminology_workloads.py`：生产命令向审核切片提供窄委托。
- 修改 `src/transbridge/ui/tools/terminology/draft_write_queue.py`：逐项成功/失败回调、待保存数和整批完成信号；支持明确撤销等待项及重入入队。
- 新增 `src/transbridge/ui/tools/terminology/save_close.py`：独立处理保存中的关闭选择、等待和失败恢复，不阻塞 Qt 主线程等待工作线程。
- 新增 `src/transbridge/ui/tools/terminology/term_edit_dialog.py`：完整表单、保存中防重复、失败保留输入和脏输入取消保护。
- 新增 `src/transbridge/ui/tools/terminology/term_editing.py`：准备草稿、编辑读取和期望引用绑定，区分基础术语与方案派生内容。
- 修改 `src/transbridge/ui/tools/terminology/draft_interactions.py`：先准备再收集输入；表单异步保存、有效词条定位和批量确认；普通编辑使用所选身份。
- 修改 `src/transbridge/ui/tools/terminology/draft_view.py`：本页多选、选择数量、批量动作与合法状态控制。
- 修改 `src/transbridge/ui/tools/terminology/presenter.py`：复用独立编辑切片，保留原 UI 门面接口。

### S11：方案生命周期

- 新增 `src/transbridge/application/terminology_profiles/base_scheme.py`：从未叠加方案的项目术语快照建立完整映射，保留作用域。
- 新增 `src/transbridge/application/terminology_profiles/publication.py`：冻结差异、名称、草稿修订、发布修订、使用者及目标选择；提交时逐项复验，支持纯重命名新修订。
- 修改 `src/transbridge/application/terminology_profiles/service.py`：提供基于默认术语创建、发布预览及已审查提交入口。
- 修改 `src/transbridge/application/terminology_profiles/ports.py`：增加发布并采用的仓储事务契约。
- 修改 `src/transbridge/application/terminology_profiles/in_memory.py`：与持久化一致的预览复验和失败整体回滚。
- 修改 `src/transbridge/persistence/terminology/profiles.py`：发布和当前翻译版本采用处于同一 SQLite 写事务。
- 新增 `src/transbridge/ui/tools/terminology_profiles/manager_layout.py`：提取方案管理布局，集中新建、重命名、发布、采用入口。
- 新增 `src/transbridge/ui/tools/terminology_profiles/manager_workflows.py`：后台准备创建/发布、审查确认和提交，活动期间保护窗口与上下文。
- 新增 `src/transbridge/ui/tools/terminology_profiles/mapping_filter.py`：原名/当前译名/方案译名搜索与修改项筛选。
- 新增 `src/transbridge/ui/tools/terminology_profiles/publication_dialog.py`：显示名称及映射差异、受影响翻译版本和采用目标。
- 修改 `src/transbridge/ui/tools/terminology_profiles/manager_dialog.py`：委托布局/工作流，支持定位原名、直接重命名及默认复制入口；说明名称待发布状态。
- 修改 `src/transbridge/ui/tools/terminology_profiles/edit_prompts.py`：统一“重命名译名方案”提示。
- 修改 `src/transbridge/ui/tools/terminology_profiles/mapping_editor.py`：筛选、多选移除确认、定位及编辑保护。
- 修改 `src/transbridge/ui/tools/terminology_profiles/mapping_model.py`：行集合移除及模型更新支撑。
- 修改 `src/transbridge/ui/workbench/terminology_profile_controller.py`：开放定位/重命名/基于默认创建参数，说明名称尚未发布及已采用名称。
- 修改 `src/transbridge/ui/tools/terminology/schemes_view.py`：直接显示新建、编辑/管理、重命名；默认译名不是独立方案时解释不可重命名原因。
- 修改 `src/transbridge/ui/tools/terminology/schemes_controller.py`：接通三个直接入口。
- 修改 `src/transbridge/ui/tools/terminology/context_binding.py`：固定上下文下转发新的管理参数，保留旧调用兼容。

### S12：工作台操作顺序

- 新增 `src/transbridge/ui/tools/terminology/workflow_state.py`：从权威资产投影阶段、待办和发布条件；不完整/过期整理不会显示可发布。
- 新增 `src/transbridge/ui/tools/terminology/workflow_bar.py`：紧凑阶段说明、继续编辑及检查影响并发布。
- 新增 `src/transbridge/ui/tools/terminology/workflow_controller.py`：连接空态、编辑、映射定位、整理完成和保存反馈，避免继续扩张窗口职责。
- 修改 `src/transbridge/ui/tools/terminology/workbench_shell.py`：压缩上下文行，操作反馈移至底部，保留术语/版本/报告导航。
- 修改 `src/transbridge/ui/tools/terminology/object_views.py`：生效/草稿/方案主视图与有数量的复核/异译操作分开，按阶段设置主要动作。
- 修改 `src/transbridge/ui/tools/terminology/effective_view.py`：生效词条进入基础编辑或方案编辑，区分首次/未发布/筛选空态；空态按钮紧邻说明，隐藏无用分页。
- 修改 `src/transbridge/ui/tools/terminology/source_browser.py`：选中映射或双击可直接定位方案编辑。
- 修改 `src/transbridge/ui/tools/terminology/operation_notice.py`：底部紧凑反馈间距。
- 修改 `src/transbridge/ui/tools/terminology/asset_presentation.py`：共享状态统一投影，整理完成指向草稿，保留不完整/过期原因。
- 修改 `src/transbridge/ui/tools/terminology/window.py`：接入保存关闭保护与流程组件；异步写回调、中文字体主题、任务结果包含操作对象。

## 测试文件与审查

- 新增 `tests/application/terminology/test_term_edit.py`：完整属性、身份替换、冲突及批量原子性。
- 新增 `tests/application/terminology_profiles/test_reviewed_publication.py`：双仓储的预览后变更拒绝、使用者集合、原子采用、默认复制及纯重命名。
- 新增 `tests/integration/bootstrap/test_terminology_editing.py`：真实 SQLite 继续编辑、再发布、来源变化拒绝、编辑与批量接线。
- 新增 `tests/ui/tools/terminology/test_save_close.py`：等待、显式放弃、取消、失败留窗及完成回调重入入队。
- 新增 `tests/ui/tools/terminology/test_term_editing.py`：完整字段、失败不丢输入、基础/方案分离、精确选择、替换关系与批量定位。
- 新增 `tests/ui/tools/terminology/test_workflow_routes.py`：首次空态、发布后继续、筛选空态、不完整/过期阻止发布及失败摘要保留。
- 新增 `tests/ui/tools/test_profile_manager_workflows.py`：默认新建、预览取消、采用、搜索删除和重命名后发布反馈。
- 修改 `tests/ui/tools/terminology/test_draft_interactions.py`：对齐完整表单并保留原快捷动作验证。
- 修改 `tests/ui/tools/terminology/test_operation_feedback.py`：操作对象文案与共享阶段投影夹具。
- 修改 `tests/ui/tools/terminology/test_schemes_view.py`：直接重命名/新建入口及默认方案解释。
- 修改 `tests/ui/tools/terminology/test_window_lifecycle.py`：确认新入口接线和原窗口生命周期。
- 修改 `tests/ui/tools/test_terminology_profile_manager.py`：确认新发布审查交互。
- 修改 `tests/ui/workbench/test_terminology_profile_switching.py`：名称未发布标记和采用后的统一显示。

独立复核发现并修正：普通草稿编辑错误沿替换链定位、批量操作沿用旧搜索定位、保存失败被后续成功覆盖、不完整整理显示可发布、纯重命名沿用旧发布名称。扩大回归发现单条操作摘要上下文变更，恢复历史格式；原 `test_terminology_save_snapshots.py` 保持不改并通过。

职责复核：41 个生产增量文件均少于 500 行；既有 `TerminologyPresenter` 为 449 行/36 方法，本轮不增加主要类方法数，新语义在 `term_editing.py`。继续拆公共 UI 门面会扩大兼容影响，保持窄委托；后续若新增门面职责，先按端口分组提取并保留合约测试。方案管理布局与异步工作流已拆出，不以 mixin 或通用工具堆积隐藏规模。

## 实际验证

所有测试通过现有 uv 环境运行，`QT_QPA_PLATFORM=offscreen`，`PYTHONIOENCODING=utf-8`；uv 缓存和 `--basetemp` 均置于本任务独占 `.tmp-terminology-workflow-20261016/`。

最终四个无重叠分组的 JUnit 用例键核对为 **783 项全部通过**：

1. 后端 **529 passed**：`uv run --offline python -m pytest`，目标为 `tests/application/{terminology,terminology_profiles,terminology_sync}`、`tests/persistence/terminology`、`tests/contracts/terminology`、`tests/contracts/io/test_terminology_profile_write.py`、`tests/integration/terminology`、`tests/application/translation/{test_terminology_run_snapshot.py,test_proofread_terminology_closure.py}`、`tests/integration/bootstrap/test_terminology*.py`（排除 `test_terminology_task_wiring.py`）和 `tests/ai_translator/test_project_terminology*.py`；报告两个文件用 `--ignore` 留给第 3 组。PowerShell 中使用 `Get-ChildItem -Filter` 展开文件目标。
2. Qt **234 passed**：`uv run --offline python -X faulthandler -m pytest tests/ui/tools/terminology tests/ui/tools/test_profile_manager_workflows.py tests/ui/workbench/test_profile_refresh_async.py`，加上展开的 `tests/ui/tools/test_terminology*.py` 和 `tests/ui/workbench/test_terminology*.py`。本组也将 `TEMP`/`TMP` 设置到任务临时目录。
3. 报告 **13 passed**：`uv run --offline python -m pytest tests/application/terminology/test_reports.py tests/contracts/terminology/test_renderer_parity.py -q`，包含 5 万条报告回归。沙箱内 Windows `os.link` 返回拒绝访问，获准在允许该文件操作的环境复验通过。
4. 生产完整链路 **7 passed**：`uv run --offline python -m pytest tests/integration/bootstrap/test_terminology_task_wiring.py -q`。沙箱内捕获自建来源返回 `PROJECT_SOURCE_PERMISSION_DENIED`，获准在允许文件操作的环境复验通过。

摘要修复后额外复验（与上述集合重叠，不重复计数）：`uv run --offline python -m pytest tests/ui/tools/terminology/test_term_editing.py tests/ui/tools/terminology/test_workflow_routes.py tests/ui/tools/terminology/test_publish_flow.py -q`：**21 passed**。

- `uv run --offline ruff check src tests`：通过。
- `uv run --offline ruff format --check src tests`：1807 文件通过。
- `git -c core.safecrlf=false diff --check`：通过，保留仓库正常换行转换。
- 实际 Qt 主题与中文字体渲染：920×620、1180×760 工作台首次/草稿/生效状态；580×540 词条表单；920×620 方案管理；960×640 发布预览。检查按钮完整、说明与动作邻近、表格可见及小窗口尺寸不被撑大。工作台使用真实 SQLite 测试数据；截图中的项目名称和方案栏默认状态由渲染脚本设置，不作为真实用户工程运行证据。截图保留在本会话 visualization 目录。

首次将全部集合合并为一个进程时，Windows 原生 Qt/扩展异常导致进程退出（`0xC0000005`），故不将该次运行记为通过；最终按职责分进程复验全部目标。其根因未作确定归因，未为测试改动生产运行保护。原有三条 SWIG 弃用警告仍存在。

## 兼容与收尾

无新增依赖、无数据库迁移、不修改普通项目译文；旧发布修订保持不可变。没有运行全仓全部测试、真实 LLM 或真实远端写入，不声称这些验证通过。初次单进程原生异常仍是测试运行层面的观察项，分组复验未重现。

更新本计划和两处直接索引；核验归属及绝对路径后删除本任务临时脚本、数据库、测试目录和缓存，保留渲染交付图。未提交或推送代码。
