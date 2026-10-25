# 下拉列表与 AI 多选筛选菜单限高修复

- **日期**：2026-10-23
- **Epic**：`ui-layout-stability`
- **Story**：S03 ParaTranz 管理页稳定、S04 工具与操作窗口稳定、S05 QA 与回退门禁的后续修复
- **关联计划**：[动态内容布局稳定](../../../../plans/ui-layout-stability/plan.md)
- **关联能力**：[UI Foundation](../../../../plans/ui-foundation-framework/plan.md)、[AI 工作流与日志窗口](../../../../plans/ai-workflow-presets/plan.md)
- **状态**：下拉限高修复完成；相关回归 224 项通过，2 项既有模式切换布局断言失败

## 问题与结果

用户反馈 LLM 日志选择器有 78 个日志时，下拉列表接近屏幕全高，并要求检查其他下拉列表。排查发现普通 `QComboBox` 没有统一的弹出高度策略；菜单式弹出样式还会忽略 `maxVisibleItems`。文件、译名方案和 `QInputDialog` 内部选择框的离屏回归复现了 788 px 的列表视口，超过 12 行所需高度。

应用统一样式现在把普通下拉列表限制为最多 12 行，选项少时按实际数量缩短，超出后滚动。规则随主题样式应用，覆盖已有和新建选择框，包括日志、文件、译名方案及可编辑插件选择框。

AI 翻译范围的状态/标记/分类筛选和混合规则多选使用另一条 `QMenu` 路径；78 项时同样能复现接近屏幕全高的菜单。它们改用共享的限高滚动容器，选项区最多容纳 12 行，并受屏幕可用高度一半的约束，重置入口留在选项区外。保留选择、键盘定位、重置与选中状态同步。

## 逐文件变更

- **修改** `src/transbridge/ui/foundation/visual_style.py`：在 `compile_application_stylesheet()` 中为全部 `QComboBox` 增加 `combobox-popup: 0` 和 `qproperty-maxVisibleItems: 12`，从统一样式解决原生菜单式弹出忽略行数限制的问题，避免每个窗口重复配置。
- **新增** `src/transbridge/ui/tools/ai_translator/_filter_popup.py`：`FilterPopupMenu` 以 `QScrollArea` 承载筛选按钮；`_resize_options()` 根据实际字号计算行高、预留滚动条宽度并限制高度；`_navigate()`、`keyPressEvent()` 和 `eventFilter()` 支持上下键、Home/End、回车、Esc 与焦点滚动，首项向上可回到重置入口。
- **修改** `src/transbridge/ui/tools/ai_translator/scope_view.py`：`_filter_menu()` 使用 `FilterPopupMenu`；`_new_button()` 把动态筛选按钮加入其滚动内容区，替代每个按钮单独占据一个菜单 action 的旧结构。
- **修改** `src/transbridge/ui/tools/ai_translator/_rule_editor_widget.py`：仅本次 `_CheckableFilterButton` 的菜单和清空逻辑属于下拉修复。选项继续由 `QAction` 保存选中值，改由 `FilterPopupMenu.add_action_option()` 展示；`_clear()` 阻塞按钮自身的对外信号，而不阻塞 action 的状态同步，避免重置后显示仍然选中。同文件的默认规则工厂和 `reset_to_default()` 改动已记录于[混合默认规则修复](../../ai-workflow-presets/story-01-to-03-implementation/2026-10-23-003-mixed-default-action-rules.md)，不重复归入本次实现。
- **新增** `tests/ui/foundation/test_combo_popup_height.py`：覆盖实际文件/译名方案/可编辑插件选择框和 `QInputDialog`，分别装载 3、78 个选项，验证高度、滚动条、最后一项可选、窗口尺寸不变、自由输入及明暗主题切换后已有/新建控件仍受限。
- **新增** `tests/ui/tools/test_filter_popup_height.py`：覆盖范围和规则两类菜单、3/78 项、9/14 pt 字号；验证限高、最后一项鼠标与键盘选择、重新打开、重置和清空后的可见选中状态。
- **新增** `tests/ui/tools/test_llm_log_popup.py`：创建 78 个模拟日志，在两种字号/窗口尺寸及强制菜单式原生样式下，验证最多 12 行、滚动与最后一项选择、正文加载正确、重新打开仍限高。最终版本使用真实应用主题夹具验证统一规则。
- **修改** `tests/ui/tools/test_ai_mode_switching.py`：把混合模式预期任务数从 0 更新为 200，使旧测试与此前已完成的默认动作规则修复一致；保留其余模式切换及布局断言。
- **新增本记录并更新索引**：`docs/changelogs/INDEX.md` 增加入口及对应 Epic 链接；`plans/INDEX.md` 仅在 `ui-layout-stability` 条目追加本记录链接，不修改原计划状态或统计。

## 范围与归并

