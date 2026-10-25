# ai_translator 模块

## 职责

AI 自动翻译功能的核心实现，包括 LLM 调用、术语库管理、批次规划、Prompt 构建、名词提取。

---

## 文件清单

| 文件 | 职责 |
|------|------|
| `translator.py` | 翻译主控器，驱动整个翻译流程，支持暂停/停止/断点续传 |
| `translation_reuse.py` | 普通批次前接纳已有译文候选，保留原批次恢复身份 |
| `../application/translation/draft_reuse.py` | 已有名称的受保护草稿提交及接续证据，不依赖 Qt |
| `../ui/tools/ai_translator/task_name_reuse.py` | 项目名称提前提交编排、未完成结果保留 |
| `term_database.py` | 术语库管理（四来源：dynamic/paratranz/json/excel）+ 向量语义检索 |
| `term_vector_index.py` | FAISS 向量索引构建/持久化/语义检索（可选依赖） |
| `batch_planner.py` | 批次规划（三轮策略 + token 估算切分） |
| `prompt_builder.py` | Prompt 构建与响应解析，支持 TOML 模板配置 |
| `llm_client.py` | LLM API 客户端抽象层（OpenAI 兼容 / Anthropic） |
| `noun_extractor.py` | 从译文中抽取专有名词 |
| `context_categories.py` | 上下文分类常量（已迁移到 converter 层，此处仅重导出） |

---

## 核心数据类

### TranslatorConfig

```python
@dataclass
class TranslatorConfig:
    llm_config: LLMConfig      # LLM 配置（provider/api_key/model 等）
    esp_path: str              # 当前 ESP 文件路径（用于术语库绑定）
    overwrite: bool = False    # True = 全部重翻，False = 仅翻未翻译条目
```

### TranslationResult

```python
@dataclass
class TranslationResult:
    success_count: int = 0         # 成功翻译条目数
    failed_count: int = 0          # 失败条目数
    skipped_count: int = 0         # 跳过条目数
    new_dynamic_terms: int = 0     # 新增动态术语数
    failed_entries: list[str]      # 失败条目 ID 列表（含错误信息）
```

### ProgressCheckpoint

```python
@dataclass
class ProgressCheckpoint:
    esp_stem: str                      # ESP 文件名（不含扩展名）
    target_entry_ids: list[str] | None # 目标条目 ID 列表
    overwrite: bool                    # 是否覆盖模式
    completed_fingerprints: list[list[str]]  # 已完成批次的 entry ID 集合
    result_so_far: dict                # 累计统计（success/failed/new_terms）
```

**持久化路径**: `data/ai_translator/{esp_stem}/{esp_stem}_progress.json`

**用途**: 旧式翻译入口的批次进度兼容文件，完成后可删除。项目 GUI 的历史恢复由任务清单、runtime-v2 候选及结果文件共同提供，不能以此文件是否存在判断任务能否继续。

### TermEntry

```python
@dataclass
class TermEntry:
    term: str              # 原文术语
    translation: str       # 译文
    source: str            # 来源（见下表）
    context: str = ""      # 上下文描述
    created_at: str = ""   # 创建时间（ISO 格式）
    case_sensitive: bool = False  # 是否区分大小写（仅 paratranz 来源）
```

**source 取值**:

| 值 | 说明 | 优先级 |
|----|------|--------|
| `manual` | 手动添加 | 最高 |
| `auto_name` | 从命名实体批次自动提取 | 高 |
| `auto_dialogue` | 从对话批次 LLM 抽取 | 高 |
| `paratranz` | ParaTranz 平台术语库 | 中 |
| `json` | 本地 JSON 文件 | 低 |
| `excel` | 本地 Excel 文件 | 最低 |

---

## 核心类详解

### AutoTranslator

**路径**: `src/transbridge/ai_translator/translator.py`

**职责**: 翻译流程主控，协调各组件完成批量翻译。支持多插件批量翻译时的术语共享。

#### 构造函数（基础参数节选）

```python
def __init__(
    self,
    config: TranslatorConfig,
    paratranz_client=None,
    project_id: int | None = None,
    shared_in_flight_terms: dict | None = None,   # 批量翻译时共享
    shared_in_flight_lock: threading.Lock | None = None,  # 批量翻译时共享
):
```

**批量翻译支持**：`shared_in_flight_terms` 和 `shared_in_flight_lock` 参数用于多插件批量翻译场景，允许多个 `AutoTranslator` 实例共享同一个 in-flight 术语缓存，实现插件间术语实时共享。

- 当使用外部共享缓存时，`_owns_in_flight_cache = False`，翻译会话开始时不会清空缓存
- 当使用内部缓存时，`_owns_in_flight_cache = True`，翻译会话开始时清空缓存

#### 关键方法

```python
def translate(
    self,
    collection: TranslationEntryCollection,
    target_entry_ids: list[str] | None,   # None = 全部条目
    progress_callback: Callable[[int, int, str, int, int, int], None],
    stop_event: threading.Event,
    pause_event: threading.Event | None = None,
    checkpoint: ProgressCheckpoint | None = None,
    log_callback: Callable[[int, str], None] | None = None,
    stream_callback: Callable[[int, str], None] | None = None,
) -> TranslationResult
```

**progress_callback 参数**:
```python
progress_callback(
    current,       # 已处理条目数
    total,         # 总条目数
    message,       # 状态消息
    success_count, # 累计成功数
    failed_count,  # 累计失败数
    new_terms      # 新增术语数
)
```

