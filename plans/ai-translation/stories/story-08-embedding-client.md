# Story 08: Embedding 客户端

**所属方案**: `plans/ai-translation/plan.md`
**状态**: ✔️ 已实现

## 概述

Embedding 客户端抽象层，支持本地 sentence-transformers 模型和 OpenAI 兼容 API 两种实现。

## 关键设计

- **EmbeddingClient 抽象基类**: `embed(texts) -> list[list[float]]`
- **LocalSentenceTransformerClient**: 使用 sentence-transformers（如 all-MiniLM-L6-v2）本地推理
- **OpenAIEmbeddingClient**: 调用 OpenAI 兼容的 /v1/embeddings 端点
- **create_embedding_client()**: 工厂函数，根据 LLMConfig 动态选择实现
- **可选依赖**: sentence-transformers 和 faiss-cpu 为可选依赖

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ai_translator/embedding_client.py` | EmbeddingClient + 两个实现 + 工厂函数 |
