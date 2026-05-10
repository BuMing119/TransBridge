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
| `checkpoint.py` | 后处理断点续传数据类 |

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

### PostProcessCheckpoint

**路径**: `src/transbridge/ai_translator/post_processor/checkpoint.py`

**职责**: 后处理断点续传数据类，支持各阶段进度的持久化与恢复。

```python
@dataclass
class PostProcessCheckpoint:
    esp_stem: str
    completed_batches: dict[str, list[list[str]]]  # phase -> 已完成批次的 entry_id 列表
    issues: list[dict]                               # 已完成的检测问题
    refine_results: dict[str, dict]                  # entry_id -> RefineResult
    polish_results: dict[str, dict]                  # entry_id -> PolishResult
    decisions: dict[str, dict]                       # entry_id -> ArbiterDecision
```

**存储路径**: `data/ai_translator/{esp_stem}/{esp_stem}_post_process.json`

**方法**:
- `save(esp_path)` / `load(esp_path)` / `delete(esp_path)` — 持久化操作
- `is_batch_completed(phase, entry_ids)` — 检查某批次是否已完成
- `mark_batch_completed(phase, entry_ids)` — 标记批次完成
- `issue_to_dict()` / `issue_from_dict()` — `PostProcessIssue` 序列化
- `refine_result_to_dict()` / `refine_result_from_dict()` — `RefineResult` 序列化
- `polish_result_to_dict()` / `polish_result_from_dict()` — `PolishResult` 序列化
- `decision_to_dict()` / `decision_from_dict()` — `ArbiterDecision` 序列化

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

QualityGateChecker 的提示词从 TOML 配置文件加载，支持多语言扩展：

```
data/prompts/quality_gate/
├── zh_CN.toml    # 中文提示词
├── en.toml       # 英文提示词（未来扩展）
└── ja.toml       # 日文提示词（未来扩展）
```

**配置文件结构** (`zh_CN.toml`):
```toml
[single_check]
system = """你是 $game_name 本地化质量检测员...
"""
user = """原文：$original
译文：$translation
上下文：$context
术语表：$terms

请判断译文质量..."""

[batch_check]
system = """你是 $game_name 本地化质量检测员（批量模式）...
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

配置文件路径：`data/prompts/refinement/{target_lang}.toml`

```toml
[refinement]
system = """你是专业的游戏本地化修复专家..."""
user = """【原文】..."""
batch_system = """你是专业的游戏本地化修复专家（批量模式）..."""
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

配置文件路径：`data/prompts/polish/{target_lang}.toml`

```toml
[polish]
system = """你是专业的游戏本地化润色专家..."""
user = """【原文】..."""
batch_system = """你是专业的游戏本地化润色专家（批量模式）..."""
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

配置文件路径：`data/prompts/arbitration/{target_lang}.toml`

```toml
[arbitration]
system = """你是游戏本地化质量裁决官..."""
user = """【原文】...
【修复后译文】...
【润色后译文】...（如有）
..."""
batch_system = """你是游戏本地化质量裁决官（批量模式）..."""
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
   - **Pause**：丢弃该批次结果（不写入 checkpoint，不标记完成），等待 `pause_event.wait()` 恢复后继续重新执行该批次

#### 断点续传

`process_entries()` 支持传入 `checkpoint` 和 `esp_path`：

```python
from .checkpoint import PostProcessCheckpoint

checkpoint = PostProcessCheckpoint.load(esp_path) or PostProcessCheckpoint(esp_stem=stem)
post_processor.process_entries(
    entries,
    checkpoint=checkpoint,
    esp_path=esp_path,
)
```

**续传逻辑**：
- 各 LLM 阶段在批次执行前检查 fingerprint（`sorted(entry_ids)`）
- 若该批次已在 `checkpoint.completed_batches[phase]` 中，直接跳过
- 每完成一个批次后，在 `threading.Lock` 内更新结果并调用 `checkpoint.save(esp_path)`
- 执行阶段无需 checkpoint，直接从已恢复的 `decisions` 应用裁决

**断点文件**：`data/ai_translator/{esp_stem}/{esp_stem}_post_process.json`

#### 译文优先级

执行阶段确定最终译文时，优先级如下：

```
润色后译文 > 修复后译文 > 原始译文
```

即：如果某条目同时经历了修复和润色，**润色结果将作为最终译文**。

---

## 与AI翻译流程的集成

### 集成点（五阶段协作流程）