**stream_callback**: 非 None 时启用 LLM 流式响应，签名为 `(batch_idx: int, chunk: str)`。`_run_one_batch` 在调用 `_run_batch` 前将当前批次 idx 绑定到回调（`lambda chunk: stream_callback(idx, chunk)`），确保并发时各批次流互不干扰。每请求的 `TranslationStream` 只解码新完成的结果项，重复 ID 会撤销先前暂存项；`StreamRepetitionDetector` 跨片段维护重复状态。最终响应校验及候选持久化仍决定是否接纳译文。

调用日志和 `batch_{idx:03d}.log` 保留各自内容，由 `WorkflowLogStore` 的后台写入器合并写盘：默认每 100ms 或累计 64KiB 刷新，待写文本按每字符 4 字节保守计费，最多 4MiB，满载时背压。文件池按任务并发配置为 `2 × max_concurrent + 16`，硬上限 272；通道关闭和任务关闭在后台等待排空。磁盘错误会停用日志并保留诊断，不改判模型结果。进程异常退出可能损失尚未刷新的诊断尾部；候选检查点的可靠刷盘与恢复机制不使用这层缓冲。

**流式候选**：流式解析将已完整返回的翻译对交给候选会话持久化，更新进度和日志；普通模型结果经统一受保护提交后才写入任务草稿。可复用名称使用下面的提前提交路径，不能将“候选已收到”当作“草稿已写入”。

#### 项目任务与即时复用流程

项目任务由 `task_name_preparation.py` 统一准备名称，`AutoTranslator` 承接来源内翻译。所有操作在既有 worker / coordinator 中执行。

1. 扫描选中的真实名称，按原文、类别及插件作用域约束分组，加载有效术语。只处理允许翻译的状态；同名异类、插件特例及本地异译不能强行合并。
2. 已有译法通过非空和保护标记检查后，由 `task_name_reuse.py` 调用 `application/translation/draft_reuse.py`：先保存候选，再经暂停/取消、一致性及修订保护提交到各来源的 `TaskDraft.collection`。此时译文已存在、stage 为 2（有疑问）、修订已增加，尚未发送剩余名称的模型请求。
3. 保存 `CommittedDraftTranslations`：原始输入、提交后精确快照及 commit ID。界面先显示“写入已有译文”，随后显示实际“已写入草稿”的独立名称数；写入阶段的条数按名称出现位置计数。
4. 仅为没有已有译法的代表名称调用模型。未知名称仍在隔离集合中翻译、校验；失败传播到对应出现位置，已有草稿结果不回退。名称准备结束、无歧义名称发布完成后，放行各来源后续流程。
5. `AutoTranslator` 校验提前提交证据、范围和当前草稿。已写入项不再进入候选接纳、翻译批次或第二次提交；其原始输入、成功计数及结果证据仍包含在报告内。其余可直填条目由 `translation_reuse.py` 在普通翻译批次前保存候选，仅剩余条目进入三轮模型调度。
6. 合并本次提交与提前提交的证据。所有成功项按配置继续后处理/校对及最终共享参考差异评估。检测疑点按词条连同原文、上下文、当前译文和参考词一起交给 AI；不因术语字符串不同而拒收或强制重翻。全量复用时翻译模型调用为零；启用校对、质量检测或补充综合校对时，仍可能有相应模型调用。
7. 任务结果通过既有 `TaskSession` 应用入口写入项目来源。提前写入任务草稿不等于主窗口来源已变更，也不触发项目自动保存。取消或名称准备异常时保留已写入项的诊断与译文，不把尚未完成检查的项标为可应用成功。

提前写入按来源提交，不是整个项目的单一事务。后续来源失败不会撤销已经成功提交的前一来源；候选保存失败在该来源提交前终止，阻止剩余名称模型启动。暂停等待下一次提交，取消阻止迟到提交；接续时任何原文、上下文、修订或提交后内容变化都会拒绝复用证据。

`committed_draft` 是可选的 `AutoTranslator` 构造参数，默认空；`prepared_names` 按完整身份承载名称准备结果，已提交项由草稿证据排除，`defer_generated_terms` 用于隔离名称准备阶段的自动术语发布。普通独立翻译调用保持兼容。历史恢复会验证原始输入后把旧候选移入新的私有执行，重新取得提交证据；不能仅凭非空译文或旧提交标记推断正式项目已保存。旧任务缺少范围或输入证据时保留只读记录。

后处理由 `LLMConfig.enable_post_process` 和已选策略控制；五阶段策略与直接校对策略沿各自现有流程运行。配置的权威定义见 `config/llm.py`、`ai_translator/post_processor.py` 及 UI 后处理配置模块。

实现合同见 [ADR-048](../adr/048-ai-project-concurrency-and-terms.md)、[S10 计划](../../plans/ai-project-concurrency/plan.md) 和 [即时复用 QA](../test-reports/ai-name-immediate-reuse-qa-2026-10-24.md)。

#### 暂停/停止机制

```python
# 控制流异常（BaseException 以跳过 except Exception 块）
class _CancelledByPause(BaseException): ...
class _CancelledByStop(BaseException): ...

# 监控循环（每 50ms 检查）
while not done.wait(timeout=0.05):
    if stop_event.is_set():
        self._llm.cancel()  # 关闭 HTTP 连接
        raise _CancelledByStop()
    if pause_event is not None and not pause_event.is_set():
        self._llm.cancel()
        raise _CancelledByPause()
```

---

### TermDatabaseManager

**路径**: `src/transbridge/ai_translator/term_database.py`

