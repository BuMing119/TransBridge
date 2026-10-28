# post_processor 模块

## 职责

AI 翻译后处理系统，负责译文质量检查、一致性校验、格式验证、修复、润色与裁决。

---

## 文件清单

| 文件 | 职责 |
|------|------|
| `base.py` | 后处理器抽象基类，定义统一接口 |
| `consistency_checker.py` | 一致性检查器（术语一致性） |
| `format_validator.py` | 格式验证器（占位符、标记、引号匹配等） |
| `quality_gate.py` | 质量关卡检测器（检测明显质量问题：通过/失败/待定） |
| `llm_refiner.py` | LLM修复者（专注修复检测到的问题） |
| `polisher.py` | **LLM润色器**（专注提升译文流畅度和风格） |
| `llm_arbiter.py` | LLM裁决者（最终质量判定） |
| `post_processor.py` | 后处理主控器，协调各阶段执行 |

---

## 核心数据类

### PostProcessIssue

```python
@dataclass
class PostProcessIssue:
    entry_id: str              # 问题条目ID
    issue_type: str            # 问题类型（见下表）
    severity: str              # 严重程度: error/warning/info
    message: str               # 问题描述
    original: str              # 原文
    translation: str           # 译文
    suggestion: str = ""       # 修复建议（可选）
```

**issue_type 取值**:

| 值 | 说明 | 来源检查器 | 严重级别 |
|----|------|-----------|---------|
| `term_mismatch` | 术语不匹配 | consistency_checker | warning |
| `placeholder_missing` | 占位符缺失 | format_validator | error |
| `placeholder_mismatch` | 占位符不匹配 | format_validator | error |
| `format_tag_broken` | 格式标记损坏/非法字符 | format_validator | warning/error |
| `quote_mismatch` | 引号不匹配 | format_validator | warning |
| `low_quality` | 质量检查不通过 | quality_gate | warning/error |
| `refine_failed` | LLM修复失败 | llm_refiner | error |
| `polish_failed` | LLM润色失败 | polisher | error |
| `arbitration_reject` | 裁决打回 | llm_arbiter | error |
| `arbitration_pending` | 裁决待定 | llm_arbiter | warning |

### PostProcessResult

```python
@dataclass
class PostProcessResult:
    total_checked: int         # 检查条目总数
    issue_count: int           # 问题总数
    issues: list[PostProcessIssue]  # 问题列表
    auto_fixed: int            # 自动修复数
    needs_review: list[str]    # 需人工审核的条目ID
```

### 后处理恢复记录

当前记录位于 `application/translation/postprocess_checkpoint.py`，由应用层后处理任务适配负责。综合校对使用 `application/translation/proofread_checkpoint.py`。两者根据固定输入和运行身份恢复，计算器不维护按插件名称写入的第二份进度文件。

---

## 核心类详解

### BaseChecker（抽象基类）

**路径**: `src/transbridge/ai_translator/post_processor/base.py`

**职责**: 定义所有后处理检查器的统一接口。

```python
class BaseChecker(ABC):
    @abstractmethod
    def check(self, entry: TranslationEntry) -> list[PostProcessIssue]:
        """检查单个条目，返回问题列表。"""
        ...

    @abstractmethod
    def check_batch(self, entries: list[TranslationEntry]) -> list[PostProcessIssue]:
        """批量检查（用于需要跨条目分析的检验器）。"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """检查器名称。"""
        ...
```

---

### ConsistencyChecker

**路径**: `src/transbridge/ai_translator/post_processor/consistency_checker.py`

**职责**: 检查术语一致性。

#### 术语库加载方式

直接从硬盘加载合并后的术语缓存，无需依赖 `TermDatabaseManager`：
- 缓存路径：`data/ai_translator/{esp_stem}/cache/merged_terms.json`
- 该缓存由 `TermDatabaseManager` 在 `load_all()` 时自动生成
- 支持离线检查（无需配置 API）

#### 检查规则

| 规则 | 说明 |
|------|------|
| 术语命中检查 | 原文中的术语是否在译文中使用了标准译法 |
| 术语变体检查 | 原文中出现术语变体时，同样提示使用主术语的标准译法 |

#### 术语变体支持

