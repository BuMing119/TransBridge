# Story 14：后处理提示词契约修复与阶段级缓存

## 所属 Plan

[AI 翻译后处理](../plan.md)

## 状态

已确认（待实现）

## 目标

修复质量检测、问题修复、翻译润色和质量裁决提示词与代码之间的既有契约偏差，并为每个“阶段 × 单条/批量”提示词建立一个完整稳定 `SYSTEM` 前缀和一个阶段级缓存断点。

本 Story 必须保持当前后处理结果模型、单条/批量输出形态、术语匹配逻辑、裁决阈值和失败降级策略兼容。它不照搬翻译主链路的 A/B 双 System 分层，也不通过扩写无意义规则追求缓存资格。

## 原始验收标准

- [ ] 八个提示词变体均保持 `SYSTEM -> USER` 两消息结构，每个变体只有一个位于完整稳定 `SYSTEM` 末尾的缓存断点。
- [ ] 不建立跨后处理阶段共享的 A/B 双层 System；各阶段使用独立 cache key。
- [ ] 润色 System 中的游戏、源语言和目标语言变量被正确渲染，不向模型泄漏 `$...` 占位符。
- [ ] Refiner 恢复“只修复明确问题”的既有 Story 边界，不承担 Polisher 的润色职责。
- [ ] 单条 Arbiter 能看到润色后译文、润色详情和润色者信心度。
- [ ] 批量 QualityGate 使用每条现有动态术语，术语匹配、顺序和语义不变。
- [ ] 单条/批量输出协议、结果数据类、解析器及裁决阈值保持兼容。
- [ ] JSON 示例均为合法 JSON，枚举约束与示例数据分开表达。
- [ ] 官方 OpenAI、Anthropic 与非官方兼容端点复用 Story-15 的 Provider 缓存转换与清理边界。
- [ ] 缓存未达 token 门槛或 Provider 不支持时安全降级，不填充无意义提示词。

## 决策摘要

### 采用：每个提示词变体一个完整稳定 SYSTEM

后处理共有八个稳定提示词变体：

```text
quality_gate.single
quality_gate.batch

refinement.single
refinement.batch

polish.single
polish.batch

arbitration.single
arbitration.batch
```

每个变体的请求结构固定为：

```text
SYSTEM：该阶段、该请求形态的完整稳定规则
├─ 游戏与语言对
├─ 阶段角色与职责边界
├─ 阶段专属规则
├─ 格式、术语和数据边界规则
├─ 输出字段约束
└─ 合法 JSON 固定示例

---------------- 单一 Cache 断点 FINAL ----------------

USER：本次动态数据
├─ 运行设置
├─ 条目原文及各阶段译文
├─ 上下文
├─ 检测问题或修改详情
├─ 现有动态术语
└─ 条目 ID
```

### 不采用：跨阶段通用 SYSTEM(A) + 阶段 SYSTEM(B)

拒绝原因：

- 检测、修复、润色和裁决是不同任务，不是同一任务的轻量模式。
- “遵循术语”“保留格式”等文字在四个阶段代表不同动作，抽成共同指令容易模糊职责。
- 当前完整 System 约 460～931 个字符，共同片段只会更短，通常不足以单独形成有价值的缓存前缀。
- 两个断点会增加消息组合、验证和 Provider 写入复杂度，却无法稳定提供额外命中收益。
- 正常流水线会重复调用相同的批量阶段模板，阶段级完整前缀已经覆盖主要复用场景。

### 模板维护策略

- 允许不同阶段重复少量相似规则，以保持各阶段语义完整、可独立审查。
- 不要求新建跨阶段公共 System 消息。
- 如实现模型希望在加载层复用纯文本片段，最终发给模型的仍必须是一个完整 System 消息。
- 继续遵守 ADR-005：使用 TOML + `string.Template`，不引入 LangChain、Jinja2 或新模板依赖。

## 范围

### 本 Story 包含

- 修复后处理 TOML 与 Python 构造逻辑的变量、字段和职责偏差。
- 将八个 System 模板整理成稳定、可独立审查的阶段契约。
- 严格验证模板占位符，禁止 `$...` 未替换内容进入模型请求。
- 为每个变体建立独立稳定 cache key 和一个 `FINAL` 断点。
- 扩展 Story 15 计划中的缓存元数据协议，使其同时支持翻译 A/B 和后处理单前缀两种策略。
- 官方 OpenAI、Anthropic 和非官方兼容端点继续共用同一 Provider 发送逻辑。
- 增加提示词契约、缓存指令和现有行为的非回归测试。