**职责**: 加载四来源术语，按优先级合并，提供术语匹配接口（子串匹配 + 语义召回）。支持术语缓存，供后处理工具离线使用。

#### 术语缓存机制

为加速加载和供离线工具使用，各来源术语和合并结果分别缓存到硬盘：

**缓存目录**: `data/ai_translator/{esp_stem}/cache/`

| 缓存文件 | 说明 | 生成时机 |
|----------|------|----------|
| `paratranz_terms.json` | ParaTranz API 术语缓存 | `load_all()` 时 |
| `json_terms.json` | 本地 JSON 术语缓存 | `load_all()` 时 |
| `excel_terms.json` | 本地 Excel 术语缓存 | `load_all()` 时 |
| `merged_terms.json` | 合并后的最终术语库 | `load_all()` 时 |

**缓存结构**:
```json
{
  "cached_at": "2026-04-03T10:30:00",
  "count": 150,
  "sources": ["dynamic", "paratranz", "json", "excel"],
  "entries": [
    {"term": "Whiterun", "translation": "白漫城", "source": "paratranz", ...},
    ...
  ]
}
```

**容错机制**: 若某来源（如 ParaTranz API）加载失败，自动尝试从对应缓存恢复，确保离线可用。

**供外部工具使用**: `ConsistencyChecker` 等后处理工具通过 `TermDatabaseManager.load_merged_cache(esp_path)` 直接读取合并缓存，无需创建实例或配置 API。

#### 关键方法

| 方法 | 说明 |
|------|------|
| `load_all()` | 加载并合并所有来源，返回 `{term: translation}`；同时初始化向量索引；自动保存各来源和合并缓存 |
| `get_load_log()` | 返回各来源加载结果 `[(source, count, error_or_None), ...]` |
| `match_terms(text_batch)` | 在原文列表中扫描匹配的术语（正向子串 + 冠词规范化 + 反向匹配） |
| `match_terms_enhanced(entries, enable_semantic, max_terms, in_flight_terms)` | 两阶段增强匹配（精确 + 子串 + 语义召回），支持并发术语缓存 |
| `exact_match(originals)` | 精确全等匹配，返回 `{original: translation}`，用于术语直填 |
| `semantic_match(text_batch, top_k)` | 语义召回术语（基于向量索引） |
| `has_term(term)` | 检查术语是否已存在（大小写不敏感） |
| `get_dynamic_db()` | 获取动态术语库实例 |
| `rebuild_vector_index()` | 手动重建向量索引（术语库更新后调用） |
| `load_merged_cache(esp_path)` | **静态方法**，直接从硬盘加载合并后的术语缓存，无需创建实例 |

#### 优先级合并逻辑

```python
# 配置项 term_priority: ["dynamic", "paratranz", "json", "excel"]
# 从低到高加载，后者覆盖前者
for source in reversed(config.term_priority):  # excel → json → paratranz → dynamic
    entries = loader()
    for entry in entries:
        term_map[entry.term] = entry  # 覆盖
    _load_log.append((source, len(entries), None))   # 成功：记录条数
    # 异常时：_load_log.append((source, 0, str(e)))   # 失败：记录错误信息
```

**最终优先级**:
```
dynamic > paratranz > json > excel
```

**加载失败处理**：各来源加载时若抛出异常（如 ParaTranz API 网络错误），不会中断翻译流程：
1. 首先尝试从该来源的缓存文件恢复（`paratranz_terms.json` 等）
2. 若缓存也不可用，错误信息记入 `_load_log`，格式：
```
⚠ 术语来源 [paratranz] 加载失败: HTTP request failed: ...SSLError...
  术语来源 [dynamic]: 42 条
  术语来源 [json]: 0 条
```
3. 翻译开始前通过 `log_callback` 输出到日志

若看到 `from cache`，表示 API 失败但已从缓存恢复。

**手动条目保护**: `source="manual"` 的条目不会被自动翻译覆盖。

#### 动态术语实时可见（`_effective_terms`）

`match_terms` 和 `exact_match` 内部调用 `_effective_terms()` 而非直接读 `_merged_terms` 缓存。`_effective_terms` 会额外合并 `_dynamic_db` 中在初始加载之后新增的条目，确保 Round1 翻译后加入动态库的术语在 Round2/3 中即可命中精确匹配，不再发给 LLM。

```python
def _effective_terms(self) -> list[TermEntry]:
    if not self._merged_terms:
        self._merged_terms = self._load_all_with_metadata()
    # 补充动态追加的新条目（_merged_terms 仅缓存初始状态）
    merged_lower = {e.term.lower() for e in self._merged_terms}
    extra = [e for e in self._dynamic_db.as_list() if e.term.lower() not in merged_lower]
    return self._merged_terms + extra
```

#### match_terms 增强匹配策略

```python
def match_terms(self, text_batch: list[str]) -> dict[str, str]:
    """在 text_batch 的原文中扫描匹配的术语。

    匹配策略（按顺序，命中即止）：
    1. 正向子串：术语是原文的子串
    2. 冠词规范化：忽略术语开头的 The/A/An 后重试正向子串
    3. 反向前缀：原文是术语的词边界前缀
       例："Black Briar" → 术语 "Black Briar Lodge → 黑棘据点"
    4. 反向后缀：原文是术语的词边界后缀
       例："Meadery" → 术语 "Black Briar Meadery → 黑棘酿酒坊"
    """
```

#### match_terms_enhanced 两阶段召回

