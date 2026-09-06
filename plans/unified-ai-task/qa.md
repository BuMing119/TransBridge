# 统一 AI 任务 QA

日期：2026-09-06。验收范围为统一入口、四模式、来源隔离、版本提交、配置草稿及生命周期。

## 覆盖

- 单/多插件使用同一任务范围、预检、冻结配置、并发预算和执行器。
- 跨插件相同 legacy ID 的 V2 EntryKey 隔离；混合串行/并行的阶段等待与异常传播。
- 取消、失败、预览取消、版本变化不提交；全任务成功才一次提交，失败插件重试保留成功副本。
- 真实版本持久化、执行前与保存后快照、GUI 线程提交、后台报告生成、重复/迟到完成信号。
- 报告明确区分接受、拒绝、失败和待审；接受原文不误计为拒绝。
- 草稿不自动修改全局或自定义预设；显式预设保存不泄露服务凭据与本地文件路径。
- 入口兼容、布局/主题、源模板、本次统计与实际候选一致。

## 验证记录

- 最终综合命令：`uv run pytest tests/ui tests/contracts/translation tests/application/translation tests/contracts/projects/test_authoritative_mutation_paths.py -q -k "not test_task_center_escape_never_stops_task_and_stop_description_names_object_and_recovery and not test_progressive_menu_bar_keeps_one_account_action_and_collapses_after_pointer_leaves"`：**1021 passed、1 skipped、2 deselected**（19.11 秒）。2 项隔离原因如下。
- `uv run pytest tests/ui/tools -q`：首轮 301 passed；随后补充异常结果、报告和来源失效测试，纳入最终综合回归。
- `uv run pytest tests/ui/test_background_gui_operations.py tests/ui/test_command_palette_help.py tests/ui/test_main_window_shell.py tests/ui/test_workbench_story07.py tests/ui/ux/test_current_user_journeys.py tests/contracts/projects/test_authoritative_mutation_paths.py -q`：69 passed。
- `uv run pytest tests/contracts/translation tests/application/translation -q`：157 passed。
- `uv run pytest tests/ui/foundation/test_locale_service.py tests/ui/test_main_window_shell.py -q`：21 passed。
- `uv run ruff check src tests`、`uv run ruff format --check src tests`：通过。
- `git diff --check`：通过。

## 扩大回归例外

- `test_task_center_escape_never_stops_task_and_stop_description_names_object_and_recovery`：整组及单项执行均提前终止 Qt 测试进程，未产生 Python traceback；对应 task_center 生产模块未在本任务修改。最终综合回归隔离此项。
- `test_progressive_menu_bar_keeps_one_account_action_and_collapses_after_pointer_leaves`：整组运行出现收起时序断言失败，单独复跑所在模块通过；最终综合回归隔离此项，保留单独 21 项通过证据。
- 未执行真实付费 LLM、远端 ParaTranz、打包安装和人工联机验证；离线测试不代表这些环境已验证。

## 视觉验收

实际 Qt 窗口用构造示例数据渲染。确认左侧来源列表、四模式、四配置页、公共范围、精确统计及统一开始按钮均存在。
离屏平台初次缺少字体，显式加载本机微软雅黑后重渲染确认中文显示正常；未改变生产字体逻辑。
任务专用渲染脚本在结束前清理，实际截图保留在本任务可视化目录。
