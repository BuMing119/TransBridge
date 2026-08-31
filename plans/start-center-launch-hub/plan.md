# 开始中心启动 Hub 实施计划

- **Feature slug**：`start-center-launch-hub`
- **状态**：S01～S06 功能与自动化 QA 已完成（2026-08-25，账户菜单与工程切换视觉待用户复验）
- **日期**：2026-08-25
- **对应需求**：[FR28.1～FR28.6、FR26.2～FR26.3、NFR1.4～NFR1.7](../../docs/requirements.md)
- **架构约束**：[ADR-022](../../docs/adr/022-modern-workbench-visual-composition.md)、[ADR-025](../../docs/adr/025-project-window-launch-and-open-progress.md)
- **依赖**：[guided-ui-workflows](../guided-ui-workflows/plan.md)、[modern-workbench-visual-shell](../modern-workbench-visual-shell/plan.md)

## 目标

把现有纵向散落的开始中心升级为现代桌面启动 Hub：左侧按用户任务开始插件/FOMOD/已有工程/高级空工程，右侧管理只读本地工程目录，底部以聚合横幅连接权威任务中心；开始、工作台和 ParaTranz 共享现有导航壳层，同时保留自动恢复、dirty、任务和 canonical intent 语义。

## 非目标

- 不增加工程搜索、最近打开时间、翻译进度、重定位/移除或三点工程菜单。
- 不修改 Project catalog、Project/Variant 数据 schema 或自动恢复持久化语义。
- 不复制 TaskRuntime、恢复目录或任务中心状态，不在 View 中直接恢复任务。
- 不逐像素复刻生成效果图，不引入第三方主题框架、局部硬编码颜色或新的主题 owner。
- 不重做 guided project draft、FOMOD 面板、工作台或 ParaTranz 页面内部布局。

## 当前实现事实与关键约束

- `StartCenterWidget` 当前把标题、主按钮、状态通知、两个列表和次级按钮顺序放入全窗口 `QVBoxLayout`；空列表只隐藏列表、不隐藏标题，宽屏剩余空间会被各控件分配。
- `StartCenterController` 已把插件、打开工程、最近工程、空工程和 FOMOD 映射到现有 intent；恢复项激活目前只显示“可在任务中心继续”，没有提交 `TASK_OPEN_ACTIVITY`。
- `ProjectCatalogEntry` 仅提供 id、名称、路径、active、available 和 reason；`V2ProjectCatalog` 按 active、名称、id 排序，因此目标栏目必须称为“本地工程”。
- `StartCenterWidget` 位于外层 `central_stack`，`WorkspaceShell/NavigationRail` 只包裹工作台与 ParaTranz。目标稿的常驻“开始”导航需要把 landing 纳入同一 shell，但 `mode_tabs` 的工作台=0、ParaTranz=1 兼容索引必须保持。
- View 继续只渲染 immutable `StartCenterViewState` 并发信号；Controller/composition 负责 projection、intent 和页面切换。主题继续由应用 palette 与集中编译样式拥有。
- `start_center.py`、`main_window.py` 已接近仓库 500 行责任审查阈值；新的 landing 结构组件应抽到内聚模块，不继续扩大 MainWindow。

## Story 01：共享壳层中的开始导航

**验收标准**：

- [x] NavigationRail 显示“开始、工作台、ParaTranz 管理”，开始页与现有页面共享同一 rail。
- [x] `WorkspaceShell` 保持工作台逻辑索引 0、ParaTranz 逻辑索引 1；现有 `setCurrentIndex/currentIndex/widget/addTab` 调用方无需改业务语义。
- [x] 正常自动恢复成功仍直接显示工作台；点击“开始”只请求显示开始中心，不关闭工程、清 dirty 或取消任务。
- [x] 设置、帮助、关于、用户状态和既有 intent 转发保持唯一 owner。

**文件落点**：`src/transbridge/ui/shell/navigation_rail.py`、`src/transbridge/ui/main_window.py`、`src/transbridge/ui/shell/start_center_controller.py`，对应 shell/start-center 测试。

**实施步骤**：

1. 为 `WorkspaceShell` 增加 landing page 组合与逻辑索引映射，NavigationRail 用同一互斥按钮组表达开始和两个内容页。
2. MainWindow 将 StartCenter 注入 WorkspaceShell，而不是在外层复制第二套导航；保留必要 compatibility port。
3. 将 start-center/workbench 页面切换收敛到 WorkspaceShell 的公开方法，并验证活动页面与导航选中态同步。