### 本 Story 不包含

- 不改变术语匹配、筛选、排序、裁剪、来源、优先级或 token 预算。
- 不改变 `QualityVerdict`、`RefineResult`、`PolishResult`、`ArbiterDecision` 的字段。
- 不统一单条和批量返回协议。
- 不把批量动态输入整体迁移为新的 JSON 入参协议。
- 不修改置信度阈值、快速通过规则、严格裁决策略或人工审核策略。
- 不修改后处理批次大小、并发、checkpoint、候选提交或报告流程。
- 不引入本地响应缓存、翻译结果缓存或第三方 Prompt 框架。
- 不为满足 Provider 最小 token 门槛填充无意义规则或重复示例。
- 不修改翻译主链路现有精确直填代码。

## 当前实现事实与已确认问题

### QualityGateChecker

当前单条构造会渲染 System，并把原文、译文、上下文和相关术语放入 User。

当前批量构造只把原文、译文和上下文放入 User，未调用现有术语管理器，却要求模型识别“术语错误”。这导致单条与批量检测依据不一致。

实现入口：

- `QualityGateChecker._check_single()`
- `QualityGateChecker._check_batch_internal()`
- `QualityGateChecker._build_batch_prompt()`
- `QualityGateChecker._get_relevant_terms()`

### LLMRefiner

当前 `data/prompts/refinement/zh_CN.toml` 将 Refiner 描述为“修复与润色专家”，包含 `$polish_level` 和 `polish_changes`；但：

- `LLMRefiner._build_refinement_prompt()` 没有传入 `polish_level`。
- `string.Template.safe_substitute()` 会把未提供的 `$polish_level` 原样留在请求中。
- `RefineResult` 没有 `polish_changes` 字段，解析后的润色详情无法进入结果模型。
- Story 04 明确 Refiner 只负责针对性修复，Polisher 才负责独立润色。

因此本 Story 将 Refiner 恢复为“只修复检测到的明确问题”，这是对现有 Story 和结果模型的契约恢复，不是新增翻译策略。

实现入口：

- `LLMRefiner._load_prompts()`
- `LLMRefiner._build_refinement_prompt()`
- `LLMRefiner._build_batch_refinement_prompt()`
- `LLMRefiner._parse_refinement_response()`

### LLMPolisher

当前润色 TOML 的单条和批量 System 都包含 `$game_name`，但 `LLMPolisher` 直接发送 `self._prompts["system"]` / `self._prompts["batch_system"]`，没有调用 `_render()`。

模型可能实际收到字面量 `$game_name`。源语言与目标语言虽然已加载到 `ctx`，当前 System 也没有稳定表达语言对。

实现入口：

- `LLMPolisher._load_prompts()`
- `LLMPolisher._build_polish_prompt()`
- `LLMPolisher._build_batch_polish_prompt()`

### LLMArbiter

Python 构造器已经向单条 User 渲染传入：

- `polished_translation`
- `polish_details`
- `polisher_confidence`

但当前 `data/prompts/arbitration/zh_CN.toml` 的单条 User 模板没有引用这些字段。配置文件覆盖了更完整的内置 fallback，导致单条裁决看不到最终润色结果，而批量裁决能够看到。

实现入口：

- `LLMArbiter._build_arbitration_prompt()`
- `LLMArbiter._build_batch_arbitration_prompt()`
- `LLMArbiter._format_polish_details()`

### JSON 示例

当前多个 System 用以下形式描述枚举：

```text
"verdict": "pass" | "fail" | "uncertain"
```

批量示例还使用 `...`。这些是说明性伪 JSON，不是合法 JSON，可能增加输出解析失败概率。

本 Story 要求：

- 枚举允许值在示例外单独列出。
- 固定示例本身必须可以被标准 JSON 解析器解析。
- 示例字段必须与现有解析器和结果数据类一致。

## 阶段提示词契约

### QualityGate：只检测，不修改

稳定 System 必须表达：

- 角色是本地化质量检测员。
- 只判断输入译文，不生成替代译文。
- `pass`、`fail`、`uncertain` 的边界保持现有语义。
- 原文回显和异常重复仍必须判为 `fail`。
- 不吹毛求疵；无法确定时使用 `uncertain`。
- 术语表用于判断当前译文是否采用标准译法，不执行术语替换。
- 单条输出对象、批量输出数组保持现有字段。

