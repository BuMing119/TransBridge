# Story 11：横向对象导向术语工作台与渐进 projection

## 所属 Plan

[项目全来源术语构建、版本管理与统一报告计划](../plan.md)

## 状态

已实现（2026-08-28 横向 UI 重设计；功能回归与定向 Ruff 检查通过）

## 目标

提供不暴露内部处理流程的横向术语工作台，以“概览、术语、版本、报告”四个稳定业务区域承载创建/更新、不同译法处理、人工调整、发布、历史与导出；继续使用异步 keyset pagination 和 TaskRuntime projection，UI 不拥有业务规则或全量数据。

## 验收标准

- [x] 桌面窗口使用顶部项目上下文、横向业务导航与单一主工作区；不显示编号步骤、流程箭头或要求用户按顺序切换的向导。
- [x] 顶层只提供“概览、术语、版本、报告”四个业务区域；创建/更新、不同译法决定、人工调整、发布、比较、恢复和导出作为上下文操作出现。
- [x] 概览始终展示当前 Project、Variant、来源范围、术语摘要、当前版本和需要关注的事项；发布候选证据不参与运行时能力判断。
- [x] 首次无术语库时主操作称为“创建术语库”，已有术语库时称为“更新术语库”；内部 build/preflight/reduce/persist 阶段不作为长期导航。
- [x] 默认界面展示结果、需要决定的事项、发布影响和下一步；revision/fingerprint/namespace/entry key/diagnostic code/发布检查项只在可复制技术详情中展开。
- [x] 术语、冲突和历史列表继续使用异步 keyset pagination 与有界缓存；窗口重组不创建全量 Qt item，既有查询替换和迟到结果丢弃语义保持不变。
- [x] 创建/更新、发布和导出继续委托后台命令与 TaskRuntime，窗口只投影单一进度和停止操作；本 Story 不在 Qt 主线程新增解析、归并或导出工作，量化预算由 Story 12 统一复验。
- [x] partial、stale、抑制、回退和覆盖确认使用 FR5.16 的业务语言，明确影响、历史保留和恢复方式；发布成功但日志失败给出重试入口。

## 当前实现事实

- `WorkbenchWidget.open_tool()`、shell `IntentId` / `DEFAULT_ACTION_CATALOG`、`IntentComposition.register()` 和 `ToolWindows` 是现有可发现入口。
- `WorkbenchWorkflowPresenter` 是 Qt-free projection 先例，但仍围绕当前 collection。
- `AppContext` 已超过规模门禁；其 project/variant/runtime access 只能作为窄投影，不能承担来源枚举或业务查询。
- `TaskProjectionBinding` / `TaskProjectionReducer` 已提供 owner filtering、事件顺序/终态防倒退与幂等 `close()`。
- 当前已存在 `src/transbridge/ui/tools/terminology/`、`TerminologyWindow`、五个分散 view 和 `KeysetPagedTableModel`，但窗口使用流程箭头与六个 `QTabWidget` 页签，且曾把发布候选 blocker 拼入普通用户文案。

## UI 边界与数据流

- 保留 `TerminologyPresenter`、`TerminologyUiServices`、`KeysetPagedTableModel`、`TerminologyTaskAdapter` 和 application use-case 边界；窗口只重组 presentation，不改变业务事实来源。
- `TerminologyWindow` 使用横版 shell：顶部横向四区导航，下方为 `QStackedWidget` 主工作区。概览、术语、版本和报告 view 各自保持单一 UI 责任；不得把所有布局继续堆入 `window.py`。

```text
Workbench intent -> launcher -> presenter -> application use cases
                                      -> immutable page/command result
                                      -> Qt view/model projection
TaskRuntime events -> TerminologyTaskAdapter -> progress view
```

## 实施步骤

1. preflight 只捕获 Project/Variant/source 和当前操作真实需要的业务条件；发布候选 evidence 不进入 presenter、技术详情或创建/更新按钮状态。
2. 用横向导航 shell 和 `QStackedWidget` 替换流程箭头与六个 `QTabWidget`；导航只暴露概览、术语、版本、报告，切区不触发业务重算。
3. 概览组合项目范围、术语/待关注/版本摘要、上下文创建/更新操作和单一任务进度；空状态、真实业务前置条件和已有资产状态均有明确投影。
4. 术语区复用 draft/conflict 两个有界分页模型，在同一区域以筛选和选择详情承载不同译法处理与人工调整；不把两者继续暴露为流程步骤。
5. 版本区组合待发布影响和不可变历史列表；发布、比较、恢复均成为所选版本或当前草稿旁的上下文操作。
6. 报告区保留质量报告、版本更新日志、失败重试和技术详情，但使用内容型操作布局，不铺满空白页面。
7. `KeysetPagedTableModel` 继续只持可见页和有界 cache；`CURSOR_STALE` 清空旧页并重启首屏，窗口关闭释放订阅和后台 query ownership。
8. 所有统计、diff、冲突选择和 artifact 命名仍由 use case 返回；View 只布局，Qt model 只在主线程更新。

## 文件与测试

修改 `window.py`、`presenter.py`、`view_models.py` 和现有 build/conflicts/draft/history/reports views；按责任新增 `overview_view.py`、`terms_view.py`、`versions_view.py` 或等价窄 view，避免 `window.py` 承担全部视觉结构。补充 `tests/ui/tools/terminology/` 和 `tests/integration/terminology/test_ui_workflow.py`，shell intent/composition/launcher 接线保持兼容。

建议命令：

```powershell
uv run pytest tests/ui/tools/terminology tests/integration/terminology/test_ui_workflow.py -q
```

覆盖四区横向导航、动态“创建/更新术语库”文案、无发布证据时仍可构建、真实业务诊断、术语筛选与选择操作、版本发布/比较/恢复、空数据、partial/stale、取消、query replacement、cursor stale、发布成功日志失败、有限缓存与窗口销毁资源释放。

## 实施与验证证据（2026-08-28）

- `TerminologyWorkbenchShell` 使用顶部品牌/项目区、横向业务导航与 `QStackedWidget` 主工作区；`TerminologyWindow` 不再创建流程标签或 `QTabWidget`。
- `TermsView` 将草稿和待关注异译收敛为同一术语对象的两种视图；`VersionsView` 将发布与不可变历史收敛为版本对象的上下文操作。
- preflight 只投影 Project/Variant/source 与真实业务前置条件；发布候选 blocker 已从 UI 和运行时命令链移除。
- 构建完成后从生产命令边界读取最新不可变构建结果并刷新概览摘要；空表头、列宽、空状态和空技术详情框已修正。
- `uv run --no-cache pytest tests/ui/tools/terminology tests/integration/terminology tests/integration/bootstrap/test_terminology_task_wiring.py -q`：42 passed。
- `uv run --no-cache ruff check src/transbridge/ui/tools/terminology src/transbridge/bootstrap/terminology_workloads.py tests/ui/tools/terminology tests/integration/terminology/test_ui_workflow.py`：通过。
- `uv run --no-cache ruff format --check src/transbridge/ui/tools/terminology src/transbridge/bootstrap/terminology_workloads.py tests/ui/tools/terminology tests/integration/terminology/test_ui_workflow.py`：通过。

## 边界、风险与回退

- UI 不能读取 `AppContext.slots`、SQLite 或创建全量 Qt items。
- Qt model 只在主线程变更；后台只返回 immutable page。
- Esc/关闭窗口不得误等价为停止后台任务；按现有 ownership policy 明确 detach 或 cancel。
- 新 intent 必须同步 catalog、menu/command search 和 availability；UI gate 可独立关闭而不影响 use cases/资产。
