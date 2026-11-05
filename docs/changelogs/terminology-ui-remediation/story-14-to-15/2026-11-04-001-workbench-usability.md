# 术语工作台入口简化与真实预检反馈

- 日期：2026-11-04
- Epic / Story：`terminology-ui-remediation` / S14～S15
- 需求：FR5.16.30～32、FR5.18.25
- 计划：[术语工作台体验整改](../../../../plans/terminology-ui-remediation/plan.md)
- 验证：[独立 QA 报告与截图](../../../test-reports/terminology-workbench-usability-2026-11-04.md)
- Git HEAD：`3ccddc01f19e3a155a2e050792425fe4a2b0711d`。本轮另保存开始时工作区快照，保留上一轮完整来源导入和其他既有未提交变更；以下仅记录截图反馈后的实际增量。未提交或推送。

## 用户问题与完成结果

用户截图中的首次使用页面同时展示多个版本概念、新建/管理/重命名、重复的生成草稿指令及来源检查错误，真正需要的词表导入藏在菜单内。本轮将正常页面收敛为“翻译时使用”的选择、直接“导入术语…”、按当前内容显示的编辑入口，以及可用词条列表。提取、完整方案条目、新建、重命名和管理进入更多菜单；草稿、审核和发布仅在编辑或有真实待处理状态时显示。真实错误、保存中提示和任务停止能力保留。

同时修复两个实际故障：预检失败时空来源明细不再被误认为零来源，真实显示名及已知数量保持；实际工作台包装器正确转发导入整批策略，避免 `unexpected keyword argument 'policy'`。未修改领域来源上限、保存/发布事务或数据格式。

## 逐文件变更

### 生产代码

- `src/transbridge/ui/tools/terminology/workbench_shell.py`（修改）：紧凑展示真实项目与翻译版本，术语版本不再占用普通页头；来源数量放入可访问描述/提示，未知与零明确区分，完整项目名保留提示。
- `src/transbridge/ui/tools/terminology/schemes_view.py`（修改）：直接导入主按钮、当前对象编辑按钮与更多菜单；无可编辑内容隐藏编辑，标签和选择器相邻，技术数量不常驻。
- `src/transbridge/ui/tools/terminology/schemes_controller.py`（修改）：导入反馈改用直接导入按钮；保留上一轮策略传递，不将该既有功能重复计作本轮新增。
- `src/transbridge/ui/tools/terminology/object_views.py`（修改）：默认可用词条视图，提取区域按需展开；草稿相关操作按状态显示，重复内容说明隐藏，提取详情最小高度保证小窗口中完整可见。
- `src/transbridge/ui/tools/terminology/effective_view.py`（修改）：空态仅一处导入指引，保留筛选无结果与查询错误区别；双击按当前选择进入方案或基础术语编辑，不再默认修改错误对象。
- `src/transbridge/ui/tools/terminology/workflow_bar.py`（修改）：编辑时使用简短标题，保留保存/发布反馈；过期草稿提供重新提取入口。
- `src/transbridge/ui/tools/terminology/workflow_controller.py`（修改）：当前编辑、查看方案、提取等动作先激活正确工作区；保留待保存可见性，空态不显示误导性的编辑入口。
- `src/transbridge/ui/tools/terminology/window.py`（修改）：通过独立数量字段更新上下文，不再用失败时空明细推断数量；预检失败不清除已知版本或抢占普通浏览，意外异常也使用真实失败投影。
- `src/transbridge/ui/tools/terminology/view_models.py`（修改）：预检状态增加 `source_count: int | None`。
- `src/transbridge/ui/tools/terminology/preflight_presentation.py`（新增）：集中处理完整的成功/失败预检投影、来源明细和数量/体积限制说明；显示名缺失时如实提示，内部身份留在技术详情。
- `src/transbridge/ui/tools/terminology/presenter.py`（修改）：委托上述预检投影，移除已迁出的实现；不向原有公共门面继续增加职责。
- `src/transbridge/ui/tools/terminology/build_view.py`（修改）：显隐由外部提取区域控制，用户主动展开时成功与失败详情均可检查。
- `src/transbridge/ui/tools/terminology/context_binding.py`（修改）：`preview_source_import` 显式接收并转发两种整批策略，修复真实窗口入口接线。

### 测试