动态 User：

```text
single：original、translation、context、terms
batch：每条 entry_id、original、translation、context、terms
```

批量术语规则：

- 对每个条目分别调用现有 `_get_relevant_terms(entry)`。
- 使用当前 `match_terms([entry.original])` 结果。
- 使用当前 dict 遍历顺序。
- 无术语时写“无”或省略该条术语段，但必须与单条语义一致。
- 不跨条目合并成全批次大术语表。

### Refiner：只修复明确问题

稳定 System 必须表达：

- 角色是本地化问题修复者，不是通用润色者。
- 只修改检测问题直接涉及的部分。
- 未检测到问题时返回当前译文，不主动润色。
- 保留格式、占位符、语义、术语和原有风格。
- 输出字段只包含 `RefineResult` 及现有解析器支持的字段。
- 不输出 `polish_changes`。
- 不使用 `polish_level`。

动态 User：

```text
single：original、current_translation、context、issues、terms
batch：每条 entry_id、original、current_translation、context、issues、terms
```

禁止：

- 不把 Polisher 的 light / moderate / aggressive 规则复制到 Refiner。
- 不因 issues 为空而默认执行润色。
- 不扩展 `RefineResult` 数据模型。

### Polisher：按级别优化表达

稳定 System 必须表达：

- 角色是指定游戏、源语言到目标语言的本地化润色者。
- 只负责流畅度、风格和语境适配。
- light / moderate / aggressive 的稳定定义可保留在 System。
- 当前选择的具体级别继续作为动态 User 设置。
- 保留原文语义、格式、占位符和术语。
- 输出字段保持 `PolishResult` 与现有解析器兼容。

动态 User：

```text
single：original、current_translation、context、terms、polish_level
batch：polish_level + 每条 entry_id、original、current_translation、context、terms
```

System 渲染要求：

- `$game_name` 必须替换为实际游戏名。
- `$source_lang`、`$target_lang` 必须替换为实际语言名。
- 单条与批量使用相同稳定渲染上下文。

### Arbiter：只裁决，不重写

稳定 System 必须表达：

- 角色是质量裁决者，只输出 pass / reject / pending 决策。
- 评估对象是最终候选译文，优先级保持“润色结果 > 修复结果 > 初始译文”。
- 不生成新的最终译文。
- `pending`、`reject` 和 confidence 语义保持现有定义。
- 不把 Python 中的快速裁决阈值复制成新的模型策略。

动态 User：

```text
single：
  original
  initial_translation
  refined_translation
  polished_translation
  context
  original_issues
  fix_details
  polish_details
  refiner_confidence
  polisher_confidence
  quality_gate_verdict

batch：保持当前字段，并确保最终译文和润色信息存在
```

单条 User TOML 必须实际引用代码已经传入的润色字段，不能只依赖内置 fallback。

## 模板验证接口

计划新增：`src/transbridge/ai_translator/post_processor/prompt_contract.py`。

建议接口：

```python
from collections.abc import Mapping, Set
from typing import Literal

PostProcessStage = Literal[
    "quality_gate",
    "refinement",
    "polish",
    "arbitration",
]
PromptShape = Literal["single", "batch"]


class PromptTemplateContractError(ValueError):
    pass


def validate_prompt_template(
    *,
    name: str,
    template: str,
    allowed_variables: Set[str],
    required_variables: Set[str],
) -> None:
    ...


def render_prompt_template(
    *,
    name: str,
    template: str,
    values: Mapping[str, object],
) -> str:
    ...


def build_postprocess_cache_key(
    *,
    stage: PostProcessStage,
    shape: PromptShape,
    rendered_system: str,
) -> str:
    ...


def build_postprocess_messages(
    *,
    stage: PostProcessStage,
    shape: PromptShape,
    rendered_system: str,
    user_content: str,
) -> list[dict[str, object]]:
    ...
```

### 模板验证规则

- 使用 `Template.get_identifiers()` 或等效标准库能力枚举占位符。
- TOML 模板包含未知变量时，判为模板配置错误。
- 单条 User 缺少本阶段 required variable 时，判为模板配置错误。
- System 只允许稳定变量：`game_name`、`source_lang`、`target_lang`。
- 动态原文、译文、术语、问题、置信度或运行设置不得进入 System。
- 最终消息中不得存在匹配 `\$[A-Za-z_][A-Za-z0-9_]*` 的未解析占位符。
- 正常渲染使用严格 `Template.substitute()`，不再依赖 `safe_substitute()` 隐藏变量遗漏。

