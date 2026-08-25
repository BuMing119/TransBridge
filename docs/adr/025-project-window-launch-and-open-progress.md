# ADR-025：工程独立窗口启动与可见打开进度边界

- **状态**：提议
- **日期**：2026-08-25
- **对应需求**：[FR28.6、FR19.4、NFR1.5～NFR1.7](../requirements.md)
- **关联 ADR**：[ADR-016](016-modular-monolith-application-composition.md)、[ADR-018](018-project-session-persistence-v2.md)、[ADR-021](021-ui-presentation-modularization.md)、[ADR-022](022-modern-workbench-visual-composition.md)

## 背景与约束

开始中心已经能从只读 Project catalog 打开其他工程，但工程准备和激活期间的进度当前只投射到 Workbench 内部的 `ProgressView`；用户停留在开始中心时看不到该控件，因此后台工作虽然已使用 `ApiWorker`，体验仍表现为无反馈卡顿。工程行也只有一个直接打开动作，无法保留当前工程并把另一工程放到独立窗口。

本决策必须满足以下约束：

- 当前窗口切换继续复用 `CurrentProjectOpener.prepare_path()/activate()` 和 FR19.4 dirty 决策，不在 View 中读写 Project。
- 新窗口不能在同一个 `QApplication` 和同一个 `AppRuntime` 中复制第二套 `MainWindow` 状态；不同窗口需要独立 Qt、RuntimeContext、worker 和关闭生命周期。
- PyInstaller 入口实际执行 `transbridge.main -> transbridge.cli:main`，源码/安装态入口同样由 CLI 选择 GUI adapter，因此显式工程路径必须沿这条入口链传递。
- `start_center_landing.py` 已超过 500 行责任审查阈值；新的工程菜单、进度和列表交互不得继续堆入该文件。
- 工程准备没有稳定的总步骤或字节分母，不能显示虚构百分比；进度必须使用不确定模式和真实阶段文案。

## 决策

### 1. 新窗口使用独立 GUI 进程

“在新窗口打开”通过 Qt `QProcess.startDetached()` 启动独立进程，不在当前进程内构造第二个 `MainWindow`。源码/安装态以当前 Python 启动 `-m transbridge.cli gui --open-project <path>`；PyInstaller 冻结态以当前可执行文件启动 `gui --open-project <path>`。参数使用独立 argument list 传递，不拼接 shell 字符串。

`transbridge.cli`、GUI entrypoint、`ui.app.main()` 和 `MainWindow` 增加可选的显式工程路径端口。该参数缺省时保持现有启动语义；存在时，`ProjectCoordinator.init_workspace()` 跳过全局活动指针的自动恢复，优先通过 `CurrentProjectOpener.prepare_path()` 校验并打开指定工程。

### 2. 当前窗口继续使用单一权威打开流水线

开始中心只发出“当前窗口打开路径”或“新窗口打开路径”两种 UI intent。当前窗口请求仍由 `StartCenterController -> PROJECT_OPEN -> ProjectCoordinator` 进入现有生命周期；dirty 保存/丢弃/取消、repository 归属、schema、活动版本和源基线校验均保持唯一实现。

已有 project-open worker、save worker 或 foreground worker 时拒绝第二次当前窗口切换并提供可见提示。失败不得改变原活动工程；成功后才进入工作台。

### 3. 工程面板与打开方式弹出页成为独立 View 组件

从 `start_center_landing.py` 抽取本地工程列表、工程行、空状态和打开进度为 `start_center_projects.py`，并由独立 `project_open_choice_dialog.py` 呈现打开方式。组件只消费 recent-project view state 并发出稳定信号，不依赖 catalog、ProjectCoordinator、进程或 repository。

非活动可用工程的鼠标或键盘激活打开专用模态弹出页，展示所选工程名称和路径，并以两个完整按钮提供“在当前窗口打开”和“在新窗口打开”；活动工程继续直接返回现有工作台，不可用工程保持禁用。弹出页不得把默认打开方式写成隐式全局偏好。

### 4. 开始中心与工作台分阶段显示进度

当前窗口工程准备期间，工程面板显示不确定 `QProgressBar` 和“正在校验并加载〈工程名〉…”等真实文案，并禁用会产生冲突的工程和建项入口。完成、业务失败、线程异常后必须统一复位。

工程准备成功并切换到 Workbench 后，源插件解析继续使用既有 Workbench `ProgressView`。其进度只在昂贵的 `AppContext.add_slot()` 及同步渲染完成后隐藏，避免在主线程最后一段工作开始前提前消失。

### 5. 新窗口启动只确认进程接受，不伪装工程已打开

`startDetached()` 成功只表示新进程已被操作系统接受。当前窗口显示“已请求在新窗口打开”而不宣称目标工程加载完成；新进程自行显示开始中心进度并处理校验失败。启动失败或异常由当前窗口显示诊断，且不改变当前工程、dirty、页面或任务。

## 关键契约

- Project catalog 路径只作为新进程输入；新进程必须再次执行 canonical path、schema 和 repository 归属校验。
- 显式启动路径优先于 `active-project.json`；参数缺省时现有自动恢复完全不变。
- 活动工程行不提供新窗口菜单，避免从当前窗口直接复制同一工程编辑上下文。
- 当前窗口加载期间只允许一个 project-open worker；进度可见性与 worker 终态一一对应。
- View 不调用 `QProcess`，Controller 不解析 Project 文件，launcher 不修改 GUI/application 状态。
- 不新增依赖、不使用 shell 命令字符串、不通过环境变量或临时文件传递工程路径。

## 备选方案

### A. 同一进程创建多个 MainWindow

不采用。它会在一个 `QApplication` 内复制 AppRuntime、全局 worker bus、TaskManager、主题和关闭协议，现有组合根并未提供多 owner 隔离证明，故障影响面显著大于独立进程。

### B. 在工程行内常驻两个打开按钮

不采用。它会显著增加每行密度和误触面积，并使活动、不可用和普通工程行的结构不一致；专用弹出页能在不挤压列表的前提下呈现工程上下文和两个完整选项。

### C. 显示估算百分比

不采用。项目来源数量、插件大小、解析与主线程渲染成本没有可比较的统一分母；不确定进度条配合阶段文案更诚实。

## 影响与风险

- 多进程共享同一持久化根，最后成功激活的进程会更新全局活动工程指针；已打开窗口的内存上下文不会因此切换。首期只从非活动工程提供新窗口入口，不声称具备跨进程同一工程编辑锁。
- Windows/PyInstaller 与源码命令形态不同，必须以纯命令构造测试和冻结态分支测试锁定，正式发布仍需要成品启动冒烟。
- `AppContext.add_slot()` 触发的主线程大表渲染仍可能占用短暂时间；本轮保证进度不提前消失并减少无反馈时间，不把表格模型迁移纳入工程打开功能。

## 迁移与回退

1. 新增可选启动参数，缺省行为保持兼容。
2. 抽取工程面板和打开方式弹出页，但保留 `StartCenterLanding` 的公开信号和 `StartCenterWidget` compatibility aliases。
3. 接入当前窗口进度，再接入独立进程 launcher。
4. 回退时可移除新窗口 action 和启动参数；现有 `PROJECT_OPEN`、自动恢复、catalog 与持久化 schema 均无需迁移。
