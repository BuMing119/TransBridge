# Story 01: infra/ 共享基础设施提取与搬迁

**所属方案**: `plans/agent-upgrade/plan.md`
**技术模块**: infra
**状态**: 已确认
**创建日期**: 2026-05-10

## 前置依赖

### 上游 Story
- 无（本 Story 是基础设施层，所有后续 Story 依赖它）

### 引用的架构决策
- [ADR-008: 新增 infra/ 包结构](../../../docs/adr/008-smart-assistant-code-layering.md)
- [ADR-010: infra/ 提取决策 + 跨包 import 契约](../../../docs/adr/010-infra-extraction.md)

## 验收标准

- [ ] `src/transbridge/infra/__init__.py` 导出 5 个公开符号
- [ ] `llm_client.py` 从 ai_translator/ 搬迁到 infra/，内容不变
- [ ] `embedding_client.py` 从 ai_translator/ 搬迁到 infra/，内容不变
- [ ] `LLMConfig` 类从 paratranz/config_manager.py 提取到 infra/config.py
- [ ] `vector_store.py` 新建，包含 VectorStore 类（create_index/add/search/save/load/remove）
- [ ] 9 个受影响文件的 import 全部按 ADR-010 契约更新
- [ ] `python -c "from src.transbridge.infra import LLMClient, create_llm_client, EmbeddingClient, LLMConfig, VectorStore"` 无 ImportError

## 数据流

纯文件搬迁 + import 更新，无运行时数据流变化。搬迁后依赖方向：

```
infra/  (共享基础设施)
  ├──→ faiss, numpy, openai, anthropic (外部依赖)
  └──← ai_translator/translator.py
  └──← ai_translator/term_database.py
  └──← ai_translator/prompt_builder.py
  └──← ai_translator/post_processor/post_processor.py
  └──← smart_assistant/chat_widget.py
  └──← smart_assistant/chat_worker.py
  └──← smart_assistant/tool_registry.py
  └──← smart_assistant/memory/memory_store.py (Story-04)
  └──← paratranz/config_manager.py
```

## 关键接口

### `infra/__init__.py`

```python
from .llm_client import LLMClient, create_llm_client
from .embedding_client import EmbeddingClient
from .config import LLMConfig
from .vector_store import VectorStore

__all__ = ["LLMClient", "create_llm_client", "EmbeddingClient", "LLMConfig", "VectorStore"]
```

### `infra/vector_store.py` — VectorStore 类

```python
import numpy as np
import faiss

class VectorStore:
    def __init__(self, dimension: int, index_path: str | None = None): ...
    def create_index(self, vectors: np.ndarray, ids: list[str]) -> None: ...
    def add(self, vectors: np.ndarray, ids: list[str]) -> None: ...
    def search(self, query_vector: np.ndarray, top_k: int = 5) -> list[tuple[str, float]]: ...
    def save(self, path: str) -> None: ...
    @staticmethod
    def load(path: str, dimension: int) -> "VectorStore": ...
    def remove(self, ids: list[str]) -> None: ...
```

### `infra/config.py` — LLMConfig + EmbeddingConfig（提取+扩展）

```python
@dataclass
class EmbeddingConfig:
    mode: str = "disabled"       # "api" | "local" | "disabled"
    provider: str = "openai"
    model: str = "text-embedding-3-small"
    api_key: str = ""
    base_url: str = ""
    local_model_path: str = ""   # local 模式 ONNX/GGUF 路径

@dataclass
class LLMConfig:
    provider: str = "openai"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    # ... 其他字段
```

## 实现步骤

### 步骤 1: 创建 infra/ 包 + `__init__.py`

**涉及文件**: `src/transbridge/infra/__init__.py`（新建）

**实现要点**: 创建目录，编写 `__init__.py` 导出 5 个符号，包内相对导入

**伪代码**: 见上方「关键接口」

**边界条件**: 目录已存在 → 仅创建/覆盖 `__init__.py`

**测试策略**: 搬迁完成后 `python -c "from src.transbridge.infra import *"` → 无 ImportError

### 步骤 2: 搬迁 + 扩展 llm_client.py + embedding_client.py

