# Story 16：逐条术语作用域与翻译 JSON 绑定

## 所属与状态

- **所属计划**: `plans/ai-translation/plan.md`
- **状态**: ✔️ 已实现（2026-08-24）
- **依赖 Story**: Story 03（术语库）、Story 07（向量召回）、Story 15（提示词分层与缓存）
- **相关 ADR**: ADR-005（TOML + `string.Template` Prompt）

## 目标

保持现有术语库、召回规则、优先级、语义 Top 3、批次术语上限、精确直填和 AI 返回协议不变；在请求生命周期内保留“翻译条目 → 入选术语”的临时归属，把每条术语嵌入对应条目的输入 JSON，避免批次级术语影响无关条目。

## 原始验收标准

- 现有平面 `{term: translation}` 结果继续可用，并新增仅存在于请求生命周期内的逐条术语绑定。
- 精确、正向子串、variant、冠词规范化、反向匹配、相关 in-flight 与语义召回均能保留条目归属；语义召回仍为每条 Top 3。
- 批次候选仍按既有优先级和 `max_terms_per_batch` 选择，不产生“每条各 50 个”的放大。
- 已被精确直填的条目及仅属于它们的术语不进入 LLM 请求；现有直填实现无修改。
- 动态 USER 不再包含批次级 `mandatory_terminology`，每个待翻译 JSON 项使用 `source` 和可选 `terms`；无术语时省略 `terms`。
- AI 返回协议仍为 `{id: 译文}`，流式解析、截断恢复和候选写回保持兼容。
- 缓存 A/B 拓扑不变，逐条术语和原文仍位于断点 B 后。
- 术语检索关闭或向量能力不可用时安全降级，仍可构造仅含 `source` 的合法请求。

## 强制边界

### 必须保持不变

- `TermEntry` 字段、Dynamic/ParaTranz/JSON/Excel 文件格式、来源覆盖顺序和合并缓存。
- `TermDatabaseManager.match_terms_enhanced()` 的平面 `{term: translation}` 返回协议。
- `AutoTranslator._run_batch()` 中精确全等词条直填代码块及其相对执行顺序。
- `PromptBuilder.parse_translation_response()`、`extract_partial_pairs()` 和模型输出 `{id: 译文}` 协议。
- SYSTEM(A) → SYSTEM(B) → USER 的消息顺序和 Provider 缓存元数据协议。
- 语义检索触发条件及每条 `top_k=3`；批次唯一术语上限仍来自 `max_terms_per_batch`。

### 不在本 Story 处理

- 不改变术语的“必须遵循”约束强度，不新增强制/参考分级。
- 不处理同一条目内部的多义词消歧，例如已经绑定到同一条目的 `Chest → 箱子`。
- 不修改精确直填对主术语、大小写或 variant 的现有行为。
- 不新增术语持久化字段，不建立新的本地翻译结果缓存。
- 不改变后处理各阶段的术语注入协议。

## 当前事实

`TermDatabaseManager.match_terms_enhanced()` 当前对整个批次执行精确/子串/反向/in-flight/语义召回，按优先级裁剪后只返回一个平面词典。`TermVectorIndex.search_hybrid_batch()` 本来按查询文本返回结果，但上层合并时丢失了条目归属。`PromptBuilder.build_translation_prompt()` 把平面词典渲染成批次级 `<mandatory_terminology>`，再把 `{id: original}` 放入 `translation_entries`。

精确直填发生在增强召回之后。直填条目会从 `llm_entries` 移除，但其术语目前仍可能保留在批次级术语段中。

## 推荐数据流

```text
四来源术语合并（结构不变）
        ↓
获取 in-flight 快照
        ↓
按现有规则生成批次候选 + 条目归属
├─ exact
├─ forward / variant / article
├─ reverse
├─ relevant in-flight
└─ semantic Top 3 per unmatched original
        ↓
按现有 priority 和 max_terms_per_batch 选择平面术语
        ↓
用入选平面术语过滤逐条归属
        ↓
现有精确直填代码块
        ↓
仅保留 llm_entries 的逐条归属
        ↓
构造 {id: {source, terms?}}
        ↓
SYSTEM(A) + SYSTEM(B) + USER
        ↓
模型返回 {id: 译文}
```