从 v0.12 开始，`ConsistencyChecker` 支持检查术语变体：

**匹配策略**

1. **主术语优先**: 如果原文同时匹配主术语和变体，优先使用主术语报告
2. **变体映射**: 变体匹配时，提示使用主术语的标准译法
3. **去重处理**: 同一主术语的多个变体被匹配时，只报告一次

**问题消息格式**

```
# 主术语匹配
术语 '{main_term}' 标准译法为 '{translation}'，译文未使用

# 变体匹配
术语 '{main_term}'（变体 '{matched_variant}'）标准译法为 '{translation}'，译文未使用
```

**示例场景**

```json
// 术语库条目
{
  "term": "Skyrim",
  "translation": "天际",
  "variants": ["skyrim", "SKYRIM"]
}
```

| 原文 | 匹配形式 | 问题消息 |
|------|----------|----------|
| `"Welcome to Skyrim"` | 主术语 | `术语 'Skyrim' 标准译法为 '天际'...` |
| `"welcome to skyrim"` | 变体 | `术语 'Skyrim'（变体 'skyrim'）标准译法为 '天际'...` |

**匹配算法**

1. **精确全等匹配**: 原文与术语/变体完全一致（大小写可选）
2. **词边界子串匹配**: 使用正则 `\bterm\b` 确保完整单词匹配

---

### FormatValidator

**路径**: `src/transbridge/ai_translator/post_processor/format_validator.py`

**职责**: 验证格式标记、占位符、引号等的完整性。

#### 检查规则

| 规则 | 说明 |
|------|------|
| 占位符完整性 | `%s`、`%d`、`{0}`等数量和顺序与原文一致 |
| 格式标记保留 | `<br>`、`[pagebreak]`、`\n`等被正确保留 |
| 引号匹配 | 引号、方括号、圆括号正确闭合 |
| 非法字符 | 过滤控制字符、零宽字符等可能导致游戏崩溃的字符 |

#### 非法字符检测详情

**控制字符（严重：error级别）**

| 范围 | Unicode | 说明 |
|------|---------|------|
| U+0000 - U+0008 | NUL, SOH...BS | 字符串终止符、设备控制，可能导致文本截断 |
| U+000B - U+000C | VT, FF | 垂直制表、换页，可能导致布局错乱 |
| U+000E - U+001F | SO...US | 设备控制、转义序列，可能触发意外行为 |
| U+007F - U+009F | DEL, C1控制字符 | 删除字符、控制序列，可能导致编码问题 |

**零宽字符（警告：warning级别）**

| Unicode | 字符 | 名称 | 说明 |
|---------|------|------|------|
| U+FEFF | BOM | 字节顺序标记 | 文件头标识，出现在文本中间会导致解析错误 |
| U+200B | ZWSP | 零宽空格 | 不可见，可能导致单词断行异常 |
| U+200C | ZWNJ | 零宽非连接符 | 不可见，可能影响字符连接 |
| U+200D | ZWJ | 零宽连接符 | 不可见，可能产生意外的字符组合 |
| U+2060 | WJ | 单词连接符 | 不可见，影响自动换行 |

**风险说明**

上古卷轴5使用自定义字符串格式，控制字符可能被解释为指令而非文本。`NUL (U+0000)`在C/C++字符串中作为终止符，会导致文本被截断。零宽字符肉眼不可见，导致"看起来一样但就是不对"的调试困难。

---

### QualityGateChecker

**路径**: `src/transbridge/ai_translator/post_processor/quality_gate.py`

**职责**: 调用轻量级LLM判断翻译是否存在明显质量问题，输出三态结果：通过/失败/待定。

#### 检测逻辑（LLM判断）

| 状态 | 判定标准 | 处理方式 |
|------|----------|----------|
| **PASS** | LLM判定无明显质量问题 | 不生成issue，译文通过 |
| **FAIL** | LLM判定有明显质量问题 | 生成error级别issue，建议打回重翻 |
| **UNCERTAIN** | LLM无法确定是否有问题 | 生成warning级别issue，需人工审核 |

#### LLM检测维度

LLM基于以下维度判断：
1. 是否漏翻、错翻
2. 是否过度删减或添加无关内容
3. 格式标记（`<br>`、`%s`等）是否完整
4. 术语使用是否正确
5. 是否有明显语言错误

