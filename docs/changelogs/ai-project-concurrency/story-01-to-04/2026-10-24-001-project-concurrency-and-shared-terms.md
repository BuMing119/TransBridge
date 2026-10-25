# AI 多插件并发与项目共享术语

- 日期：2026-10-24（工作开始于 2026-10-23）
- Epic：`ai-project-concurrency`；Story：S01～S04
- 依据：[实现计划](../../../../plans/ai-project-concurrency/plan.md)、[ADR-048](../../../adr/048-ai-project-concurrency-and-terms.md)、FR5.13.9、11、12。
- 范围依据：对照本任务开始时保存的源代码、测试和文档基线，并核对 Git 未提交差异。仓库中其他既有修改不计入本记录；无 commit/push。

## 用户可见结果

多个插件可分别进入翻译、术语抽取和校对阶段，共用逻辑模型调用上限。昂贵检索在独立准备额度内执行，准备不再被计为模型调用。明确同一 Quest 保持前后批依赖，空 Quest 批次可以并行；并发设置不改变 Token 批次。

同项目、Variant、目标语言使用同一持久化生成术语池。已发布及插件特例保持权威，冲突保留记录，成功保存并确认后才共享。所有来源结束后检查成功候选，仅定向重翻受共享术语影响的条目；失败保留候选与原因，撤销自动应用证据。界面区分准备中、等待准备、等待发送和模型调用中。

## 逐文件变更

以下路径均相对仓库根目录。

### S01：请求准入与准备

- 修改 `src/transbridge/application/translation/ai_request_budget.py`：独立准备额度、按来源/阶段轮转及快照；保留暂停、取消、超时和幂等释放。
- 新增 `src/transbridge/infra/prepared_llm_request.py`：定义昂贵准备与最终消息合入的两阶段协议。
- 修改 `src/transbridge/infra/limited_llm_client.py`：先释放准备额度再等待调用额度；准入后轻量合入，记录准备及等待时间。
- 新增 `src/transbridge/ai_translator/translation_request.py`：一次检索与发送前共享术语刷新，批次按插件复用快照；共享模式忽略未确认的流式暂存词。
- 修改 `tests/application/translation/test_ai_request_budget.py`：准备与调用互不占槽、公平、暂停和取消回归。
- 修改 `tests/infra/test_limited_llm_client.py`：两阶段顺序、取消、异常释放及额度共用回归。
- 修改 `tests/ai_translator/test_translator_term_conflicts.py`：验证提前检索、准入后取得最新词；保存替身提供持久化内容以核对实际接纳数。

### S02：来源及批次调度

- 新增 `src/transbridge/ai_translator/batch_execution.py`：固定线程、来源轮转、有限就绪窗口；每条依赖链只放行队首，异常/取消等待在途批次。
- 新增 `src/transbridge/ai_translator/translation_rounds.py`：保留每来源三轮屏障，明确 Quest 分链，空 Quest 独立。
- 修改 `src/transbridge/ai_translator/translator.py`：接入共享执行器、来源/阶段 lane、来源检查点身份、独立准备和定向术语重翻；生成数量按实际接纳计数，对话异译交共享池记录。
- 新增 `src/transbridge/ui/tools/ai_translator/source_dispatch.py`：有界并行来源工作流，稳定顺序归还，单来源错误隔离及取消分类。
- 修改 `src/transbridge/ui/tools/ai_translator/task_worker.py`：接入共享批次执行器、并行来源和最终术语检查；最终检查异常撤销成功证据；转发请求计数。
- 修改 `src/transbridge/ui/tools/ai_translator/source_execution.py`：各来源复制配置，绑定共享池/来源身份，组合翻译、校对及最终修复；取消保留正确状态。
- 新增 `src/transbridge/ui/tools/ai_translator/source_reporting.py`：从 `source_execution.py` 等价提取报告与快照投影，旧导入位置继续重导出。
- 新增 `tests/ai_translator/test_batch_execution.py`：依赖顺序、来源公平、未知 Quest、取消/失败及有界提交回归。
- 新增 `tests/ai_translator/test_translation_rounds.py`：跨轮屏障、恢复过滤、同 Quest 后继与空 Quest 重叠、停止和异常回归。
- 新增 `tests/ui/tools/test_ai_source_concurrency.py`：真实额度下 A 校对/B 翻译重叠、取消排队、稳定结果和同名来源隔离，以及真实 worker 工厂接线。
- 修改 `tests/ui/tools/test_unified_ai_execution.py`：暂停事件替身遵循带超时 `wait` 协议。

