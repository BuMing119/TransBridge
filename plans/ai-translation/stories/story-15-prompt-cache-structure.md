# Story 15：翻译提示词分层与 Provider Prompt Cache

## 状态

草稿（结构与范围已确认，待实现）

## 目标

在不改变现有翻译质量、术语语义和精确直填流程的前提下，将 AI 翻译提示词拆成两个稳定的 `SYSTEM` 前缀和一个动态 `USER` 后缀，使官方 OpenAI 与 Anthropic Provider 能复用稳定前缀缓存；非官方 OpenAI 兼容端点继续收到无内部元数据的标准消息。

本 Story 只定义交接级实现方案，不包含业务代码实现。

## 已确认的最终结构

```text
SYSTEM：通用稳定部分
├─ [游戏与语言对：英文 → 简体中文]
├─ [角色与任务目标]
├─ [指令优先级]
├─ [通用翻译规范]
├─ [格式保护规则]
├─ [质量检查规则]
├─ [JSON 输出协议]
└─ [固定示例]

---------------- Cache 断点 A ----------------

SYSTEM：模式稳定部分
└─ [翻译模式：实体短文本 / 对话 / 长文本]

---------------- Cache 断点 B ----------------

USER：本批次动态部分
├─ [具体分类：人名 / 地名 / 物品等]
├─ [现有动态术语表]
└─ [待翻译文本]
```

设计约束：

- 源语言和目标语言由当前 `PromptBuilder` / 翻译配置确定，在同一稳定配置内属于通用 `SYSTEM` 内容，不随批次重复进入 `USER`。
- 模式层只声明三选一的模式标签，不新增“翻译模式专属规则”。
- 具体分类仍是本批次数据，例如人名、地名、物品、对话。
- 动态术语表只从原 `SYSTEM` 迁移到断点 B 之后的 `USER`；内容、顺序、约束强度及产生方式保持不变。
- 固定示例必须是稳定静态内容，不得插入当前批次文本或动态术语。
- 不为断点 B 之后的动态后缀建立第三个显式缓存断点。

## 范围

### 本 Story 包含

- 将现有翻译 `SYSTEM` 提示词整理成结构清晰的通用稳定块。
- 增加仅包含模式标签的稳定 `SYSTEM` 块。
- 将具体分类、现有动态术语和待翻译 JSON 放入动态 `USER` 块。
- 把当前具体分类归一化成“实体短文本 / 对话 / 长文本”三种模式。
- 定义两个内部缓存标记 A、B，以及 Provider 适配器如何消费并清理标记。
- 官方 OpenAI 的稳定 cache key、显式断点和新旧模型兼容策略。
- Anthropic 的两个显式 `cache_control` 断点。
- 普通请求与流式请求共用同一套缓存转换逻辑。
- 明确保护现有精确直填流程和动态术语行为。

### 本 Story 不包含

- 不修改 `AutoTranslator._run_batch` 中现有精确全等词条直填代码，不调整其执行顺序，也不移除该流程。
- 不解决“Chest”在不同上下文中可能表示“胸部”或“箱子”的歧义；该问题不通过本 Story 改写直填逻辑。
- 不优化动态术语的筛选、排序、裁剪、分级、上下文匹配或 token 预算。
- 不改变术语管理器、在途术语、全等词条替换及其他术语来源。
- 不引入本地翻译结果缓存、本地计算缓存或动态后缀缓存。
- 不新增模式专属翻译规则。
- 不修改抽取任务提示词。
- 不为非官方 OpenAI 兼容端点发送 Provider 私有缓存参数。
- 不实现代码，不执行 changelog 归档。

## 现状与必须保留的行为

### 当前提示词形态

当前翻译提示词主要由一个 `SYSTEM` 消息承载：通用翻译规则、语言对和动态术语混在一起；`USER` 主要承载具体分类及待翻译 JSON。因为术语随批次变化，稳定前缀难以形成清晰、可控的缓存边界。

### 当前动态术语形态

当前术语按既有 `matched_terms` 迭代顺序渲染，每项保持：

```text
  原词 → 译词
```