#### 提示词配置

QualityGateChecker 从通用 TOML 模板加载提示词，源语言和目标语言在运行时注入：

```
data/prompts/quality_gate/
└── default.toml    # 与目标语言无关的质量检测提示词
```

**配置文件结构** (`default.toml`):
```toml
[single_check]
system = """You are a $game_name localization quality inspector for translations from $source_lang into $target_lang...
"""
user = """Source: $original
Translation: $translation
Context: $context
Terminology: $terms

Judge the translation quality..."""

[batch_check]
system = """You are a $game_name localization quality inspector for multiple $target_lang translations...
"""
```

**模板变量**:
| 变量 | 来源 | 说明 |
|------|------|------|
| `$game_name` | `data/prompts/games/{game_profile}.toml` | 游戏名称 |
| `$source_lang` | `data/prompts/langs/{target_lang}.toml` | 源语言名称 |
| `$target_lang` | `data/prompts/langs/{target_lang}.toml` | 目标语言名称 |
| `$original` | 动态传入 | 原文内容 |
| `$translation` | 动态传入 | 译文内容 |
| `$context` | 动态传入 | 上下文信息 |
| `$terms` | 动态传入 | 术语表 |

文件缺失或解析失败时，自动 fallback 到内置默认值。

#### 批量检测

QualityGateChecker 支持批量检测，一次LLM调用可检测多条，提高效率。

```python
checker = QualityGateChecker(
    llm_client=llm_client,      # LLM客户端（必需）
    term_manager=term_manager,  # 术语管理器（可选）
    batch_size=10,              # 批量检测条数
    game_profile="skyrim_se",   # 游戏配置文件名
    target_lang="zh_CN",        # 目标语言配置文件名（决定加载哪个提示词文件）
)
```

**批量Prompt格式**:
```json
[
  {"entry_id": "xxx", "verdict": "pass", "reason": "", "issues": []},
  {"entry_id": "yyy", "verdict": "fail", "reason": "漏翻", "issues": ["缺少后半句"]}
]
```

**注意**：质量关卡检测需要配置LLM，未配置时自动跳过。

---

### LLMRefiner

**路径**: `src/transbridge/ai_translator/post_processor/llm_refiner.py`

**职责**: **专注修复问题**。根据检测阶段发现的问题，使用LLM生成修复后的译文。

#### 核心数据类

**RefineResult** - 修复结果

```python
@dataclass
class RefineResult:
    entry_id: str
    original_translation: str
    refined_translation: str
    fixes_applied: list[FixApplied]      # 应用的修复项
    confidence: float                    # 0-1，对修复结果的信心度
    needs_arbitration: bool              # 是否需要裁决（信心度低或改动大）
    note: str                            # 额外说明
```

**FixApplied** - 应用的修复项
```python
@dataclass
class FixApplied:
    issue_type: str          # 原问题类型
    original_problem: str    # 原问题描述
    fix_description: str     # 修复方式说明
```

#### 功能

| 功能 | 说明 |
|------|------|
| 问题修复 | 根据检测到的问题，使用LLM生成修复后的译文 |
| 质量评估 | 输出 confidence 分数，标记是否需要人工裁决 |
| 批量处理 | 支持批量修复，减少LLM调用次数 |

#### 修复原则

1. 必须保留原文的所有占位符（`%s`, `%d`, `{0}`等）
2. 必须保留原文的格式标记（`<br>`, `[pagebreak]`, `\n`等）
3. 术语必须使用提供的标准译法
4. 不得改变原文的语义
5. 引号、括号等必须正确闭合
6. **只修复检测到的问题，不过度改写**

#### 提示词配置

配置文件路径：`data/prompts/refinement/default.toml`

```toml
[refinement]
system = """You are a $game_name localization correction specialist for translations from $source_lang into $target_lang..."""
user = """[SOURCE]..."""
batch_system = """Correct multiple translations from $source_lang into $target_lang..."""
```

---

### LLMPolisher（润色器）

**路径**: `src/transbridge/ai_translator/post_processor/polisher.py`