```python
def match_terms_enhanced(
    self,
    entries: list[TranslationEntry],
    enable_semantic: bool = True,
    max_terms: int = 100,
    in_flight_terms: dict[str, str] | None = None,
) -> dict[str, str]:
    """增强版术语匹配：两阶段召回策略。

    阶段1：子串扫描 - 找"明确出现"的术语
    阶段2：语义召回 - 为未命中原文补充"语义相关"的术语

    优先级：精确全等 > 正向子串 > 反向匹配 > in-flight > 语义
    """
```

**in-flight 术语缓存**：并发批次间实时共享的术语缓存，Round1 翻译完成后写入，供并发执行的 Round2/3 批次使用。

---

### TermVectorIndex

**路径**: `src/transbridge/ai_translator/term_vector_index.py`

**职责**: 基于 FAISS 的语义检索索引，支持术语的向量化和相似度检索。

**持久化文件**:
- `data/ai_translator/{esp_stem}/{esp_stem}_terms.faiss`：FAISS 索引
- `data/ai_translator/{esp_stem}/{esp_stem}_terms_meta.json`：术语元数据

**关键方法**:

| 方法 | 说明 |
|------|------|
| `build_index(terms, force)` | 构建/重建向量索引，支持增量检测 |
| `search(query, top_k)` | 单条语义检索，返回 `list[VectorSearchResult]` |
| `search_batch(queries, top_k)` | 批量语义检索，返回 `{query: [VectorSearchResult]}` |
| `available` | 向量索引是否可用（依赖缺失时自动降级） |

**Embedding 支持**：通过 `EmbeddingClient` 抽象层，支持本地模型和 API 服务。详见下文。

**可选依赖**：`faiss-cpu`。使用本地模型时还需 `sentence-transformers`。

---

### EmbeddingClient

**路径**: `src/transbridge/ai_translator/embedding_client.py`

**职责**: Embedding 编码客户端抽象层，支持本地模型和各厂商 API 服务。

#### 类层次

```
EmbeddingClient (ABC)
    │
    ├─► LocalSentenceTransformerClient
    │       本地 sentence-transformers 模型
    │       可选依赖：sentence-transformers
    │
    └─► OpenAIEmbeddingClient
            OpenAI 兼容 API（支持 OpenAI、DeepSeek、阿里云等）
            依赖：openai
```

#### 配置项（LLMConfig）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `embedding_provider` | `local` | `local` \| `openai` \| `custom` |
| `embedding_model` | `text-embedding-3-small` | API 模型名 |
| `embedding_api_key` | `""` | 留空则复用 `api_key` |
| `embedding_base_url` | `""` | 留空则复用 `base_url` |
| `embedding_local_model` | `paraphrase-multilingual-MiniLM-L12-v2` | 本地模型名 |

#### 支持的服务商

| Provider | 模型示例 | 说明 |
|----------|----------|------|
| `local` | paraphrase-multilingual-MiniLM-L12-v2 | 本地 sentence-transformers |
| `openai` | text-embedding-3-small, text-embedding-ada-002 | OpenAI 官方 |
| `openai` + `embedding_base_url` | 任意兼容模型 | DeepSeek、阿里云、智谱等兼容接口 |

#### 工厂函数

```python
def create_embedding_client(config: LLMConfig) -> EmbeddingClient:
    """按配置创建 EmbeddingClient 实例。"""
```

---

### DynamicTermDatabase

**路径**: `src/transbridge/ai_translator/term_database.py`

**职责**: 按 ESP 绑定的动态术语库，持久化到 `data/ai_translator/{esp_stem}/{esp_stem}_terms.json`。

#### 关键方法

| 方法 | 说明 |
|------|------|
| `load()` | 从 JSON 文件加载 |
| `save()` | 保存到 JSON 文件 |
| `add(term, translation, source, context)` | 添加单条术语 |
| `add_many_and_save(terms)` | 原子批量写入（加锁） |
| `as_list()` | 返回 TermEntry 列表 |

#### 并发安全

```python
def add_many_and_save(self, terms: list[tuple]) -> None:
    with self._lock:  # threading.Lock
        for term, translation, source, context in terms:
            self.add(term, translation, source, context)
        self.save()  # 同一锁内写入文件
```

---

### BatchPlanner

**路径**: `src/transbridge/ai_translator/batch_planner.py`

**职责**: 将翻译条目按三轮策略分批，支持 token 估算切分。

#### 数据结构

```python
@dataclass
class Batch:
    entries: list[TranslationEntry]
    batch_type: str        # "人名" | "地名" | "对话" | "长文本" 等
    quest_formid: str = "" # 仅对话批次有值

@dataclass
class BatchPlan:
    round1: list[Batch]  # 命名实体批次
    round2: list[Batch]  # 对话批次
    round3: list[Batch]  # 长文本批次

    def all_batches(self) -> list[Batch]: ...
    def round2_by_quest(self) -> dict[str, list[Batch]]: ...
```

#### 三轮分类规则

| 轮次 | 分类依据 | context 示例 | batch_type |
|------|----------|--------------|------------|
| Round1 | `ROUND1_CATEGORIES` | `NPC_:FULL`, `BOOK:CNAM` | 人名/地名/书名/物品/法术技能/任务名 |
| Round2 | `ROUND2_PREFIXES` | `INFO:NAM1\|xxx`, `DIAL:FULL\|xxx` | 对话 |
| Round3 | `ROUND3_CONTEXTS` | `BOOK:DESC`, `QUST:CNAM` | 长文本 |

分类常量定义于 `src/transbridge/converter/context_categories.py`。

