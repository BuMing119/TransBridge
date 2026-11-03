# 多窗口当前工程标记与导航修复

- 日期：2026-11-03
- 归属：maintenance / multi-window-project-isolation（非计划维护）
- 状态：实现、相关自动化验证、源码桌面双进程验证和增量记录完成；未提交 Git。

## 问题与根因

用户报告：启动自动恢复 A，在新窗口打开 B，关闭其中一个窗口后，剩余窗口仍显示 A 的工作台，且无法打开 B。

新窗口使用独立进程，但工程目录查询的 `active` 来自共享的 `active-project.json`。原 `StartCenterController._recent_project_projection()` 直接使用这个标记，导致 B 打开后，A 窗口刷新列表时也把 B 标成“正在使用”。`StartCenterProjectsPanel._activate_project()` 对当前项目直接返回已有工作台，不执行项目打开，因此点击 B 会返回 A 的工作台。此路径可解释所述现象；不能据此反推用户当时具体关闭了哪个窗口。

## 逐文件变更

- 修改 `src/transbridge/ui/shell/start_center_controller.py`：`_recent_project_projection()` 使用本窗口 `context.active_project_id` 与列表项目身份比较，生成 `RecentProjectViewState.active`；补充共享启动指针与窗口工作台状态的区别说明。项目目录仍提供名称、路径、可用性与原因。
- 修改 `tests/ui/test_start_center_guided_project.py`：`test_recent_projects_prefer_v2_read_only_catalog()` 的窗口上下文 fixture 明确提供当前项目身份。
- 新增 `tests/ui/test_start_center_window_isolation.py`：两个独立持久化服务实例共用测试目录，验证不关闭、关闭 A、关闭 B 后各存活窗口的当前项目标记和项目点击分流；增加空窗口不误用全局活动标记的用例。通过 Qt 延迟删除事件清理对话框和面板，避免测试退出时对象生命周期异常。
- 新增本增量记录；修改 `docs/changelogs/INDEX.md`，添加本记录入口，不改写其他增量。

## 自动化验证

本次任务中已执行并通过，收尾复核时复用这些结果；本轮记录阶段未改业务代码、未重复运行测试。

```text
uv run pytest tests/ui/test_start_center_window_isolation.py tests/ui/test_start_center_guided_project.py tests/ui/test_project_window_launcher.py tests/ui/test_project_open_guards.py -q
```

结果：退出码 0，47 passed；3 条 SWIG 类型 DeprecationWarning。新增回归在修复前出现 3 个失败；修复后四个新增用例全部通过。首次验证中 Qt 测试清理导致提前退出，补齐延迟销毁处理后上述联合运行正常结束。

```text
uv run ruff check src tests
uv run ruff format --check src tests
git diff --check
```

结果：均通过；格式检查覆盖 2352 个文件。Git 输出换行转换提示，无空白错误。普通沙箱中的 uv 解释器缓存写入受限，经权限提升后使用仓库已有 Python 3.12.12 环境完成验证。

## 真实桌面验证

使用当前源码和真实可见 Qt 窗口，通过 Windows computer-use 操作。临时数据和 Qt 设置隔离在本任务新建目录，测试项目 A/B 分别有 `QA_A` / `QA_B` 插件和不同原文、译文。新窗口均从项目列表的“在新窗口打开”启动；通过进程命令行确认 A/B 是独立 GUI 进程。

1. 第一轮：无指定项目启动，自动恢复 A；从 A 新开 B；正常关闭 A，确认只剩 B 且内容仍为 B；从项目列表在当前窗口完成 B → A → B 往返切换。
2. 第二轮：显式启动 A，从界面新开 B；正常关闭 B，确认只剩 A 且内容仍为 A；重新进入项目列表刷新后，B 为“可打开”、A 为“正在使用”；完成 A → B → A 往返切换。
3. 每次切换核对插件、词条 key、原文与译文，未发现串项或无法打开。测试日志搜索 `ERROR|CRITICAL|Traceback|FAILED` 无匹配。
4. 所有测试窗口正常关闭，进程查询确认测试进程退出；本任务临时目录 `.tmp-multiwindow-ui-20261103` 已删除，未保留启动隔离脚本、测试项目和临时日志。

## 兼容性与验证边界

- 不修改项目文件格式、公共接口或全局启动恢复语义，无需数据迁移。
- 自动化测试使用同进程内独立服务实例；真实桌面验证另行覆盖独立进程和正常关闭流程，两者不混称。
- 未重新运行全库 pytest：变更限于开始中心的展示与导航判断，已完成相关 47 项回归和双进程桌面验证。
- 未构建或验证打包安装版；不将源码桌面结果等同于发行包验收。
- 未新增 ADR、plan 或 Story 文档：本次为局部行为修复，采用维护归属；仓库其他未提交工作不属于本记录。
- 遗留修复项：无。发行包验证不在本次完成声明内。