**职责**: **专注提升译文质量**。对译文进行风格优化和流畅度提升，**无需前置问题检测**。

#### 与 LLMRefiner 的区别

| 维度 | LLMRefiner | LLMPolisher |
|------|------------|-------------|
| 输入 | 条目 + 问题列表 | 条目（无需问题列表） |
| 职责 | 修复检测出的问题 | 提升流畅度/风格 |
| 触发条件 | 仅针对有问题的条目 | 可配置范围（见下方） |
| Prompt | 强调"修复" | 强调"润色" |
| 输出 | `RefineResult` | `PolishResult` |

#### 核心数据类

**PolishResult** - 润色结果

```python
@dataclass
class PolishResult:
    entry_id: str
    original_translation: str
    polished_translation: str
    changes: list[dict]           # 改动说明列表
    confidence: float             # 润色信心度
    needs_arbitration: bool       # 是否需要裁决
    note: str                     # 额外说明
```

**changes 字段结构**:
```python
{
    "aspect": "fluency",          # 改动维度
    "before": "改动前片段",
    "after": "改动后片段",
    "reason": "改动理由"
}
```

#### 润色维度

| 维度 | 说明 |
|------|------|
| `fluency` | 流畅度优化，消除生硬表达 |
| `style` | 风格适配，符合游戏氛围 |
| `context` | 语境适配，保持角色性格 |
| `terminology` | 术语一致性优化 |

#### 润色级别

| 级别 | 说明 |
|------|------|
| `light` | 仅修正明显错误，保持原译文风格 |
| `moderate` | 适度优化流畅度和表达（默认） |
| `aggressive` | 深度润色，追求最佳表达 |

#### 提示词配置

配置文件路径：`data/prompts/polish/default.toml`

```toml
[polish]
system = """You are a $game_name localization polishing specialist for translations from $source_lang into $target_lang..."""
user = """[SOURCE]..."""
batch_system = """Polish multiple translations from $source_lang into $target_lang..."""
```

---

### LLMArbiter

**路径**: `src/transbridge/ai_translator/post_processor/llm_arbiter.py`

**职责**: 对修复和润色结果做最终判定，决定条目是接受、打回还是待审。

#### 核心数据类

**ArbiterDecision** - 裁决结果

```python
@dataclass
class ArbiterDecision:
    entry_id: str
    verdict: Literal["pass", "reject", "pending"]  # 最终裁决
    reason: str                                      # 裁决理由
    confidence: float                                # 裁决信心度 (0-1)
    suggested_action: str                            # 建议动作
    alternatives: list[str]                          # 替代方案
```

**ArbitrationContext** - 裁决上下文

```python
@dataclass
class ArbitrationContext:
    entry: TranslationEntry
    original_issues: list[PostProcessIssue]
    refine_result: RefineResult | None               # 修复结果
    polish_result: PolishResult | None               # 润色结果（新增）
    quality_gate_verdict: str | None                 # 原质量关卡判定
```

#### 裁决结果

| 裁决 | 判定标准 | 处理方式 |
|------|----------|----------|
| **pass** | 译文质量合格，修复/润色有效 | 接受最终译文，可以发布 |
| **reject** | 存在严重问题，修复/润色失败 | 打回重翻，必须重新翻译 |
| **pending** | 质量存疑，需要人工判断 | 标记为待审核，需人工确认 |

#### 快速判定规则（无需LLM）

对于明确的场景，Arbiter 会直接返回判定结果，无需调用LLM：

| 场景 | 判定 | 说明 |
|------|------|------|
| 无问题且无修复/润色 | pass | 直接通过 |
| 修复失败 (confidence=0) | pending/reject | 根据严格模式决定 |
| 修复信心度很高 (>0.9) | pass | 快速通过 |
| 修复信心度很低 (<0.5) | pending/reject | 根据严格模式决定 |
| 有严重error且未修复 | pending/reject | 根据严格模式决定 |

#### 严格模式

通过 `strict_mode` 参数控制：

- **普通模式** (`strict_mode=False`): uncertain → pending，倾向于人工审核
- **严格模式** (`strict_mode=True`): uncertain → reject，倾向于打回重翻

#### 提示词配置

配置文件路径：`data/prompts/arbitration/default.toml`

