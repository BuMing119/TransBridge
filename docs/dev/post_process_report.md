# AI 翻译与校对报告

## 目标

桌面翻译和润色任务以应用层 `ReportSnapshot` 生成质量报告。Qt 报告对话框、JSON、CSV 和 Excel 从同一快照读取计数、候选和诊断。助手查询已保存的任务成果时，使用任务中心的报告读取流程。

这项约束保证同一次运行的 `run_id`、终态、计数、词条候选、诊断和阶段信息在所有展示方式中一致。报告渲染失败不会回滚已经提交的翻译或润色结果。

## Canonical 数据模型

核心定义位于 `src/transbridge/application/translation/`：

- `postprocess.py`：`ReportSnapshot`、`PostProcessCandidate`、`PostProcessStageOutcome`。
- `completion_report.py`：将 AI 翻译计数和可选后处理快照合成为最终翻译报告。
- `polish_report.py`：将独立校对结果和用户接受/拒绝决定投影为 `ReportSnapshot`。
- `postprocess_report.py`：JSON、CSV、Excel renderer 和三格式 bundle 渲染。

`ReportSnapshot` 至少记录：

- schema、run_id 和 COMPLETED/PARTIAL/FAILED/CANCELLED 终态；
- 输入、接受、问题、失败计数；
- 每条目的稳定 EntryKey、revision、原文、处理前译文、最终候选、stage、接受状态、上下文和阶段链；
- typed diagnostics、阶段 outcome、耗时和有效运行规格摘要。

独立校对通过 candidate 的 `report_details` 保留 `result_status`、confidence、changes、note、needs_arbitration、verdict、refined_translation 和原始问题投影。该字段必须可安全序列化为 JSON，不得包含 Prompt、API Key、Authorization header 或完整模型消息。

## 生成流程

### AI 自动翻译

1. `AutoTranslator` 完成候选提交和可选后处理。
2. `build_translation_report_snapshot()` 合并翻译计数、失败/取消诊断和后处理快照。
3. 单文件或批量 worker 在后台调用统一 bundle renderer。
4. 完成窗口与详细报告对话框直接消费相同快照和 worker 返回的产物路径。

未启用后处理时仍生成报告；此时 stage outcomes 可以为空。批量运行按真实 ESP stem 为每个插件生成独立快照和产物。

### 从智能助手启动校对

`start_polish` 对已有译文运行桌面润色任务。翻译与混合任务通过 `start_translation` 启动，在任务内使用相应模式保存的质量处理配置，也可用 `profile` 指定同一基础模式下的工作流名称或 ID。用户在桌面“质量处理”页选择校对或严格策略，严格策略按已启用的阶段执行。[桌面配置页](../../src/transbridge/ui/tools/ai_translator/single_task_view.py) · [共享来源执行器](../../src/transbridge/ui/tools/ai_translator/source_execution.py)

`get_quality_report` 在当前会话及工程版本中选择最近一项已结束的助手 AI 任务，并按桌面任务中心的报告读取流程返回各来源摘要。未产生快照的候选记录使用任务中心已有的候选投影。查询指定成果和合入状态使用 `get_task_result`。[任务中心报告读取](../../src/transbridge/application/tasks/history_service.py)

### 独立校对

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

AI 翻译窗口底部的“任务记录”打开“历史报告”。窗口与助手 `list_quality_reports` 均调用 [list_exported_reports](../../src/transbridge/ai_translator/report_history.py)，读取配置数据目录下 `ai_translator/*/reports/*.xlsx`。桌面支持打开和删除，助手只列出元数据。带时间的 `{esp}_{mode}_report_{timestamp}.xlsx` 按文件名时间排序，摘要文件名和其他无法解析时间的文件按修改时间排序。新报告继续使用摘要文件名。

## 失败语义

- renderer 失败返回 `REPORT_RENDER_FAILED`，其他成功格式继续保留；
- 已提交的业务结果不因报告失败回滚；
- 独立润色的用户拒绝是有效完成结果，不等同于运行失败；
- 缺失结果、无有效候选或阶段异常进入 failed count 和诊断；
- Prompt、凭据和模型内部消息不得进入快照或产物。

## Legacy 边界

`src/transbridge/ai_translator/post_processor/report_generator.py` 的五工作表翻译投影和三工作表润色投影已由 canonical renderer 取代。后处理 checker、refiner、polisher、arbiter 和兼容 `PostProcessResult` 仍可作为算法适配层存在；它们不得重新成为 UI、Excel 或历史报告的数据源。
