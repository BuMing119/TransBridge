# 术语改为参考，疑点交给 AI 综合校对并在结果中标注

- 日期：2026-10-24
- Epic：`ai-project-concurrency`；Story：S11～S13。
- 依据：用户明确要求去掉术语不一致的翻译失败条件，将检测问题统一交给 AI 综合判断，Excel 清晰标注处理结果与待复核项，耗时工作放后台。
- 计划：[S11～S13](../../../../plans/ai-project-concurrency/terminology-advisory-plan.md)；架构：[ADR-028](../../../adr/028-compact-proofread-and-custom-workflow-profiles.md)、[ADR-048](../../../adr/048-ai-project-concurrency-and-terms.md)。
- 归属：对照本轮开始保存的源码/测试/文档基线、目标 Git 差异和各切片修改记录。既有并发调度、启动后台化、名称草稿复用及 ParaTranz 等工作不归入本记录。未提交或推送代码，未调用真实模型，未修改用户词库。

## 结果行为

术语表仅作为可结合语境判断的参考。名称预翻译、普通翻译、标准校对和严格后处理不再因为没有包含某个参考译文而拒收结果，也不再在翻译前强制重译已有译文。身份、有效响应、非空译文、程序标记与真实提交/修订保护仍保留。

同一条目的原文、译文、上下文、参考资料和全部检测疑点一起发给模型。AI 可修订、保留、指出参考术语可疑或标注待复核；检测器原来的语义 ERROR 不能在 AI 判断后再次否决结果。程序标记损坏与真实执行失败仍能被定位。

项目收尾只对当前译文尚未评估的新参考差异补充校对。相同文本和参考内容已有有效判断时不重复请求；补充校对失败保留已成功译文、已有疑点和待复核标注。关闭后处理时不会偷偷调用补充校对模型。异常回调若先改草稿后失败，未验证改动会回滚。

Excel 新增“校对标注”工作表，展示来源、词条 ID、原文、校对前/当前译文、AI 状态、理由、疑点与可应用状态，支持筛选、冻结标题及文字换行。待复核和疑似参考错误使用不同颜色，并说明它们不等于翻译失败。CSV/JSON、任务结果、历史与恢复记录保持同一证据；多次校对时前后文本对应最近一次判断。复核状态与技术结果分开。

扫描、模型请求、结果证据整理和 Excel 导出继续在既有 worker/后台结果入口执行；GUI 负责交付和展示。实际导出回归验证了线程身份与 GUI 心跳，未以同步调用包装冒充后台执行。

## 逐文件修改

以下路径相对仓库根；同文件中此前已有的工作不归入本次增量。

### 翻译接受与项目补充校对

- 修改 `src/transbridge/ai_translator/translator.py`：删除译前冲突强制重译队列及字面术语验收；恢复指纹加入 `advisory-v1`；严格后处理接入同次参考解析。历史公开参数仍兼容，旧强制修复记录不再触发重译。
- 修改 `src/transbridge/ai_translator/translation_request.py`：旧修复参数引用不到资料时保留正常翻译流程和说明，不把参考缺失升级为请求失败。
- 修改 `src/transbridge/ai_translator/translation_entry_outcomes.py`：逐条结果增加默认空的校对证据，供项目收尾和台账传递。
- 修改 `src/transbridge/ai_translator/prompt_builder.py` 与 `data/prompts/translation/default.toml`：翻译提示改为参考优先、结合完整实体和语境判断，去掉“每条映射必须精确使用”。
- 修改 `src/transbridge/ui/tools/ai_translator/task_name_preparation.py`：删除名称结果中的术语包含检查，保留身份、提交证据、非空和受保护标记验证。
- 新增 `src/transbridge/ui/tools/ai_translator/source_reference_review.py`：复用有界模型预算、暂停取消与日志，在后台评估新差异，并通过修订保护提交草稿；文本不变但 stage 改变也提交。
- 修改 `src/transbridge/ui/tools/ai_translator/source_terms.py`：最终检查改接综合校对，不回到普通翻译；关闭后处理时保留待复核。
- 修改 `src/transbridge/ui/tools/ai_translator/task_term_closure.py`：跳过已评估的相同证据；失败保留有效译文和全部既有疑点；合并复核元数据，回滚不可信回调改动，保留真实技术冲突处理。
- 修改 `src/transbridge/application/translation/postprocess_progress.py`、`src/transbridge/ui/tools/ai_translator/task_progress_presentation.py`：将当前阶段标为项目参考差异校对，不再暗示必须修复为词库译法。