### 配置错误处理

- 外部 TOML 中某个变体违反契约时，对该变体记录 warning 并回退到对应内置默认模板。
- 其他变体不受影响，不能因为单条模板损坏而全部回退。
- 内置默认模板必须在单元测试中通过相同契约；默认模板不合法属于开发错误，不再静默回退。
- 如果运行期仍发生 `PromptTemplateContractError`，进入当前阶段已有的 LLM 失败/保守降级路径，不能把半渲染提示词发给 Provider。
- 日志只记录模板名和缺失/未知变量名，不记录原文、译文、术语或问题正文。

## 后处理缓存协议

### Cache key

每个变体使用独立 key：

```text
transbridge.postprocess.v1.<stage>.<shape>.<sha256(rendered_system)[:24]>
```

约束：

- key 由阶段、请求形态和完整渲染 System 决定。
- 游戏与语言对已进入渲染 System，因此变化会自然产生新 key。
- 动态术语、条目、issues、polish_level、置信度和上下文不得进入 key。
- single 与 batch 不共享 key。
- 四个阶段不共享 key。
- 不做运行时随机分片，不把 run ID、batch ID 或 entry ID 写入 key。

### 对 Story 15 缓存元数据的扩展

Story 15 计划新增 `src/transbridge/infra/prompt_cache.py`。为避免后处理重新实现 Provider 私有协议，该模块应支持两种指令策略：

```python
from typing import Literal, TypedDict

class PromptCacheDirective(TypedDict):
    key: str
    profile: Literal["translation_layered", "single_stable_prefix"]
    breakpoint: Literal["A", "B", "FINAL"]
```

验证规则：

```text
translation_layered
  必须是 SYSTEM(A) -> SYSTEM(B) -> USER
  A/B 使用相同 key

single_stable_prefix
  必须是 SYSTEM(FINAL) -> USER
  每个请求只能有一个 FINAL
```

Provider 转换器只关心经过验证的有序断点，不重复实现业务阶段判断。

如果 Story 15 已经以更通用的结构实现，可使用等价字段，不要求机械采用上述名称；但必须同时保留两种验证策略，不能放宽为“任意消息都能加缓存”。

### 官方 OpenAI

- 仅官方 OpenAI base URL 可以接收 OpenAI 私有缓存字段。
- 对已验证支持显式断点的模型，将完整 System 转为一个 `text` content block，并在该 block 添加 `prompt_cache_breakpoint: {"mode": "explicit"}`。
- 请求使用 `prompt_cache_options: {"mode": "explicit"}`，动态 User 不产生隐式缓存写入。
- 请求携带阶段独立 `prompt_cache_key`。
- 仅支持自动前缀缓存的旧模型使用相同消息顺序和阶段 key，不发送显式断点字段。
- 不支持或拒绝缓存参数时按 Story 15 的一次性无缓存降级处理。

### Anthropic

- 完整稳定 System 作为一个 system text block。
- 该 block 添加 `cache_control: {"type": "ephemeral"}`。
- 动态 User 不添加缓存标记。
- 不支持或拒绝缓存参数时按 Story 15 的一次性无缓存降级处理。

### 非官方兼容端点

- 彻底删除 `_transbridge_prompt_cache` 或等价内部元数据。
- 不发送 OpenAI `prompt_cache_key`、`prompt_cache_options`、`prompt_cache_breakpoint`。
- 不发送 Anthropic `cache_control`。
- 继续发送普通的 `SYSTEM -> USER` 标准消息。

### 缓存资格与观测

- 实现时用目标模型 tokenizer 测量八个完整 System 前缀。
- 低于 Provider 门槛时记录结构化 debug 信息，按普通请求运行。
- 不在本 Story 中扩写提示词以跨过门槛。
- 可记录 `stage`、`shape`、System token 数、cache mode 和 Provider 返回的缓存 token 计数。
- 不记录 System 正文、动态 User 正文或完整 cache key。
- 本 Story 不以“必须实际命中缓存”作为离线单元测试条件；验收 Provider 请求形态和可观测字段即可。

## 数据流