- `tests/ui/tools/terminology/test_preflight_context.py`（新增）：真实 51 个来源/50 上限、明确零/未知数量、显示名及技术身份、无文件租约读取、失败保持已知版本。
- `tests/ui/tools/terminology/test_workbench_source_import.py`（新增）：从真实 `TerminologyWindow` 包装器经共享 JSON reader、实际应用服务及仓库创建/选用方案，两种策略均覆盖，不绕过真实包装层。
- `tests/ui/tools/terminology/test_workbench_simplification.py`（新增）：首屏主入口、按需提取、上下文错误反馈、编辑路由、跨页菜单导航、空态与草稿状态显隐。
- `tests/ui/tools/terminology/test_schemes_view.py`（修改）：验证直接导入、菜单归属和按当前对象显示编辑。
- `tests/ui/tools/terminology/test_source_browser.py`（修改）：更新原导入菜单入口断言；上一轮独立词状态测试保持。
- `tests/ui/tools/terminology/test_window_lifecycle.py`（修改）：更新紧凑页头和主动提取详情合同，保留关闭与生命周期验证。
- `tests/ui/tools/terminology/test_workflow_routes.py`（修改）：验证默认编辑、方案编辑、完整方案和提取的实际目标页。
- `tests/ui/tools/terminology/test_workflow_guidance.py`（修改）：默认浏览去除重复引导，编辑与待保存/发布反馈仍在。

### 文档与记录

- `docs/requirements.md`（修改）：原位更新 FR5.16.30～32 的默认入口、真实预检反馈和截图验收要求，不覆盖上一轮或其他工作线修改。
- `plans/terminology-ui-remediation/plan.md`（修改）：新增 S14～S15 范围、分工、验收和实际结果，历史实施记录保持。
- `plans/INDEX.md`（修改）：仅更新该 Epic 的本轮状态和记录链接。
- `docs/test-reports/terminology-workbench-usability-2026-11-04.md`（新增）：独立审查、真实窗口八张截图、精确分组测试脚本、通过证据与验证边界。
- 本文件（新增）；`docs/changelogs/INDEX.md`（修改）：增加本轮入口，未改写旧增量记录。

## 验证与实际视觉检查

按用户指定模型分工：Astra high 负责需求、计划、分工、独立审核和 QA；GPT-6.1 Sol high 负责扫库及实现。

最终六个独立进程分别 `47 / 62 / 52 / 79 / 71 / 120 passed`，JUnit 去重为 **431 项**，所有进程退出码为 0，无失败、错误或跳过。不与此前聚焦 40/52/27/26 项重复累计。

首次单进程联合运行出现累积 Qt 控件下主题测试明显变慢，已中断，未取得完整通过结论。相同主题文件独立 `5 passed`，随后包含该文件的分组 `52 passed`；最终分组覆盖相同集合，没有修改或跳过主题测试。具体命令和分组脚本保存在 [QA 报告](../../../test-reports/terminology-workbench-usability-2026-11-04.md)。

实际执行并通过：

```powershell
.venv/Scripts/python.exe .tmp-terminology-ux-20261104-c83f/run_qa_groups.py
uv --cache-dir D:/MyCode/TransBridge/.tmp-terminology-ux-20261104-c83f/uv-cache run --no-sync ruff check src tests
uv --cache-dir D:/MyCode/TransBridge/.tmp-terminology-ux-20261104-c83f/uv-cache run --no-sync ruff format --check src tests
git diff --check
```

全仓格式检查覆盖 2371 个文件；pytest 仍有既有 SWIG 弃用警告。uv 使用原环境及 `--no-sync`，未修改依赖或锁文件。

实际 `TerminologyWindow` 以仓库主题、真实 SQLite 与应用服务渲染空态、已导入、草稿编辑和来源超限四种状态，各 920×620 / 1180×760，共八张图逐一检查；示例项目名及词条为测试夹具，不是用户数据。修复了选择器标签间距和提取详情裁切。交付图保存在本次可视化目录，具体路径见 QA 报告；测试数据库、脚本、缓存和临时图由主会话核验归属后清理。

## 兼容、职责与遗留

本轮无数据库或领域语义变更。保留上一轮完整词表导入、两种整批策略和普通编辑发布审核；并非自动发布或自动继承基础译名。

预检实现已从 presenter 抽出，presenter 为 395 行/37 方法，window 为 466 行/31 方法。两个现有门面达到方法数复核阈值，但本次只保留委托与窄接线，未增加新业务职责；导航在已有 workflow controller 中处理。

未运行全仓 pytest、真实模型、真实远端写入、高 DPI 全平台手动桌面交互或长期性能验证。当前功能验收无未解决问题；本轮不提交或推送。
