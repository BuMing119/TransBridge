# 向量化语义术语检索实施方案

> **状态**: ✔️ 已实现（2026-03-26）
>
> **实现文件**:
> - `src/transbridge/ai_translator/term_vector_index.py` — FAISS 向量索引
> - `src/transbridge/ai_translator/term_database.py` — 两阶段术语匹配
> - `src/transbridge/ai_translator/embedding_client.py` — Embedding 客户端抽象
> - `src/transbridge/ai_translator/translator.py` — in-flight 术语缓存
> - `src/transbridge/paratranz/config_manager.py` — 向量检索配置项

## 1. 现状分析

### 1.1 原有问题

原有的术语匹配（`match_terms`）仅支持精确子串匹配，存在以下局限：

| 问题 | 示例 | 影响 |
|------|------|------|
| 复数形式不匹配 | `Dragon` vs `Dragons` | 术语库只有单数，无法匹配复数 |
| 词形变化不匹配 | `Run` vs `Running` | 动词变形无法关联 |
| 语义相近不匹配 | `mage` vs `sorcerer` | 同义词无法关联 |
| 性能问题 | 500+ 术语时 O(n) 遍历 | 每批次重复计算 |

### 1.2 解决方案

引入 **FAISS 向量索引 + 两阶段召回**：
- **第一阶段（精确匹配）**: 正向子串 + 冠词规范化 + 反向前缀/后缀匹配
- **第二阶段（语义检索）**: FAISS 语义相似度检索（可选，通过配置开关控制）

## 2. 实现差异说明

| 项目 | 原方案 | 实际实现 |
|------|--------|---------|
| Embedding 客户端 | 单一实现 | 抽象基类 + 本地/API 双实现 |
| 向量索引存储 | 单一格式 | FAISS + JSON metadata |
| 配置模型 | 独立字段 | 集成到 LLMConfig |
| 数据目录 | 项目级 | 按 ESP 隔离 |

## 3. 配置项

```ini
[llm]
enable_semantic_match = true
semantic_similarity_threshold = 0.75
semantic_top_k = 5
max_terms_per_batch = 50

# Embedding 配置
embedding_provider = local        ; local | openai
embedding_model = all-MiniLM-L6-v2
embedding_api_key =
embedding_base_url =
embedding_local_model = all-MiniLM-L6-v2
```

## 4. 相关 ADR

- [ADR-003: 三轮 AI 翻译策略](../../docs/adr/003-three-round-translation-strategy.md) — 术语匹配在翻译流程中的位置
- [ADR-005: TOML Prompt 模板](../../docs/adr/005-toml-prompt-no-langchain.md) — 不使用 LangChain 的决策（含术语检索考量）

---

> 完整原始方案见 [docs/dev/vector_term_retrieval_plan.md](../../docs/dev/vector_term_retrieval_plan.md)
