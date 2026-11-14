# S13：接通项目术语 LLM 抽取与审核发布

- 日期：2026-11-13
- Epic / Story：`project-terminology-build-versioning-reporting` / S13
- 计划：[S13](../../../../plans/project-terminology-build-versioning-reporting/plan.md)
- ADR：[ADR-034](../../../adr/034-project-terminology-build-versioning-reporting.md)，仍为提议。

## 行为

项目术语窗口现在可选择规则整理或规则＋LLM。新窗口默认推荐 LLM；已有构建恢复其原模式，避免旧规则草稿因默认配置改变而无法发布。读取当前项目已启用来源与 Variant 后，名称字段按规则收集，双语正文按 token 和条目上限分批调用配置的模型。原文或译文缺失、隐藏、存疑条目仍沿用原资格规则排除。

模型配置和提示词在本次捕获中固定；模型实际名称、进度、失败批次和候选过滤可见。缺少模型或密钥时明确提示恢复方式，可切换仅规则模式。成功批次保存在项目独立的可丢弃 extraction cache；普通重试复用成功批次，强制重抽先废弃对应缓存，失败批次不会以旧缓存冒充成功。

结构化输出包含证据 ID；只接受本批次同一双语词条内可定位的完整词和可见文本，拒绝跨批证据、词内子串和纯格式标记。新 LLM 候选进入待复核；人工确认、修改和禁用继续受现有草稿协调规则保护。LLM 部分失败标记不完整，不能发布；审核完成后沿用既有版本发布与有效术语消费。

## 文件与责任

- 新增 `src/transbridge/bootstrap/terminology_extraction.py`：生产输入捕获包装、冻结模型/提示词、非秘密配置身份、请求预算、token 分批和抽取会话生命周期。
- 修改 `src/transbridge/bootstrap/terminology.py`、`terminology_build.py`：注入捕获包装，在真实 BuildRunner 中打开抽取会话并明确启用 LLM；旧无抽取端口快照维持规则模式。
- 新增 `src/transbridge/application/terminology/extraction_execution.py`：暂态执行、批次与响应缓存端口；模型凭据不进入 canonical payload 或持久化领域资产。
- 新增 `src/transbridge/application/terminology/extraction_batches.py`：有界并发、成功批次复用、强制更新、可见失败和取消后的迟到结果隔离；沿用现有 canonical digest 作为缓存身份，防止错误复用。
- 修改 `src/transbridge/application/terminology/input_capture.py`：快照增加不参与 repr/比较的暂态抽取端口。
- 修改 `src/transbridge/application/terminology/extraction.py`：接入批次执行、限制证据归属、复用 `contains_term` 校验、提前取消及 `LLM_NO_TEXT` 状态说明。
- 修改 `src/transbridge/application/terminology/build.py`、`build_drafts.py`：LLM 部分失败不可发布，新 LLM 候选需复核。
- 新增 `src/transbridge/infra/project_terminology_llm.py`：复用现有抽取提示词，扩展证据 ID 结构化合同，拒绝不合法响应，隔离输入文本中的指令。
- 修改 `src/transbridge/infra/llm_client.py`：本次仅新增基础 `close()` 生命周期接口与 OpenAI/Anthropic 资源释放实现；该文件原有模型兼容性修改不属于本次增量。
- 修改 `src/transbridge/persistence/terminology/cache.py`：提供单 key 的可丢弃缓存移除操作，不触及草稿和版本。
- 新增 UI `extraction_options.py`、`build_projection.py`：方式/强制重抽/模型显示，以及候选分类、真实 LLM 状态和可操作错误恢复提示。
- 修改 UI `build_view.py`、`preflight_presentation.py`、`presenter.py`、`window.py`：后台捕获配置、实际模型展示、配置修订号隔离迟到预检，保留来源范围。
- 修改 UI `asset_presentation.py`、`task_adapter.py`、`view_models.py`、`workflow_state.py`：恢复已有模式、真实批次计数、部分失败路由，避免错误显示成功或可发布。
- 新增测试 `tests/application/terminology/test_extraction_batches.py`、`tests/infra/test_project_terminology_llm.py`、`tests/integration/bootstrap/test_terminology_llm_build.py`：批次并发/缓存/取消，提示及响应合同，生产构建→审核→发布，以及 OpenAI Responses / Anthropic Messages 的真实 SDK 离线 HTTP 请求。
- 新增 UI 测试 `test_llm_extraction_workflow.py`、`test_extraction_mode_restore.py`；修改 `test_task_adapter.py`、`test_window_lifecycle.py`，覆盖方式选择、模式恢复、并发预检与结果反馈。
- 修改 `tests/application/terminology/test_extraction.py`：完整词和纯标记过滤回归；修改 `tests/integration/bootstrap/test_terminology_task_wiring.py` 的私有组合断言以适配捕获包装。
- 修改计划及两个索引，仅更新本次 S13；保留其他未提交工作。

