# ADR-009: Agent 文件解析、长期记忆与 Reflexion 自纠错

- **状态**: 已接受
- **日期**: 2026-05-10
- **决策者**: BuMing
- **对应需求**: [FR7.13](../requirements.md)
- **关联 ADR**: [ADR-008](008-smart-assistant-code-layering.md)（子包结构）、[ADR-005](005-toml-prompt-no-langchain.md)（Skill TOML 格式）

## Context

FR7.13 Phase 1 需要为 smart_assistant 新增三个能力——文件上传解析、长期记忆、Reflexion 自纠错——它们涉及技术选型和接口设计，需要独立的架构决策。

## Decision

### 1. 文件解析器：统一接口 + 多格式插件

**决策**: 定义 `FileParser` 抽象基类作为统一接口，每种格式实现为独立解析器。

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class ParsedDocument:
    """解析后的结构化文档"""
    source_path: Path
    format: str              # "excel" / "csv" / "markdown" / "pdf" / "word" / "paratranz"
    title: str
    sections: list[dict]     # [{"heading": str, "content": str, "rows": list[dict]}]
    raw_text: str            # 纯文本提取（供向量嵌入）
    metadata: dict

class FileParser(ABC):
    supported_extensions: list[str] = []
    
    @abstractmethod
    def parse(self, path: Path) -> ParsedDocument: ...
    
    @abstractmethod
    def can_handle(self, path: Path) -> bool: ...
    
    @classmethod
    def get_parser(cls, path: Path) -> "FileParser | None":
        """工厂方法：根据扩展名返回合适的解析器"""
```

**解析器注册**:

| 解析器 | 格式 | 依赖 |
|--------|------|------|
| `TextFileParser` | .xlsx, .csv, .md, .txt, .json | openpyxl (已有), csv (内置), json (内置) |
| `BinaryFileParser` | .pdf, .docx | PyPDF2/pdfplumber, python-docx **(新增)** |
| `ParatranzParser` | ParaTranz 导出 .zip/.json | 已有 ParatranzConfig/API |

**理由**: 
- 统一接口简化 agent 调用逻辑——agent 只需 `FileParser.get_parser(path).parse(path)` 
- 新增格式只需添加一个 parser 类，不修改现有代码
- `ParsedDocument` 同时保留结构化数据（sections/rows）和纯文本（raw_text），前者供精确引用，后者供向量嵌入

### 2. 长期记忆：infra/VectorStore + JSON 元数据双存储

**决策**: 向量存储通过 `infra/vector_store.py` 的 `VectorStore` 类操作 FAISS 索引，嵌入生成通过 `infra/embedding_client.py` 的 `EmbeddingClient`。新增 JSON 元数据索引做精确匹配。详见 [ADR-010](010-infra-extraction.md)。

```
data/projects/{project}/{variant}/
├── current.json                    # 现有：翻译数据
├── memory/
│   ├── memory_index.faiss          # FAISS 向量索引
│   ├── memory_metadata.json        # {memory_id: {type, summary, timestamp, source}}
│   └── memory_embeddings.npy       # 嵌入向量缓存
```

**MemoryStore 接口**:

```python
@dataclass
class MemoryEntry:
    memory_id: str           # UUID
    type: str                # "preference" / "term_decision" / "correction" / "conversation"
    summary: str             # 一句话摘要
    content: str             # 完整内容
    source: str              # 来源（对话轮次 / 文件名 / 手动录入）
    timestamp: str           # ISO format
    embedding: list[float] | None = None
    metadata: dict = field(default_factory=dict)

class MemoryStore:
    def add(self, entry: MemoryEntry) -> str: ...
    def search(self, query: str, top_k: int = 5, type_filter: list[str] | None = None) -> list[MemoryEntry]: ...
    def get(self, memory_id: str) -> MemoryEntry | None: ...
    def delete(self, memory_id: str) -> bool: ...
    def list_by_type(self, type: str) -> list[MemoryEntry]: ...
```

**两阶段召回**:
1. **精确匹配**: 按 type + 关键词过滤 `memory_metadata.json`（毫秒级）
2. **语义检索**: FAISS 向量检索 top_k（百毫秒级）
3. **合并排序**: 精确匹配结果排前，语义结果排后，去重

**理由**: FAISS 已在项目中用于术语向量检索（FR5.3），无需引入新的向量数据库（如 ChromaDB/Milvus）。JSON 元数据索引提供了精确查询和类型筛选能力，弥补纯向量检索的不足。记忆存储在项目目录下，随项目切换自动隔离。

### 3. Reflexion 自纠错：ExecutionEngine 包裹模式

**决策**: 在 `ExecutionEngine._run_single()` 中注入 `RetryHandler`，每个工具步骤执行失败时触发 LLM 分析 → 参数调整 → 重试。

**集成方式**（不改 ExecutionEngine 的 DAG 拓扑逻辑）:

```python
class ExecutionEngine:
    def __init__(self, ...):
        self._retry_handler = RetryHandler(max_retries=3, llm_client=...)
    
    def _run_single(self, step: dict) -> StepResult:
        attempt = 0
        while True:
            try:
                result = self._execute_step(step)
                return result
            except Exception as exc:
                if attempt >= self._retry_handler.max_retries:
                    return StepResult(success=False, message=str(exc))
                # LLM 分析失败并调整参数
                adjusted_step = self._retry_handler.analyze_and_adjust(
                    step, str(exc), attempt
                )
                if adjusted_step is None:
                    return StepResult(success=False, message=str(exc))
                step = adjusted_step
                attempt += 1