```text
后处理阶段初始化
  ├─ 加载游戏、语言和阶段 TOML
  ├─ 按 single / batch 分别验证模板变量契约
  ├─ 违规变体回退到对应内置模板
  ├─ 渲染两个稳定 System
  └─ 分别计算 single / batch cache key

每次阶段调用
  ├─ 使用当前业务逻辑取得条目、issues、术语和设置
  ├─ 只在 User 中渲染动态内容
  ├─ 选择 single 或 batch 稳定 System
  ├─ 给 System 添加 single_stable_prefix / FINAL 内部标记
  └─ 返回 SYSTEM(FINAL) -> USER

LLMClient 发送前
  ├─ 验证并删除内部缓存元数据
  ├─ 官方 OpenAI：转换单显式断点或自动缓存
  ├─ Anthropic：转换单 system cache_control
  └─ 兼容端点：只发送清洁标准消息
```

## 对现有类的接口约束

以下公开接口保持不变：

```python
QualityGateChecker.check(entry)
QualityGateChecker.check_batch(entries)

LLMRefiner.refine(entry, issues)
LLMRefiner.refine_batch(entries, issues_map)

LLMPolisher.polish(entry)
LLMPolisher.polish_batch(entries)

LLMArbiter.arbitrate(context)
LLMArbiter.arbitrate_batch(contexts)
```

以下结果模型保持不变：

```python
QualityGateResult
RefineResult
PolishResult
ArbiterDecision
ArbitrationContext
```

允许内部 builder 改为调用 `build_postprocess_messages()`，但不要求调用方感知 cache key 或 Provider 参数。

## 实施步骤

### 1. 建立当前行为基线

涉及：八个当前提示词构造入口及四个 TOML。

- 为每个 single / batch builder 捕获消息角色、动态字段、输出字段和当前 fallback。
- 增加能复现四个已确认契约偏差的失败测试。
- 对术语顺序、输出字段和解析器行为建立非回归断言。

验证：测试在实现前应能准确暴露 `$game_name`、`$polish_level`、单条裁决缺字段和批量检测无术语问题。

### 2. 新增严格模板契约工具

文件：`src/transbridge/ai_translator/post_processor/prompt_contract.py`。

- 定义 stage、shape、模板错误类型和验证函数。
- 定义严格渲染函数。
- 定义阶段 cache key 和消息组装函数。
- 模板错误日志不得包含动态正文。

验证：未知变量、缺少 required variable、未解析占位符、合法模板和 fallback 分别有单元测试。

### 3. 修正 QualityGate 提示词

文件：

- `data/prompts/quality_gate/zh_CN.toml`
- `src/transbridge/ai_translator/post_processor/quality_gate.py`

操作：

- 将 single / batch System 整理成“只检测”的完整稳定契约。
- 提供合法 JSON 固定示例。
- 批量动态 User 为每条加入现有相关术语。
- 构造 single / batch 消息时分别使用独立稳定 key 和 FINAL 断点。

验证：批量每条术语正确对应，不跨条目污染；单条/批量 verdict 和解析行为不变。

### 4. 恢复 Refiner 职责

文件：

- `data/prompts/refinement/zh_CN.toml`
- `src/transbridge/ai_translator/post_processor/llm_refiner.py`

操作：

- 从 Refiner System / User / 输出示例移除润色级别和 `polish_changes`。
- 明确只修改 issues 涉及部分，无问题时返回当前译文。
- 保留当前相关术语和格式保护规则。
- single / batch 分别使用独立稳定 key 和 FINAL 断点。
- 删除或停止加载 refinement TOML 中不再使用的 `[polish_levels]`。

验证：请求中不存在 `$polish_level`；RefineResult 和解析器字段没有变化；无 issues 时提示词不要求润色。

### 5. 修正 Polisher 稳定渲染

文件：

- `data/prompts/polish/zh_CN.toml`
- `src/transbridge/ai_translator/post_processor/polisher.py`

操作：

- single / batch System 都使用稳定 ctx 严格渲染。
- System 明确游戏和语言对。
- 三种级别定义保持稳定，当前选中级别只位于 User。
- single / batch 分别使用独立稳定 key 和 FINAL 断点。

验证：请求中出现实际游戏/语言名，不出现 `$game_name`；级别变化不改变 cache key。

### 6. 对齐 Arbiter 单条与批量输入

文件：

- `data/prompts/arbitration/zh_CN.toml`
- `src/transbridge/ai_translator/post_processor/llm_arbiter.py`

操作：

