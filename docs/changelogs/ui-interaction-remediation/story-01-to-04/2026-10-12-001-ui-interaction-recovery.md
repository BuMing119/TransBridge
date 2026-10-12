# 工作台交互一致性与失败恢复

- 日期：2026-10-12
- Epic / Story：ui-interaction-remediation / S01～S04
- 计划：[UI 交互审查整改](../../../../plans/ui-interaction-remediation/plan.md)
- 入口：用户请求 UI 交互审查，随后通过 bm-pilot 授权推进本轮八项问题的修复、验证与记录。

## 范围与归属

本轮修复编辑失败丢草稿、选中范围误导、查看疑问词条不更新列表、快照重复触发、统计筛选丢搜索、顶部状态被压缩、隐藏筛选难清除和错误提示难查看。解析失败回归另确认 PluginParser 将读取异常返回为空列表，导致界面显示成功 0 条；一并在原解析边界增加兼容的显式异常模式。

修改前保存相关 UI、测试、索引及后续涉及解析器/翻译模板的基线副本，以文件差异区分本轮贡献。工程保存、目标 ESP 写入、助手、AI 任务等原有未提交修改不计入本记录。未改依赖、锁文件、持久化格式或安装配置。

## 逐文件变更

- 修改 `src/transbridge/ui/workbench/workflow_presenter.py`：统计卡通过 dataclass replace 只替换状态条件；操作文案改为“查看有疑问”和“导出整个文件”；完整文件导出可用性与筛选结果数量解耦。
- 修改 `src/transbridge/ui/workbench/workflow_mixin.py`：分别传入实际选中数、筛选结果数及当前文件是否有内容，避免零选择冒充全选。
- 修改 `src/transbridge/ui/workbench/workflow_actions_view.py`：明确显示两种数量，导出按钮提示完整文件范围与下一步输出路径确认。
- 修改 `src/transbridge/ui/workbench/filters_view.py`：增加可见的启用条件摘要；重置同时清除文字、分类、状态、标签和聚焦条件；保留旧 clear_search 方法供既有调用使用。
- 修改 `src/transbridge/ui/shell/intent_composition.py`：查看疑问词条直接应用工作台筛选，保留其他已有条件。
- 修改 `src/transbridge/ui/shell/action_catalog.py`：同步查看疑问词条的命令名称。
- 修改 `src/transbridge/ui/i18n/messages.pot`：替换对应的源语言条目，保持命令与翻译模板一致。
- 新增 `src/transbridge/ui/workbench/inline_edit.py`：集中处理行内提交及失败恢复；完整保留输入、诊断与原词条快照，提供显式重试、复制、放弃；按工程、版本、内容和精确 EntryKey 隔离多份草稿，内容变化后拒绝覆盖；提交前捕获数据，兼容同步删除 Qt 表项的刷新。草稿仅在当前窗口保留，并在面板说明。
- 修改 `src/transbridge/ui/workbench/step2.py`：把行内编辑委托给专责控制器，装配恢复面板，正确同步筛选区可见状态和摘要；保留原局部刷新接口。
- 修改 `src/transbridge/ui/main_window.py`：移除快照创建/载入的重复接线；仅扩展通知转发参数，将保存和未单独处理的后台失败标为持久错误。
- 修改 `src/transbridge/ui/workbench/widget.py`：工程/版本/保存与内容/云端目标分成两行。
- 修改 `src/transbridge/ui/workbench/_project_bar.py`：工程名与保存状态各保留至少 120 像素，长文本继续省略并保留完整提示。
- 新增 `src/transbridge/ui/shell/notification_view.py`：普通消息五秒显示；最近 20 条未关闭错误持续显示，可切换、展开、复制、关闭或执行明确的恢复回调；普通消息不覆盖未处理失败，窗口缩放时重新定位，关闭时释放定时器与回调。
- 修改 `src/transbridge/ui/shell/status_presenter.py`：抽离旧单行 toast，保留兼容状态投影并转发错误与恢复信息。
- 修改 `src/transbridge/ui/coordinators/parse_coordinator.py`：单文件失败显示路径和原因；批量失败保留各文件诊断；恢复入口重新打开文件配置，用户确认后执行，不自动重放；插件解析显式启用异常传播。
- 修改 `src/transbridge/parser/plugin_parser.py`：新增默认关闭的 raise_on_error，保留原异常；重用解析器前清除旧插件与 Strings 状态；其他调用方仍默认返回空列表，合法无可译字符串的插件仍正常返回空列表。
- 新增 `tests/ui/test_interaction_filters.py`：4 项组合筛选、实际选择、零结果完整导出及全部重置回归。
- 新增 `tests/ui/test_inline_edit_recovery.py`：7 项完整输入/复制/重试、词条变化、内容切换、工程/版本切换、异常以及原文限定身份的多草稿回归。
- 新增 `tests/ui/test_workbench_context_layout.py`：3 种主题窗口宽度与保存状态检查，以及创建/载入快照取消后不重开的 2 项接线回归。
- 新增 `tests/ui/test_notification_recovery.py`：7 项持久错误、完整详情、有界多错误、恢复异常、缩放/销毁及 EET/插件/批量真实后台失败检查。
- 新增 `tests/parser/test_plugin_parser_failures.py`：2 项原始异常、默认兼容、旧解析状态清理及合法空插件回归。
- 修改 `tests/ui/test_save_completion_state.py`：测试替身支持错误语义，并核对自动保存失败进入错误通知。
- 修改 `tests/ui/test_parse_hydration_authority.py`：测试替身对齐通知参数；原权威数据与写回验收保持。
- 新增 `plans/ui-interaction-remediation/plan.md`，最小更新 `plans/INDEX.md` 和 `docs/changelogs/INDEX.md`：记录本轮范围、验收、证据和入口。