重构后仍使用同一内容、同一迭代顺序和同一“必须遵循”语义。若当前实现原本在无术语时省略术语段，则重构后也应省略，不能为了模板整齐改变行为。

### 受保护的精确直填流程

`AutoTranslator._run_batch` 中现有精确全等词条直填是独立于大模型提示词的既有流程。实现本 Story 时：

- 不修改该段代码。
- 不把该逻辑搬进 PromptBuilder 或 Provider。
- 不改变直填发生在 AI 翻译前后的现有顺序。
- 不用新的术语缓存、预替换或本地计算方案绕过它。

该边界是强制性非回归要求。

## 模式归一化

PromptBuilder 负责把具体分类归一化为稳定的三种模式标签：

```text
实体短文本
  种族与派系、人名、地名、书名、物品、法术技能、任务名、互动

对话
  对话

长文本
  长文本、未分类，以及当前无法识别的未来分类
```

规则：

- 具体分类原值仍进入动态 `USER`，不得只传归一化模式。
- 未知分类回退到“长文本”，避免新增分类破坏翻译请求。
- 模式块只输出模式名，不根据模式追加不同翻译规则或示例。

## 提示词模板设计

`data/prompts/langs/zh_CN.toml` 的翻译段建议调整为以下键，抽取段保持原样：

```toml
[translation]
common_system = """..."""
mode_system = """<translation_mode>$translation_mode</translation_mode>"""
user = """...$batch_type...$terms_section...$input_json..."""
```

同时把内置 fallback 常量同步拆为：

```python
_DEFAULT_TRANSLATION_COMMON_SYSTEM
_DEFAULT_TRANSLATION_MODE_SYSTEM
_DEFAULT_TRANSLATION_USER
```

加载契约：

- `PromptBuilder.__init__()` 完成 TOML 读取和通用 `SYSTEM` 的一次性渲染。
- 新模板占位符增加 `$translation_mode` 与 `$terms_section`。
- 现有 `$game_name`、`$format_notes`、`$source_lang`、`$target_lang`、`$batch_type`、`$input_json` 保持有效。
- `extraction.system`、`extraction.user` 及其 fallback 不做修改。
- 若需要兼容仓库外的旧语言配置，旧 `translation.system` 只能作为 `common_system` 的迁移输入；迁移适配不得把动态术语重新追加到该 system。

### 通用 SYSTEM 模板

建议在现有提示词配置文件中将通用部分拆成可审查的小节，但运行时拼成一个稳定 `SYSTEM` 消息：

```text
<game_and_language_pair>
游戏：$game_name
源语言：$source_language
目标语言：$target_language
</game_and_language_pair>

<role_and_objective>
...
</role_and_objective>

<instruction_priority>
...
</instruction_priority>

<general_translation_rules>
...
若 USER 提供 mandatory_terminology，必须沿用现有术语约束语义。
</general_translation_rules>

<format_protection_rules>
...
</format_protection_rules>

<quality_checks>
...
</quality_checks>

<json_output_protocol>
...
</json_output_protocol>

<fixed_examples>
...
</fixed_examples>
```

要求：

- 保留当前有效翻译规范，只做结构化归位，不借本 Story 改写翻译策略。
- 模板中不得出现 `batch_type`、动态术语或本批次输入 JSON。
- 游戏、语言对或通用模板变化时，自然产生新的稳定 cache key。
- 不允许通过无意义填充扩大提示词；缓存资格应来自真实规则内容。
- 实现时必须按目标模型 tokenizer 测量 A、B 前缀；前缀未达到 Provider 最小缓存门槛时，只记录“本配置暂不可缓存”，不得为了凑够 token 改写翻译规则或堆入无意义示例。

### 模式 SYSTEM 模板

```text
<translation_mode>$translation_mode</translation_mode>
```

要求：

- `$translation_mode` 只能是“实体短文本”“对话”“长文本”之一。
- 不包含具体分类。
- 不包含模式专属规则。
- 不包含术语或输入文本。

### 动态 USER 模板

