# ADR-010: 共享基础设施提取 — infra/ 包

- **状态**: 已接受
- **日期**: 2026-05-10
- **决策者**: BuMing
- **对应需求**: [FR7.13](../requirements.md)
- **关联 ADR**: [ADR-008](008-smart-assistant-code-layering.md)（目录结构）、[ADR-009](009-agent-file-memory-reflexion.md)（memory 子系统）

## Context

当前项目的 LLM 基础设施散布在两个包中：

- `ai_translator/llm_client.py` — LLMClient 抽象 + OpenAI/Anthropic 实现
- `ai_translator/embedding_client.py` — 文本嵌入生成
- `paratranz/config_manager.py` — LLMConfig（与 ParatranzConfig 共享 INI 文件）

Agent 框架升级（FR7.13）后，`smart_assistant/memory/` 也需要嵌入和向量检索能力。直接依赖 `ai_translator/` 包会造成业务包之间的双向依赖（smart_assistant → ai_translator，ai_translator 的工具也被 smart_assistant 调用），违反分层原则。

## Decision

### 1. 新建 `src/transbridge/infra/` 包

```
src/transbridge/infra/
├── __init__.py                  # 公开 API 导出
├── llm_client.py                # ← 从 ai_translator/ 搬迁
├── embedding_client.py          # ← 从 ai_translator/ 搬迁
├── config.py                    # ← LLMConfig 从 paratranz/ 提取
└── vector_store.py              # NEW: FAISS 索引统一管理
```

### 2. 搬迁 / 提取清单

| 文件 | 来源 | 目标 | 内容 |
|------|------|------|------|
| `llm_client.py` | `ai_translator/` | `infra/` | `LLMClient`(ABC), `OpenAILLMClient`, `AnthropicLLMClient`, `create_llm_client()` |
| `embedding_client.py` | `ai_translator/` | `infra/` | `EmbeddingClient` 类 |
| `config.py` | `paratranz/config_manager.py` | `infra/` | `LLMConfig` 类（从 ParatranzConfig 所在文件提取） |
| `vector_store.py` | — | `infra/` 新建 | `VectorStore` 类：`create_index()`, `add()`, `search()`, `save()`, `load()` |

### 3. 不提取（留在原地）

| 组件 | 位置 | 理由 |
|------|------|------|
| `AutoTranslator` | `ai_translator/translator.py` | ai_translator 的业务逻辑 |
| `PostProcessor` | `ai_translator/post_processor/` | ai_translator 的业务逻辑 |
| `TermDatabaseManager` | `ai_translator/term_database.py` | ai_translator 的业务逻辑 |
| `ParatranzConfig` | `paratranz/config_manager.py` | paratranz 的业务逻辑 |
| `PromptBuilder` | `ai_translator/prompt_builder.py` | ai_translator 的业务逻辑 |

smart_assistant 通过 ToolRegistry 的工具调用这些业务组件，而非直接 import。

### 4. 跨包 import 契约（冻结）

搬迁后，所有对基础设施的引用 SHALL 使用以下路径：

```python
# LLM 客户端
from src.transbridge.infra.llm_client import LLMClient, create_llm_client

# 嵌入生成
from src.transbridge.infra.embedding_client import EmbeddingClient

# LLM 配置
from src.transbridge.infra.config import LLMConfig

# 向量存储
from src.transbridge.infra.vector_store import VectorStore
```

**影响范围**（需更新 import 的文件）：

| 文件 | 需更新的 import |
|------|---------------|
| `ai_translator/translator.py` | `llm_client` → `infra.llm_client` |
| `ai_translator/term_database.py` | `embedding_client` → `infra.embedding_client` |
| `ai_translator/prompt_builder.py` | 如引用 llm_client 则更新 |
| `ai_translator/post_processor/post_processor.py` | `llm_client` → `infra.llm_client` |
| `smart_assistant/chat_widget.py` | `create_llm_client` → `infra.llm_client` |
| `smart_assistant/chat_worker.py` | LLMClient 类型引用 |
| `smart_assistant/tool_registry.py` | `LLMConfig` → `infra.config` |
| `smart_assistant/memory/embedding.py` | `EmbeddingClient` → `infra.embedding_client` |
| `smart_assistant/memory/memory_store.py` | FAISS 操作 → `infra.vector_store` |
| `paratranz/config_manager.py` | 移除 LLMConfig 类定义，改为 `from src.transbridge.infra.config import LLMConfig` |

