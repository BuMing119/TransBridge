# 逐条校验隔离与实时任务摘要

- 日期：2026-10-03
- 关联：[校对容错计划](../../../../plans/ai-proofread-tolerance/plan.md)
- 授权：用户要求 bm-pilot 多子 Agent 分别修复逐条校验、完整失败原因、实时日志和最终摘要。
- 证据：真实模型批次返回 4 条结果，其中 3 条可通过全部校验；第 4 条复制输入字段且缺少 final_translation。旧的整批 Schema 异常连带拒绝了前三条。

## 文件与符号变化

- `src/transbridge/application/translation/proofread_response.py`：使用原条目 Schema 严格逐条校验，隔离未知、重复和非法身份；缺失译文、多余字段及类型错误均保留。响应含额外顶层字段记录诊断，可信 results 继续检查；无法解析、没有 results 数组或重复 JSON 对象成员时整份拒绝。
- `src/transbridge/application/translation/_open_proofread_stage.py`：从结构化异常携带的原始响应恢复可验证结果；各轮已通过条目不再重复请求，仅对剩余失败集合恢复或拆分。每批仍最多 4 次逻辑调用，单条最多 2 次，取消不再派发后续请求。发出即时重试、拆批、恢复事件。
- `src/transbridge/infra/structured_validation_details.py`（新增）、`llm_structured_outputs.py`：收集全部 Schema 校验错误，缺少字段优先于类型和多余字段；元数据不包含响应字段值。
- `src/transbridge/ai_translator/post_processor/proofread_diagnostics.py`：条目详情优先显示“未返回译文”，保留其余字段错误；内部诊断码仍可用于精确分类。
- `src/transbridge/application/translation/proofread_events.py`（新增）：线程安全进度事件，普通进度按 10% 或 5 秒合并；回调异常记录告警，不中断处理。
- `src/transbridge/application/translation/proofread_stage.py`、`terminology_closure.py`、`src/transbridge/ai_translator/post_processor/proofread_pipeline.py`：接通校对、术语检查、修复及恢复日志；保留旧 runner 兼容。结束后不再把技术诊断整段输出到主日志，原诊断继续用于详情与持久记录。
- `src/transbridge/ui/tools/ai_translator/task_run_presentation.py`（新增）：按最终条目账本生成摘要，区分成功、有疑问子集、未完成、未处理、已取消及应用/未采纳状态；每个失败条目仅计入一个主要原因。普通 stage 2 条目不冒充本次校对疑问。
- `src/transbridge/ui/tools/ai_translator/task_run.py`、`task_progress.py`：运行日志使用来源名称、简短中文事件和 QPlainTextEdit；即时显示暂停、继续、取消，结束后单独显示最终摘要，重试和应用后刷新。
- `src/transbridge/ai_translator/translation_entry_outcomes.py`：独立审查发现翻译后校对路径丢失成功疑问原因；现在仅在后处理及提交成功时传递对应疑问证据，不把警告变为失败，也不把旧 stage 2 算作本轮疑问。
- `tests/application/translation/test_proofread_schema_recovery.py`、新增 `test_proofread_response_isolation.py`：验证混合合法/非法项、跨轮成功保留、身份隔离、全部错误、调用边界及事件。
- `tests/infra/test_llm_structured_outputs.py`、`tests/ai_translator/post_processor/test_proofread_diagnostics.py`：验证全部校验信息、缺译文优先及不泄漏字段值。
- 新增 `tests/ai_translator/post_processor/test_proofread_live_events.py`，调整 `test_proofread_entry_wiring.py`：验证运行中日志、节流、回调异常隔离与详细诊断保留。
- 新增 `tests/ui/tools/test_ai_task_presentation.py`、调整 `tests/ai_translator/test_translation_entry_outcomes.py`：验证真实条目统计、应用/拒绝/取消、纯文本和翻译后校对疑问传递。
- `docs/requirements.md` FR5.13.7、FR5.15.4、FR5.15.6 更新现行契约；计划与两个索引同步本增量。

## 验证

```text
uv run pytest tests/application/translation tests/ai_translator/post_processor tests/infra/test_llm_structured_outputs.py tests/infra/test_openai_structured_outputs.py tests/infra/test_anthropic_structured_outputs.py tests/ui/tools tests/integration/translation -q
975 passed（补齐翻译后校对疑问原因之前）

uv run pytest tests/ai_translator/test_translation_entry_outcomes.py tests/ai_translator/test_translator_term_conflicts.py -q
32 passed（最后补丁之后）

uv run pytest tests/ui/tools/test_ai_task_presentation.py -q
5 passed

uv run pytest tests/ui/tools/test_ai_partial_task.py -q
18 passed

uv run ruff check src tests
All checks passed

uv run ruff format --check src tests
1474 files already formatted

git -c core.safecrlf=false diff --check
通过
```

真实失败日志只读离线回放：首次输入 4 条，第二次只请求缺译文的 1 条；最终 3 条通过、1 条未完成，前三条文本与真实响应一致，第 4 条保留原译文。未改写真实历史任务或原始日志。三个子 Agent 分别完成逐条校验、实时日志、UI 摘要；逐条校验 Agent 交叉审查 UI，主会话修复其发现的疑问原因漏传并经独立复核。

一次补充测试合并运行在进入 Qt partial-task 组时无断言输出而提前退出（exit 1），分成独立进程后上述 32/5/18 项全部通过；975 项完整联合运行正常结束。既有弃用警告仍存在。

## 兼容与边界

新进度与诊断 helper 独立承担职责，原较大流水线仅增加接线；不新增配置、依赖或历史数据迁移。Schema 与领域规则未放宽到条目级，多余字段与缺少译文仍会拒绝该条目。只有现有明确的语法疑问规则可以保留原译文并计成功。

未运行真实模型整任务、手工 GUI 或安装包验收；不承诺服务商会在重试时修复缺失译文。旧任务计数保持原记录。没有创建需手动清理的临时文件或目录；没有提交或打包。本轮授权的代码工作无遗留项。