```text
<task_category>$batch_type</task_category>

$terms_section

<translation_entries>
$input_json
</translation_entries>

请严格按 SYSTEM 中的 JSON 输出协议返回结果。
```

`$terms_section` 的生成规则：

```text
<mandatory_terminology>
  原词 → 译词
  原词 → 译词
</mandatory_terminology>
```

- 内容直接来自现有 `matched_terms`。
- 不排序、不转为新的 JSON 协议、不建立强制/参考分层。
- 保持现有遍历顺序。
- 无术语时整个 `$terms_section` 为空，避免引入新的空段语义。

`$input_json` 保持现有序列化协议、字段名、条目顺序和缩进策略。

## PromptBuilder 契约

对外接口保持兼容，不要求调用方重写批次流程：

```python
class PromptBuilder:
    def build_translation_prompt(
        self,
        entries: list[TranslationEntry],
        matched_terms: Mapping[str, str],
        batch_type: str,
    ) -> list[dict[str, object]]:
        ...
```

内部建议状态与辅助函数：

```python
self._translation_common_system: str
self._translation_cache_key: str

def _translation_mode(batch_type: str) -> Literal[
    "实体短文本", "对话", "长文本"
]: ...

def _build_mode_system(translation_mode: str) -> str: ...

def _format_terms_section(
    matched_terms: Mapping[str, str],
) -> str: ...
```

返回消息顺序固定为：

```python
[
    common_system_message_with_breakpoint_a,
    mode_system_message_with_breakpoint_b,
    dynamic_user_message,
]
```

不允许 Provider、翻译器或流式分支自行重新拼装提示词。

## 内部缓存标记协议

新增独立模块，例如 `src/transbridge/infra/prompt_cache.py`，集中定义内部元数据，避免 PromptBuilder 感知 Provider 私有字段。

```python
from typing import Literal, TypedDict

PROMPT_CACHE_METADATA_KEY = "_transbridge_prompt_cache"

class PromptCacheDirective(TypedDict):
    key: str
    breakpoint: Literal["A", "B"]

def build_prompt_cache_key(
    namespace: str,
    stable_prefix: str,
) -> str: ...

def attach_prompt_cache_directive(
    message: dict[str, object],
    *,
    cache_key: str,
    breakpoint: Literal["A", "B"],
) -> dict[str, object]: ...

def extract_prompt_cache_directives(
    messages: list[dict[str, object]],
) -> tuple[list[dict[str, object]], tuple[PromptCacheDirective, ...]]: ...

def is_official_openai_base_url(base_url: str) -> bool: ...

def openai_cache_capability(
    model: str,
) -> Literal["explicit_breakpoints", "automatic_prefix", "disabled"]: ...
```

缓存 key 建议格式：

```text
transbridge.translation.v2.<sha256(common_system)[:24]>
```

关键约束：

- key 只由通用稳定 `SYSTEM` 的最终渲染文本和固定 namespace 计算。
- A、B 使用同一个 key；B 通过 breakpoint 值区分，不把模式写进 key。
- 这样不同模式仍可复用 A，共同模式可继续复用 A+B。
- 每次翻译请求最多各有一个 A 和一个 B，顺序必须为 A 在 B 前。
- A 只能挂在通用 `SYSTEM`，B 只能挂在模式 `SYSTEM`。
- `extract_prompt_cache_directives` 必须返回彻底移除内部元数据的标准消息。
- 内部标记无效时记录 warning，并降级为无缓存的普通请求；不能中断翻译。
- 日志不得输出完整系统提示词、动态术语或待翻译文本。

## Provider 适配契约

### 官方 OpenAI

当前仓库的 OpenAI 路径统一使用 Chat Completions。请求辅助函数必须服务于 `OpenAICompatibleClient.chat()` 和 `chat_stream()`：

```python
def prepare_openai_chat_cache_request(
    *,
    model: str,
    base_url: str,
    messages: list[dict[str, object]],
) -> OpenAIPromptCacheRequest: ...
```

`OpenAIPromptCacheRequest` 至少表达：

```python
class OpenAIPromptCacheRequest(TypedDict):
    messages: list[dict[str, object]]
    request_options: dict[str, object]
    cache_mode: Literal[
        "explicit_breakpoints",
        "automatic_prefix",
        "disabled",
    ]
```

