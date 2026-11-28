# AI 初译按原文发送最多三个语义候选

2026-11-28 · ai-translation · [Story 16 逐条术语作用域](../../../../plans/ai-translation/stories/story-16-entry-scoped-terminology.md)

向量检索有时会为泛称召回具体专名。初译请求现在把语义候选放进该条原文的可选字段 `related_terms`，提示模型仅在原文支持该指代时采用。

## 实现

- 新增 `ai_translator/related_terms.py`，保留检索排名，按项目的原词规范化规则去重，将每条原文的候选限制为三个。
- 修改 `ai_translator/term_database.py`，在普通混合检索和工程作用域检索中分别保留直接匹配与语义候选的归属，沿用批次术语总量限制。
- 修改 `ai_translator/translation_request.py`，将两组结果交给提示词构建器。修改 `ai_translator/prompt_builder.py`，按发送时的最终 `terms` 排除重复候选，候选为空时省略字段。上述 Python 模块均位于 `src/transbridge/`。
- 修改 `data/prompts/translation/default.toml` 和内置回退提示词，说明候选的使用条件，要求保留原文中的泛称。此变更仅影响请求，无需迁移已有术语库。

## 回归验证

- `tests/ai_translator/test_term_database.py` 覆盖两条检索路径的实际请求内容、同批条目隔离和发送前新到译名的优先级。`test_prompt_builder.py` 覆盖规范化去重、候选顺序、三个上限、空字段省略与回退提示词。
- `tests/ai_translator/test_term_validation.py` 核验无效值过滤后候选的归属。`test_term_lookup_cache.py` 和 `test_translator_term_conflicts.py` 的测试替身适配新增参数。`tests/contracts/translation/test_workload_commit.py` 核验流式请求传递候选。
- `uv run --offline --no-sync --no-cache pytest tests/ai_translator tests/contracts/translation -m "not llm and not integration" -q` 通过 857 项。最终复跑术语库、提示词、术语有效性和翻译提交四个测试文件，通过 90 项。
- `uv run --offline --no-sync --no-cache ruff check src tests` 和 `uv run --offline --no-sync --no-cache ruff format --check src tests` 均通过。

实现遗留项为零。本次验证覆盖离线请求协议，未调用真实模型评测译文质量。
