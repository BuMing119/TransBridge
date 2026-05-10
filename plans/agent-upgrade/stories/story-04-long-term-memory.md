# Story 04: 长期记忆

**所属方案**: `plans/agent-upgrade/plan.md`
**技术模块**: smart_assistant/memory
**状态**: 已确认
**创建日期**: 2026-05-10

## 前置依赖

### 上游 Story
- Story-01（同 plan）：infra/VectorStore + EmbeddingClient 就绪
- Story-03（同 plan，弱依赖）：文件上传的 raw_text 可作为记忆来源

### 引用的架构决策
- [ADR-009: MemoryStore 双存储设计](../../../docs/adr/009-agent-file-memory-reflexion.md)
- [ADR-010: infra/VectorStore 接口](../../../docs/adr/010-infra-extraction.md)

## 验收标准

- [ ] `MemoryStore` 支持 add/search/get/delete/list_by_type
- [ ] `MemoryRetriever` 实现两阶段召回（精确匹配 → 语义检索）
- [ ] 对话结束时自动记录翻译上下文记忆
- [ ] 新对话开始时自动检索相关记忆并注入 system prompt
- [ ] 记忆存储在项目目录下（`data/projects/{project}/{variant}/memory/`）
- [ ] 项目切换时记忆自动隔离

## 数据流

```
对话完成 (_on_llm_finished)
  → 自动生成记忆摘要（LLM 一句话总结本轮关键决策）
  → EmbeddingClient.embed(summary) → 向量
  → MemoryStore.add(entry)
  → 持久化 FAISS + JSON

新对话开始 (_on_send)
  → MemoryRetriever.retrieve(user_input):
      1. 精确: JSON 元数据按 type+keyword 过滤
      2. 语义: FAISS search(query_vector, top_k=5)
      3. 合并去重排序
  → 注入 ConversationManager.add_system(memory_context)
  → LLM 推理时参考记忆
```

## 关键接口

### MemoryEntry（memory_store.py）

```python
@dataclass
class MemoryEntry:
    memory_id: str       # uuid4
    type: str            # "preference"/"term_decision"/"correction"/"conversation"
    summary: str         # 一句话摘要
    content: str         # 完整上下文
    source: str          # 来源描述
    timestamp: str       # ISO
    embedding: list[float] | None = None
    metadata: dict = field(default_factory=dict)
```

### MemoryStore — 三模式适配（memory_store.py）

```python
class MemoryStore:
    def __init__(self, storage_dir: Path, dimension: int = 1536, 
                 embedding_mode: str = "disabled"):
        self._mode = embedding_mode
        self._metadata: dict[str, MemoryEntry] = {}
        if self._mode != "disabled":
            self._vector_store = VectorStore(dimension=dimension)  # FAISS
        else:
            self._vector_store = None  # 降级：无向量存储
    
    def add(self, entry: MemoryEntry) -> str:
        if self._mode != "disabled":
            entry.embedding = self._embedding_client.embed(entry.summary)
        self._metadata[entry.memory_id] = entry
        if self._vector_store:
            self._vector_store.add(...)
        self._save_metadata()
    
    def search(self, query_vector=None, top_k=5,
               type_filter=None, keywords=None) -> list[MemoryEntry]:
        if self._mode == "disabled":
            return self._exact_search(type_filter, keywords)  # 仅JSON精确匹配
        return self._hybrid_search(query_vector, top_k, type_filter)
```

### MemoryRetriever（memory_retriever.py）

```python
class MemoryRetriever:
    def __init__(self, store: MemoryStore, embedding_client: EmbeddingClient): ...
    def retrieve(self, query: str, top_k: int = 5) -> list[MemoryEntry]:
        """两阶段：精确→语义→合并"""
```

## 实现步骤

### 步骤 1-3: 新建 3 个核心文件
`memory/memory_store.py` → `memory/embedding.py`（EmbeddingClient 封装）→ `memory/memory_retriever.py` → `memory/__init__.py`

### 步骤 4: ChatWidget 集成
**涉及文件**: `chat_widget.py`（改）

- `__init__`: 初始化 MemoryStore + MemoryRetriever
- `_on_send`: 调用 `retriever.retrieve(user_input)` → 有结果则注入 conversation
- `_on_llm_finished`: 调用 LLM 生成摘要 → `store.add(entry)`

### 步骤 5: ConversationManager 扩展
**涉及文件**: `conversation_manager.py`（改）

- 新增 `inject_memory_context(entries: list[MemoryEntry])` 方法
- 在 system prompt 中追加记忆参考：
  ```
  ## 相关历史记忆
  - [偏好] 用户偏好使用"龙裔"而非"抓根宝"
  - [术语] "Whiterun" 已统一译为"雪漫城"
  ```

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `memory/__init__.py` | 新建 | 子包导出 |
| `memory/memory_store.py` | 新建 | MemoryEntry + MemoryStore |
| `memory/embedding.py` | 新建 | EmbeddingClient 封装 |
| `memory/memory_retriever.py` | 新建 | 两阶段召回 |
| `chat_widget.py` | 修改 | 初始化+检索+记录 |
| `conversation_manager.py` | 修改 | inject_memory_context |

## 风险与注意事项

- **风险**: FAISS 索引损坏导致记忆不可用 → 自动重建索引（从 metadata JSON 重新嵌入）
- **风险**: 记忆过多(>1000条)检索变慢 → 设置容量上限，LRU 淘汰旧记忆
- **注意**: 嵌入维度需与 EmbeddingClient 模型一致（默认 text-embedding-3-small = 1536）