```

**RetryHandler**:

```python
class RetryHandler:
    def __init__(self, max_retries: int = 3, llm_client=None):
        self._max_retries = max_retries
    
    def analyze_and_adjust(self, step: dict, error: str, attempt: int) -> dict | None:
        """分析失败原因，返回调整后的 step 参数。返回 None 表示不可修复"""
        prompt = f"""
        工具 {step['tool']} 执行失败 (第 {attempt+1}/{self._max_retries} 次):
        参数: {step.get('args', {})}
        错误: {error}
        
        请分析失败原因，调整参数后重试。如果无法修复，返回 {{"retry": false}}。
        返回 JSON: {{"retry": true/false, "adjusted_args": {{...}}, "reason": "..."}}
        """
        response = self._llm_client.chat([{"role": "user", "content": prompt}])
        parsed = json.loads(response)
        if parsed.get("retry"):
            step["args"] = parsed["adjusted_args"]
            return step
        return None  # 不可修复，放弃重试
```

**理由**: 
- 在 ExecutionEngine 层面注入，不改动 DAG 拓扑逻辑——Reflexion 是执行层的横切关注点
- LLM 分析失败原因而非简单重试，提高了重试成功率
- 最多 3 次重试避免死循环，不可修复时优雅降级

### 4. 依赖变更

| 库 | 用途 | 状态 |
|----|------|------|
| `PyPDF2` 或 `pdfplumber` | PDF 解析 | **新增** |
| `python-docx` | Word .docx 解析 | **新增** |
| `tomllib` (Python 3.11 内置) | Skill TOML 解析 | 无需新增 |
| `faiss-cpu` | 向量存储 | 已有 |

### 更新: 2026-05-10 - MemoryStore 三模式降级策略

**决策**: MemoryStore 根据 `EmbeddingConfig.mode` 运行在不同模式。

| 模式 | 向量存储 | 检索方式 | 能力 |
|------|---------|---------|------|
| `api` / `local` | FAISS 索引 + JSON 元数据 | 语义检索 + 精确匹配，合并去重 | 全功能 |
| `disabled` | 仅 JSON 元数据 | 精确匹配（type 过滤 + 关键词过滤） | 降级可用 |

**MemoryStore 构造函数**:

```python
class MemoryStore:
    def __init__(self, storage_dir: Path, dimension: int = 1536, 
                 embedding_mode: str = "disabled"):
        self._mode = embedding_mode
        self._metadata: dict[str, MemoryEntry] = {}
        self._load_metadata()
        if self._mode != "disabled":
            self._vector_store = VectorStore(dimension=dimension)
            self._load_index()
        else:
            self._vector_store = None
    
    def search(self, query_vector=None, top_k=5, 
               type_filter=None, keywords=None) -> list[MemoryEntry]:
        if self._mode == "disabled":
            return self._exact_search(type_filter, keywords)
        return self._hybrid_search(query_vector, top_k, type_filter, keywords)
```

**理由**: 用户可选择不配置 embedding 服务，此时记忆系统仍可用（降级为结构化查询）。disabled 模式下 `add()` 跳过嵌入生成，仅写入 JSON 元数据（summary + content + type + timestamp）。search() 通过 type 过滤 + JSON 内容关键词匹配返回结果。

**影响**:
- `MemoryStore.__init__` 新增 `embedding_mode` 参数
- 新增 `_exact_search()` 和 `_hybrid_search()` 方法
- `add()` 在 disabled 模式下跳过 `EmbeddingClient.embed()` 调用
- 无需修改 MemoryRetriever 接口

## Consequences

- **正面**: 文件解析、记忆、自纠错三个子系统独立解耦；FAISS 复用避免引入新的向量数据库；ExecutionEngine 无侵入式集成 Reflexion
- **负面**: 新增 PDF/Word 解析依赖增加打包体积（~20MB）；FAISS 索引随记忆增长需定期维护
- **风险**: PDF 解析质量依赖第三方库，复杂排版可能提取不完整 → 提供纯文本 fallback，提示用户转换格式

### 更新：2026-08-18 — 知识文件、格式 I/O 与重试边界（已接受）

Smart Assistant 的 `FileParser/ParsedDocument` 只负责知识文件提取，不替代 [ADR-017](017-translation-io-kernel-v2.md) 的翻译格式 Adapter；ParaTranz JSON 的业务导入必须走明确 format_id 和双 ID 合同。Memory/Embedding disabled 模式不得加载向量索引或语料。Reflexion 只可重试经错误分类判定为安全且幂等的步骤，并受 ADR-019 的次数、取消、owner 和 idempotency key 约束；不得调整参数后重复不可逆副作用。