```
AutoTranslator.translate()
    │
    ▼
完成所有批次翻译
    │
    ▼
检查 LLMConfig.enable_post_process
    │
    ├─► true  → 加载 PostProcessCheckpoint
    │           │
    │           ▼
    │           阶段1: 检测（QualityGate + Consistency + Format）
    │           │   Consistency/Format 串行执行
    │           │   QualityGate 并发执行（max_workers）
    │           │   每批次完成后保存 checkpoint
    │           │
    │           ▼
    │           阶段2: 修复（LLMRefiner，如启用）
    │           │   并发执行（max_workers）
    │           │   每批次完成后保存 checkpoint
    │           │
    │           ▼
    │           阶段3: 润色（LLMPolisher，如启用）
    │           │   并发执行（max_workers）
    │           │   每批次完成后保存 checkpoint
    │           │
    │           ▼
    │           阶段4: 裁决（LLMArbiter）
    │           │   并发执行（max_workers）
    │           │   每批次完成后保存 checkpoint
    │           │
    │           ▼
    │           阶段5: 执行
    │           │   输出质量检查摘要（日志）
    │           │   根据裁决结果调整 entry.stage
    │           │
    │           ▼
    │           删除 PostProcessCheckpoint
    │           删除 ProgressCheckpoint
    │
    └─► false → 跳过质量检查
                删除 ProgressCheckpoint
                输出 "质量检查已跳过（用户设置）"

stage 调整逻辑:
    - verdict=pass: 更新为 stage=1（检查通过）
    - verdict=pending: 保持 stage=2（AI翻译待审核/拿不定）
    - verdict=reject: 重置为 stage=0（待翻译）
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

**实际代码** (translator.py):
```python
# 根据用户配置决定是否执行后处理
if not stop_event.is_set() and result.success_count > 0 \
        and getattr(self._cfg.llm_config, 'enable_post_process', True):
    _log(f"\n── 开始质量检查 ──")

    from .post_processor import PostProcessor, PostProcessorConfig
    from .post_processor.checkpoint import PostProcessCheckpoint

    pp_config = PostProcessorConfig.from_llm_config(self._cfg.llm_config)
    if not bool(self._llm):
        pp_config.enable_quality_gate = False
    post_processor = PostProcessor(pp_config)
    post_processor.register_default_checkers(
        term_manager=self._term_mgr,
        llm_client=self._llm,
    )

    # 加载或创建后处理断点
    pp_checkpoint = PostProcessCheckpoint.load(self._cfg.esp_path) \
        or PostProcessCheckpoint(esp_stem=esp_stem)

    # 五阶段协作处理（支持并发、暂停/停止、断点续传）
    pp_result = post_processor.process_entries(
        entries_to_check,
        progress_callback=lambda phase, c, t, m: _emit(f"后处理[{phase}] {m}"),
        stop_event=stop_event,
        pause_event=pause_event,
        checkpoint=pp_checkpoint,
        max_workers=max_workers,
        log_callback=lambda line: log_callback(-1, line),
        esp_path=self._cfg.esp_path,
    )

    # 输出摘要
    _log(f"质量检查完成：检查 {pp_result.total_checked} 条")
    error_count = sum(1 for i in pp_result.issues if i.severity == "error")
    warning_count = sum(1 for i in pp_result.issues if i.severity == "warning")
    _log(f"  发现问题：{error_count} 个错误，{warning_count} 个警告")
    if pp_result.needs_review:
        _log(f"  需审核条目：{len(pp_result.needs_review)} 条")

    # 根据裁决结果更新 stage
    stage_stats = post_processor.update_entry_stages(collection, pp_result)

    # 附加结果到 TranslationResult
    result.post_process_result = pp_result

    if not stop_event.is_set():
        # 后处理正常完成，删除后处理断点
        pp_checkpoint.delete(self._cfg.esp_path)
else:
    _log(f"\n── 质量检查已跳过（用户设置）──")
```

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
    ├─► checkpoint (PostProcessCheckpoint，断点续传)
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
│   └── {target_lang}.toml       # 目标语言专属模板（翻译风格）
├── quality_gate/
│   └── {target_lang}.toml       # 质量检测提示词
├── refinement/                   # 修复提示词
│   └── {target_lang}.toml
├── polish/                       # 润色提示词（新增）
│   └── {target_lang}.toml
└── arbitration/                  # 裁决提示词
    └── {target_lang}.toml
```

### 配置优先级

1. **按 `target_lang` 加载提示词文件**
   - 例：`target_lang="zh_CN"` → 加载 `data/prompts/quality_gate/zh_CN.toml`
   - 文件不存在 → 使用内置默认值

2. **模板变量替换**
   - `$game_name` 来自 `data/prompts/games/{game_profile}.toml`
   - `$source_lang`, `$target_lang` 来自 `data/prompts/langs/{target_lang}.toml`

3. **动态变量**
   - `$original`, `$translation`, `$context`, `$terms` 在检测时传入

### 添加新语言支持

1. 创建 `data/prompts/langs/{lang}.toml`（翻译提示词）
2. 创建 `data/prompts/quality_gate/{lang}.toml`（质量检测提示词）
3. 创建 `data/prompts/refinement/{lang}.toml`（修复提示词）
4. 创建 `data/prompts/polish/{lang}.toml`（润色提示词）
5. 创建 `data/prompts/arbitration/{lang}.toml`（裁决提示词）
6. 在 LLMConfig 中设置 `target_lang="{lang}"`

---

## 相关文档

- [ai_translator.md](ai_translator.md) - AI翻译模块
- [ARCHITECTURE.md](ARCHITECTURE.md) - 系统架构