#### Token 估算切分

```python
def _split_by_tokens(self, entries: list[TranslationEntry]) -> list[list[TranslationEntry]]:
    # 简单估算：1 token ≈ 4 chars（英文），留 1.5 倍安全系数
    char_limit = self._max_tokens * 3
    # 按字符数累积，超限则切分
```

---

### PromptBuilder

**路径**: `src/transbridge/ai_translator/prompt_builder.py`

**职责**: 构建翻译/抽取 Prompt，解析 LLM 响应，支持 TOML 模板配置。

#### 构造参数

```python
def __init__(self, game_profile: str = "skyrim_se", target_lang: str = "zh_CN"):
    # 加载 data/prompts/games/{game_profile}.toml
    # 严格加载 data/prompts/langs/{target_lang}.toml 的语言档案
    # 加载 translation/default.toml 与 extraction/default.toml 通用模板
```

#### 关键方法

| 方法 | 说明 |
|------|------|
| `build_translation_prompt(entries, matched_terms, batch_type)` | 构建翻译 Prompt |
| `build_extraction_prompt(translated_pairs)` | 构建名词抽取 Prompt |
| `parse_translation_response(response, expected_ids)` | 解析翻译响应（完整/截断兜底） |
| `parse_extraction_response(response)` | 解析名词抽取响应 |
| `extract_partial_pairs(buffer)` | 从不完整的流式 buffer 中提取已完成的翻译对，供增量写回使用 |

#### TOML 配置文件结构

**游戏配置** (`data/prompts/games/skyrim_se.toml`):
```toml
[game]
name = "上古卷轴5：天际特别版（SSE）"
format_notes = "保留原文中的特殊标记，如 <br>、[pagebreak]、\\n 换行符、%s 等格式占位符。"
```

**语言配置** (`data/prompts/langs/zh_CN.toml`):
```toml
[lang]
source = "英文"
target = "中文"

[example]
source = "Hello, traveler."
target = "你好，旅人。"
```

语言文件只保存元数据和可选的成对示例；完整提示词分别维护在
`data/prompts/translation/default.toml` 与 `data/prompts/extraction/default.toml`。
语言档案缺失或无效时直接报错，不会回退到简体中文。

**占位符**: 使用 `$var` 格式（`string.Template.safe_substitute`），与 JSON 花括号不冲突。

#### 响应解析容错

`parse_translation_response` 采用两阶段容错策略，专门处理上下文溢出导致的截断响应：

```
1. 提取 ```json ... ``` 代码块（如有）
2. 提取 {…} 最外层对象；若有 { 无 } 则从 { 开始截取（截断场景）
3. json.loads 解析
4. 失败 → 调用 _extract_partial_json_pairs 逐对提取完整键值对
5. 过滤只保留 expected_ids 中的有效条目
```

**`_extract_partial_json_pairs`**（模块级辅助函数）：

```python
# 匹配完整的 "key": "value" 对，支持 JSON 转义序列
pattern = re.compile(r'"((?:[^"\\]|\\.)*?)"\s*:\s*"((?:[^"\\]|\\.)*?)"')
```

- 对截断的 JSON 对象，逐对匹配两端均为完整 JSON 字符串的键值对
- 末尾残缺的半截条目不会被匹配，自然跳过
- 提取后用 `json.loads(f'"{raw}"')` 反转义（处理 `\n`、`\\`、`\"` 等）

**效果**：当 LLM 因 `max_tokens` 耗尽中途截断时，已完成的 150+ 条仍可被提取；`_run_batch` 的 `missing` 集合只剩末尾真正未翻译的少数几条，仅对它们发起重试，避免整批折半重发。

---

### LLMClient

**路径**: `src/transbridge/ai_translator/llm_client.py`

**职责**: LLM API 客户端抽象层，支持 OpenAI 兼容 API 和 Anthropic API。

#### 类层次

```
LLMClient (ABC)
    │
    ├─► OpenAICompatibleClient
    │       支持：OpenAI 官方、本地模型（Ollama/vLLM 等）
    │       参数：api_key, base_url, model
    │
    └─► AnthropicClient
            支持：Claude 系列
            参数：api_key, model
```

#### 关键方法

```python
class LLMClient(ABC):
    @abstractmethod
    def chat(self, messages: list[dict], max_tokens: int = 0) -> str:
        """发送消息并返回模型回复。供应商支持时，max_tokens=0 表示不设置应用上限。"""

    def chat_stream(self, messages: list[dict], max_tokens: int,
                    chunk_callback: Callable[[str], None]) -> str:
        """流式调用，每收到一个 chunk 即调用 chunk_callback(text)，最终返回完整文本。
        默认实现：退化为普通 chat，一次性回调整个响应。子类覆盖以实现真正的流式输出。
        """

    def cancel(self) -> None:
        """中断当前请求（关闭 HTTP 连接），重建客户端供后续使用。"""
```

`_monitored_chat()` 接受可选 `chunk_callback`：非 None 时走 `chat_stream`，否则走 `chat`。暂停/停止时调用 `cancel()` 关闭连接，流式迭代随即中断并抛出异常，监控线程捕获后抛出 `_CancelledByPause/_CancelledByStop`。

#### 工厂函数

```python
def create_llm_client(config: LLMConfig) -> LLMClient:
    if config.provider == "anthropic":
        return AnthropicClient(api_key=config.api_key, model=config.model)
    # 默认 openai_compatible
    return OpenAICompatibleClient(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
    )
```

