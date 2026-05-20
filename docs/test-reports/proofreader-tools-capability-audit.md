# 后处理工具能力审计 — 对比 GUI PostProcessor 流水线

**日期**: 2026-05-18
**审计范围**: `tool_proofreader.py` 6 工具 vs GUI `PostProcessor` 五阶段流水线
**结论**: 🔴 **全部 5 个执行类工具均无法运行**，与 GUI 后处理完全不等价

---

## 一、根本原因

### `_run_postprocess_phase` 工厂函数调用不存在的 API（`tool_proofreader.py:42`）

```python
result = processor.process(collection)  # ← 5个类全都没有这个方法！
```

**验证结果**：

| 类 | `process` 方法 | `__init__` 必需参数 |
|----|:---:|---|
| `ConsistencyChecker(BaseChecker)` | ❌ 不存在 | `esp_path_or_manager` |
| `FormatValidator(BaseChecker)` | ❌ 不存在 | `*args, **kwargs` |
| `LLMRefiner` | ❌ 不存在 | `llm_client`(必填), `term_manager`, `game_profile`, `target_lang` |
| `LLMPolisher` | ❌ 不存在 | `llm_client`(必填), `term_manager`, `game_profile`, `target_lang`, `polish_level` |
| `LLMArbiter` | ❌ 不存在 | `llm_client`(必填), `game_profile`, `target_lang`, `strict_mode` |
| `PostProcessor`(GUI用) | ✅ 存在 | `llm_client`, `config`, `term_manager`, `esp_path` |

- `BaseChecker` 只有抽象方法 `check(entry)` — 逐条检查，不是批量 `process(collection)`
- `LLMRefiner/Polisher/Arbiter` 没有统一的 `process` 方法，各自有不同的入口（`refine()`/`polish()`/`arbitrate()`）
- 只有 GUI 的 `PostProcessor` 有 `process(collection)` 方法

### 即使修复 API 调用，LLM 工具也无法运行

`LLMRefiner/Polisher/Arbiter` 的 `__init__` 都需要 `llm_client: LLMClient` 作为**必填**第一参数，但 `_run_postprocess_phase` 不创建 LLMClient，只传 `SimpleNamespace` 配置对象。

---

## 二、逐阶段对比

| 阶段 | GUI PostProcessor | AI 助手工具 | 状态 |
|------|------------------|-----------|:---:|
| 1a. 一致性检查 | `ConsistencyChecker(esp_path).check(entry)` 逐条 + 批量聚合 | `run_consistency_check` → `processor.process(collection)` | 🔴 崩溃 |
| 1b. 格式校验 | `FormatValidator().check(entry)` 逐条 | `run_format_validation` → `processor.process(collection)` | 🔴 崩溃 |
| 1c. 质量关卡 | `QualityGateChecker().check(entry)` 逐条 | **无对应工具** | 🔴 缺失 |
| 2a. LLM修复 | `LLMRefiner(llm_client, term_manager).refine(entries, issues_by_entry)` | `run_llm_refinement` → `processor.process(collection)` | 🔴 崩溃 |
| 2b. LLM润色 | `LLMPolisher(llm_client, ...).polish(entry)` | `run_llm_polish` → `processor.process(collection)` | 🔴 崩溃 |
| 3. LLM裁决 | `LLMArbiter(llm_client, ...).arbitrate(entries, issues, ...)` | `run_llm_arbitration` → `processor.process(collection)` | 🔴 崩溃 |
| 4. Stage更新 | 根据结果更新 `entry.stage` | **不更新** | 🟡 缺失 |
| 报告 | `PostProcessResult` 含完整统计 | `get_quality_report`（仅缓存最近一次） | 🟡 弱 |

---

## 三、GUI PostProcessor 的完整管线（工具无法复制）

```
PostProcessor.process_entries():
  │
  ├─ 阶段1: 检测 (Checker)
  │   ├─ ConsistencyChecker.check(entry)  → 收集 issues
  │   ├─ FormatValidator.check(entry)      → 收集 issues
  │   └─ QualityGateChecker.check(entry)   → 收集 issues
  │        ↓ issues_by_entry
  ├─ 阶段2a: 修复 (LLMRefiner)
  │   └─ LLMRefiner.refine(entries, issues_by_entry, llm_client, term_manager)
  │        ↓ refine_results
  ├─ 阶段2b: 润色 (LLMPolisher)
  │   └─ LLMPolisher.polish(entry, llm_client) for each entry
  │        ↓ polish_results
  ├─ 阶段3: 裁决 (LLMArbiter)
  │   └─ LLMArbiter.arbitrate(entries, issues, refine_results, llm_client)
  │        ↓ decisions
  └─ 阶段4: 执行
      └─ 根据 decisions 更新 entry.stage + 写入 collection
```

**关键特性工具完全缺失**：
- 五阶段串行依赖链（前一阶段输出是后一阶段的输入）
- LLMClient 共享（避免重复创建 API 连接）
- TermDatabaseManager 共享（术语一致性检查需要术语库）
- 暂停/停止/断点续传
- 并发控制（ThreadPoolExecutor）
- Entry stage 自动更新

---

## 四、问题清单

| # | 级别 | 工具 | 问题 |
|---|------|------|------|
| 1 | 🔴 Blocker | 全部5个执行工具 | 调用不存在的 `processor.process(collection)`，运行时 `AttributeError` 崩溃 |
| 2 | 🔴 Blocker | LLM修复/润色/裁决 | `__init__` 需要 `llm_client` 必填参数，工厂函数未传入 |
| 3 | 🔴 Blocker | 全部 | 缺少 QualityGate 工具 |
| 4 | 🟡 Major | 全部 | 工具独立运行，无法复现 GUI 的五阶段串行管线 |
| 5 | 🟡 Major | 全部 | 不更新 entry.stage |
| 6 | 🟡 Major | LLM修复 | 需要 `issues_by_entry`（前置检测结果），但工具独立调用无法传入 |
| 7 | 🟢 Minor | get_quality_report | 仅缓存最近一次结果，无法查看历史报告 |

---

## 五、修复建议

**根本方案**：废弃当前单工具调用模式，改为一个统一的 `run_postprocess` 工具：

```python
def _tool_run_postprocess(args: dict, ctx) -> ToolResult:
    """运行完整的五阶段后处理流水线（与 GUI 一致）。"""
    # 1. 创建 LLMClient + TermDatabaseManager
    # 2. 创建 PostProcessor(llm_client, config, term_manager)
    # 3. 调用 postprocessor.process_entries(entries, ...)
    # 4. 返回完整 PostProcessResult
```

参数：`phases` (选择运行的阶段)、`entry_ids` (可选，指定条目)、`scope` (翻译作用域)。

这样 LLM 一次调用即可完成与 GUI 完全一致的后处理流水线，而非手动编排 5 个独立工具（且当前全部崩溃）。
