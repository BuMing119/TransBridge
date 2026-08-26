# AI 翻译与校对润色报告

## 目标

AI 翻译、后处理、智能助手校对和独立润色都以应用层 `ReportSnapshot` 作为唯一报告事实来源。Qt 对话框、JSON、CSV、Excel 和历史报告只是该快照的投影，不得从 `TranslationResult`、legacy `PostProcessResult` 或 `PolishResult` 临时重新统计。

这项约束保证同一次运行的 `run_id`、终态、计数、词条候选、诊断和阶段信息在所有展示方式中一致。报告渲染失败不会回滚已经提交的翻译或润色结果。

## Canonical 数据模型

核心定义位于 `src/transbridge/application/translation/`：

- `postprocess.py`：`ReportSnapshot`、`PostProcessCandidate`、`PostProcessStageOutcome`。
- `completion_report.py`：将 AI 翻译计数和可选后处理快照合成为最终翻译报告。
- `polish_report.py`：将独立校对润色结果和用户接受/拒绝决定投影为 `ReportSnapshot`。
- `postprocess_report.py`：JSON、CSV、Excel renderer 和三格式 bundle 渲染。

`ReportSnapshot` 至少记录：

- schema、run_id 和 COMPLETED/PARTIAL/FAILED/CANCELLED 终态；
- 输入、接受、问题、失败计数；
- 每条目的稳定 EntryKey、revision、原文、处理前译文、最终候选、stage、接受状态、上下文和阶段链；
- typed diagnostics、阶段 outcome、耗时和有效运行规格摘要。

独立校对润色通过 candidate 的 `report_details` 保留 `result_status`、confidence、changes、note、needs_arbitration、verdict、refined_translation 和原始问题投影。该字段必须可安全序列化为 JSON，不得包含 Prompt、API Key、Authorization header 或完整模型消息。

## 生成流程

### AI 自动翻译

1. `AutoTranslator` 完成候选提交和可选后处理。
2. `build_translation_report_snapshot()` 合并翻译计数、失败/取消诊断和后处理快照。
3. 单文件或批量 worker 在后台调用统一 bundle renderer。
4. 完成窗口与详细报告对话框直接消费相同快照和 worker 返回的产物路径。

未启用后处理时仍生成报告；此时 stage outcomes 可以为空。批量运行按真实 ESP stem 为每个插件生成独立快照和产物。

### 智能助手后处理

`run_postprocess` 默认读取内置 `polish` 预设并以 `combined` 执行一次校对润色；显式选择 `strict` 或沿用旧 `phases` 参数时才组装独立多阶段链。工具也可按名称/UUID读取具名自定义工作流，并允许覆盖作用域、润色强度及共享并发/Token 额度，但 Provider、模型、端点、凭据和本地术语路径仍只来自全局配置。

两种策略都使用 `PostProcessExecutionService` 返回的 canonical snapshot，并调用相同的 bundle renderer。`run_spec_summary`、最近报告和任务元数据记录最终生效的 profile、strategy、stages、scope、limits 与 LLM log 目录；`report_file` 优先指向 Excel。不得再通过 `SimpleNamespace` 伪造翻译结果或调用 legacy 报告生成器。

### 独立校对润色

润色 worker 返回逐条候选后，由唯一提交边界记录 accepted/rejected/failed entry IDs。`build_polish_report_snapshot()` 将原始候选、最终用户决定和有效执行档案合并；报告对话框只读取该快照。预览对话框仍只负责逐条接受/拒绝，不拥有第二套报表数据模型。

## 产物

每份快照默认生成：

- JSON：完整 canonical snapshot，供审计和程序读取；
- CSV：一行一个候选，并携带 JSON 形式的 report details；
- Excel：供人工检查。

Excel 固定包含：

- `Summary`：schema、run_id、终态、计数和有效运行规格；
- `Entries`：原文、处理前/最终译文、stage、接受状态、阶段、上下文，以及润色状态、信心度、裁决、修复候选、变更说明和备注；
- `Diagnostics`：entry ID、诊断代码、严重度、分类、消息和 retryable；
- `Stages`：阶段、耗时、候选数和诊断数。

插件报告保存到：

```text
data/ai_translator/{esp_stem}/reports/
```

文件名使用内容摘要：

```text
postprocess-report-{sha256前16位}.{json|csv|xlsx}
```

每种格式保留最近 20 份。相同内容可以复用同一摘要文件，轮转失败只记录警告，不影响报告结果。

## UI 与历史

`_TranslationReportDialog` 只接收 `ReportSnapshot`，根据 snapshot schema/source 显示翻译或润色字段。汇总、条目筛选、诊断筛选、双击定位和打开 Excel 均不重新计算业务结果。

历史窗口扫描配置数据目录下各插件的 `reports` 目录。新报告使用摘要文件名；旧 `{esp}_{mode}_report_{timestamp}.xlsx` 文件仍可被识别和打开。历史兼容只负责读取既有文件，不允许新生产入口继续生成 legacy 格式。

## 失败语义

- renderer 失败返回 `REPORT_RENDER_FAILED`，其他成功格式继续保留；
- 已提交的业务结果不因报告失败回滚；
- 独立润色的用户拒绝是有效完成结果，不等同于运行失败；
- 缺失结果、无有效候选或阶段异常进入 failed count 和诊断；
- Prompt、凭据和模型内部消息不得进入快照或产物。

## Legacy 边界

`src/transbridge/ai_translator/post_processor/report_generator.py` 的五工作表翻译投影和三工作表润色投影已由 canonical renderer 取代。后处理 checker、refiner、polisher、arbiter 和兼容 `PostProcessResult` 仍可作为算法适配层存在；它们不得重新成为 UI、Excel 或历史报告的数据源。