#### Anthropic 特殊处理

```python
# Anthropic API 要求 system 消息单独传递，不放在 messages 列表中
def chat(self, messages: list[dict], max_tokens: int = 0) -> str:
    system_content = ""
    user_messages = []
    for msg in messages:
        if msg.get("role") == "system":
            system_content = msg.get("content", "")
        else:
            user_messages.append(msg)

    if max_tokens <= 0:
        raise ValueError("Anthropic API requires a positive max_tokens value")
    kwargs = dict(model=self._model, max_tokens=max_tokens, messages=user_messages)
    if system_content:
        kwargs["system"] = system_content
```

OpenAI 兼容供应商在配置为 `0` 时省略 `max_tokens`，由模型/服务端决定输出上限。Anthropic Messages API 要求该参数为正整数，因此配置为 `0` 时在发送请求前给出明确错误，并要求用户在「输出 Token」中设置正数；不得隐式替换为固定上限。

---

### NounExtractor

**路径**: `src/transbridge/ai_translator/noun_extractor.py`

**职责**: 从已翻译的对话条目中抽取专有名词，写入动态术语库。

#### 使用场景

仅在 Round2 对话批次翻译完成后调用，用于发现对话中新出现的专有名词。

```python
def extract(self, translated_pairs: list[dict]) -> list[TermEntry]:
    """
    translated_pairs: [{"original": ..., "translation": ...}]
    返回: TermEntry 列表（source='auto_dialogue'）
    """
```

---

## in-flight 术语缓存

### 设计目的

在并发翻译场景下，Round1 的命名实体批次完成后，其翻译结果需要立即对并发执行的 Round2/3 批次可见，而不是等待整轮完成。在多插件批量翻译场景下，术语还可在插件间实时共享。

### 实现方式

```python
class AutoTranslator:
    def __init__(
        self,
        ...,
        shared_in_flight_terms: dict | None = None,
        shared_in_flight_lock: threading.Lock | None = None,
    ):
        # 支持外部注入以实现多插件间共享
        if shared_in_flight_terms is not None and shared_in_flight_lock is not None:
            self._in_flight_terms = shared_in_flight_terms
            self._in_flight_lock = shared_in_flight_lock
            self._owns_in_flight_cache = False
        else:
            self._in_flight_terms: dict[str, str] = {}
            self._in_flight_lock = threading.Lock()
            self._owns_in_flight_cache = True

    def translate(self, ...):
        # 仅当缓存是本实例拥有时才清空，批量翻译时使用共享缓存不应清空
        if self._owns_in_flight_cache:
            with self._in_flight_lock:
                self._in_flight_terms.clear()

    def _run_batch(self, batch, ...):
        # 术语匹配时合并 in-flight 缓存
        with self._in_flight_lock:
            in_flight_snapshot = dict(self._in_flight_terms)
        matched_terms = self._term_mgr.match_terms_enhanced(
            entries=batch.entries,
            in_flight_terms=in_flight_snapshot,
            ...
        )

        # 流式翻译完成时，Round1 术语即时写入缓存
        if ctx in AUTO_TERM_CONTEXTS:
            with self._in_flight_lock:
                self._in_flight_terms[entry.original] = trans
```

### 优先级

```
精确全等 > 正向子串 > 反向匹配 > in-flight > 语义召回
```

---

## 三轮翻译策略

### 设计目的

| 轮次 | 目标 | 目的 |
|------|------|------|
| Round1 | 命名实体（NPC 名、地点名等） | 先翻译专有名词，建立术语基础 |
| Round2 | 对话（按 quest 分组） | 同一任务的对话上下文连贯 |
| Round3 | 长文本（描述、日志等） | 最后处理需要完整上下文的文本 |

### 并发策略

新配置的默认并发为 **100**，界面可设为 **1～128**。配置、界面和自定义流程缺省值统一取自 `config/llm.py`。已有明确保存的 3、20、50 等值和恢复任务快照保持原值，不自动迁移。

该值是同一任务各来源及翻译、术语、校对阶段共享的最大在途逻辑模型调用数，由 `AiRequestBudget` 管理，包含调用内部重试。它不是每秒请求数、每批 JSON 行数或账号全局连接数；一个请求可以返回多条译文。现有 HTTP 传输默认最多 100 个连接，本次不扩大连接池，也不保证设置 128 时始终有 128 个实际网络请求同时运行。

| 轮次 | 并发模式 | 说明 |
|------|----------|------|
| Round1 | 全部并发 | 命名实体相互独立，无上下文依赖 |
| Round2 | quest 间并发，quest 内串行 | 同一 quest 的对话需要上下文连贯 |
| Round3 | 全部并发 | 长文本相互独立 |

```python
# Round2 并发控制
quest_groups = plan.round2_by_quest()  # {quest_formid: [Batch, ...]}
with ThreadPoolExecutor(max_workers=max_workers) as executor:
    futures = [
        executor.submit(_run_quest_group, batches)  # 每个 quest 一个线程
        for batches in quest_groups.values()
    ]
```

### 自动术语提取

- **Round1**: 仅将 context 属于 `AUTO_TERM_CONTEXTS` 的条目（NPC 名、地点名等）的原文→译文写入动态术语库（`source='auto_name'`）。**同批次中 context 不在 `AUTO_TERM_CONTEXTS` 里的条目不写入。**
- **Round2**: 调用 `NounExtractor` 通过 LLM 抽取专有名词（`source='auto_dialogue'`），抽取前过滤掉已在任意来源中存在的术语（`has_term` 检查）。