责任复审：已有 `bootstrap/terminology.py` 为 701 行，本次只增加组合引用、端口类型与一处注入；完整新责任已经放入独立的 `terminology_extraction.py`，没有向既有同步/版本组合类加入业务逻辑。`input_capture.py`（556 行）只增加暂态字段；`llm_client.py`（638 行）仅补同一资源生命周期的释放接口。后续扩展上述既有文件的业务职责前应先按来源/同步/生命周期拆分，本次不将无关大范围重构混入接线修复。Presenter 的结果展示已抽出，新增配置并发保护属于既有捕获职责。

## 验证

以下命令使用现有 uv 环境与 lockfile，`--no-sync` 不安装依赖。`$qa` 为本次新建并最终清理的仓库独立临时目录。

```powershell
uv run --no-sync --cache-dir "$qa/uv" pytest tests/application/terminology tests/persistence/terminology tests/contracts/terminology tests/integration/terminology tests/integration/bootstrap/test_terminology_build_review.py tests/integration/bootstrap/test_terminology_presenter_build.py tests/integration/bootstrap/test_terminology_task_wiring.py tests/integration/bootstrap/test_terminology_llm_build.py tests/infra/test_project_terminology_llm.py tests/infra/test_openai_structured_outputs.py tests/infra/test_anthropic_structured_outputs.py tests/ui/tools/terminology -q --basetemp "$qa/final" --tb=short
uv run --no-sync --cache-dir "$qa/uv" ruff check src tests
uv run --no-sync --cache-dir "$qa/uv" ruff format --check src tests
git diff --check
```

- 联合回归：**725 passed，11 warnings，106.57 秒**。警告为 SWIG 类型和测试 fixture 的旧式 context 赋值弃用提示。
- Ruff：全仓 check 通过；format check **2558 文件**通过；diff check 通过。
- 初次较窄回归在沙箱中 390 通过、12 失败，其中报告/项目硬链接受 WinError 5 限制；提升权限后复验消除权限失败，并修正捕获包装引起的一处旧测试私有对象断言。最终联合测试无失败。
- UI 模式恢复的并发修复另有 38 / 64 / 9 项相关验证通过；不将这些重复测试数累加到 725。
- OpenAI/Anthropic 测试通过 `httpx.MockTransport` 执行安装 SDK 的实际序列化和响应处理，未请求真实服务、未使用用户词条或付费模型。

## 兼容与剩余验证

无需数据库或文件格式迁移；原版本和人工决定保留。`llm_enabled=False` 与旧空规则配置归一化为相同身份，`force_llm` 是执行策略，不进入发布 freshness。模型或提示词发生变化会使旧抽取缓存失效。

本地实现与自动化验收完成。尚未验证真实模型的抽取质量和服务端兼容性，也未制作或发布安装包；这些不冒充本次离线测试结果。原 S12 性能发行门禁的历史未完成状态保持不变。
