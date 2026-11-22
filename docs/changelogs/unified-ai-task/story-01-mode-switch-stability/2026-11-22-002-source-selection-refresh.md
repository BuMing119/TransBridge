# AI 翻译任务的来源选择与术语摘要读取

2026-11-22 · [统一 AI 翻译任务，Story 01](../../../../plans/unified-ai-task/plan.md)

## 变更

- `ai_translator_window.py` 合并同一轮 Qt 事件中的来源选择，统一刷新筛选项、来源数量、请求估算和启动条件。取消窗口时停止尚未执行的刷新。范围预设、状态、分类及标签变化也复用估算中的预检。
- `task_scope.py` 在候选词条还包含缺少原文的条目时记录对应来源。`_window_actions.py` 复用该结果，只对已勾选来源提示补充原文。恢复任务继续检查当前工程的原文。
- `project_terminology_controller.py` 仅在项目术语面板可见时调度摘要读取，开始读取前再次检查可见性。可见时仍响应工程、版本、方案和窗口激活事件。
- `test_ai_mode_switching.py` 验证连续勾选只筛选一次，并覆盖取消待刷新任务。`test_ai_project_terminology.py` 验证隐藏时不读取、显示后获取摘要，以及关闭时取消排队刷新。

## 验证

Windows、Python 3.12、Qt offscreen。四个内存插件分别含 13,317、7,648、4,123 和 3,941 条合成词条，其中未翻译 917、483、349 和 259 条。默认选中第一个插件，模拟打开、全选、处理 Qt 事件、关闭。修改前后各运行五次，未连接远程服务。

- 首次显示耗时中位数由 89 ms（89–290）降至 79 ms（77–279）。
- 全选到统计显示完成的中位数由 156 ms（149–164）降至 106 ms（104–116）。两组关窗耗时均为 2 ms。
- `uv run --offline --no-sync --no-cache pytest` 运行 AI 模式切换、项目术语、启动保护、统一任务配置、混合预检、AI 原文范围、统一运行、来源列表及窗口组件测试，共 101 项通过。
- `ruff check src tests` 与 `ruff format --check src tests` 通过。

真实工程的磁盘读取耗时尚未测量。已显示术语页并开始的快照读取可以在关窗后完成，结果会被丢弃。扩大验证遇到插件写回用例 `test_plugin_write_binds_only_the_current_operation[wrong]` 等待前台任务结束超时，换回修改前的四个 AI 模块后独立复现。