```toml
[arbitration]
system = """You are a $game_name localization quality arbiter for $target_lang translations of $source_lang source text..."""
user = """[SOURCE]...
[CORRECTED TRANSLATION]...
[POLISHED TRANSLATION]...
..."""
batch_system = """Make final quality decisions on multiple $target_lang translations of $source_lang source text..."""
```

---

### PostProcessorConfig

**路径**: `src/transbridge/ai_translator/post_processor/post_processor.py`

**配置项**:

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enable_consistency_check` | bool | True | 启用术语一致性检查 |
| `enable_format_validation` | bool | True | 启用格式验证 |
| `enable_quality_gate` | bool | True | 启用LLM质量关卡（需配置LLM） |
| `quality_gate_batch_size` | int | 10 | 质量关卡批量检测条数 |
| `enable_refinement` | bool | True | 启用LLM修复 |
| `refinement_batch_size` | int | 5 | 修复批量条数 |
| `enable_polish` | bool | False | **启用LLM润色（新增）** |
| `polish_scope` | str | "all" | 润色范围：`all`/`passed`/`has_issues` |
| `polish_level` | str | "moderate" | 润色强度：`light`/`moderate`/`aggressive` |
| `polish_batch_size` | int | 5 | 润色批量条数 |
| `enable_llm_arbitration` | bool | True | 启用LLM裁决 |
| `strict_arbitration` | bool | False | 严格模式（uncertain→reject） |
| `arbitration_batch_size` | int | 10 | 裁决批量条数 |
| `game_profile` | str | "skyrim_se" | 游戏配置文件名 |
| `target_lang` | str | "zh_CN" | 目标语言配置文件名 |

#### polish_scope 说明

| 值 | 说明 |
|-----|------|
| `all` | 润色所有有译文的条目 |
| `passed` | 只润色无问题的条目（检测通过的） |
| `has_issues` | 只润色有问题且已修复的条目 |

#### 从配置文件加载

`PostProcessorConfig` 可以从 `LLMConfig` 自动读取配置：

```python
# 从 data/paratranz_config.ini 的 [llm] 节读取 game_profile 和 target_lang
config = PostProcessorConfig.from_llm_config()
processor = PostProcessor(config)
```

#### 用户控制选项

后处理的启用/禁用由用户在 **AI翻译配置窗口** 控制，通过 `LLMConfig.enable_post_process` 字段持久化保存：

```ini
[llm]
enable_post_process = true
```

- **默认**: `true`（启用质量检查）
- **UI位置**: AITranslatorWindow → 翻译范围区 → "翻译后进行质量检查（需要额外LLM调用）"
- **影响**: 禁用后跳过整个后处理流程，包括术语一致性检查、格式验证和LLM质量检测

---

### PostProcessor（主控器）

**路径**: `src/transbridge/ai_translator/post_processor/post_processor.py`

**职责**: 协调五阶段执行，汇总结果，支持自动修复和 stage 更新。从 v0.13 开始支持**并发执行**、**暂停/停止同步**和**断点续传**。

#### 五阶段流程

```
阶段1: 检测（Detection）
    ├─► ConsistencyChecker（术语一致性）— 串行
    ├─► FormatValidator（格式验证）— 串行
    └─► QualityGateChecker（LLM质量关卡）— 并发
    
阶段2: 修复（Refinement）
    └─► LLMRefiner（修复检测到的问题）— 并发
    
阶段3: 润色（Polishing）
    └─► LLMPolisher（提升译文质量）— 并发
    
阶段4: 裁决（Arbitration）
    └─► LLMArbiter（最终质量判定）— 并发
    
阶段5: 执行（Execution）
    └─► 根据裁决结果更新 entry.stage — 串行
        - pass: stage=1（检查通过）
        - reject: stage=0（打回重翻）
        - pending: stage=2（待人工审核）