**额外关注**: `embedding_client.py` 搬迁后需扩展三模式支持：
- 构造函数接收 `EmbeddingConfig`，按 mode 分支
- `api`: 调用云端 embedding API
- `local`: ONNX 模型推理（预留，暂不实现下载）
- `disabled`: `embed()` 返回 None

### 步骤 2b: git mv 搬迁文件

**涉及文件**: 
- `src/transbridge/ai_translator/llm_client.py` → `src/transbridge/infra/llm_client.py`（搬迁）
- `src/transbridge/ai_translator/embedding_client.py` → `src/transbridge/infra/embedding_client.py`（搬迁）

**实现要点**: 使用 `git mv` 保留文件历史，文件内容不修改

**边界条件**: 
- llm_client.py 内部可能引用了其他 ai_translator 子模块（如 prompt_builder）→ 搬迁后检查，如有则需调整为跨包绝对导入
- embedding_client.py 内部引用 ai_translator 组件 → 同上

**测试策略**: `python -c "from src.transbridge.infra.llm_client import LLMClient; from src.transbridge.infra.embedding_client import EmbeddingClient"`

### 步骤 3: 提取 LLMConfig → infra/config.py

**涉及文件**:
- `src/transbridge/infra/config.py`（新建）
- `src/transbridge/paratranz/config_manager.py`（修改）

**实现要点**:
- 从 `config_manager.py` 复制 `LLMConfig` 类到 `infra/config.py`
- 在 `config_manager.py` 中原位置替换为 `from src.transbridge.infra.config import LLMConfig`
- 保持 ParatranzConfig 类在原文件不变

**边界条件**:
- `LLMConfig.load_from_file()` 引用了 ParatranzConfig 的数据目录逻辑 → 提取后需调整为独立实现或通过参数注入
- config_manager.py 中可能有 `from .config_manager import LLMConfig` 的内部引用 → 需保持重导出：`LLMConfig = LLMConfig`（从 infra 导入后 re-export）

**测试策略**: `python -c "from src.transbridge.paratranz.config_manager import LLMConfig, ParatranzConfig"` → 两者均可正常导入

### 步骤 4: 新建 vector_store.py

**涉及文件**: `src/transbridge/infra/vector_store.py`（新建）

**实现要点**: 封装 FAISS IndexFlatIP 操作，提供统一接口。dimension 在构建时指定

**伪代码**:
```python
class VectorStore:
    def __init__(self, dimension: int, index_path: str | None = None):
        self._dimension = dimension
        self._index = faiss.IndexFlatIP(dimension)
        self._id_map: dict[int, str] = {}  # faiss_id → external_id
        if index_path:
            self._index = faiss.read_index(index_path)
    
    def add(self, vectors: np.ndarray, ids: list[str]) -> None:
        start_id = self._index.ntotal
        self._index.add(vectors.astype(np.float32))
        for i, ext_id in enumerate(ids):
            self._id_map[start_id + i] = ext_id
    
    def search(self, query_vector: np.ndarray, top_k: int = 5) -> list[tuple[str, float]]:
        q = query_vector.reshape(1, -1).astype(np.float32)
        distances, indices = self._index.search(q, top_k)
        results = []
        for i in range(indices.shape[1]):
            idx = indices[0, i]
            if idx >= 0 and idx in self._id_map:
                results.append((self._id_map[idx], float(distances[0, i])))
        return results
```

**边界条件**: FAISS 未安装 → ImportError 提示安装 `faiss-cpu`；空索引搜索 → 返回空列表；embedding mode=disabled 时不初始化 VectorStore（条件初始化）

**测试策略**: `python -c "from src.transbridge.infra import VectorStore; vs = VectorStore(128); vs.add(np.random.randn(3,128).astype(np.float32), ['a','b','c']); print(vs.search(np.random.randn(128).astype(np.float32), 2))"`

### 步骤 5-7: 更新 9 文件 import

**涉及文件**（按 ADR-010 契约）:

