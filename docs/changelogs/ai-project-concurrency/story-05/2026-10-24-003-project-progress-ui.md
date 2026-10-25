# 并发翻译任务的项目进度与窗口布局修复

- 日期：2026-10-24
- Epic：`ai-project-concurrency`；Story：S04、S05
- 依据：[计划](../../../../plans/ai-project-concurrency/plan.md)、[ADR-048](../../../adr/048-ai-project-concurrency-and-terms.md)、FR5.13.7，以及用户反馈的双插件进度截图。
- 归属：只记录本轮开始保存的 `src/tests/docs/plans` 基线与当前内容之间的差异；工作区已有并发、术语和其他修改保持原样，不提交、不推送。

## 原因与结果

原窗口把最后收到的来源进度直接写入顶部进度条，项目名称准备又复用了来源翻译和整理结果的回调，导致来源分母互相覆盖，甚至被整理结果的 1/1 替换。来源行同时拼接外层计数和翻译器消息中的计数，出现重复数字；来源表填满窗口，而过程日志默认隐藏。名称统计和加载日志还会被原简短事件过滤规则挡住。

名称准备现在按统计、术语加载、复用约束、代表名称翻译、结果校验、保存共享名称分别建立固定分母并累计全部来源进度。提交和整理回调只改变说明，不能替换名称计数。普通来源流程开始后，顶部显示已结束的来源流程数，各插件各自显示阶段和条目进度；来源流程结束后仍等待项目最终术语检查，最终成功由原任务运行时确认。

窗口新增项目阶段摘要，来源表拆为处理内容、阶段、进度、当前处理四列，数字只出现一次。列表按真实行高最多展示六行，更多来源可滚动；过程日志默认展开，收起时由空白区域吸收余高，顶部控件保持位置。长内容省略并可悬停查看。并发来源启动不再抢选中行，重试清理参与来源的旧终态，旧 worker 和取消后的迟到进度仍被忽略。

## 逐文件变更

路径相对仓库根目录。

- 新增 `src/transbridge/ui/tools/ai_translator/project_name_progress.py`：不可变项目/来源快照与线程安全累计器，阶段固定总数，来源计数单调累加，取消保留实际进度。
- 修改 `src/transbridge/ui/tools/ai_translator/task_name_preparation.py`：协调器注册每阶段全部来源分母、名称出现/独立/复用统计并转发进度；校验按首条、每 256 条和各来源末条节流，零名称不虚构已处理条目。
- 修改 `src/transbridge/ui/tools/ai_translator/source_execution.py`：名称翻译回调更新累计器，内部子步骤只更新说明；保留旧五参数来源回调。
- 修改 `src/transbridge/ui/tools/ai_translator/task_worker.py`：新增项目名称快照和来源流程结束信号，保持旧自定义 executor 工厂参数兼容。
- 修改 `src/transbridge/ui/tools/ai_translator/source_dispatch.py`：可选 `finished` 回调在一个来源返回结果时触发，稳定结果排序及最终术语收尾不变。
- 新增 `src/transbridge/ui/tools/ai_translator/task_progress_presentation.py`：纯展示状态分离项目阶段与来源计数，清理翻译器重复前缀，独立管理当前尝试和来源流程结束数。
- 修改 `src/transbridge/ui/tools/ai_translator/task_run.py`：接线新信号，同步任务中心进度，保持暂停/取消标题、来源选择和旧 worker 隔离；重试重置参与来源的旧显示状态。
- 修改 `src/transbridge/ui/tools/ai_translator/task_progress.py`：项目摘要、四列表格、真实行高限高、日志默认展开和隐藏日志占位；终态优先于旧运行快照。
- 修改 `src/transbridge/ui/tools/ai_translator/task_run_presentation.py`：允许名称统计、加载及校验的简短事件进入过程日志，保留技术路径过滤。
- 新增 `tests/ui/tools/test_ai_project_name_progress.py`：8 项并发固定分母、分阶段计数、取消、无名称、子步骤不覆盖名称以及工厂兼容回归。
- 新增 `tests/ui/tools/test_ai_task_progress_presentation.py`：5 项项目累计、交错阶段、活动同步、选中行、暂停取消、重试/旧 worker 和零总数回归。
- 新增 `tests/ui/tools/test_ai_task_progress_layout.py`：4 项项目摘要/四列/默认日志、折叠几何稳定、七来源滚动、终态替换及大字号窄窗口检查。
- 修改 `tests/ui/tools/test_ai_source_concurrency.py`：新增来源结束回调先于其他来源完成的验证，保持最终结果排序。
- 修改 `tests/ui/tools/test_ai_task_action_flow.py`：适配日志默认展开行为。
- 修改 `tests/ui/tools/test_ai_task_presentation.py`：分别检查整体与来源计数，并验证名称事件过滤。
- 修改 `tests/ui/tools/test_unified_task_progress.py`：顶部采用来源流程计数，来源校对仍保留 2/5，完成/关闭不取消成功任务的断言不变。
- 修改 `docs/requirements.md`、`docs/adr/048-ai-project-concurrency-and-terms.md`：明确进度层级和窗口行为；更新当前计划及直接相关索引。