- 最初对 `_llm_log_viewer.py` 单独设置行数和局部样式，扩展排查后已移入应用统一样式并删除局部重复配置。最终该文件相对 HEAD 无净差异，日志窗口通过继承统一样式获得限高，不把原有日志扫描、自动刷新或懒加载逻辑算作本次改动。
- 本记录依据当前 Git 差异、未跟踪新文件内容和本任务执行证据；不归入工作区其他同步、上传、后台生命周期或诊断日志改动。
- 无配置、翻译文件、日志落盘格式或数据迁移。修改作用于源码版 UI；本次未构建安装包。

## 验证证据

环境：Windows、仓库现有 Python 3.12.12 / uv 环境、Qt offscreen；没有真实 LLM 调用。以下是修复阶段实际执行的结果，本次增量记录阶段未重跑业务测试。

### 复现与专项回归

- 修复前执行 `uv run --no-sync pytest tests/ui/foundation/test_combo_popup_height.py -q`：退出码 1，4 failed、5 passed；失败涉及 78 项文件/方案/输入对话框及主题切换后的弹出高度。
- 多选菜单修复前执行 `uv run --no-sync pytest tests/ui/tools/test_filter_popup_height.py -q`：退出码 1，4 failed；78 项菜单分别高 789/800 px，短列表用例尚无滚动容器。
- 修复过程专项组合：

```powershell
uv run --no-sync pytest tests/ui/tools/test_filter_popup_height.py tests/ui/tools/test_ai_rule_editor.py tests/ui/foundation/test_combo_popup_height.py tests/ui/tools/test_llm_log_popup.py -q
```

结果：退出码 0，**27 passed**，3 条依赖弃用警告。随后补齐清空时 action 状态同步，最终版本再次通过下述联合回归中的全部下拉专项用例。

### 最终联合回归

```powershell
uv run --no-sync pytest tests/ui/foundation tests/ui/tools/test_llm_log_popup.py tests/ui/tools/test_live_llm_logs.py tests/ui/tools/test_filter_popup_height.py tests/ui/tools/test_ai_rule_editor.py tests/ui/tools/test_ai_mode_switching.py tests/ui/tools/test_unified_task_config.py tests/ui/tools/test_layout_stability.py tests/ui/tools/test_mixed_default_rules.py tests/ui/workbench/test_terminology_profile_switching.py tests/ui/tools/terminology/test_term_editing.py tests/ui/test_paratranz_layout_stability.py tests/ui/test_paratranz_readable_controls.py tests/ui/test_dialogue_input_visuals.py -q
```

结果：退出码 1，**224 passed、2 failed、3 warnings**。失败均为 `tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors` 的 `size1-14`、`size2-10` 参数：字段纵向坐标分别有 22 px、15 px 的变化。

为区分本次改动影响，曾在独立 Python 进程内用 `git show HEAD:<path>` 取回 `_filter_menu()`、`_new_button()` 和 `_CheckableFilterButton` 的旧定义，仅在内存替换后运行该测试文件，工作区业务文件未回退。使用 `.tmp-dropdown-c77c/baseline` 作为 pytest 临时目录的对照仍复现完全相同的两项坐标失败；当时另一项混合任务数旧断言也失败，后者已按默认规则修复更新为 200。该对照不表示所有历史行为已完成验证。

### 静态与视觉检查

```powershell
uv run --no-sync ruff check src/transbridge/ui/foundation/visual_style.py src/transbridge/ui/tools/ai_translator/_filter_popup.py src/transbridge/ui/tools/ai_translator/scope_view.py src/transbridge/ui/tools/ai_translator/_rule_editor_widget.py tests/ui/foundation/test_combo_popup_height.py tests/ui/tools/test_filter_popup_height.py tests/ui/tools/test_llm_log_popup.py tests/ui/tools/test_ai_mode_switching.py
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
git -c core.safecrlf=false diff --check
```

- 本次 8 个生产/测试文件 Ruff check：退出码 0。
- 最后一次全仓库 Ruff check：退出码 1，`tests/ui/tools/test_legacy_ai_worker_lifecycle.py:17` 的 `_translation_worker` 导入未使用，属于本次范围外文件；这是当时工作区的检查结果，不宣称后续其他工作线的状态。
- 全仓库 Ruff format check：退出码 0，2003 个文件格式通过。
- Git diff check：退出码 0。
- 使用应用主题和微软雅黑字体生成并检查两类多选菜单的 14 pt 离屏截图，确认限高、滚动条和选中外观；这不等同于用户实窗验收。
- `.tmp-dropdown-c77c` 内的基线对照目录和两张截图在验证目录归属后已清理。

## 遗留与未执行项

- 两项既有模式切换布局断言失败仍保留，本次未修改对应布局实现或弱化断言。
- 全仓库 Ruff 在上述检查时仍有一处范围外未使用导入；未混入本次修复。
- 未运行全仓库测试、真实桌面交互验收或安装包构建；未提交、推送 Git，也未重启用户正在运行的程序。