**测试策略**：页面实例复用、逻辑索引兼容、开始/工作台/ParaTranz 切换、导航 intent 唯一性、自动恢复目的地回归。

## Story 02：任务入口与本地工程双栏 Landing

**验收标准**：

- [x] 开始中心标题、副标题、任务面板和本地工程面板顶部聚合；宽屏内容最大宽度受限并居中。
- [x] “新建本地翻译工程”是唯一公共建项入口；开始中心在该流程内提供选择插件和高级空工程，FOMOD 与打开 TransBridge 工程保持次级层级。
- [x] 本地工程显示活动、普通可用和不可用三种文本/图标/可访问语义；活动工程激活返回工作台，普通工程打开路径，不可用工程不可激活。
- [x] 无工程时在工程面板内显示空状态，不保留“继续工作/可恢复任务”等孤立标题或巨大通知框。
- [x] 较窄窗口仍可访问全部任务和工程；draft 页面及其焦点/返回语义不受 landing 重排影响。

**文件落点**：新增 `src/transbridge/ui/shell/start_center_landing.py`；修改 `src/transbridge/ui/shell/start_center.py` 与集中主题样式；更新开始中心、布局稳定性、主题测试。

**实施步骤**：

1. 抽取 landing 专用任务卡、工程行、面板与响应式组合，使用 Tabler 图标和语义属性，不硬编码颜色。
2. `StartCenterWidget` 保持 landing/draft facade 和既有公开信号，代理 landing 的点击与 render。
3. 以 bounded content wrapper 和动态布局方向控制宽屏/窄屏，不让空数据改变核心操作位置。

**测试策略**：空/三类工程状态、宽屏居中/最大宽度、窄屏可达、焦点、Enter、禁用原因、主题切换 identity 与 revision 保持。

## Story 03：恢复、活动工程与状态连续性

**验收标准**：

- [x] 聚合横幅只统计 `recoverable=True` 的任务并显示“有 N 个任务可以继续”；无可恢复任务时完全隐藏。
- [x] “打开任务中心”只提交一次 `TASK_OPEN_ACTIVITY`；View 不直接恢复 checkpoint 或创建任务窗口。
- [x] `RESTORING_LAST` 使用紧凑状态横幅并禁用会切换工程的入口；离开该状态后能力按真实可用性恢复。
- [x] `START_CENTER_RECOVERY_FAILED` 显示稳定诊断但仍允许打开其他工程；`START_CENTER_USER_REQUESTED` 显示活动/dirty 状态并可返回工作台。
- [x] projection 查询异常有可见诊断或安全降级，不再静默伪装为“没有恢复任务”。

**文件落点**：`src/transbridge/ui/shell/start_center.py`、`start_center_landing.py`、`start_center_controller.py`，必要的 task/project projection 测试。

**实施步骤**：

1. 将逐项恢复列表替换为 ViewState 驱动的数量摘要和任务中心信号。
2. 区分活动工程的 return 与普通工程 open，保留不可用工程原因和 revision guard。
3. 为恢复中、恢复失败、用户返回和能力不可用状态建立一致的 enabled/focus/accessibility 更新。

**测试策略**：可恢复/不可恢复混合统计、canonical intent、恢复中冲突操作、失败降级、dirty 返回、重复点击与迟到 revision。

## Story 04：QA、视觉合同与交付门禁

**验收标准**：

- [x] 聚焦 UI/shell 测试、相关集成测试、Ruff check 和 format check 通过。
- [x] 1920×1080 代表性离屏布局满足顶部聚合、居中、最大宽度和双栏几何不变量；不采用逐像素 golden。
- [x] 浅色/深色/系统主题切换不改变页面、工程条目、恢复横幅、focus 或业务 revision。
- [x] 无新增 polling、窗口树扫描、重复 projection、重复业务 command、raw theme color 或遗留调试代码。
- [x] 最终 diff 不包含 Project/Variant schema、FOMOD/Workbench 内部行为或无关格式化。

**文件落点**：`tests/ui/test_start_center_guided_project.py`、`tests/ui/test_modern_workbench_visual_shell.py`、`tests/ui/test_layout_stability.py`、`tests/ui/test_accessibility_contracts.py`、`tests/ui/test_shell_theme_migration.py`，必要时更新本计划状态。

