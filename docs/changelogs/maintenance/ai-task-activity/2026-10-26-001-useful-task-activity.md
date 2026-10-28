# AI 翻译任务动态与异常可见性

- 日期：2026-10-26
- Epic/Story：maintenance / ai-task-activity（用户授权的 BM pilot 直接维护路线）
- 阶段：范围确认、开发、QA、增量记录均完成。

## 目的与用户行为

过程日志曾默认占据窗口主要空间，并按中文前缀筛选，导致 API 失败、持久化失败和带分数的拆分重试被过滤。现在默认显示最近三条任务动态、最近提醒、任务用时及距上次进度更新的时长；展开可查看最近 500 条。动态带时间、来源和批次，连续重复消息合并，失败和重试说明影响与后续操作。取消、结果应用、项目保存及 LLM 日志保持现有执行流程。

## 本次文件与变化

- 新增 `src/transbridge/ai_translator/activity.py`：字符串兼容的事件类型、发生时间和批次身份，避免改变已有翻译日志回调调用方式。
- 修改 `src/transbridge/ai_translator/translator.py`：在请求失败、候选保存异常、拆分重试及无法继续拆分处产生明确类型的摘要；删除零散批次计时和基于全局计数差的批次成功数量输出。
- 修改 `src/transbridge/ai_translator/post_processor/post_processor.py`：质量检查、精修、润色和裁决异常产生用户提醒，原有失败证据仍保留诊断原因。
- 新增 `src/transbridge/ui/tools/ai_translator/task_activity.py`：事件归一化、旧字符串消息适配、技术内容摘要与保留元数据的批量载荷。
- 修改 `task_run_presentation.py`：删除中文开头白名单，重导出原有 `concise_event` 导入入口。
- 修改 `source_execution.py`：翻译回调附加批次身份时保留事件元数据。
- 修改 `task_feedback.py`：沿用有界队列及完成屏障，普通日志过载时单独保留每来源最近的被挤出提醒，补充明确的省略说明。
- 修改 `task_worker.py`：旧双字符串信号显式转换为字符串，批量通道保留类型信息。
- 修改 `task_worker_binding.py`：GUI 线程按批传递来源与事件，继续隔离旧 worker 和完成后的迟到消息。
- 修改 `task_run.py`：仅调整本次 `logged` 内部 Qt 信号与来源事件载荷，保留进入本任务前已有的任务生命周期改动。
- 新增 `task_activity_panel.py`：最近动态、展开历史、连续重复合并、独立提醒及停止后冻结的用时展示。
- 修改 `task_progress.py`：接入动态组件，保留 `logs`、`log_toggle` 访问入口及现有结果、保存操作；保留进入本任务前已有的成果审查改动。
- 新增 `tests/ui/tools/test_ai_task_activity.py`：覆盖异常和分数重试可见性、敏感诊断摘要、低价值消息过滤、事件元数据、来源/批次隔离、重复合并、有界记录、过载提醒保护和用时。
- 修改 `tests/ui/tools/test_ai_task_progress_layout.py`：验证默认紧凑呈现，720×680 窄窗口的收起/展开布局，并渲染截图检查。
- 修改 `tests/ui/tools/test_ai_worker_feedback_binding.py`：测量 worker 反馈前等待初始化保存结束，避免混入既有保存回调；修改前保存的两个界面模块同样复现了原断言失败。
- 新增 `tests/ai_translator/test_translation_activity.py`：通过真实翻译编排与模拟模型响应覆盖失败及拆分重试，核验最终成功/失败数与事件类型。
- 修改 `tests/ai_translator/post_processor/test_strict_execution_evidence.py`：验证四个严格校对阶段异常既保留失败证据，又产生不含原始错误的用户提醒。
- 修改 `docs/dev/ai_translator.md`：说明动态呈现、事件契约、缓冲边界和旧文本适配的移除条件。
- 修改 `docs/changelogs/INDEX.md`：仅添加本条记录。

以上未写完整路径的生产文件均位于 `src/transbridge/ui/tools/ai_translator/`。工作区其余既有修改不属于本记录。

## 验证

最终运行（本任务专属 `UV_CACHE_DIR`；因 Windows 沙箱禁止恢复记录使用的硬链接，测试在获准的沙箱外运行）：

```powershell
uv run --no-sync pytest tests/ui/tools/test_ai_task_activity.py tests/ui/tools/test_ai_task_presentation.py tests/ui/tools/test_ai_task_progress_layout.py tests/ui/tools/test_ai_task_progress_presentation.py tests/ui/tools/test_ai_task_feedback.py tests/ui/tools/test_ai_feedback_polling.py tests/ui/tools/test_ai_worker_feedback_binding.py tests/ui/tools/test_unified_task_runtime.py tests/ui/tools/test_workflow_progress.py tests/ui/tools/test_translation_workflow_closure_audit.py tests/ui/tools/test_ai_project_name_progress.py tests/ai_translator/test_translation_activity.py tests/ai_translator/test_translation_progress.py tests/ai_translator/test_translation_rounds.py tests/ai_translator/test_translation_entry_outcomes.py tests/ai_translator/test_translation_reuse.py tests/ai_translator/test_stream_repetition.py tests/ai_translator/post_processor/test_strict_execution_evidence.py -q -p no:cacheprovider --basetemp .tmp-ai-activity-20261026/pytest-verified --tb=short
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
git diff --check
```

结果：189 项通过，20 条既有依赖/直接修改条目的弃用警告；全库 Ruff 与 2201 个文件格式检查通过，差异空白检查通过。已查看 Qt 离屏渲染的收起/展开截图，窄窗口内提醒与底部操作完整可见。验证过程中修正了新测试缺少必填参数的问题，最终无失败。

## 兼容、责任边界与遗留项

- 无数据格式或迁移变化，未修改依赖、锁文件、LLM 请求内容和日志存储。
- 内部 `AiTaskRun.logged` 改用 object 信号承载字符串兼容的批量事件；普通 Python 字符串接收方仍可读取，直接使用 Qt `str` 类型槽的内部扩展需改为 object 槽。
- 翻译主控与严格后处理器原已超过文件规模阈值，本次仅局部替换已有日志语句；事件模型、筛选和呈现均独立提取。未向大模块增加职责；后续扩展编排职责前仍须按仓库规则拆分。
- 旧文本适配为兼容现有回调保留，所有生产者迁移至明确类型后可移除文本分类部分。窗口历史是有界过程反馈，不作为持久恢复记录。
- 未运行全库 pytest、真实付费模型调用或安装包验证；本次范围为过程反馈与呈现。
- 本次功能无未完成项；自有临时基线、测试数据和截图在收尾核验后清理。