### 标准综合校对

- 新增 `src/transbridge/application/translation/proofread_advisory.py`：汇总格式、同原文异译与术语疑点；同原文分组按线性扫描构建、每组最多提供 8 个不同译法示例；提供显式审核证据契约。
- 新增 `src/transbridge/application/translation/proofread_prompt.py`：同一次解析的参考用于提示及证据；统一提交语境和疑点，要求状态与解释；保留结构恢复反馈。
- 修改 `src/transbridge/application/translation/_open_proofread_stage.py`：接入本地疑点收集与上述请求构建，保持有界并发和技术恢复。
- 修改 `src/transbridge/application/translation/proofread_stage.py`：标准策略不再追加术语强修复，使用模型综合判断。
- 修改 `src/transbridge/application/translation/proofread_response.py`：解析四种复核状态和理由；文字变化与“修订/保留”状态一致；缺少判断时保留有效候选并标记未审核，不能伪造成功审核。
- 修改 `src/transbridge/application/translation/proofread_checkpoint.py`：保存同次参考和判断；旧文本断点不计为已审核，不能绕过新审核证据要求。
- 修改 `src/transbridge/application/translation/proofread_review.py`：去掉把旧术语闭环错误作为当前标准校对结果的逻辑。
- 删除 `src/transbridge/application/translation/terminology_closure.py`、`terminology_refinement.py`：移除标准校对后按字面术语做确定性强修复的旧职责，其调用方已接入综合校对。
- 修改 `src/transbridge/ai_translator/structured_schemas.py`：标准校对和单条修复/裁决采用新版状态字段；批量协议以严格旧/新对象分支兼容旧响应，保留结构化 provider 的 require-all 约束。

### 严格、自定义与旧入口兼容

- 新增 `src/transbridge/application/translation/postprocess_review.py`：严格流程的疑点/语境/参考输入、判断元数据解析与继承；判断和实际文字变化一致，记录本次判断的输入。
- 修改 `src/transbridge/application/translation/postprocess_stages.py`：检测疑点存入候选，语义疑点不直接成为执行 ERROR；模型阶段发送完整内容并保留判断，校验最终程序标记。
- 新增 `src/transbridge/ai_translator/post_processor/review_decision.py`：明确区分语义参考疑点与真实技术错误，尊重已经取得的 AI 判断。
- 修改 `src/transbridge/ai_translator/post_processor/consistency_checker.py`：术语差异作为待评估线索，不默认断言参考译法正确。
- 修改 `src/transbridge/ai_translator/post_processor/llm_refiner.py`、`refinement_response.py`：综合评估检测疑点，解析修订/保留/参考可疑/待复核，保留旧响应兼容与技术失败证据。
- 修改 `src/transbridge/ai_translator/post_processor/llm_arbiter.py`：去掉字面术语拒绝准则，使用已有语义判断，保留实际执行失败和最终标记检查。
- 修改 `src/transbridge/ai_translator/post_processor/polisher.py`：将前序判断带入润色，避免再次盲目套用参考译法。
- 修改 `src/transbridge/ai_translator/post_processor/quality_gate.py`：质量提示明确参考可出错，字面差异本身不是错误。
- 修改 `src/transbridge/ai_translator/post_processor/post_processor.py`：接入判断归并，待复核保留有效候选，不由旧术语 ERROR 推回失败。
- 修改 `src/transbridge/ai_translator/post_processor/proofread_pipeline.py`：标准及兼容入口投影状态、理由、疑点、参考与审核前译文；复核不确定与执行失败分离。
- 修改 `src/transbridge/ai_translator/post_processor/checkpoint.py`、`strict_execution_evidence.py`：恢复判断数据，校对待复核不等于处理失败，真实阶段失败仍保留。
- 修改 `data/prompts/refinement/default.toml`、`arbitration/default.toml`、`polish/default.toml`、`quality_gate/default.toml`：默认资源与 Python fallback 一致地采用参考策略，避免安装资源重新引入强制用词。
- 新增 `src/transbridge/ai_translator/post_processor/review_evidence.py`：在旧 Refiner/Arbiter 构造请求时捕获同份参考、疑点及输入文本，绑定输出证据；后续润色改文使旧判断失效，本地裁决和断点不擅自升级审核版本。提示使用原有资料段，不追加重复数据。
- 新增 `tests/ai_translator/post_processor/test_legacy_review_evidence.py`：15 项覆盖调用期间术语变化、请求与证据一致、旧响应、断点、本地裁决、前后文本和项目收尾不重复请求。