**测试策略**：先运行开始中心和 shell 聚焦测试，再扩展相关 UI 套件、Ruff check 与 Ruff format check；记录因环境无法执行的检查及替代证据。

## QA 结果（2026-08-25）

- 完整 UI 套件：`370 passed, 1 skipped`。
- 本次 Python 改动文件：Ruff check 与 Ruff format check 通过。
- 用户真实应用截图证明初始实现的控件尺寸与目标视觉密度差距过大；返工后在真实应用主题下重新截图复核，并以几何测试锁定 1400px 最大宽度、520px 任务栏、600px 面板、主次任务卡高度、居中双栏和窄屏纵向重排。
- 最终视觉状态仍以用户在真实应用中的复验为准，自动化 QA 通过不代替视觉确认。
- 仓库全量 Ruff 门禁仍受本次改动外的既有告警影响；本次未批量格式化或修复无关文件。

## Story 05：导航账户与服务入口

**验收标准**：

- [x] 左下角用户身份区整体可点击、可聚焦并支持 Enter/Space，hover/focus 反馈由集中主题样式提供。
- [x] 激活后在身份区上方打开非模态账户菜单，不切换开始/工作台/ParaTranz 页面，也不触发工程或任务命令。
- [x] ParaTranz 已连接时菜单显示真实用户名并复用 `SETTINGS_ACCOUNT`；未连接时明确显示“未连接”并复用 `SETTINGS_SERVICES`。
- [x] 菜单始终提供“服务与 API 配置…”和“外观与通用设置…” canonical intent；不显示 Token，不为尚未接入的站点伪造条目。
- [x] 菜单结构可继续增加 Nexus Mods 等真实 provider 条目，长用户名、浅/深主题及屏幕阅读器语义保持稳定。

**文件落点**：`src/transbridge/ui/shell/navigation_rail.py`、`src/transbridge/ui/foundation/visual_style.py`、shell/无障碍/布局相关测试。

**实施步骤**：

1. 将纯展示 `QFrame` 用户区改为保留头像、名称和在线状态布局的语义按钮，并为完整区域提供 tooltip、focus 与无障碍描述。
2. 增加由 NavigationRail 管理的账户菜单，按现有 user projection 更新 ParaTranz 条目文本和目标 intent；所有命令继续走既有 `intent_requested`。
3. 用独立菜单 action 划分 provider 信息、服务连接管理和通用设置，保持未来 provider 扩展点但不提前承诺不存在的登录能力。
4. 补充鼠标/键盘、已连接/未连接、canonical intent、页面不变、菜单位置和主题样式回归。

**测试策略**：聚焦 `test_modern_workbench_visual_shell.py`、无障碍与视觉样式测试，再运行相关 UI 回归和定向 Ruff。

### S05 QA 结果（2026-08-25）

- 账户菜单与视觉样式聚焦测试：`9 passed`。
- shell、无障碍、布局稳定性相关回归：`36 passed`。
- 完整 `tests/ui`：`371 passed, 1 skipped`；6 条 warning 均为既有 `TranslationEntry` 直接字段写入弃用提示。
- 本次 4 个 Python 改动文件的 Ruff check 与 format check 通过；菜单不包含 Token、Nexus Mods 占位或新增网络请求。

## Story 06：工程打开方式与可见进度

**验收标准**：

- [x] 非活动且可用的工程行通过鼠标或键盘打开独立模态弹出页，展示工程信息并提供“在当前窗口打开”和“在新窗口打开”两个完整按钮；活动工程仍直接返回工作台，不可用工程仍不可激活。
- [x] 当前窗口打开继续复用 dirty 决策、`CurrentProjectOpener` 和单一 project-open worker；准备期间开始中心显示不确定进度与真实文案，并禁用冲突入口，所有终态均复位。
- [x] 新窗口打开使用独立 GUI 进程，显式传递 canonical Project 路径；当前窗口工程、dirty、页面和任务不变，启动失败有可诊断提示。
- [x] 带 `--open-project` 启动的 GUI 优先校验并打开显式工程，而不是恢复活动指针；缺省启动行为和冻结态 CLI 入口保持兼容。
- [x] 工作台源解析进度覆盖同步集合落地，不在主线程最后一段昂贵渲染前提前消失；重复工程打开、保存或前台任务冲突不会并发提交。

