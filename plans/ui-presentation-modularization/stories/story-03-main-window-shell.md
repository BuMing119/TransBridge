# Story-03：MainWindow 壳化与顶层协调器

- **所属 Plan**：[UI 展示层模块化与上帝窗口拆分](../plan.md)
- **状态**：已完成（2026-08-19）
- **优先级**：P0
- **前置依赖**：S01、S02
- **下游**：S04、S06、S07；为工具窗口和 Workbench 提供公开壳端口

## 目标

保留 `MainWindow` 作为稳定应用入口，把菜单、状态、工具、关闭和三类业务编排移入有界对象，使它成为真正的 composition shell。

## 原始验收标准

- [x] `transbridge.ui.main_window.MainWindow` 与 `_AutoSaveManager` 等已用公共符号路径保持兼容。
- [x] MainWindow 只负责顶层布局/导航、feature composition、公共消息和单一关闭协议。
- [x] 菜单/Action、状态呈现、工具窗口复用、geometry/close 分别有明确 owner。
- [x] 解析/迁移、上传下载写回、项目/版本/快照分别进入窄 coordinator；每个 intent 最多提交一次 command。
- [x] coordinator 通过公开 ViewPort/use-case port 工作，不读 `_step2._table`、`_worker` 等私有状态。
- [x] 创建/关闭 100 次无残留订阅、timer、worker 或 deleted QObject 回调。

## 当前职责与目标归属

- `_init_menu()`/`_init_shortcuts()` → `MenuBuilder`，产出 `MainWindowActions`；不直接执行业务。
- `_ApiStatusIndicator`/HTTP callbacks/`show_message()` → `StatusPresenter`。
- `closeEvent()`、background close、geometry/QSettings、`_AutoSaveManager` ownership → `WindowLifecycle`；兼容符号仍从原模块导出。
- AI/字典/FOMOD/Smart Assistant 创建与复用 → `ToolWindows`。
- `_run_parse_*`、migration → `ParseCoordinator`。
- `_on_*upload/download/write`、`_op_run_worker()` → `OperationCoordinator`。
- workspace/project/variant/snapshot/import/export → `ProjectCoordinator`。

## 计划端口与顺序

- `WorkbenchShellPort`：当前 collection/selection/progress/locate 等公开操作，禁止暴露具体 Step2 widget。
- `ProjectBarPort`：render project/variant list、busy/error；不暴露 `_project_bar`。
- `ToolHostPort`：top-level parent、show/reuse/close；工具内部业务由自身 composition 管理。
- `CloseParticipant.prepare_close()/close()`：生命周期协调；UI subscription 与全局 Task owner 分离。

```text
QAction/user intent -> one coordinator method -> application use case/task
projection/result -> coordinator -> narrow ViewPort render
closeEvent -> lifecycle asks participants -> persist window state -> detach -> accept/defer
```

## 实施步骤

1. 通过 characterization 明确每组方法读写的字段、signal 和错误消息；新增窄 shell ports，先由旧 MainWindow 实现。
2. 抽 `MenuBuilder`，MainWindow 仍连接 action 到旧 handler，证明菜单/快捷键等价。
3. 抽 StatusPresenter、ToolWindows 和 WindowLifecycle；集中 owner 和 close 顺序。
4. 依次迁 Parse、Operation、Project coordinator；每迁一组即切换 action、比较 command 序列、删除原分支。
5. 让 MainWindow 只创建/持有 feature facade 与 coordinators；禁止新建总控 `MainWindowController`。
6. 保留旧 import 符号的薄重导出，补 public import tests；运行 100 次创建/关闭。

## 边界与错误

- 初始化中途失败须关闭已创建 Binding/participant，不留下半连接窗口。
- close 时运行任务遵循现有 TaskRuntime 策略；UI 只能解绑自己的观察者，不能清空全局任务。
- autosave debounce、手动保存、background close 的顺序和“保存失败是否允许关闭”保持基线。
- ToolWindows 只负责窗口实例复用和 parent，不缓存工具内部业务状态。
- 一个 intent 若 coordinator 抛错，不得由 MainWindow 再次提交或重复弹同一错误。

## 文件变更

- 修改 `src/transbridge/ui/main_window.py`
- 新增 `src/transbridge/ui/shell/{menu_builder,status_presenter,window_lifecycle,tool_windows}.py`
- 新增 `src/transbridge/ui/coordinators/{parse_coordinator,operation_coordinator,project_coordinator}.py`
- 修改/新增 `tests/ui/test_main_window_*.py` 与 public import/lifecycle tests

## 测试与建议命令

- `pytest tests/ui -k "main_window or background_gui_operations" -q`
- `pytest tests/ui/characterization/test_main_window_contract.py -q`
- 运行 S01 的窗口打开、close、heartbeat/RSS case。

## 风险与回退

项目/Variant 路径最复杂，按 coordinator 单独切换。每组旧 handler 在等价测试通过前保留，回退只恢复 action→旧 handler 绑定；禁止旧新两路同时提交 command。

## 未决问题

- `_AutoSaveManager` 最终是否正式公开不在本 Story 决定；本次只保障事实兼容。
- `QSettings` geometry 后续可由 FR24 统一配置评估，本 Story不改 key/语义。