```

#### 并发执行

`process_entries()` 新增参数 `max_workers`（默认 1），用于控制涉及 LLM 阶段的并发线程数：

```python
post_processor.process_entries(
    entries,
    max_workers=llm_config.max_concurrent,  # 通常复用 LLM 并发配置
)
```

| 阶段 | 并发策略 | 说明 |
|------|----------|------|
| **检测-Consistency/Format** | 串行 | 纯本地计算，无需并发 |
| **检测-QualityGate** | `ThreadPoolExecutor` | 按 `quality_gate_batch_size` 拆分批次并发检测 |
| **修复** | `ThreadPoolExecutor` | 按 `refinement_batch_size` 拆分批次并发修复 |
| **润色** | `ThreadPoolExecutor` | 按 `polish_batch_size` 拆分批次并发润色 |
| **裁决** | `ThreadPoolExecutor` | 按 `arbitration_batch_size` 拆分批次并发裁决 |
| **执行** | 串行 | 纯内存状态更新，瞬间完成 |

共享结果（`issues`、`refine_results`、`polish_results`、`decisions`）均通过 `threading.Lock` 保护。

#### 同步机制（暂停 / 停止）

`process_entries()` 支持透传 `stop_event` 和 `pause_event`：

```python
post_processor.process_entries(
    entries,
    stop_event=stop_event,    # threading.Event
    pause_event=pause_event,  # threading.Event（clear=暂停，set=运行）
)
```

内部实现：
1. 若 `llm_client` 存在，启动**守护监控线程**，每 50ms 检查 event
2. 一旦检测到 `stop_event.is_set()` 或 `pause_event.is_cleared()`，立即调用 `llm_client.cancel()` 中断当前所有进行中的 LLM HTTP 请求
3. `QualityGateChecker` / `LLMRefiner` / `LLMPolisher` / `LLMArbiter` 内部捕获 HTTP 异常后返回 fallback 结果
4. 主控器在 worker 返回后检查 event：
   - **Stop**：直接退出循环，返回当前已累积的结果
   - **Pause**：丢弃该批次结果（不发布该批候选，不标记完成），等待 `pause_event.wait()` 恢复后继续重新执行该批次

#### 断点续传

后处理的恢复由应用层任务适配负责，`PostProcessor.process_entries()` 不再接受旧 `checkpoint` 参数，也不写入 `{esp_stem}_post_process.json`。

严格后处理使用 `application/translation/postprocess_checkpoint.py` 的当前候选记录；综合校对使用 `ProofreadCheckpoint` 保存已完成批次。记录绑定任务身份、固定输入和执行条件，只有匹配的成功结果可以复用。最终成果先保留，再通过公共逐条合入处理工作台变化；取消和部分失败不删除已经保存的成果。

#### 译文优先级

执行阶段确定最终译文时，优先级如下：

```
润色后译文 > 修复后译文 > 原始译文
```

即：如果某条目同时经历了修复和润色，**润色结果将作为最终译文**。

---

## 与AI翻译流程的集成

### 集成点（五阶段协作流程）

```text
AutoTranslator.translate()
    → 固定输入与执行配置
    → 当前翻译候选恢复与剩余批次执行
    → 按配置选择应用层后处理或综合校对
    → 生成并保存逐条成果及报告（保留成功部分）
    → 通过公共三方合入提交工作台变化
```


### 五智能体协作说明

| 智能体 | 角色 | 输入 | 输出 |
|--------|------|------|------|
| **QualityGateChecker** | 初检员 | 原文 + 译文 | 问题列表 + 质量判定 |
| **ConsistencyChecker** | 术语检查员 | 原文 + 译文 + 术语库 | 术语不匹配问题 |
| **FormatValidator** | 格式检查员 | 原文 + 译文 | 格式问题 |
| **LLMRefiner** | 修复者 | 原文 + 译文 + 问题列表 | 修复后译文 + confidence |
| **LLMPolisher** | 润色者 | 原文 + 译文 | 润色后译文 + confidence |
| **LLMArbiter** | 裁决官 | 完整上下文 + 修复/润色结果 | 最终裁决 |

### 配置选项

在 `PostProcessorConfig` 中控制五阶段流程：

```python
@dataclass
class PostProcessorConfig:
    # 阶段1: 检测器
    enable_consistency_check: bool = True
    enable_format_validation: bool = True
    enable_quality_gate: bool = True

    # 阶段2: 修复者
    enable_refinement: bool = True
    refinement_batch_size: int = 5

    # 阶段3: 润色者
    enable_polish: bool = False          # 默认关闭
    polish_scope: str = "all"            # all/passed/has_issues
    polish_level: str = "moderate"       # light/moderate/aggressive
    polish_batch_size: int = 5

    # 阶段4: 裁决者
    enable_llm_arbitration: bool = True
    strict_arbitration: bool = False