## 关键数据结构与接口

### 计划新增：请求期匹配结果

在 `src/transbridge/ai_translator/term_database.py` 新增只用于内存传递的结果类型：

```python
@dataclass
class ScopedTermMatches:
    flat_terms: dict[str, str]
    terms_by_entry: dict[str, dict[str, str]]
```

语义：

- `flat_terms` 与现有 `match_terms_enhanced()` 返回内容及顺序兼容，代表通过批次优先级和上限后的唯一术语。
- `terms_by_entry` 的键是 `TranslationEntry.key`，值只包含该条目相关且已经进入 `flat_terms` 的术语。
- 该类型不持久化、不进入配置、不写入 checkpoint，不改变术语库结构。
- 两个映射都保持 `flat_terms` 的最终迭代顺序，不额外排序。

### 计划新增：逐条匹配入口

```python
def match_terms_scoped(
    self,
    entries: list[TranslationEntry],
    enable_semantic: bool = True,
    max_terms: int = 100,
    in_flight_terms: dict[str, str] | None = None,
) -> ScopedTermMatches:
    ...
```

兼容入口保持：

```python
def match_terms_enhanced(...) -> dict[str, str]:
    return self.match_terms_scoped(...).flat_terms
```

这样现有调用方、测试替身和外部使用者仍可取得原平面词典；`AutoTranslator` 使用新入口获取临时归属。

### PromptBuilder 兼容接口

现有前三个参数保持：

```python
def build_translation_prompt(
    self,
    entries: list[TranslationEntry],
    matched_terms: Mapping[str, str],
    batch_type: str,
    *,
    terms_by_entry: Mapping[str, Mapping[str, str]] | None = None,
) -> list[dict]:
    ...
```

- 生产翻译路径必须传入 `terms_by_entry`。
- `matched_terms` 保留用于调用兼容和保守 fallback，不再渲染批次级术语段。
- 未传 `terms_by_entry` 时，只能把在条目原文中可直接定位的平面术语绑定到该条目；无法恢复归属的语义术语不得复制给所有条目。

## 逐条归属规则

### 主术语、variant 与大小写

- 使用与现有 matcher 相同的主术语、variant、`case_sensitive` 和冠词规范化规则判断条目关联。
- JSON 中始终使用主术语作为 key、标准译文作为 value；variant 只参与命中，不改变输出术语 key。

### 精确、正向与反向匹配

- 精确全等关系记录为 priority 0。
- 正向子串关系记录为 priority 1。
- 反向前缀/后缀关系记录为 priority 2。
- 一个术语可绑定多个条目；一个条目可绑定多个术语。

### in-flight

- 为保持平面兼容结果，in-flight 快照仍按当前规则参与批次候选和 priority 2 裁剪。
- 逐条 JSON 只绑定按现有文本匹配规则与该条目相关的 in-flight 术语，不把整个快照复制给每条。
- 无法与任何待翻译条目建立关系的 in-flight 术语即使仍在兼容平面结果中，也不进入逐条 JSON。

### 语义召回

- 仍只对当前规则判定为“没有子串命中”的原文执行。
- `search_hybrid_batch(..., top_k=3)` 保持不变。
- 在合并为批次平面词典前记录查询原文对应的结果；重复原文的不同 entry key 共享同一组语义结果。
- 若某语义术语已因另一条目的显式匹配进入平面词典，仍可绑定到当前语义命中的条目。
- 最终只有进入 `flat_terms` 的语义术语才能保留在 `terms_by_entry`。

### 批次上限