行为要求：

- 始终先剥离内部元数据。
- 当前配置没有单独的 `openai` Provider 枚举；只有移除尾部 `/` 后严格等于 `https://api.openai.com/v1` 的 `base_url` 才按官方 OpenAI 处理。Azure、自建网关、代理和其他 URL 均按非官方兼容端点处理。
- 对已验证支持显式断点的官方模型（实现时的保守基线为 GPT-5.6 及以后）：把 A、B 所在的 system 字符串转换成 Chat Completions `text` content block，并在对应 block 添加 `prompt_cache_breakpoint: {"mode": "explicit"}`。
- 显式断点请求同时传递 `prompt_cache_options: {"mode": "explicit"}`，关闭最新 user 消息的隐式断点，避免动态后缀产生缓存写入。
- 同时传递稳定 `prompt_cache_key`，值来自通用 `SYSTEM`。
- 对仅支持自动前缀缓存的官方旧模型：保留相同稳定消息顺序并传递稳定 key，不发送未知显式字段。
- 对不支持缓存或能力未知且无法安全探测的模型：降级为标准请求。
- 正常请求与流式请求必须调用同一辅助函数，不得出现参数漂移。
- Provider 返回“不支持缓存参数”的确定性错误时，只允许去除缓存参数重试一次。

显式断点支持必须由集中式、可测试的保守能力表决定，不能用散落的模型名字符串判断。未知模型默认走自动前缀缓存，不得猜测其支持显式字段。

OpenAI 官方约束记录：

- 缓存只对完全相同的前缀命中，因此静态内容在前、动态内容在后是本 Story 的核心收益来源。
- GPT-5.6 及以后显式断点前的完整渲染前缀至少需要 1,024 tokens；更早模型的自动缓存门槛依模型为 1,024～2,048 tokens。
- 一个请求可以使用多个显式断点；本 Story 只使用 A、B 两个。
- 具体 SDK 参数优先使用已锁定 SDK 版本支持的正式关键字；若项目保留过宽的最低依赖范围，则实现时同步收窄版本或在单一适配函数内使用 `extra_body`，不能散布兼容分支。