### S03：共享术语与最终检查

- 新增 `src/transbridge/ai_translator/shared_term_pool.py`：身份隔离、修订快照、冲突记录与保存/确认事务；复用 OS lease 阻止两个独立池的检查与写入竞态，外部修改不被隐式接纳。
- 新增 `src/transbridge/ai_translator/shared_term_lookup.py`：不重做向量检索的适用词匹配，保留局部和已发布权威。
- 修改 `src/transbridge/ai_translator/term_database.py`：接入共享池及插件作用域视图，旧插件动态库仍只在该插件适用；索引身份包含池修订与作用域。
- 修改 `src/transbridge/ai_translator/generated_term_writer.py`：统一保存/确认边界，返回实际接纳数；发布术语冲突及插件特例不泄漏至全局。
- 修改 `src/transbridge/ai_translator/existing_term_extractor.py`：共享模式不再用其他插件的 `existing_text` 作为本插件初始化完成标记；初始化数量使用保存后的接纳证据。
- 新增 `src/transbridge/ui/tools/ai_translator/source_terms.py`：项目池创建、稳定来源身份、无 ESP 来源缓存身份及最终定向修复回调；仅报告本任务新增冲突。
- 修改 `src/transbridge/ui/tools/ai_translator/task_consistency.py`：共享词按作用域复合键登记；本任务修订和仅冲突写入可确认，外部编辑仍被拦截，已发布版本身份继续冻结。
- 新增 `src/transbridge/ui/tools/ai_translator/task_term_closure.py`：扫描成功翻译/已接受校对候选；每来源至多一次定向修复，检查提交、身份、术语和保护标记；失败恢复草稿并更新证据与报告。
- 修改 `src/transbridge/ui/tools/ai_translator/proofread_composition.py`：校对共享池及调用 lane；无 ESP 路径和无已发布绑定也能使用项目生成词。
- 新增 `tests/ai_translator/test_shared_term_pool.py`：项目隔离、发布/局部优先、冲突、保存/确认回滚、独立池写入互斥和未确认数据不可读回归。
- 新增 `tests/ai_translator/test_shared_term_seeding.py`：跨插件已有译文抽取和实际新增数量回归。
- 新增 `tests/ui/tools/test_shared_task_term_consistency.py`：生成修订、复合作用域、外部修改、旧局部库和冲突记录确认回归。
- 新增 `tests/ui/tools/test_ai_task_term_closure.py`：成功候选筛选、别名、错误身份、未提交、标记损坏、异常/取消、全局检查失败防误应用回归。
- 新增 `tests/ui/tools/test_ai_source_term_closure.py`：真实翻译入口与持久化池闭环；B 后产词仅修复 A 受影响条目，失败不自动应用，新任务恢复池。
- 新增 `tests/ui/tools/test_project_proofread_terms.py`：无 ESP 来源的真实校对管线读取共享池及运行中新词。
- 修改 `tests/ui/tools/test_proofread_resume_wiring.py`：已有真实恢复回归的共享池、缓存及提示词资源重定向临时目录，避免污染开发数据。

### S04：显示、日志与文档