`AUTO_TERM_CONTEXTS` 定义于 `src/transbridge/converter/context_categories.py`：
```python
AUTO_TERM_CONTEXTS = {
    "NPC_:FULL", "NPC_:SHRT", "TACT:FULL",
    "LCTN:FULL", "WRLD:FULL", "CELL:FULL", "DOOR:FULL", "REFR:FULL",
    "BOOK:FULL",
}
```

---

## 断点续传机制

### 任务身份与恢复入口

`TaskRecoveryBinding` 在模型工作前为项目翻译、校对、混合任务创建恢复清单。`ai-task-recovery/` 保存去凭据执行配置、覆盖选项、完整源范围、原始输入和历次执行身份；原文、上下文、当前译文及状态必须匹配。启动提示和 AI 任务记录均可继续或重新开始，重新开始使用独立执行链。取消任务不自动复活。

GUI 捕获输入后，读取、校验、候选重建、结果整理及落盘均在 worker 中执行。同进程启动预留与按逻辑任务持有的非阻塞 OS 锁防止重复恢复；进程退出自动释放锁，文件存在本身不代表任务仍在运行。

### 任务参考与重试

`TaskReferenceStore` 将首次加载的本地、ParaTranz 和已发布项目术语保存为任务参考；各来源的名称、翻译、校对、项目收尾及失败重试复用同一基线。公共输入只读取一次，历史插件动态词库保持来源作用域，本任务共享生成术语仍随成功发布更新。`ai-task-recovery/references/` 的独立原子文件带任务身份和校验和，保存参考内容及已发布快照，不包含配置凭据。跨进程继续复用它；新建任务或重新开始重新捕获。

参考版本、文件时间或内容变化不能使已成功译文失效，也不能阻止仅重试未完成项。`TaskConsistency` 只把执行配置变化作为任务有效性条件；原文、当前译文、项目和修订的正式写入保护保持有效。生成术语文件被其他操作修改或占用时，`TermPublicationConflict` 仅停止本次术语发布、保留译文并记录诊断，不能覆盖外部文件。真实存储故障和损坏继续明确报错。

项目收尾显示“加载参考 → 扫描差异 → AI 综合校对 → 整理结果 → 完成”，顶部显示当前来源及实际阶段，不把“来源流程 2/2”当作全任务完成。扫描进度限频合并，模型校对每批完成更新；无需补充校对也明确结束。扫描在条目边界暂停或取消，未完成检查的条目保留译文供后续接续。

### 翻译与后处理

`runtime-v2/translation-*.json` 和其 journal 是翻译候选的持久证据。schema 1 快照与 schema 2 校验链均支持只读恢复；只接受对应任务、来源、范围与输入版本的候选。名称、主翻译及多次执行按确定顺序合并，原文件不改写。有效候选进入新执行的私有草稿，跳过对应模型翻译请求，仍经过正常提交、综合校对与后处理。旧提交标记只证明旧私有草稿提交，不能代表正式项目保存。

`ai-translation-resume/` 按逻辑任务/来源、设置和完整内容身份保存后处理阶段。只有成功完成的阶段可跳过；失败或取消阶段继续处理。`ai-proofread-resume/` 保存校对及最终项目参考复核的已完成批次。改变输入或参考条件时重新评估，不能把不同条件的结果当作完成证据。旧式 `ProgressCheckpoint` 仅为兼容入口保留，不是 GUI 历史恢复的唯一权威来源。

### 结果确认与项目保存

完整处理结果及采纳决定原子保存在 `ai-task-recovery/results/`，带任务身份与校验和，包括待复核原因和报告字段。恢复后已经完成的词条不再请求模型；待确认、已采纳、未采纳状态分别保留。决定先持久化，再进行正式项目应用。项目已经保存但任务元数据未更新时，按当前内容与已采纳结果的严格匹配识别，避免重复应用。最终项目写入和保存仍经过既有版本与修订校验。

单条恢复记录损坏会保留文件并明确说明原因，不应阻断其他健康任务的发现。缺失或修改的来源/词条不能静默当作已完成。

### 旧任务兼容

没有恢复清单的历史任务只在报告保留完整原始范围、动作可确定且存在相应翻译检查点时提供接续。旧任务没有完整配置快照，界面明确使用当前配置，先打开配置页；确认开始后才执行模型工作。旧候选作为待校验草稿进入新恢复链。无法证明范围或动作的旧报告继续只读，不能猜测覆盖。

已有恢复清单但没有完整参考快照的旧任务，首次继续时重新捕获当前参考一次并记录兼容提示；后续继续及重试复用该快照。旧候选仍按完整身份和输入校验，不因术语变化整体作废。快照损坏不会静默覆盖原文件。

---

## 坑点与注意事项

### 1. 控制流异常

`_CancelledByPause` / `_CancelledByStop` 继承自 `BaseException`，会跳过 `except Exception` 块：

```python
try:
    self._run_batch(...)
except _CancelledByStop:   # 会被捕获
    ...
except Exception:         # _CancelledByPause/_CancelledByStop 不会被这里捕获
    ...
```

### 2. 动态术语库并发安全

写入必须使用 `add_many_and_save()` 而非单独 `add()` + `save()`：

```python
# 正确 ✓
self._term_mgr.get_dynamic_db().add_many_and_save(terms)

# 错误 ✗（无锁，并发写入可能丢失）
for term, trans, src, ctx in terms:
    self._term_mgr.get_dynamic_db().add(term, trans, src, ctx)
self._term_mgr.get_dynamic_db().save()
```