## 验证

使用仓库已有 uv 环境，无依赖安装。缓存与 pytest 输出限定到任务临时目录；沙箱拒绝部分格式化写入和 pytest 临时目录访问后，通过正常本地权限执行相同检查，未修改权限配置。该临时目录在交付前清理。

扩展回归（退出码 1）：

```powershell
uv run --no-sync --offline pytest tests/ui tests/parser/test_plugin_parser.py tests/parser/test_plugin_parser_failures.py -q -p no:cacheprovider --basetemp=.tmp-ui-interaction-pilot-01a12ed3/pytest-full --tb=short
```

结果：1509 passed、1 skipped、1 failed，171.29 秒。唯一失败为命令更名遗漏 messages.pot；已更新源模板。跳过项为 Windows 环境无法创建符号链接的拖放检查。警告主要来自既有兼容 mutation API 和 Qt/SWIG。该轮收集后增加的 2 项工程/版本切换测试包含在下面的最终复验中；不把重叠结果累加。

最终受影响模块复验（退出码 0）：

```powershell
uv run --no-sync --offline pytest tests/ui/test_interaction_filters.py tests/ui/test_inline_edit_recovery.py tests/ui/test_notification_recovery.py tests/ui/test_workbench_context_layout.py tests/ui/test_step2_incremental_rendering.py tests/ui/test_workbench_slices.py tests/ui/test_workbench_story07.py tests/ui/test_main_window_shell.py tests/ui/test_background_gui_operations.py tests/ui/test_save_completion_state.py tests/ui/test_parse_hydration_authority.py tests/ui/test_workbench_theme_migration.py tests/ui/foundation/test_locale_service.py tests/parser/test_plugin_parser.py tests/parser/test_plugin_parser_failures.py -q -p no:cacheprovider --basetemp=.tmp-ui-interaction-pilot-01a12ed3/pytest-final --tb=short --disable-warnings
```

结果：117 passed，11.23 秒，含本轮新增 25 项回归，已覆盖模板修复与最终代码。

```powershell
uv run --no-sync --offline ruff check src tests
uv run --no-sync --offline ruff format --check src tests
git diff --check
```

全部通过，1605 个 Python 文件格式符合要求。差异复核使用任务开始基线与当前文件对比，未覆盖原有修改。

用示例数据、内置浅色主题和微软雅黑进行 Qt 离屏渲染，人工检查 900/1130 像素工作台及展开的长错误详情。900 像素时工程名/保存状态分别为 172/171 像素，1130 像素时为 287/286 像素；工程、版本、状态、内容和云端目标未发生零宽挤压。另有 1600 像素自动化布局检查。

## 兼容与边界

无需数据迁移。写回仍使用整个文件，行内编辑仍沿用原有首尾空白规范化，但失败草稿保留未经裁剪的原始输入。错误历史和失败草稿均限当前窗口，不构成跨重启恢复系统。

Step2 提取后仍有 716 行；本轮不再增加其业务职责。继续拆分的风险、边界及触发条件已记录于计划。MainWindow 的改动仅限既有接线与通知转发。

已确认修复范围无未完成项。未运行全仓库测试、安装包构建、真实网络/LLM 调用或人工操作实际用户工程；视觉验证为离屏示例。未提交、推送或发布。
