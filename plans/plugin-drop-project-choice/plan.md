# 插件拖放的工程选择

状态：草稿（方案标签保持；按用户明确目标完成 S01/S02 本地实现与验证，2026-10-23）  
需求：FR26.11；沿用现有工程身份、来源命令和 Qt worker/视图安装边界。  
日期：2026-10-23

## 目标与边界

单个 ESP/ESM/ESL 拖入主窗口时，无项目直接进入预填的新建草稿；有项目时询问加入当前项目、新建项目或取消。同一路径已登记时明确提示并禁用加入，仍可新建。加入保留已有译文、来源、版本和未保存修改；新建前对未保存内容提供保存/取消，保存成功才继续。名称冲突沿用现有创建校验。

不改变非插件拖放、多文件限制、发布/同步和文件格式；不自动提交或推送。

## 当前事实与实现约束

- `DropRouter` 只做格式识别，不知道当前项目，插件候选为 `PROJECT_CREATE`；由 shell 根据上下文选择实际操作。
- 本任务前序已修复 `SafeDropBinding` 缺失 DragMove 和子控件事件接收的问题，加入本次 S01 验证与记录。
- `ShellIntentComposition` 提取前为 509 行，现为 488 行。完整拖放编排放在 `shell/drop_controller.py`，不继续扩张该类。
- 复用 `gui_project_commands.add_source` 的版本/修订保护、重复路径/内容身份检查；新建仍走 `StartCenterController`。
- 添加来源通过独立 `ProjectSourceAddOperation` 组合后台 worker、来源 hydration 和原子视图安装，不在 GUI 线程解析插件，不改持久化协议。
- 对同内容不同路径，初始界面只做规范化路径比较；实际加入时应用层拒绝重复内容身份并显示诊断，不以文件名误判不同插件。
- 完整失败恢复测试发现：重开时先读取旧来源、随后保存，会遗漏未保存的新来源并触发 `VARIANT_SOURCE_IDENTITY_MISMATCH`。在现有 `ProjectOpenOperation` 中将用户明确选择的保存移到读取来源之前，应用层保存增加可选的工程/版本/修订锁内校验；保存失败或目标过期不继续打开。
- `lifecycle.py` 已有大体量，本次只在现有 `save_active` 内补齐锁内校验，属于局部正确性修复，不新增职责。全面拆分会扩大本次事务回归风险；后续增加生命周期能力前须先按事务责任拆分。

## 阶段进度

- [x] 明确范围并检查工作区归属。
- [x] 计划与文件边界。
- [x] S01/S02 实现及针对性回归。
- [x] bm-qa：相关 UI/应用测试、Ruff、差异审查；记录扩大回归中的范围外失败。
- [x] bm-chronicle：逐文件记录、最小索引更新和链接复核。

本会话未提供 `update_plan` 工具，阶段状态由本节维护。

## S01：按工程上下文选择拖放目的地

文件：`ui/shell/drop_controller.py`、`ui/shell/plugin_drop_dialog.py`（新增），`ui/shell/intent_composition.py`（提取），现有拖放绑定与相关测试。

验收：

- [x] 无项目直接预填新建页，不创建记录。
- [x] 有项目显示项目名称、插件路径及加入/新建/取消；重复路径禁用加入。
- [x] 新建前保存/取消未保存内容；取消和保存失败均不切换。
- [x] 弹窗期间项目/版本改变、后台操作冲突时拒绝使用过期选择。
- [x] 普通文字拖放、其他窗口和非插件识别/确认保持原有行为。

步骤：提取 shell 拖放所有权；增加具体选择窗口；复用正式新建 intent；在选择时重新核验工程身份；补入真实 Qt 事件和窗口旅程。

## S02：将插件安全加入当前工程

依赖：S01。

文件：`ui/coordinators/source_add_operation.py`（新增）、`ui/coordinators/project_open_operation.py`、`application/projects/gui_facade.py`、`application/projects/lifecycle.py`；对应 UI/生命周期回归、来源命令测试（验证），FR26.11 与本计划索引。

验收：

- [x] 后台调用现有来源命令，成功后保留全部旧来源并选中新来源，当前工程/版本不变。
- [x] 已有译文与未保存修改保留，保存/重新打开后来源完整。
- [x] 重复内容、无效插件和过期工程上下文失败时旧内容保持可用。
- [x] 命令提交后视图安装失败明确提示恢复方式并阻止继续编辑；已验证保存后重开能恢复全部来源和译文。

步骤：捕获目标工程/版本和修订；暂停投影交付与编辑；worker 添加并构建新来源视图；在 GUI 线程统一安装和释放；测试解析失败、重复、取消、安装失败与保存回读。

## 验证与风险

先运行拖放绑定/路由、真实主窗口、新建转换和新增来源专项测试，再运行相关项目来源应用测试、UI 壳层与可访问性测试。执行 `uv run ruff check src tests` 与 `uv run ruff format --check src tests`。Windows 原生文件管理器拖拽及打包版手测不作为自动化结果宣称。

无数据迁移。回退可分别恢复 shell 拖放编排与打开前的保存顺序修复；项目数据沿用现有格式。工作区其他同步、术语改动不属于本任务，不归入本次增量。

## 交付证据

- 拖放流程专项：13 项通过；恢复、打开、生命周期及保存事务组合：98 项通过。
- 最终扩大回归：324 项通过、1 项符号链接环境跳过、1 项 ParaTranz 同步计划断言失败。失败为 `test_reordered_source_projects_into_context_only_remote_plan`，在本次保存顺序修复前已独立复现，不涉及新增拖放路径；未改动该同步测试或相关同步实现。
- 全库 `ruff check src tests` 与 `ruff format --check src tests` 通过（1962 个 Python 文件格式合格）。未运行全仓 pytest、Windows 文件管理器原生拖放或打包版手测。
- 精确命令、逐文件变化与剩余项见[增量记录](../../docs/changelogs/plugin-drop-project-choice/story-01-to-02/2026-10-23-001-plugin-drop-project-choice.md)。
