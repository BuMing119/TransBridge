# 单条超预算发送与校对疑问状态

- 日期：2026-10-03
- 归属：maintenance/proofread-tolerance；[任务计划](../../../../plans/ai-proofread-tolerance/plan.md)
- 用户确认：保留保护语法检查和恢复流程；最终仅语法差异时保留原译文、标为有疑问并计入成功。单条不受组批预算拒绝。

## 变更

- `src/transbridge/application/translation/token_batching.py`：修改 `StableContentBatcher.plan`，单条超预算独立成批，包括与空条目相邻的情况；指纹版本升为 2。保留 `ContentBatchPlan.oversized` 和诊断类型的公共接口兼容，新的计划中该集合始终为空。
- `src/transbridge/application/translation/_open_proofread_stage.py`、`terminology_closure.py`、`postprocess_stages.py`：删除超预算跳过分支，沿用现有请求调度、恢复、取消和模型输出上限。
- `src/transbridge/ai_translator/existing_term_extractor.py`、`translator.py`、`post_processor/post_processor.py`：删除旧预算拒绝分支，使术语初始化、对话抽取、翻译和严格后处理使用相同规则。大模块仅删去相关分支，没有新增职责。
- 新增 `src/transbridge/application/translation/proofread_review.py`：校对和术语恢复结束后，对仅剩语法不一致且原译文非空的条目回退原译文、设置 stage=2、接受结果并输出无需重试的疑问诊断。存在取消、恢复调用失败或其他失败时不提升为成功。
- `src/transbridge/application/translation/proofread_stage.py`：接入最终疑问规则。
- `src/transbridge/ai_translator/post_processor/proofread_pipeline.py`：为兼容结果新增可选 `target_stage`，传递疑问状态；旧调用不提供时维持原有行为。
- `src/transbridge/ai_translator/post_processor/proofread_diagnostics.py`：增加简短疑问诊断说明。
- `src/transbridge/application/translation/postprocess_execution.py`、`src/transbridge/ai_translator/translation_entry_outcomes.py`：状态变化即使译文相同，也经过受控提交并要求提交证据。
- `src/transbridge/application/translation/polish_report.py`、`src/transbridge/ui/tools/ai_translator/result_presenter.py`、`task_entry_results.py`：贯通报告、直接应用、预览应用、任务账本和持久化中的目标状态；疑问条目计入成功且不进入失败重试。
- `src/transbridge/ui/tools/ai_translator/single_task_view.py`：设置文案改为“组批 Token 预算”，提示单条超出时独立发送。
- `docs/requirements.md`：更新 FR5.13.9 的单条预算规则及 FR5.15.4 的疑问回退规则。

## 回归测试

修改以下测试，覆盖预算边界、独立发送、输出上限、保留原文、成功统计、项目状态和历史记录：

- `tests/application/translation/test_token_batching.py`
- `tests/application/translation/test_postprocess_token_limits.py`
- `tests/application/translation/test_proofread_stage.py`
- `tests/application/translation/test_proofread_terminology_closure.py`
- `tests/ai_translator/post_processor/test_proofread_pipeline.py`
- `tests/ai_translator/test_existing_term_extractor.py`
- `tests/ui/tools/test_ai_partial_task.py`
- `tests/ui/tools/test_ai_translator_slices.py`

验证命令及结果：

```text
uv run pytest tests/application/translation tests/ai_translator tests/contracts/translation tests/integration/translation tests/ui/tools/test_ai_partial_task.py tests/ui/tools/test_proofread_failure_history.py tests/ui/tools/test_ai_translator_slices.py tests/ui/tools/test_unified_task_progress.py tests/ui/tools/test_ai_task_session.py tests/ui/tools/test_mixed_completion_integrity.py -q
715 passed

uv run pytest tests/application/translation/test_proofread_stage.py -q
35 passed（最终补充校对回归，与上面数量重叠）

uv run ruff check src tests
All checks passed

uv run ruff format --check src tests
1467 files already formatted

git -c core.whitespace=blank-at-eol,blank-at-eof diff --check
通过
```

首次定向测试中的旧“超预算拒绝”和“语法差异失败”断言已按新规则更新；最终检查均通过。测试有既有弃用警告。

## 兼容与限制

用户配置字段保持原名；业务预算只限制多条目成批，服务商上下文容量与输出上限保持原值。旧任务历史不会自动改为新结果，需要重新运行。没有修改用户数据、调用真实模型、构建安装包或提交 Git。本次没有创建临时文件目录。

遗留：未执行真实服务商调用与安装包验证；单条超过服务商上下文或输出截断仍可能按现有错误流程失败。