- 继续按当前 `priority` 选择最多 `max_terms` 个不同主术语：exact 0 → forward 1 → reverse/in-flight 2 → semantic 3。
- 同优先级继续按术语长度升序裁剪。
- 上限仍针对批次唯一术语，不改成每条独立上限。
- JSON 序列化可能因同一术语绑定多条而重复文本；本 Story 不引入第二套可配置上限，但测试需要记录典型批次 token 变化。

## 输入与输出协议

### 动态 USER

批次分类仍位于 JSON 外：

```text
<task_category>地名</task_category>

<translation_entries>
{
  "1001": {
    "source": "Speak to the Dragonborn.",
    "terms": {
      "Dragonborn": "龙裔"
    }
  },
  "1002": {
    "source": "His chest hurts."
  }
}
</translation_entries>
```

规则：

- 每个值必含 `source`。
- `terms` 非空时才存在，空映射时省略。
- `terms` 只适用于同一 JSON 项，不得跨项使用。
- 不再生成 `<mandatory_terminology>`。
- `ensure_ascii=False`、缩进和 entry 输入顺序保持现状。

### 稳定 SYSTEM

通用 SYSTEM 把“遵循术语表”改为“遵循每个条目自身的 `terms`”；明确 `terms` 仅作用于同一条目。它属于稳定规则，修改后自然生成新的 translation cache key。模式 SYSTEM 和缓存断点不变。

### 模型返回

无论输入是否包含 `terms`，输出继续是扁平对象：

```json
{
  "1001": "与龙裔交谈。",
  "1002": "他的胸口很痛。"
}
```

不得返回嵌套 `translation` 字段；现有解析器不增加新响应格式。

## 依赖有序实施步骤

1. **建立逐条匹配结果**：在 `term_database.py` 增加请求期结果类型和 scoped 入口，复用现有 matcher、优先级、语义 Top 3 和批次裁剪；旧 enhanced 入口委托后只返回 `flat_terms`。
2. **接入翻译控制器**：在 `_run_batch()` 用 scoped 入口取得平面日志数据和逐条绑定；保持精确直填代码块原样，在其后只提取 `llm_entries` 的绑定并传入 PromptBuilder。
3. **更新 Prompt 契约**：在 `prompt_builder.py` 生成嵌套输入 JSON，移除批次术语段；在 `zh_CN.toml` 和 fallback SYSTEM/USER 中同步逐条作用域及扁平输出说明。
4. **补充术语测试**：验证平面兼容、显式/反向/variant 归属、in-flight 约束、语义 Top 3、重复原文、全局上限和无向量降级。
5. **更新 Prompt 与翻译链测试**：验证逐条 JSON、空 `terms` 省略、共享术语、输出协议、直填条目不进入请求、缓存拓扑不变。
6. **回归与静态检查**：运行相关 pytest、Ruff、`git diff --check`，测量代表性输入 token；失败时只修复本 Story 引入的回归。

## 文件变更清单

- `plans/ai-translation/plan.md`（改）：Story 16 状态、范围和验收。
- `plans/INDEX.md`（改）：Story 数量和链接。
- `plans/ai-translation/stories/story-16-entry-scoped-terminology.md`（增）：本文档。
- `src/transbridge/ai_translator/term_database.py`（改）：scoped 匹配结果与兼容入口。
- `src/transbridge/ai_translator/translator.py`（改）：请求期绑定接线。
- `src/transbridge/ai_translator/prompt_builder.py`（改）：逐条 JSON。
- `data/prompts/langs/zh_CN.toml`（改）：稳定规则和动态模板。
- `tests/ai_translator/test_term_database.py`（增）：逐条召回与兼容测试。
- `tests/ai_translator/test_prompt_builder.py`（改）：新输入协议。
- `tests/contracts/translation/test_workload_commit.py`（按需改）：生产链测试替身与直填边界。

## 边界与失败处理