### 报告、台账与后台导出

- 新增 `src/transbridge/application/translation/review_report.py`：状态中文映射、疑点展示、Excel“校对标注”工作表、筛选及颜色/布局。
- 修改 `src/transbridge/application/translation/postprocess_report.py`：统一 Excel/CSV 审核字段与 JSON 快照，保留公式防护。
- 修改 `src/transbridge/application/translation/polish_report.py`：独立校对报告包含同一元数据，审核前译文优先使用本次判断输入；旧记录回退兼容。
- 新增 `src/transbridge/ui/tools/ai_translator/task_result_evidence.py`：按来源一次建立候选索引，归并逐条判断，避免逐条反复扫描报告。
- 修改 `src/transbridge/ui/tools/ai_translator/task_entry_results.py`：台账保存独立序列化的审核证据；重试清除旧证据，公开读取不共享可变字典。
- 修改 `src/transbridge/ui/tools/ai_translator/task_recovery_persistence.py`：应用记录保存报告所需审核子集；完整参考保留在规范报告和检查点，不把业务参考词当作配置凭据检查。
- 修改 `src/transbridge/ui/tools/ai_translator/task_result_projection.py`、`task_result_dialog.py`：后台投影复核状态和筛选项，结果详情显示理由及疑点；GUI 导出使用后台渲染和原子写入。
- 修改 `src/transbridge/ui/tools/ai_translator/reporting.py`：规范快照导出及待复核统计，取消不覆盖已有文件。

### 回归与当前文档

- 修改 `tests/ai_translator/test_translator_term_conflicts.py`：冲突不扩大范围、不强制重译，旧修复检查点不重放强制工作。
- 修改 `tests/ai_translator/test_original_matched_entries.py`、`test_prepared_names.py`：新审核响应及 advisory 指纹，原文限定身份和真实准备失败仍保留。
- 修改 `tests/ai_translator/test_structured_output_contracts.py`、`tests/infra/test_llm_structured_outputs.py`：新版 provider schema、旧/新严格分支、缺字段优先级与诊断隐私。
- 新增 `tests/ai_translator/post_processor/test_advisory_terminology.py`；修改同目录 `test_refiner_prompt.py`、`test_proofread_pipeline.py`、`test_proofread_live_events.py`：错误参考保留合理译文、未决状态与技术失败分离、阶段/事件回归。
- 新增 `tests/application/translation/test_postprocess_advisory_review.py`：所有疑点入模、严格流程判断、旧 ERROR 不再否决、最终标记保护及多阶段前后文本。
- 修改 `tests/application/translation/test_proofread_stage.py`、`test_proofread_terminology_closure.py`、`test_terminology_refinement_concurrency.py`：将旧强修复预期替换为参考综合评估、同条目问题汇总及有界调用。
- 修改 `tests/application/translation/test_proofread_checkpoint.py`、`test_proofread_recovery_feedback.py`、`test_postprocess_progress.py`：新审核断点、结构恢复与阶段语义。
- 新增 `tests/contracts/translation/test_advisory_review_report.py`：Excel/CSV/JSON 对齐、颜色、过滤、完整业务数据及公式防护。
- 新增 `tests/ui/tools/test_ai_advisory_report_ui.py`、`test_ai_advisory_result_history.py`：复核筛选、真实 GUI 后台导出心跳、记录/恢复证据。
- 新增 `tests/ui/tools/test_ai_reference_review_stage.py`：仅 stage 改变的真实提交、台账和 Excel 链路；补充校对最新输入、缺失/空值兼容。
- 修改 `tests/ui/tools/test_ai_project_name_preparation.py`、`test_ai_project_name_scheduling.py`：Guards/瓜尔、Rielle 多义释义、人名不同译法不再仅凭术语差异拒收。
- 修改 `tests/ui/tools/test_ai_source_term_closure.py`、`test_ai_task_term_closure.py`：补充校对、待复核、已评估证据不重复请求、回滚、取消和保留既有疑点。
- 修改 `tests/ui/tools/test_project_proofread_terms.py`、`test_proofread_resume_wiring.py`、`test_task_recovery_discovery.py`：运行中共享参考进入模型与保存证据；旧文本断点不冒充有效审核，损坏任务不隐藏健康记录。
- 修改 `docs/requirements.md`：FR5.2、FR5.13.11/12、FR6.9.2/8 将术语从接受标准改为参考，补齐 AI 判断和可追踪报告。
- 修改 `docs/adr/028-compact-proofread-and-custom-workflow-profiles.md`、`docs/adr/048-ai-project-concurrency-and-terms.md`：直接替换已被本次决策废止的硬匹配/确定性强修复规则。
- 修改 `docs/dev/ai_translator.md`、`plans/ai-project-concurrency/plan.md`：更新当前数据流与产品边界，历史验收段落保留原时点事实。
- 新增 `plans/ai-project-concurrency/terminology-advisory-plan.md`：S11～S13 契约、验收、兼容及大模块责任审查。
- 修改 `docs/INDEX.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md`：仅更新本次直接相关链接和状态。