- 单条 User 模板引用已有润色后译文、润色详情和润色者信心度。
- System 明确只裁决最终候选，不生成新译文。
- single / batch 输出字段与现有解析器保持一致。
- single / batch 分别使用独立稳定 key 和 FINAL 断点。

验证：存在 PolishResult 时单条消息包含最终润色信息；不存在时沿用现有 N/A / fallback 语义。

### 7. 扩展共享缓存协议

依赖：`ai-translation` Story 15。

文件：

- `src/transbridge/infra/prompt_cache.py`（Story 15 计划新增）
- `src/transbridge/infra/llm_client.py`

操作：

- 增加 `single_stable_prefix / FINAL` 校验策略。
- 保持 `translation_layered / A+B` 校验不变。
- OpenAI、Anthropic、兼容端点从同一清理后消息生成各自请求。
- 保证普通后处理 `chat()` 和翻译流式路径互不影响。

验证：翻译 A/B 和后处理 FINAL 均能通过各自契约；错误策略组合降级为无缓存请求。

### 8. 执行集成回归

- 运行新增 prompt contract 测试。
- 运行后处理执行、候选、checkpoint、报告和 HTTP 集成测试。
- 运行翻译 Story 15 的缓存协议测试，防止扩展 FINAL 时破坏 A/B。
- 对比业务文件，确认未修改阈值、批次大小、术语管理器和结果数据类。

## 文件变更清单

计划新增：

- `src/transbridge/ai_translator/post_processor/prompt_contract.py`
- `tests/ai_translator/post_processor/__init__.py`
- `tests/ai_translator/post_processor/test_prompt_contract.py`
- `tests/ai_translator/post_processor/test_quality_gate_prompt.py`
- `tests/ai_translator/post_processor/test_refiner_prompt.py`
- `tests/ai_translator/post_processor/test_polisher_prompt.py`
- `tests/ai_translator/post_processor/test_arbiter_prompt.py`

计划修改：

- `data/prompts/quality_gate/zh_CN.toml`
- `data/prompts/refinement/zh_CN.toml`
- `data/prompts/polish/zh_CN.toml`
- `data/prompts/arbitration/zh_CN.toml`
- `src/transbridge/ai_translator/post_processor/quality_gate.py`
- `src/transbridge/ai_translator/post_processor/llm_refiner.py`
- `src/transbridge/ai_translator/post_processor/polisher.py`
- `src/transbridge/ai_translator/post_processor/llm_arbiter.py`
- `src/transbridge/infra/prompt_cache.py`（取决于 Story 15 实现顺序）
- `src/transbridge/infra/llm_client.py`
- Story 15 对应缓存协议测试文件。

明确禁止修改：

- `src/transbridge/ai_translator/translator.py` 中精确直填逻辑。
- 术语管理器匹配与合并实现。
- `PostProcessorConfig` 中阈值和批次大小默认值。
- 后处理 checkpoint、候选提交和报告数据模型。

## 测试策略

### 模板契约测试

- 八个 TOML System 只包含允许的稳定变量。
- 四个单条 User 模板包含各自 required variables。
- 所有内置 fallback 通过相同验证。
- 未知变量和缺失 required variable 触发单变体 fallback。
- 最终发送消息中没有未解析 `$identifier`。
- JSON 固定示例可以被 `json.loads()` 解析。

### 消息分层测试

- 八个变体均严格返回两条消息：`system(FINAL) -> user`。
- System 不含当前条目、terms、issues、polish level 或置信度。
- User 包含阶段所需动态字段。
- 每个请求只有一个 FINAL 断点。
- stage 或 shape 改变时 key 改变。
- 仅动态 User 改变时 key 不变。

### QualityGate 测试

- single 保持现有术语输入。
- batch 为每个条目分别匹配和渲染术语。
- 两个条目的术语不会串到对方。
- 无译文条目过滤行为不变。
- pass / fail / uncertain 解析和降级行为不变。

### Refiner 测试

- System 和 User 不含润色职责、`polish_changes` 或 `$polish_level`。
- issues、suggestion 和现有术语完整进入 User。
- 无 issues 时提示返回当前译文。
- `RefineResult` 字段和解析行为不变。
- 批量失败降级为单条的行为不变。

### Polisher 测试

- single / batch System 都包含实际游戏、源语言和目标语言。
- single / batch System 不包含字面量 `$game_name`。
- light / moderate / aggressive 只改变 User，不改变 System key。
- terms 内容和遍历顺序不变。
- `PolishResult` 字段和失败保留原译文行为不变。