- `entries=[]`：返回空平面词典和空绑定，不调用向量检索。
- 术语检索关闭：scoped 结果为空；Prompt 仍为合法的 source-only JSON。
- 向量索引不存在、不可用或抛出错误：沿用现有向量层降级，只保留显式和相关 in-flight 绑定。
- `max_terms <= 0`：不发送任何术语绑定，避免 Python 负切片产生意外保留。
- 重复 entry key：沿用翻译系统“key 唯一”的上游合同，不在本 Story 中定义覆盖行为。
- 重复 original、不同 key：两条都保留各自 key，并共享相同的显式/语义绑定。
- 某条绑定为空：省略 `terms`，不输出空对象。
- 外部自定义旧 Prompt 模板仍包含 `$terms_section`：兼容渲染为空串并发出迁移 warning；不得重新注入批次级术语。

## 测试策略

### 单元测试

- `match_terms_enhanced()` 与 `match_terms_scoped().flat_terms` 相等。
- 主术语、variant、大小写、冠词、反向前后缀只绑定相关 entry key。
- in-flight 平面兼容但只在相关条目 JSON 中出现。
- 两条语义未命中原文各自只收到自己的 Top 3；批次平面去重。
- 超过 `max_terms` 时保留顺序与旧规则一致，并同步过滤绑定。
- 嵌套 JSON 保持 entry 顺序；无术语省略 `terms`；共享术语分别出现。
- SYSTEM(A)/SYSTEM(B) 无动态内容，USER 无 `mandatory_terminology`。
- 响应解析仍只接受预期扁平 key/value。

### 集成/合同测试

- 直填与 LLM 混合批次中，直填条目不出现在请求 JSON，其专属术语不泄漏到其他项。
- 流式增量解析和候选提交继续使用扁平响应。
- OpenAI/Anthropic 转换后的缓存拓扑和动态后缀位置保持不变。

### 建议命令

```text
pytest tests/ai_translator/test_term_database.py tests/ai_translator/test_prompt_builder.py tests/infra/test_llm_client_prompt_cache.py -q
pytest tests/contracts/translation/test_workload_commit.py -q
ruff check src/transbridge/ai_translator/term_database.py src/transbridge/ai_translator/translator.py src/transbridge/ai_translator/prompt_builder.py tests/ai_translator/test_term_database.py tests/ai_translator/test_prompt_builder.py
git diff --check
```

## 风险与回退

- **输入协议风险**：弱模型可能模仿嵌套输入。用稳定 SYSTEM 和 USER 尾部同时声明扁平输出，并保留严格解析测试。
- **token 风险**：共享术语会在多条 JSON 项中重复。保持批次唯一术语上限，并在 QA 中报告代表性 token 变化。
- **性能风险**：不得为每条原文重新扫描完整术语库或逐条调用 embedding；应复用一次 matcher map 和批量向量查询。
- **兼容回退**：旧平面入口保留；若逐条绑定为空，翻译请求仍可仅靠 source 正常运行。

## 未决问题

无。用户已确认每条只携带自己的术语，术语库结构不变，并授权端到端实现。

## 实现与验证结果

- 已增加 `ScopedTermMatches` 与 `match_terms_scoped()`；兼容入口 `match_terms_enhanced()` 继续返回平面词典。
- `AutoTranslator` 在现有精确直填完成后，只把剩余 `llm_entries` 的术语绑定交给 PromptBuilder；直填代码块未修改。
- 动态输入已改为 `{id: {source, terms?}}`，无术语时省略 `terms`；模型响应仍为扁平 `{id: 译文}`。
- SYSTEM(A) → SYSTEM(B) → USER 与 A/B 缓存元数据保持不变，动态术语仍完全位于断点 B 后。
- 相关单元、缓存和 workload 合同回归共 75 项通过；目标文件 Ruff lint 通过，实际 TOML 模板冒烟通过。
- 代表性 12 条/12 个唯一术语样本中，动态字符数由 709 增至 1260（+551）；增长来自逐项 JSON 字段与作用域绑定，不进入稳定缓存前缀。
