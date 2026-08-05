# Story 01: Phase 0 — Token 精确测量报告

**所属方案**: `plans/tool-prompt-layering/plan.md`
**测量日期**: 2026-08-05
**Tokenizer**: tiktoken `cl100k_base` (DeepSeek-v4 近似编码)
**工具版本**: 42 活跃工具 / 7 namespace / 0 废弃

---

## 1. System Prompt 各段 Token 测量

| 段名 | Token 数 | 占比 |
|------|---------|------|
| Template (正文) | 989 | 29.4% |
| Context (上下文) | ~19 | 0.6% |
| Tools section | 2,373 | 70.5% |
| **TOTAL (with context)** | **~3,365** | 100% |
| **TOTAL (no context)** | **~3,346** | — |

> **发现**: 工具段实际为 2,373 tokens，远超方案预估的 ~1,040 tokens。主要原因是工具目录 (directory) 实际为 1,324 tokens（预估 ~500 tokens），因为 42 个工具的 `[ns] name — summary` 行累积超出预期。

---

## 2. 工具段细分

| 子段 | Token 数 | 说明 |
|------|---------|------|
| Preloaded tools (2) | 260 | get_app_state + get_statistics 完整 Schema |
| get_tool_help schema | 386 | get_tool_help 工具定义 + 4 条使用规则 |
| Routing table | 401 | Markdown 表格: 7 行意图→namespace 映射 + 4 条规则 |
| Tool directory | 1,324 | 42 工具的 `[ns] name — summary` 行 |
| **Tools section TOTAL** | **2,373** | |

---

## 3. 各工具 Schema Token 排名 (Top 10)

| 排名 | 工具名 | Schema Tokens | Namespace |
|------|--------|--------------|-----------|
| 1 | set_filters | 407 | editor |
| 2 | manage_entry_labels | 392 | editor |
| 3 | set_stage | 346 | editor |
| 4 | run_postprocess | 339 | proofreader |
| 5 | write_back | 285 | writer |
| 6 | start_polish | 279 | translator |
| 7 | edit_translation | 266 | editor |
| 8 | get_tool_help | 265 | default |
| 9 | parse_sst | 256 | parser |
| 10 | set_translation_config | 246 | translator |

**统计** (42 工具):
- 总计: 8,376 tokens
- 均值: 199.4 tokens
- 中位数: 203 tokens
- 最大: 407 tokens (set_filters)
- 最小: 96 tokens (list_local_projects)

---

## 4. 分层 vs 全量 Schema 对比

| 方式 | Token 数 | 
|------|---------|
| 全量 Schema (一次性注入 42 工具) | **9,183** |
| 分层加载 (preloaded + directory + get_tool_help) | **3,365** |
| **节省** | **5,818 (63.4%)** |

> 注：全量 Schema 方式为方案设计前的旧方案（所有工具完整定义直接注入 system prompt）。实际运行时，LLM 按需通过 `get_tool_help(namespace="xxx")` 加载单个 namespace 的工具定义（单个 namespace ~200-400 tokens），整体 system prompt 保持在 ~3,400 tokens。

---

## 5. 各 Namespace 工具分布

| Namespace | 工具数 | Schema 合计 (tokens) |
|-----------|--------|---------------------|
| default | 8 | 1,165 |
| editor | 7 | 1,958 |
| translator | 9 | 1,717 |
| paratranz | 9 | 1,457 |
| parser | 5 | 1,141 |
| proofreader | 3 | 653 |
| writer | 1 | 285 |
| **合计** | **42** | **8,376** |

---

## 6. Phase 4 Baseline 数据

| 指标 | 值 |
|------|-----|
| 预加载工具数 | 2 (get_app_state, get_statistics) |
| 预加载 tokens | 260 |
| 路由表行数 | 7 |
| 路由表 tokens | 401 |
| 工具目录 tokens | 1,324 |
| get_tool_help Schema tokens | 386 |
| 总 namespace 数 | 7 |
| 活跃工具 | 42 |
| 废弃工具 | 0 |
| 全量 Schema → 分层 节省 | 63.4% |

---

## 7. 发现的问题

### 7.1 工具目录 Token 数超出预估

方案预估工具目录 ~500 tokens，实际为 1,324 tokens。原因：42 个工具 × 平均 ~31 tokens/行（`[namespace] tool_name — summary_text`）。S05 可考虑：
- 缩短 summary 文本（当前 `__post_init__` 截断上限 80 chars，可降至 50 chars）
- 合并小 namespace 的目录格式

### 7.2 双重导入隐患 (ToolRegistry 分离)

测量过程中发现：`tools/` 子包内的模块使用 `from src.transbridge.smart_assistant.tool_registry import ToolRegistry`（绝对路径含 `src.` 前缀），而 `prompts.py`/`tool_registry.py` 内部使用相对导入。当 Python path 同时包含项目根和 `src/` 时，这两个导入路径创建**两个独立的 `_ToolRegistry` 类实例**，导致工具注册到其中一个但另一个查询为空。

建议 S05 统一导入路径。

---

## 8. 验收标准达成

- [x] 使用 target tokenizer（tiktoken cl100k_base）测量当前 system prompt 各段 token
- [x] 产出测量报告：template 段 / context 段 / 工具段 / 总计
- [x] 工具段细拆：42 个工具各自的 Schema token 数排名
- [x] 为 Phase 4 建立 baseline

**结论**: Story 01 验收标准全部达成。数据移交 Phase 4 (Story 05) 进行调优。