```

**当前调用链**：`AutoTranslator` 根据固定执行配置选择应用层后处理或综合校对适配，使用独立输入和当前候选恢复存储。`PostProcessor` 负责计算与报告，不拥有另一套旧文件恢复流程，也不直接替换整个工作台集合。

GUI 与助手把结果写入任务成果账本，提供查看、导出及再次合入；再次合入复用已经保存的结果，不重新请求模型。运行许可、取消及来源身份检查继续在各自边界执行。

### UI集成

- **Workbench Step2**: 新增"质量检查"按钮，手动触发后处理（待实现）
- **AI翻译完成弹窗**: 显示后处理摘要（问题数、需审核数、润色数等）
- **问题查看器**: 对话框展示所有问题，支持一键跳转编辑（待实现）

---

## 依赖关系

```
post_processor
    │
    ├─► converter (TranslationEntry, TranslationEntryCollection)
    │
    ├─► ai_translator/term_database (TermDatabaseManager，用于术语一致性检查)
    │
    ├─► ai_translator/llm_client (用于QualityGate/Refiner/Polisher/Arbiter的并发中断)
    │
    ├─► [可选] llm_refiner (用于LLM修复，需配置LLM)
    │
    ├─► [可选] polisher (用于LLM润色，需配置LLM)
    │
    └─► [可选] llm_arbiter (用于最终质量裁决，需配置LLM)
```

### 智能体协作关系

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  QualityGate    │     │                 │     │                 │
│  + Consistency  │────▶│   LLMRefiner    │────▶│   LLMArbiter    │
│  + Format       │     │  （修复问题）    │     │  （最终裁决）    │
└─────────────────┘     └─────────────────┘     └────────▲────────┘
         │                                               │
         │         ┌─────────────────┐                   │
         └────────▶│   LLMPolisher   │───────────────────┘
                   │  （润色优化）    │
                   └─────────────────┘
```

---

## 提示词配置详解

### 目录结构

```
data/prompts/
├── games/
│   └── {game_profile}.toml      # 游戏专属信息（名称、格式标记等）
├── langs/
│   └── {target_lang}.toml       # 语言元数据与可选示例
├── translation/
│   └── default.toml             # 通用翻译提示词
├── extraction/
│   └── default.toml             # 通用术语抽取提示词
├── quality_gate/
│   └── default.toml             # 通用质量检测提示词
├── refinement/                   # 修复提示词
│   └── default.toml
├── polish/                       # 润色提示词（新增）
│   └── default.toml
└── arbitration/                  # 裁决提示词
    └── default.toml
```

### 配置优先级

1. **按后处理阶段加载通用提示词文件**
   - 例：质量检测始终加载 `data/prompts/quality_gate/default.toml`
   - 不再读取 `{target_lang}.toml` 阶段文件；文件不存在或无效时使用 Python 内置默认值

2. **模板变量替换**
   - `$game_name` 来自 `data/prompts/games/{game_profile}.toml`
   - `$source_lang`, `$target_lang` 来自 `data/prompts/langs/{target_lang}.toml`

3. **动态变量**
   - `$original`, `$translation`, `$context`, `$terms` 在检测时传入

### 添加新语言支持

1. 创建 `data/prompts/langs/{lang}.toml`，提供 `[lang].name`、`[lang].source`、`[lang].target`，可选提供完整的 `[example].source` / `[example].target` 对。
2. 在 LLMConfig 中设置 `target_lang="{lang}"`。
3. 翻译、抽取和四个后处理阶段继续复用各自的 `default.toml`，通过 `$source_lang` 和 `$target_lang` 获得语言名称，无需复制模板。
4. 语言档案缺失、损坏或字段不完整时会在调用 LLM 前失败；不会静默回退到 `zh_CN`。

---

## 相关文档

- [ai_translator.md](ai_translator.md) - AI翻译模块
- [ARCHITECTURE.md](ARCHITECTURE.md) - 系统架构