**文件落点**：新增 `src/transbridge/ui/shell/start_center_projects.py`、`src/transbridge/ui/shell/project_open_choice_dialog.py`、`src/transbridge/ui/shell/project_window_launcher.py`；修改开始中心 facade/controller、`ProjectCoordinator`、GUI/CLI 入口和 MainWindow composition；补充开始中心、后台操作、入口与 launcher 测试。

**实施步骤**：

1. 从已超过责任审查阈值的 `start_center_landing.py` 抽出本地工程面板，并以独立模态弹出页承载工程信息和打开方式选择；面板继续拥有工程行、空状态和细线不确定进度，Landing 保留现有公开信号及测试 compatibility aliases。
2. 将当前窗口请求继续映射到 `PROJECT_OPEN`，由 `ProjectCoordinator` 在 worker 生命周期内驱动开始中心进度、冲突保护和统一复位；把 Workbench 解析进度的隐藏点移动到集合落地之后。
3. 新增纯命令构造和 `QProcess.startDetached()` launcher；Controller 只报告“已请求启动”或错误，不把进程接受伪装成工程加载成功。
4. 沿 CLI → GUI entrypoint → `ui.app` → MainWindow → `ProjectCoordinator.init_workspace()` 传递可选显式工程路径，并在新进程优先走 `prepare_path()` 校验。
5. 覆盖鼠标/键盘菜单、进度终态、并发冲突、独立进程命令、显式启动优先级及默认启动回归。

**测试策略**：先运行开始中心、ProjectCoordinator、CLI/entrypoint 与 launcher 聚焦测试，再运行相关 UI 回归；使用仓库可用 Python 执行 pytest，并以 Ruff check/format 门禁本次文件。正式冻结包的独立窗口启动留给发布冒烟验证。

### S06 QA 结果（2026-08-25）

- 功能聚焦测试：`48 passed`；覆盖打开方式弹出页、键盘激活、独立进程命令、显式启动优先级、进度终态和当前窗口状态隔离。
- 完整 `tests/ui`：`382 passed, 1 skipped`；6 条 warning 均为既有 `TranslationEntry` 直接字段写入弃用提示。
- GUI/CLI 打包合同在显式 `PYTHONPATH=src` 的源码环境下：`7 passed`；本机未安装 `transbridge-mcp.exe`，因此已安装 console-script 冒烟不作为本 Story 成品证据。
- 本次 15 个 Python 改动文件的 Ruff check 与 format check 通过；仓库全量 Ruff 仍有 430 个既有告警、全量 format 仍有 151 个既有未格式化文件，本 Story 未批量修改无关历史代码。
- PyInstaller 成品中的“在新窗口打开”仍需在发布阶段执行一次真实安装包冒烟；当前冻结态命令形态和参数传递已有自动化合同覆盖。

## 依赖顺序

`S01 -> S02 -> S03 -> S04 -> S05 -> S06`

S02 的 landing 组件可与 S01 的 shell 组合并行开发，但集成必须在 S03 前完成；S03 不改变 TaskRuntime 或 Project catalog 权威状态。

## 风险与回退

- **逻辑索引漂移**：WorkspaceShell 内部可增加 landing raw page，但对外继续映射工作台=0、ParaTranz=1；用现有 coordinator 和 shell 测试锁定。
- **恢复竞态**：恢复中禁用切换工程入口，不增加取消语义；失败后恢复入口和其他工程入口重新可用。
- **自定义行交互退化**：工程行必须保留 QListWidget/QAbstractItemView 的键盘、选择和 disabled 语义，不能只做可点击装饰 QWidget。
- **文件职责膨胀**：landing 视觉组件独立成模块，MainWindow 只做 composition；不把 projection 或 task 规则放入 View。
- **跨进程语义**：独立窗口不共享内存上下文，但最后成功激活的进程会更新全局活动工程指针；首期不承诺同一工程跨进程编辑锁。
- **虚假进度**：工程准备没有稳定总量，使用不确定进度和阶段文案；冻结包命令形态以构造测试锁定，并在发布阶段做成品冒烟。
- **回退**：可恢复旧 landing 布局和外层页面组合；回退只影响展示，不迁移或删除工程、任务、配置和用户数据。

## 明确假设

- 用户确认的最终效果图约束信息结构、密度和层级，不要求实现其中被明确排除的虚构数据能力。
- 第一版显示 catalog 中全部本地工程，活动工程优先且其余按既有名称顺序；未来若新增 last-opened 合同再独立升级为最近工程。
- 开始中心任务横幅只承诺真实可恢复数量；最近失败和完整诊断继续在权威任务中心查看。