### 3. 术语优先级配置

`term_priority` 配置决定加载顺序，**后者覆盖前者**：

```ini
[llm]
term_priority = dynamic,paratranz,json,excel
# 加载顺序：excel → json → paratranz → dynamic
# 最终优先级：dynamic > paratranz > json > excel
```

### 4. 断点文件生命周期

- 项目 AI 任务在模型调用前建立恢复清单，完成的翻译/校对批次及后处理阶段持续保存检查点。
- 任务结果及采纳决定独立保存；任务完成不代表可以清除历史恢复证据，仍用于结果查看和保存边界校验。
- 异常退出后发现未完成记录，由用户选择继续；旧式 `ProgressCheckpoint` 的完成清理不影响项目任务恢复。
- 取消任务保持取消状态；重新开始建立独立执行链，不复用原任务进度。

### 5. 手动术语保护

`source='manual'` 的术语不会被自动翻译覆盖：

```python
def add(self, term: str, translation: str, source: str, context: str = "") -> None:
    for e in self._entries:
        if e.term == term:
            if e.source == "manual":
                return  # 手动条目不被覆盖
            e.translation = translation
            e.source = source
```

### 6. 术语直填与动态术语库写入的关系

`exact_match` 命中的条目（原文与术语完全相同）直接填充译文，**不会**写入动态术语库——它们本身已来源于术语库，写回无意义。只有通过 LLM 新翻译的 Round1 条目才会写入动态 DB（`source='auto_name'`）。

日志中可通过 `[直填]` 标记区分两种来源：

```
Alduin -> 阿尔杜因 [直填]   ← exact_match 直接填充
Miraak -> 米拉克             ← LLM 翻译结果
```

### 7. 动态术语库写入仅限 AUTO_TERM_CONTEXTS

Round1 批次执行后，**只有 context 属于 `AUTO_TERM_CONTEXTS` 的条目**才会写入动态术语库。同批次中 context 为 `MISC:FULL`、`WEAP:FULL`、`ARMO:FULL` 等的条目不写入，避免普通物品名被误入术语库。

```python
# 正确逻辑（按条目 context 过滤）
auto_term_entries = [e for e in llm_entries if e.context in AUTO_TERM_CONTEXTS]
if auto_term_entries:
    self._update_dynamic_terms(auto_term_entries, id_to_translation, result, lock)

# 旧的错误逻辑（批次中有任意 AUTO_TERM_CONTEXTS 条目就整批写入）❌
if any(e.context in AUTO_TERM_CONTEXTS for e in llm_entries):
    self._update_dynamic_terms(llm_entries, id_to_translation, result, lock)
```

### 8. ParaTranz 术语加载依赖网络，可能静默失败

`_load_paratranz` 通过 HTTP 请求加载，会因 SSL 错误、网络超时、Token 无效等原因失败。现在失败信息会记入 `_load_log` 并在翻译开始时输出到日志。若看到 `⚠ 术语来源 [paratranz] 加载失败`，本次翻译将不使用 ParaTranz 术语，改用其他已配置的来源。

可配置本地 JSON 术语文件作为离线备用，避免网络问题影响术语匹配。

### 9. 向量索引依赖可选，自动降级

`sentence-transformers` 和 `faiss-cpu` 为可选依赖。若未安装：
- `_vector_index.available = False`
- `match_terms_enhanced()` 自动跳过语义召回阶段
- 仅使用精确匹配 + 子串扫描 + 反向匹配

日志中会显示 `vector_index: 0 (Missing dependency: ...)` 或 `faiss/sentence-transformers not installed`。

### 10. in-flight 术语缓存用于并发批次

`_in_flight_terms` 缓存用于并发批次间实时共享术语，不同于动态术语库：
- **动态术语库**：持久化到文件，下次翻译会话可见
- **in-flight 缓存**：仅当前翻译会话有效，会话开始时清空，Round1 完成后即时写入供 Round2/3 使用

### 11. 批量翻译时的共享缓存

多插件批量翻译时，`_BatchTranslationWorker` 创建共享的 in-flight 缓存并注入到每个 `AutoTranslator` 实例：

```python
class _BatchTranslationWorker(QThread):
    def __init__(self, slots, llm_config, overwrite, ...):
        # 共享的 in-flight 术语缓存：插件间实时共享新发现的术语
        self._shared_in_flight_terms: dict[str, str] = {}
        self._shared_in_flight_lock = threading.Lock()

    def _translate_single_slot(self, slot, plugin_name):
        translator = AutoTranslator(
            translator_cfg,
            ...,
            shared_in_flight_terms=self._shared_in_flight_terms,
            shared_in_flight_lock=self._shared_in_flight_lock,
        )
```

这样，翻译第一个插件时发现的术语会实时共享给后续插件使用。

---

## 依赖关系

```
ai_translator
    │
    ├─► converter (TranslationEntry, TranslationEntryCollection, context_categories)
    │
    ├─► paratranz/config_manager (LLMConfig, ParatranzConfig)
    │
    └─► [可选] faiss-cpu (向量语义检索)
        └─► [可选] sentence-transformers (本地 embedding 模型)
        └─► openai (API embedding 服务)
```

**被依赖**: `ui/tools/ai_translator_window.py`

---

## 相关文档

- [ARCHITECTURE.md](ARCHITECTURE.md) - 系统架构、AI 翻译流程
- [INDEX.md](INDEX.md) - 文档索引
- `src/transbridge/converter/context_categories.py` - 分类常量定义