### Arbiter 测试

- 单条存在 PolishResult 时包含润色后译文、详情和信心度。
- 单条无 PolishResult 时使用现有 fallback 值。
- 批量最终候选优先级保持“润色 > 修复 > 初始”。
- pass / reject / pending 字段和解析行为不变。
- Python 快速裁决路径和 strict mode 不受提示词重构影响。

### Provider 缓存测试

- `single_stable_prefix` 只接受 `system(FINAL) -> user`。
- FINAL 重复、角色错误、位置错误或夹带动态 System 时降级无缓存。
- 官方 OpenAI 显式模型只标记完整 System，不标记 User。
- Anthropic 只在完整 System block 添加 `cache_control`。
- 非官方端点没有内部元数据或私有缓存参数。
- translation A/B 测试继续通过。

### 建议命令

```powershell
pytest tests/ai_translator/post_processor -q
pytest tests/contracts/translation/test_postprocess_execution.py -q
pytest tests/contracts/translation/test_postprocess_checkpoint.py -q
pytest tests/contracts/translation/test_postprocess_candidate_report.py -q
pytest tests/contracts/translation/test_postprocess_report.py -q
pytest tests/integration/translation/test_http_postprocess_chain.py -q
```

缓存共享模块的实际测试路径由 Story 15 实现决定；实现后应追加运行对应 `prompt_cache` / `llm_client` 单元测试。

## 边界条件与失败处理

- TOML 文件不存在或解析失败：保持当前内置 fallback 行为。
- TOML 语法合法但变量契约错误：只回退损坏的 single 或 batch 变体。
- 动态术语为空：使用现有“无”或省略段落语义，不改变 key。
- issues 为空：Refiner 返回当前译文，不转为 Polisher。
- PolishResult 不存在：Arbiter 按现有优先级评估 RefineResult 或初始译文。
- Provider 不支持缓存：无缓存发送，业务结果不受影响。
- System 未达到最小缓存门槛：无缓存发送，不修改提示词内容。
- Provider 缓存参数首次被拒绝：按 Story 15 去除缓存参数重试一次。
- 批量调用失败：保持各阶段当前批量降级策略，不因缓存重构改变。

## 风险与缓解

### Refiner 输出风格变化

风险：当前 TOML 实际要求 Refiner 同时润色，恢复“只修复”后可能减少额外表达优化。

缓解：这是与 Story 04、`RefineResult` 和独立 Polisher 的职责对齐；通过回归样例验证问题修复率，并由 Polisher 继续承担可配置润色。

### 自定义 TOML 兼容

风险：过去依赖 `safe_substitute()` 的自定义模板可能包含未知占位符。

缓解：逐变体 warning + 内置 fallback；日志明确变量名。不得继续把未知占位符发送给模型。

### Story 15 实现顺序

风险：共享 `prompt_cache.py` 尚未实现时，后处理无法完成 Provider 缓存接入。

缓解：提示词契约修复可以先开发和验证；最终验收前再接入共享缓存协议。不得在四个后处理类中临时复制 Provider 私有字段。

### 缓存收益有限

风险：某些 System 低于 Provider 最小 token 门槛，结构正确但没有实际缓存命中。

缓解：记录实测 token 数并正常无缓存运行；不以牺牲提示词简洁性换取命中。

## 回退策略

- Provider 缓存接入可通过停用内部 FINAL 标记回退，消息语义保持不变。
- 单个 TOML 变体可回退到对应内置默认模板。
- 不允许通过恢复 `safe_substitute()` 处理变量错误。
- 若 Refiner 职责恢复造成经验证的质量回归，应单独提出产品决策 Story；不得在本 Story 中重新混入 Polisher 输出字段。

## 交接不变量

实现模型必须保持：

1. 后处理使用一个完整稳定 System 和一个 FINAL 断点，不使用跨阶段 A/B 分层。
2. 八个 stage/shape 变体独立生成 cache key。
3. 所有动态条目、术语、问题、设置和置信度只在 User。
4. Refiner 只修复，Polisher 才润色，Arbiter 只裁决。
5. 现有术语匹配与顺序不变。
6. 单条/批量结果模型和解析协议不变。
7. Provider 私有字段不泄漏到非官方兼容端点。
8. 缓存不可用时不影响后处理业务结果。

如实现需要突破任一不变量，应停止并先向用户确认。