`AiTaskRun` 当前为 477 行、31 个方法，触发方法数责任审查。新增两个方法仅为信号接线，累计与格式化逻辑已放入独立可测的展示模块；保留当前生命周期边界以避免扩大恢复/提交风险。后续再扩展此类前，应将 worker 事件订阅与释放完整提取为独立绑定组件，并用暂停、取消、重试和关闭回归验证。

## 验证

使用仓库现有 Python 3.12.12 / uv 环境，所有模型响应均为本地测试替身。

1. 首轮 88 项发现一处旧顶部进度断言；加入后端专项后的 96 项发现一处无名称扫描事件数量的过严断言。分别改为验证整体/来源分层及零工作量阶段语义，以下专项最终 **96 passed**：

```powershell
uv --cache-dir .tmp-ai-progress-ui-20261024/uv-cache run --offline --no-sync pytest tests/ui/tools/test_ai_task_progress_presentation.py tests/ui/tools/test_ai_task_progress_layout.py tests/ui/tools/test_ai_task_presentation.py tests/ui/tools/test_ai_task_action_flow.py tests/ui/tools/test_ai_source_concurrency.py tests/ui/tools/test_ai_project_name_preparation.py tests/ui/tools/test_ai_project_name_scheduling.py tests/ui/tools/test_ai_project_name_barrier.py tests/ui/tools/test_ai_project_name_progress.py tests/ui/tools/test_unified_task_progress.py tests/ui/tools/test_ai_request_state_presentation.py -q --disable-warnings --basetemp=.q27/focused
```

2. 完整相关回归 **1631 passed、2 deselected**（103.71 秒）：

```powershell
uv --cache-dir .tmp-ai-progress-ui-20261024/uv-cache run --offline --no-sync pytest tests/ai_translator tests/application/translation tests/infra/test_limited_llm_client.py tests/ui/tools --ignore=tests/ui/tools/smart_assistant --ignore=tests/ui/tools/terminology --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size1-14]' --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size2-10]' -q --disable-warnings --basetemp=.q27/full
```

两项排除项是此前已在基线复现的配置窗口布局失败，未声称通过；排除智能助手和独立术语工作台的专属测试子目录。恢复测试需要 Windows 硬链接，因此本次完整集合在获准的非受限执行中完成。未运行整个仓库全部测试。

3. 截图复核发现收起日志时标签被余高拉伸、备用行高令七行全部可见；修正布局后，只针对最终局部变更复验 UI 与生命周期，**117 passed**（17.73 秒）：

```powershell
uv --cache-dir .tmp-ai-progress-ui-20261024/uv-cache run --offline --no-sync pytest tests/ui/tools/test_ai_task_progress_presentation.py tests/ui/tools/test_ai_task_progress_layout.py tests/ui/tools/test_ai_task_presentation.py tests/ui/tools/test_ai_task_action_flow.py tests/ui/tools/test_unified_task_progress.py tests/ui/tools/test_ai_request_state_presentation.py tests/ui/tools/test_ai_partial_task.py tests/ui/tools/test_ai_task_async_apply.py tests/ui/tools/test_ai_secondary_dialog_safety.py tests/ui/tools/test_ai_runtime_control_order.py tests/ui/tools/test_ai_task_empty_source.py tests/ui/tools/test_ai_task_sources_view.py -q --disable-warnings --basetemp=.q27/layout-final
```

4. 最终 `uv run ruff check src tests` 与 `uv run ruff format --check src tests` 均通过（实际执行同样使用上述 cache/offline/no-sync 参数，2040 个文件格式通过）；`git diff --check` 通过。
5. 使用真实窗口及展示事件路径，在本地 Qt offscreen 渲染 1080×680、720×680 大字号和日志收起状态，人工检查通过。两来源 45/2158、0/6733 的模拟快照显示项目合计 45/8891；12,000 次名称出现量仅为布局夹具，不是用户项目测量。新增布局回归验证第七个来源滚动可达。

## 兼容与遗留

公开翻译流程、候选提交、持久化格式及名称去重策略不变，无数据迁移。运行中的旧进程不会自动加载源码修改，需要之后启动更新版本。真实模型吞吐、语义质量及用户正在运行的任务未被本次测试替代，也未中断用户进程。

本次修复无未完成实现；上述两项配置窗口基线失败保留为范围外事项。任务专属基线、渲染脚本、uv 缓存和测试临时目录在收尾清理，模拟预览留在会话可视化目录，不加入仓库。