## 验证

聚焦测试覆盖名称接受、错误参考、标准/严格校对、提供方结构协议、恢复、来源收尾、报告和后台导出。各子集有重叠，不累加为独立覆盖率。

最终扩大集合 **1849 passed、3 deselected**（139.54 秒），命令：

```powershell
$env:UV_CACHE_DIR = 'D:\MyCode\TransBridge\.tmp-terminology-advisory-20261024\uv-cache'
$env:PYTHONIOENCODING = 'utf-8'
$env:FAISS_OPT_LEVEL = 'generic'
$reviewTestPaths = @(
    'tests/ai_translator',
    'tests/application/translation',
    'tests/contracts/translation',
    'tests/infra/test_llm_structured_outputs.py',
    'tests/infra/test_openai_structured_outputs.py',
    'tests/infra/test_anthropic_structured_outputs.py'
) + @(rg --files tests/ui/tools | Where-Object {
    $_ -match 'test_(ai_|project_proofread|proofread_|unified_|task_|polish_|workflow_|optional_|run_request|shared_|translation_workflow)'
})
uv run pytest @reviewTestPaths -q -m 'not llm and not integration and not slow' `
  '--deselect=tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size1-14]' `
  '--deselect=tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size2-10]' `
  --basetemp=.qa-ta6
```

恢复测试的 Windows 原子硬链接需正常权限执行；沙箱拒绝 `os.link` 时未改生产代码规避。使用短临时目录以避免 SQLite 长路径限制。

两个布局节点在未排除的扩大回归中再次失败；本任务前的 [S05 记录](../story-05/2026-10-24-003-project-progress-ui.md) 和 [S10 QA](../../../test-reports/ai-name-immediate-reuse-qa-2026-10-24.md) 已记录相同基线，两份文档及对应测试与本轮开始快照一致。本次未改这些布局。另一排除项是 `test_large_async_session_reports_preparation_and_gui_latency` 的 slow 合成诊断。

随后复读修复了旧严格/自定义入口的请求证据继承，最终相关矩阵 **339 passed**：

```powershell
uv run pytest tests/ai_translator/post_processor tests/application/translation/test_proofread_stage.py tests/application/translation/test_proofread_terminology_closure.py tests/application/translation/test_proofread_checkpoint.py tests/application/translation/test_postprocess_advisory_review.py tests/ui/tools/test_ai_task_term_closure.py tests/ui/tools/test_ai_source_term_closure.py tests/ui/tools/test_ai_reference_review_stage.py tests/ui/tools/test_project_proofread_terms.py -q --basetemp .qa-pf15
```

最终 `uv run ruff check src tests` 与 `uv run ruff format --check src tests` 通过（2098 个文件格式无变化）。扩大集合在兼容入口最后一轮修订前完成，修订后执行上述 339 项相关矩阵；不把两次集合相加，也不声称再次重跑了全部 1849 项。

## 兼容、风险与遗留

新增字段有默认值；旧调用参数和历史报告可读取。旧文本检查点缺少明确审核证据时不能自动当作已综合校对。新响应契约需模型输出状态及理由，旧式可用文本降为待复核，不虚构审核结论。

任务恢复/报告格式使用已有可扩展字段，不修改依赖或项目文件格式。用户词库不自动覆盖。当前运行中的旧进程不会采用新代码；重启后新建任务才能使用新策略，旧任务里的历史失败不会自动改写。

本轮验证使用假模型及合成条目，未运行真实 LLM 语义评测、真实项目全程性能基准、打包应用桌面验收或全仓库所有测试。保留上述两项既有布局问题。没有将本轮后台回归等同于承诺所有场景零卡顿。

责任审查和后续拆分条件见 S11～S13 计划。临时基线、测试目录和缓存在收尾按本轮拥有的路径清理，保留此前已存在的目录；没有新增提交。