| 文件 | 旧 import | 新 import |
|------|----------|----------|
| `ai_translator/translator.py` | `from .llm_client import ...` | `from src.transbridge.infra.llm_client import ...` |
| `ai_translator/term_database.py` | `from .embedding_client import ...` | `from src.transbridge.infra.embedding_client import ...` |
| `ai_translator/prompt_builder.py` | `from .llm_client import ...` | `from src.transbridge.infra.llm_client import ...` |
| `ai_translator/post_processor/post_processor.py` | `from ..llm_client import ...` | `from src.transbridge.infra.llm_client import ...` |
| `paratranz/config_manager.py` | 类定义 | `from src.transbridge.infra.config import LLMConfig` |
| `smart_assistant/chat_widget.py` | `from ...ai_translator.llm_client import create_llm_client` | `from src.transbridge.infra.llm_client import create_llm_client` |
| `smart_assistant/chat_worker.py` | 如引用 LLMClient 类型 | `from src.transbridge.infra.llm_client import LLMClient` |
| `smart_assistant/tool_registry.py` | `from ...paratranz.config_manager import LLMConfig` | `from src.transbridge.infra.config import LLMConfig` |

**实现要点**: 
- 逐文件修改，每改一个验证一次导入
- 特别注意懒加载导入（函数体内的 import）——上次 smart_assistant 分层遗漏了 4 处
- Grep 全项目的 `from.*ai_translator.*llm_client` 和 `from.*ai_translator.*embedding_client` 确保无遗漏

**边界条件**: 某文件的 import 路径为变量/动态 → 不常见，但需检查；paratranz 内部可能有其他文件 import LLMConfig → Grep 全项目

### 步骤 8: 全链路 import 验证

**无代码修改**，执行以下验证命令:

```bash
# 1. 基础包导入
python -c "from src.transbridge.infra import LLMClient, create_llm_client, EmbeddingClient, LLMConfig, VectorStore; print('infra OK')"

# 2. ai_translator 导入链
python -c "from src.transbridge.ai_translator.translator import AutoTranslator; print('translator OK')"
python -c "from src.transbridge.ai_translator.term_database import TermDatabaseManager; print('term_db OK')"
python -c "from src.transbridge.ai_translator.post_processor.post_processor import PostProcessor; print('post_processor OK')"

# 3. smart_assistant 导入链
python -c "from src.transbridge.ui.tools.smart_assistant import SmartAssistantPanel; print('UI OK')"

# 4. paratranz 导入链
python -c "from src.transbridge.paratranz.config_manager import ParatranzConfig, LLMConfig; print('config OK')"
```

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/infra/__init__.py` | 新建 | 5 符号公开导出 |
| `src/transbridge/infra/llm_client.py` | 搬迁 | 从 ai_translator/ git mv |
| `src/transbridge/infra/embedding_client.py` | 搬迁 | 从 ai_translator/ git mv |
| `src/transbridge/infra/config.py` | 新建 | LLMConfig 从 paratranz/ 提取 |
| `src/transbridge/infra/vector_store.py` | 新建 | FAISS 索引封装 |
| `src/transbridge/ai_translator/llm_client.py` | 删除 | 已搬迁 |
| `src/transbridge/ai_translator/embedding_client.py` | 删除 | 已搬迁 |
| `src/transbridge/ai_translator/translator.py` | 修改 | import → infra |
| `src/transbridge/ai_translator/term_database.py` | 修改 | import → infra |
| `src/transbridge/ai_translator/prompt_builder.py` | 修改 | import → infra |
| `src/transbridge/ai_translator/post_processor/post_processor.py` | 修改 | import → infra |
| `src/transbridge/paratranz/config_manager.py` | 修改 | 提取 LLMConfig + import infra |
| `src/transbridge/smart_assistant/chat_widget.py` | 修改 | create_llm_client → infra |
| `src/transbridge/smart_assistant/chat_worker.py` | 修改 | LLMClient 引用 → infra |
| `src/transbridge/smart_assistant/tool_registry.py` | 修改 | LLMConfig → infra |

## 风险与注意事项

- **风险**: llm_client.py 内部引用了 ai_translator/ 其他模块 → **缓解**: 搬迁前先读文件内容，如有交叉引用则改为绝对导入
- **注意**: `paratranz/config_manager.py` 中 LLMConfig 可能被其他模块直接 `from paratranz.config_manager import LLMConfig` 引用 → 在 config_manager.py 中保留 `LLMConfig` 重导出（`from src.transbridge.infra.config import LLMConfig` 后 `LLMConfig = LLMConfig`）
- **注意**: 本 Story 完成后，ai_translator/ 和 smart_assistant/ 的 Story-02~05 才能正常开始（它们依赖 infra/）
