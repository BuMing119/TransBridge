# Story 05：Smart Assistant 后处理预设与丰富参数

- **日期**：2026-08-27
- **Epic**：`ai-compact-proofread-custom-profiles`
- **Story**：Story 5
- **状态**：已完成
- **关联需求**：FR6.9.9
- **关联计划**：`plans/ai-compact-proofread-custom-profiles/plan.md`

## 目的

让智能助手调用 `run_postprocess` 时默认使用内置润色预设的 `combined` 校对润色，并把 GUI 预设/具名自定义工作流已有的策略、作用域、润色强度和共享运行额度能力开放为工具参数。旧的显式 `phases` 调用仍须保留严格多阶段语义。

## 本次增量

### Smart Assistant 请求解析与执行

- **新增** `src/transbridge/smart_assistant/tools/_postprocess_tool_runtime.py`
  - 引入不可变的 `PostprocessToolRequest`，在任务启动前解析最终生效的 profile、strategy、stages、scope、intensity 和 limits。
  - 无参数请求固定回退到内置 `polish` 预设和 `combined`；`profile` 支持 translate/polish/mixed、当前选中的 custom，以及具名配置名称或 UUID。
  - 作用域优先级为 `entry_ids`、显式 `scope`、Smart Assistant 已设置作用域、预设作用域，并统一排除没有现有译文的条目。
  - `phases` 只用于 strict；仅传旧 `phases` 时自动迁移为 strict，显式 `combined + phases` 在启动任务前报参数冲突。
  - 支持覆盖 `max_concurrent`、`max_tokens_per_batch`、`max_output_tokens` 和 `max_terms_per_batch`；保留 `max_workers` 作为受限的旧并发别名。
  - combined 与 strict 都通过既有 `PostProcessExecutionService` 生成候选、受控提交和 canonical report，并将最终配置来源、阶段、额度与日志目录写入任务元数据和报告。

- **重构** `src/transbridge/smart_assistant/tools/tool_proofreader.py`
  - `ProofreaderController` 改为委托请求解析和执行切片，避免继续扩张原控制器。
  - 扩展 `run_postprocess` Schema 和帮助文本，公开 profile、strategy、phases、entry_ids、scope、intensity 及四项共享额度参数。
  - 启动结果、任务状态和最近报告摘要展示实际 profile、strategy、stages、scope 与 limits。

### 共享 LLM 运行时与强度语义

- **新增** `src/transbridge/smart_assistant/tools/_workflow_llm_runtime.py`
  - 提炼可指定 workflow 的 `WorkflowLlmRuntime`，统一组装 `AiRequestBudget`、`LimitedLLMClient`、工作流 LLM 日志和暂停/停止事件。

- **兼容重构** `src/transbridge/smart_assistant/tools/_polish_llm_runtime.py`
  - `create_polish_llm_runtime` 保留原入口和返回类型别名，内部改为调用通用 workflow 构造器。

- **修改** `src/transbridge/application/translation/combined_proofread.py`
  - `polish_level` 支持 light/moderate/aggressive，并生成对应的真实 Prompt 约束；强度不再只是报告元数据。

- **修改** `src/transbridge/ai_translator/post_processor/proofread_pipeline.py`、`src/transbridge/ai_translator/translator.py`
  - GUI/翻译后处理构建 combined 阶段时透传配置中的润色强度，使 Smart Assistant 与预设模式保持相同执行语义。

### 测试与文档

- **修改** `tests/smart_assistant/tools/test_run_postprocess.py`
  - 覆盖无参数 combined 默认值、两条短文本单次合批、具名配置解析、Schema 参数、额度元数据、combined/phases 冲突、strict 报告兼容及 LLM 日志目录。

- **修改** `tests/smart_assistant/postprocess/test_param_validation.py`
  - 将无参数合同更新为 combined，并验证旧 phases 自动迁移 strict、显式策略校验和仅处理已有译文条目。

- **修改** `tests/application/translation/test_combined_proofread.py`
  - 验证 light 强度会进入最终 Prompt；原有批处理、技术校验和提交边界回归继续覆盖。

- **整理** `tests/smart_assistant/postprocess/test_config_equivalence.py`
  - 清理本次 QA 暴露的未使用符号和导入格式，不改变配置等价断言。

- **修改** `docs/dev/post_process_report.md`、`docs/requirements.md`、`plans/ai-compact-proofread-custom-profiles/plan.md`、`plans/INDEX.md`
  - 记录默认 combined、预设/自定义配置继承、丰富参数、报告元数据和 strict 回退合同，并将 FR6.9.9 / Story 5 状态收口为已实现。

## 验证证据

- `python -m pytest tests/application/translation/test_combined_proofread.py tests/ai_translator/post_processor/test_proofread_pipeline.py tests/smart_assistant/tools/test_run_postprocess.py tests/smart_assistant/postprocess/test_param_validation.py tests/smart_assistant/tools/test_polish_llm_runtime.py -q`：81 项通过。
- `python -m pytest tests/smart_assistant/tools tests/smart_assistant/postprocess tests/smart_assistant/test_tool_prompt_layering.py tests/contracts/security/test_tool_schema_security.py tests/application/translation/test_custom_workflow_profile.py tests/config/test_ai_workflow_profile_repository.py -q`：205 项通过，1 项跳过。
- `python -m pytest tests/smart_assistant -q`：485 项通过，2 项失败；失败分别要求本机存在有效 LLM API 配置、可选 LLM 客户端可以初始化，所涉生产路径不属于本 Story。
- 相关 11 个生产/测试文件通过 `.venv\Scripts\ruff.exe check` 与 `.venv\Scripts\ruff.exe format --check`。
- `python -m compileall -q` 覆盖 Smart Assistant 工具目录、combined 阶段和两处调用方并通过。
- `git diff --check` 未发现空白错误，仅有 Windows 行尾转换提示。

## 兼容与遗留

- 旧 `phases` 调用保持 strict 多阶段行为；需要明确回退时也可传 `strategy="strict"`。
- `max_workers` 仅作为旧别名保留；新调用应使用语义明确的 `max_concurrent`。
- Smart Assistant 全量回归的 2 个环境相关失败仍存在，未通过绕过凭据校验或伪造可选客户端来掩盖。
- 最终 QA 期间 `uv run` 因本机 uv cache 目录创建冲突（os error 183）不可用；已使用当前可用 Python 完成同范围编译检查。生产功能无其他已知遗留。