协议依据：[OpenAI Prompt caching 官方文档](https://developers.openai.com/api/docs/guides/prompt-caching)。

### Anthropic

Anthropic 转换器必须保留两个 `SYSTEM` 块，不能沿用“后一个 system 覆盖前一个”的处理方式：

```python
system = [
    {
        "type": "text",
        "text": common_system,
        "cache_control": {"type": "ephemeral"},
    },
    {
        "type": "text",
        "text": mode_system,
        "cache_control": {"type": "ephemeral"},
    },
]
```

行为要求：

- A、B 分别映射到对应稳定 `SYSTEM` content block 的 `cache_control`。
- 默认采用 Anthropic 支持的短时 ephemeral 缓存语义；不擅自使用更长 TTL。
- 动态 `USER` 不加 `cache_control`。
- 普通与流式请求共用相同转换器。
- 不把 TransBridge 内部元数据传给 SDK。
- 能力不支持或请求被明确拒绝时，去掉 `cache_control` 降级重试一次。

### 非官方 OpenAI 兼容端点

- 只接收剥离内部缓存元数据后的标准 `messages`。
- 不发送 `prompt_cache_key`、显式断点、Anthropic `cache_control` 或其他 Provider 私有字段。
- 保持通用 `SYSTEM`、模式 `SYSTEM`、动态 `USER` 的顺序。
- 不因未启用缓存改变翻译提示词语义。

## 数据流

```text
PromptBuilder 初始化
  ├─ 渲染通用 SYSTEM
  └─ 计算 common cache key

每个翻译批次
  ├─ 接收具体分类、现有 matched_terms、待翻译条目
  ├─ 将具体分类归一化为三种模式之一
  ├─ 生成通用 SYSTEM + 标记 A
  ├─ 生成模式 SYSTEM + 标记 B
  ├─ 按原样生成动态术语段
  ├─ 生成动态 USER
  └─ 返回固定顺序的三条消息

Provider 发送前
  ├─ 提取并删除内部 A/B 标记
  ├─ 官方 OpenAI：按能力映射 key 与断点
  ├─ Anthropic：映射两个 system cache_control
  └─ 兼容端点：仅发送清理后的标准消息
```

## 错误与降级策略

- 模式无法识别：回退到“长文本”，保留原具体分类供模型参考。
- 缓存标记缺失：按普通无缓存请求发送。
- 只有 A 没有 B：允许无缓存降级，不自动猜测 B 的位置。
- A/B 重复、倒序、key 不一致或挂载角色错误：记录 warning，剥离全部内部标记并按普通请求发送。
- Provider 不支持缓存参数：移除缓存参数重试一次；第二次失败按原异常路径上抛。
- 动态术语为空：省略术语段，不影响 A/B。
- 提示词或语言配置变化：common system 哈希变化，自然生成新 key，无需手工失效旧缓存。

## 文件落点

实现阶段预计涉及：

- `src/transbridge/ai_translator/prompt_builder.py`
  - 生成通用、模式、动态三层消息。
  - 归一化三种模式。
  - 保持现有术语渲染语义。
- `src/transbridge/infra/prompt_cache.py`（新增）
  - 定义 A/B 内部标记、key 计算、验证和清理函数。
- `src/transbridge/infra/llm_client.py`
  - `OpenAICompatibleClient`：识别官方 base URL，转换 A/B、普通/流式参数并降级。
  - `AnthropicClient`：保留两个 system block，转换 `cache_control` 并降级。
  - 非官方端点：清理内部元数据，禁止泄漏 Provider 私有字段。
- `data/prompts/langs/zh_CN.toml`
  - 拆出通用 `SYSTEM`、模式 `SYSTEM`、动态 `USER` 模板。
- `tests/ai_translator/test_prompt_builder.py`（新增）
  - PromptBuilder 结构、A/B 标记、Provider 映射、降级和非回归测试。
- `tests/infra/test_llm_client_prompt_cache.py`（新增，或仓库等价 infra 测试目录）
  - OpenAI/Anthropic/兼容端点的请求形态和流式一致性测试。

禁止修改：

- `AutoTranslator._run_batch` 中现有精确直填逻辑。
- 术语管理器的匹配与产出规则。
- 抽取提示词及抽取 Provider 行为。

## 实施步骤

1. 为当前 PromptBuilder 行为补充快照式测试，记录现有 JSON 输入、术语内容/顺序和输出协议。
2. 新增 `prompt_cache.py`，实现稳定 key、A/B 标记、验证与清理。
3. 将翻译提示词配置拆成通用 `SYSTEM`、模式 `SYSTEM` 和动态 `USER` 三部分。
4. 在 PromptBuilder 中实现模式归一化和三消息输出，保持公开签名不变。
5. 验证术语段仅迁移位置，内容、顺序、空值行为和约束语义不变。
6. 在现有 `OpenAICompatibleClient` 中增加官方 base URL 判定、集中式模型能力判断和普通/流式共用转换器。
7. 为 Anthropic 修复多 `SYSTEM` 块转换并为 A/B 添加显式 `cache_control`。
8. 确保非官方兼容端点只收到清理后的标准消息。
9. 增加缓存参数拒绝时的一次性无缓存降级。
10. 执行单元测试和回归测试，确认精确直填相关代码未发生任何差异。

## 测试策略

### PromptBuilder 单元测试

- 返回消息严格为 `system(A) -> system(B) -> user`。
- 通用 `SYSTEM` 包含游戏与语言对、角色、优先级、通用规范、格式保护、质量检查、JSON 协议和固定示例。
- 通用 `SYSTEM` 不包含具体分类、动态术语和输入文本。
- 模式 `SYSTEM` 只包含三种模式之一，不包含专属规则。
- 各实体分类正确映射为“实体短文本”。
- 对话映射为“对话”。
- 长文本、未分类和未知分类映射为“长文本”。
- 动态 `USER` 保留原具体分类。
- 术语逐项文本、迭代顺序和“必须遵循”语义与改造前一致。
- 无术语时省略术语段。
- 输入 JSON 协议与改造前一致。
- 相同通用 `SYSTEM` 产生相同 key；改变游戏、语言对或通用模板后 key 改变。
- 不同具体分类、模式、术语和输入文本不会改变 common cache key。
- 测量 A、B 的实际 token 数；低于目标 Provider 门槛时报告不可缓存但不填充提示词。

### OpenAI 单元测试

- 显式断点模型正确映射 A、B 和稳定 key。
- 显式断点模型发送 `prompt_cache_options.mode=explicit`，两个稳定 system content block 分别带 `prompt_cache_breakpoint.mode=explicit`。
- 显式模式不在动态 `USER` 后增加缓存断点。
- 自动前缀模型不收到未知显式字段。
- 不支持缓存的模型获得标准清洁消息。
- 普通与流式请求使用同一转换结果。
- 缓存参数被拒绝时仅无缓存重试一次。

### Anthropic 单元测试

- 两个 `SYSTEM` 块都被保留，顺序不变。
- A、B 两个 block 都带 ephemeral `cache_control`。
- 动态 `USER` 不带缓存标记。
- 普通与流式结果一致。
- SDK 请求中不存在 `_transbridge_prompt_cache`。

### 兼容端点单元测试

- 消息内不存在内部缓存元数据。
- 请求参数不存在官方 OpenAI 或 Anthropic 私有缓存字段。
- 三条标准消息顺序不变。

### 非回归测试

- `AutoTranslator._run_batch` 的精确全等词条直填代码未修改。
- 直填命中与未命中的现有执行路径、结果形态保持不变。
- 动态术语匹配结果、顺序和渲染内容保持不变。
- 翻译结果 JSON 解析、条目对应关系、重试与流式拼接保持兼容。
- 抽取任务提示词和请求参数不受影响。

## 验收标准

- [ ] 运行时提示词结构严格为通用 `SYSTEM(A)`、模式 `SYSTEM(B)`、动态 `USER`。
- [ ] 通用 `SYSTEM` 包含稳定的游戏与语言对及现有通用翻译规则。
- [ ] 模式 `SYSTEM` 只声明“实体短文本 / 对话 / 长文本”，没有模式专属规则。
- [ ] 具体分类、现有动态术语和待翻译文本位于断点 B 之后。
- [ ] 动态术语仅迁移位置，其内容、顺序、约束语义和生成流程没有变化。
- [ ] 无第三缓存断点，无动态后缀缓存，无本地翻译结果缓存。
- [ ] 官方 OpenAI 对支持的模型使用稳定 key 和 A/B 显式断点；旧模型安全使用自动前缀缓存或降级。
- [ ] 仅规范化后的官方 OpenAI base URL 可以收到 OpenAI 私有缓存字段。
- [ ] Anthropic 在两个稳定 `SYSTEM` block 上使用显式 ephemeral `cache_control`。
- [ ] 非官方兼容端点只收到标准清洁消息和标准参数。
- [ ] 普通与流式请求使用相同的提示词和缓存转换逻辑。
- [ ] 缓存能力失败不会阻断翻译，可安全无缓存降级一次。
- [ ] 现有精确全等词条直填代码没有任何修改、移动或替代。
- [ ] 抽取任务行为不变。
- [ ] 单元测试覆盖结构、模式映射、术语非回归、A/B 映射、兼容端点清理及降级路径。

## 交接说明

实现模型应把本 Story 视为范围边界，而不是重新讨论翻译质量的入口。若实现过程中发现当前代码与本文接口命名不完全一致，可以调整命名和文件落点，但必须保持以下不变量：

1. 两个稳定 `SYSTEM` 块与 A/B 断点顺序不变。
2. 模式层只有模式标签。
3. 动态术语只迁移位置，不做任何质量优化。
4. 现有精确直填代码不可修改。
5. Provider 私有字段不能泄漏到非官方兼容端点。

如必须突破任一不变量，应停止实现并先向用户确认，不得自行扩大范围。