### 5. `__init__.py` 公开 API

```python
from .llm_client import LLMClient, create_llm_client
from .embedding_client import EmbeddingClient
from .config import LLMConfig
from .vector_store import VectorStore

__all__ = ["LLMClient", "create_llm_client", "EmbeddingClient", "LLMConfig", "VectorStore"]
```

### 6. `vector_store.py` 接口定义

```python
import numpy as np
import faiss

class VectorStore:
    """FAISS 向量索引统一封装"""
    
    def __init__(self, dimension: int, index_path: str | None = None): ...
    
    def create_index(self, vectors: np.ndarray, ids: list[str]) -> None: ...
    
    def add(self, vectors: np.ndarray, ids: list[str]) -> None: ...
    
    def search(self, query_vector: np.ndarray, top_k: int = 5) -> list[tuple[str, float]]: ...
    
    def save(self, path: str) -> None: ...
    
    @staticmethod
    def load(path: str, dimension: int) -> "VectorStore": ...
    
    def remove(self, ids: list[str]) -> None: ...
```

**理由**: 当前 FAISS 操作散落在 `ai_translator/term_database.py` 的 `_TermVectorIndex` 内部类中。提取到 `infra/vector_store.py` 后，ai_translator 和 smart_assistant 共享同一套索引操作逻辑，避免重复实现。

### 更新: 2026-05-10 - Embedding/RAG 三模式可选

**决策**: LLMConfig 新增 `[embedding]` section，EmbeddingClient 支持三种运行模式。

**LLMConfig 扩展** (`infra/config.py`):

```python
@dataclass
class EmbeddingConfig:
    mode: str = "disabled"      # "api" | "local" | "disabled"
    provider: str = "openai"
    model: str = "text-embedding-3-small"
    api_key: str = ""
    base_url: str = ""
    local_model_path: str = ""  # local 模式下 ONNX/GGUF 模型路径
```

`LLMConfig` 新增字段: `embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)`

**EmbeddingClient 多后端** (`infra/embedding_client.py`):

| 模式 | 行为 | 依赖 |
|------|------|------|
| `api` | 调用云端 embedding API（OpenAI/Anthropic 格式） | 已有 openai SDK |
| `local` | 加载本地 ONNX 模型做推理 | onnxruntime（新增，预留接口暂不实现下载逻辑） |
| `disabled` | `embed()` 直接返回 None | 无 |

**VectorStore 条件初始化**: 当 mode=disabled 时，MemoryStore 不创建 VectorStore 实例（`_vector_store = None`），跳过所有 FAISS 操作。

**理由**: 不是所有用户都有 embedding API 额度或愿意部署本地模型。RAG/语义检索应该是可选增强而非强制依赖，用户可在设置中随时切换模式。disabled 模式下记忆系统降级为纯结构化检索。

**影响**:
- `infra/config.py`: 新增 `EmbeddingConfig` 数据类
- `infra/embedding_client.py`: 构造函数接收 `EmbeddingConfig`，按 mode 分支
- 新增依赖: `onnxruntime`（仅在 mode=local 时需要，延迟导入）

## Consequences

- **正面**: 消除 ai_translator ↔ smart_assistant 的直接代码依赖，两者都仅依赖 infra/；FAISS 操作统一到 VectorStore；新增 embedding/vector 需求时只需引用 infra/
- **负面**: 需要更新 9+ 个文件的 import 路径；paratranz/config_manager.py 需要结构调整（LLMConfig 提取后）
- **风险**: import 更新遗漏导致 ImportError → 通过全局 grep + import 验证链规避
