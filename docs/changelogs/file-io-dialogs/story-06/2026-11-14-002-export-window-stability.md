# S06 跟进：导出窗口归属与布局稳定

- 日期：2026-11-14。
- Epic/Story：file-io-dialogs / S06；关联[实施计划](../../../../plans/file-io-dialogs/plan.md)。
- 依据：用户报告点击导出后窗口容易被其他页面遮住，窗口宽高和进度条位置反复跳动。延续 bm-pilot，沿用已授权的 GPT-6.1 Sol/high 子 Agent 完成布局切片，主会话处理窗口归属并集成验证。
- 归属：与本轮开始保存的 UI、测试和计划副本比对；此前路径存储、插件复制导出及其他未提交内容不算本轮增量。不提交 Git，不操作用户真实插件。

## 原因与行为

生产入口未给导出窗口传入主窗口，协调器又强制非模态；缺失来源的文件选择框却由主窗口持有。现在导出窗口归属主窗口，采用窗口级模态，并继续用非阻塞的 `show()`；补选来源的对话框归属当前导出窗口。没有全局置顶标志或反复抢焦点。

原界面在不同阶段显示和隐藏进度、状态和详情区域，导致布局重新分配空间。重建表单时，执行窗口基类还会短暂显示导出不需要的摘要区，在小窗口实测导致高度从 310 增长至 339 像素。现在从摘要显示入口消除该瞬时变化，选项区域可滚动，状态与技术详情共用预留区域，进度槽和按钮保持位置；窗口仍可由用户缩放。

## 逐文件增量

- `src/transbridge/ui/main_window.py`：构建操作 facade 时传入主窗口；仅修改组合接线，不向大类添加业务责任。
- `src/transbridge/ui/operations/production.py`：增加可选 parent 并传给 facade。
- `src/transbridge/ui/operations/facade.py`：保存默认窗口 owner，传入协调器；显式格式的前置来源补选使用主窗口，已经打开窗口的准备回调使用当前导出窗口。
- `src/transbridge/ui/operations/coordinator.py`：使用默认 owner 创建窗口，传递当前窗口给来源准备回调，移除强制非模态设置，保留窗口自身策略及既有生命周期清理。
- `src/transbridge/ui/coordinators/write_input_coordinator.py`：来源补选接受独立的对话框 parent，后台准备仍使用原 host，避免混淆 UI 归属与工程状态。
- `src/transbridge/ui/operations/execution_dialog.py`：新增默认开启的 `show_plan_summary` 展示策略；其他执行窗口保持原有摘要行为。
- `src/transbridge/ui/operations/write_dialog.py`：设置窗口级模态并关闭摘要策略；接入滚动选项和预留反馈区，所有执行阶段复用同一布局。
- `src/transbridge/ui/operations/write_window_layout.py`（新增）：独立负责选项滚动区、状态/详情切换区和隐藏控件的占位，不通过反复 resize 或固定整个窗口掩盖变化。
- `tests/ui/operations/test_write_window_ownership.py`（新增）：真实 Qt owner/transient parent、窗口级模态、无全局置顶、事件循环运行、关闭释放；实际来源迁移后补选一次并完成两插件导出，验证 picker owner。
- `tests/ui/operations/test_write_window_layout.py`（新增）：应用主题下检查预检、覆盖重试、执行、成功/失败、详情、再次导出及最小窗口重建的几何稳定性，验证长错误与选项可滚动。
- `plans/file-io-dialogs/plan.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md`：更新本次跟进与直接相关索引。

## 验证

环境为现有 uv Python 3.12 环境，Qt 使用 `QT_QPA_PLATFORM=offscreen`；缓存与测试数据位于任务独占的 `.tmp-export-window`。SQLite 硬链接夹具在 Windows 受限沙箱内不适用，相关测试通过批准的沙箱外执行，仅使用临时合成数据。

- 聚焦生产 facade/异步协调器：5 passed；启动/桌面导航：16 passed；几何/归属/多方案来源复用联合：13 passed。
- 首次扩大回归：542 passed、1 failed，失败为新加入的小窗口重建几何用例，随后定位并修复上述瞬时摘要显示。
- 最终 `uv run pytest tests/ui/operations tests/integration/gui/test_ui_foundation_startup.py tests/ui/test_desktop_task_navigation.py -q --basetemp=.tmp-export-window/pytest-final`：**543 passed**；仅既有 SWIG 弃用提示，无排除项。
- 最后调整首次打开的选项视口建议高度后，`uv run pytest tests/ui/operations/test_write_window_layout.py tests/ui/operations/test_operation_plan_layout_stability.py -q`：**11 passed**；全仓 Ruff 与格式检查再次通过。
- `uv run ruff check src tests`：通过。
- `uv run ruff format --check src tests`：通过，2659 文件。
- 实际 Microsoft YaHei 字体及应用主题离屏截图已检查：单文件初始 580×439，选项无需滚动；检查、执行、成功、失败、详情阶段尺寸相同，进度槽 y=365、导出按钮 y=396。主动缩至 580×310 后重建方案表单不再撑高，进度槽 y=236、导出按钮 y=267。
- 最终实际两插件、两方案首次打开及开始检查截图均为 580×531，进度槽 y=457、导出按钮 y=488，输出目录和格式首次可见，选项横纵滚动范围均为 0。

## 边界

无存储格式或 CLI/MCP 迁移。只验证 Qt owner、模态策略、事件循环和离屏几何，未进行 Windows 桌面切换/遮挡人工验收；未运行全仓测试或发行构建。本次不新增原生崩溃原因结论。临时截图及合成插件在完成核对后随本次任务目录清理，保留用户既有目录。