- 新增 `src/transbridge/ui/tools/ai_translator/request_state_presentation.py`：请求计数独立展示，旧 worker 的迟到事件被忽略。
- 修改 `src/transbridge/ui/tools/ai_translator/task_run.py`：绑定请求状态生命周期，兼容无配置的测试请求。
- 修改 `src/transbridge/ui/tools/ai_translator/task_progress.py`：局部更新请求计数，保留原暂停/取消主状态。
- 修改 `src/transbridge/application/translation/postprocess_progress.py`：增加“项目术语统一”阶段名。
- 修改 `src/transbridge/ui/tools/ai_translator/workflow_logging_client.py`：保留两阶段协议，记录最终发送消息和准备指标，明确 `logical_model_call` 范围。
- 新增 `tests/ui/tools/test_ai_request_state_presentation.py`：状态文本、迟到事件及 Qt 线程投递回归。
- 修改 `tests/ui/tools/test_workflow_logging_client.py`：最终消息与准备/调用时序及日志故障隔离回归。
- 修改 `tests/ui/tools/test_live_llm_logs.py`：在纯信号顺序断言中显式使用 DirectConnection，适配来源转入后台线程；产品 UI 仍按 Qt 队列接收。
- 修改 `docs/requirements.md`：更新 FR5.13.9，新增 FR5.13.11、12。
- 新增 `docs/adr/048-ai-project-concurrency-and-terms.md`；修改 `docs/adr/003-three-round-translation-strategy.md` 添加后继决策链接。
- 新增 `plans/ai-project-concurrency/plan.md`；最小更新 `plans/INDEX.md`、`docs/changelogs/INDEX.md`。

## 验证证据

最终完整相关集合：**1539 passed，2 deselected**，耗时 136.53 秒；被排除的两项窗口布局问题先在完整回归中失败，再使用任务开始时的源码基线确认同样失败，未归入本次修复范围。

```powershell
uv --cache-dir .tmp-ai-shared-scheduler-20261023/main-cache run --offline --no-sync pytest tests/ai_translator tests/application/translation tests/infra/test_limited_llm_client.py tests/ui/tools --ignore=tests/ui/tools/smart_assistant --ignore=tests/ui/tools/terminology --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size1-14]' --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size2-10]' -q --disable-warnings --basetemp=.q24/final
uv --cache-dir .tmp-ai-shared-scheduler-20261023/main-cache run --offline --no-sync ruff check src tests
uv --cache-dir .tmp-ai-shared-scheduler-20261023/main-cache run --offline --no-sync ruff format --check src tests
git -c core.safecrlf=false diff --check
```

Ruff 检查与格式检查通过（2024 个 Python 文件已符合格式），Git 空白检查通过。恢复测试需要本地硬链接，沙箱拒绝后使用审批通过的沙箱外离线执行；改用短临时路径避免 Windows 263 字符夹具路径失败。

基线对照命令：`uv --cache-dir .tmp-ai-shared-scheduler-20261023/main-cache run --offline --no-sync pytest tests/ui/tools/test_ai_mode_switching.py -q -o pythonpath=.tmp-ai-shared-scheduler-20261023/baseline/src --basetemp=.tmp-ai-shared-scheduler-20261023/baseline-ui-tests`，结果 4 passed、2 failed；失败为上述两个尺寸下字段坐标偏移。先前完整回归为 1535 passed、6 failed，其中日志线程假设两项和校对绑定一项已修复，恢复路径一项已在短路径复验通过。日志、恢复、无路径共享校对的聚焦复验 32 passed，最终集合已包含这些复验。

## 兼容与遗留

- 生成池新增持久化目录，不替换已发布术语库，不自动迁移旧插件局部词；无项目入口保留原动态库。独立翻译器旧检查点默认接口保留，新任务按来源身份隔离。
- `translator.py`、`term_database.py` 原已超出大小门禁，新职责已提取；保留旧候选/流式重试及缓存/向量实现的原因、风险和后续拆分条件见计划中的责任审查。
- 两项既有窗口布局失败保留；真实供应商 API 吞吐、实际 HTTP 次数、模型语义质量及真实计费未验证。最终定向重翻可能改变润色措辞。
- 未运行全仓所有无关模块测试；本轮验证覆盖完整翻译后端、应用翻译层、额度包装器及 UI 工具，排除助手/术语工作台子目录。
- 本次临时基线、测试数据、缓存和测试误建的空池 lease 已清理，不保留用户翻译数据或测试产物。
