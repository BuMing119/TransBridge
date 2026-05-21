# AI助手工具 vs 原后处理工作流 — 功能等价性评估

**日期**: 2026-05-20
**对应方案**: `plans/ai-post-process/plan.md` + `plans/agent-tool-expansion/plan.md`
**评估方法**: 独立阅读源码，不参考任何历史报告

## 评估范围

| 原后处理功能 (ai-post-process) | AI助手工具 (agent-tool-expansion) | 等价性 |
|------|------|------|
| S01-03 检测（一致性/格式/质量关卡） | `run_postprocess` (phases参数) | ✅ 等价 |
| S04 LLM修复 | `run_postprocess` (phases含refinement) | ✅ 等价 |
| S05 LLM润色 | `run_postprocess` (phases含polish) / `start_polish` | ✅ 等价 |
| S06 LLM裁决 | `run_postprocess` (phases含arbitration) | ✅ 等价 |
| S07 五阶段流水线协调 | `run_postprocess` 直接调用 `PostProcessor.process_entries()` | ✅ 等价 |
| S07 断点续传 | `run_postprocess` 创建 `PostProcessCheckpoint` | ✅ 等价 |
| S07 暂停/停止 | `stop_event` 支持 ✅ / `pause_event` 不支持 ❌ | ⚠️ 部分 |
| S09 独立润色入口 | `start_polish` | ⚠️ 部分 |
| S10 Excel报告生成 | 无工具调用 ReportGenerator | ❌ 缺失 |
| S11 报告对话框 | GUI功能，Agent工具层无需复现 | — N/A |
| S12 完成流程集成 | GUI功能，Agent工具层无需复现 | — N/A |
| S13 历史报告查看 | 无工具列出/打开历史报告 | ❌ 缺失 |

## 详细分析

### 1. 五阶段流水线 — ✅ 完全等价

**原流程** (`PostProcessor.process_entries()`):
```
阶段1 DETECT  → ConsistencyChecker + FormatValidator + QualityGateChecker
阶段2a REFINE  → LLMRefiner（仅处理有问题的条目）
阶段2b POLISH  → LLMPolisher（可选，polish_scope决定范围）
阶段3 ARBITRATE → LLMArbiter（pass/reject/pending）
阶段4 EXECUTE  → 根据裁决更新 entry.translation 和 entry.stage
```

**Agent工具** (`_tool_run_postprocess` 在 `tool_proofreader.py:22`):
- 创建相同的 `PostProcessorConfig.from_llm_config(llm_cfg)`
- 创建相同的 `PostProcessor(config)` 并 `register_default_checkers()`
- 调用相同的 `processor.process_entries()`
- phases 参数可以精细控制哪些阶段运行（比原GUI更灵活）
- 进度通过 TaskManager 暴露，`get_task_status` 可查询阶段进度
- 支持 stop_event（通过 `stop_task` 工具）
- 完成后通过 `ctx.safe_mutate()` 通知UI刷新

**结论**: 核心流水线逻辑完全一致，使用相同的 PostProcessor 类。

### 2. 翻译+后处理统一流程 — ✅ 自动集成

`start_translation` 工具调用 `AutoTranslator.translate()`，当 `enable_post_process=True` 时（默认），翻译完成**自动执行后处理**（`translator.py:622`）。无需额外调用 `run_postprocess`。

唯一的区别是 `start_translation` 中的后处理进度回调较简化（`translator.py:659`），不如独立 `run_postprocess` 的阶段进度粒度细。

### 3. 独立润色 — ⚠️ 部分覆盖

| 原Story-09功能 | `start_polish` 工具 | 状态 |
|------|------|------|
| 润色强度选择 (light/moderate/aggressive) | intensity参数 (light/medium/heavy) + 值映射 | ✅ |
| 润色范围 (all/passed/has_issues) | 不支持，需手动指定 entry_ids | ❌ |
| 润色预览确认对话框 | 无（纯GUI功能） | ❌ |
| 逐条接受/拒绝 | 不支持 | ❌ |
| 润色结果直接写入条目 | 润色结果写入 entry.translation | ✅ |
| LLM失败保留原译文 | LLMPolisher.polish() 异常捕获 | ✅ |

**关键缺失**: `start_polish` 不支持 `polish_scope`（all/passed/has_issues），必须由Agent预先筛选entry_ids。

### 4. Excel报告生成 — ❌ 完全缺失

**原功能** (`ReportGenerator` 在 `report_generator.py`):
- 翻译模式: 5 Sheet Excel（Summary/Entries/Issues/Refinements/Arbitrations）
- 润色模式: 3 Sheet Excel（Summary/Entries/Polish Details）
- 文件轮转：保留最近20份报告
- 格式样式：微软雅黑表头、自动列宽、边框

**Agent工具现状**:
- `run_postprocess` 仅在内存中保存 `_last_report` 字典（轻量摘要）
- `get_quality_report` 返回内存中的最近一份摘要
- **无任何工具调用 `ReportGenerator`**
- `smart_assistant/` 整个包中 `ReportGenerator` 零引用

**影响**: Agent无法：
- 生成Excel格式的结构化报告文件
- 查看每条条目的修复/润色/裁决详情
- 查看历史报告列表
- 导出报告供外部使用

### 5. 中间数据保留 — ⚠️ 严重截断

`_last_report` 字典 (`tool_proofreader.py:120-133`) 只保存：
```python
{
    "total_checked", "issue_count", "auto_fixed",
    "needs_review": [...],  # 仅 entry_id 列表
    "issues": [...],         # 前50条，仅entry_id/issue_type/severity/message
    "timestamp": ...,
}
```

**未保留的关键数据** (`PostProcessResult` 中包含但 `_last_report` 丢弃):
- `refine_results`: {entry_id: RefineResult} — 每条条目的修复前后译文和信心度
- `polish_results`: {entry_id: PolishResult} — 每条条目的润色变更细节
- `decisions`: {entry_id: ArbiterDecision} — 每条条目的裁决理由和建议
- `execution_result`: 通过的/打回的/待审的计数

**影响**: `get_quality_report` 无法提供 per-entry 级别的质量详情。

### 6. 高级配置能力 — ⚠️ 部分

| 配置项 | 来源 | Agent工具可配置 |
|------|------|------|
| 阶段开关 (enable_consistency_check等) | LLMConfig / phases参数 | ✅ phases参数覆盖 |
| 质量关卡批大小 | LLMConfig (`pp_quality_gate_batch_size`) | ❌ 必须通过GUI设置 |
| 修复批大小 | LLMConfig (`pp_refinement_batch_size`) | ❌ |
| 润色批大小 | LLMConfig (`pp_polish_batch_size`) | ❌ |
| 润色范围 (all/passed/has_issues) | LLMConfig (`pp_polish_scope`) | ❌ |
| 润色强度 (light/moderate/aggressive) | LLMConfig (`pp_polish_level`) | ❌ |
| 严格裁决模式 | LLMConfig (`pp_strict_arbitration`) | ❌ |
| 并发线程数 (max_workers) | 硬编码为1 | ❌ |

**影响**: 后处理的高级参数调整必须通过GUI设置，Agent无法在运行时微调。

### 7. 暂停支持 — ❌ 已移除（有意设计决策）

B5 决策：暂停为"假暂停"（status和实际行为不同步，API费用持续消耗），整个系统统一移除。`run_postprocess` 不传 `pause_event`。这是正确的设计选择，不算功能缺失。

## 问题清单

### Blocker（阻碍等价性）
1. **[BLOCKER] 无Excel报告生成** — `run_postprocess` 和 `start_translation` 均不调用 `ReportGenerator`。原后处理工作流的核心产出之一就是结构化Excel报告。Agent工具完全缺失此能力。
   - **影响范围**: 后处理结果无法以结构化文件形式持久化，用户无法离线查看或分享质量报告
   - **修复建议**: 在 `run_postprocess` 完成回调中调用 `ReportGenerator.generate_translate_report()` 生成Excel文件，并将文件路径写入 `_last_report`

### Critical（严重影响可用性）
2. **[CRITICAL] 中间数据丢弃** — `refine_results`、`polish_results`、`decisions` 全部丢弃。Agent无法回答"某条条目修复后变成了什么"、"润色改了哪些内容"、"为什么打回某条目"等问题。
   - **修复建议**: `_last_report` 保留完整的 refine/polish/decisions 数据，`get_quality_report` 支持按 entry_id 查询详情

3. **[CRITICAL] 无历史报告访问** — 没有工具列出/打开已生成的历史报告文件。原Story-13提供了历史报告查看功能。
   - **修复建议**: 在 proofreader namespace 新增 `list_quality_reports` 工具，或扩展 `get_quality_report` 支持 `historical: bool` 参数

### Major（功能缺口）
4. **[MAJOR] 润色范围缺失** — `start_polish` 不支持 `polish_scope`（all/passed/has_issues）。Agent需手动筛选entry_ids。
   - **修复建议**: `start_polish` 新增 `scope` 参数 (all/passed/has_issues)，底层调用 `_select_entries_for_polish()` 逻辑

5. **[MAJOR] 后处理高级参数不可配置** — 批大小、润色强度、严格模式等无法通过工具设置。
   - **修复建议**: `set_translation_config` 扩展支持后处理相关字段，或 `run_postprocess` 新增可选参数

6. **[MAJOR] 翻译后处理进度合并** — `start_translation` 中的后处理进度回调为简化版，Agent无法感知后处理的具体阶段进度。
   - **修复建议**: `start_translation` 的进度回调区分翻译阶段和后处理阶段

### Minor（改进建议）
7. **[MINOR] max_workers未暴露** — `run_postprocess` 始终单线程执行，批处理性能低于GUI（GUI支持多线程）。
   - **修复建议**: `run_postprocess` 新增 `max_workers` 参数

8. **[MINOR] log_callback未暴露** — LLM调用详细日志无法获取。
   - **修复建议**: 日志输出到TaskManager的progress中

## 结论

### 流水线执行 — ✅ 达到等价效果
AI助手工具的核心后处理流水线（检测→修复→润色→裁决→执行）**与GUI行为完全一致**，因为 `run_postprocess` 直接复用了相同的 `PostProcessor.process_entries()` 方法。配置加载、LLMClient创建、术语数据库初始化、断点续传均与GUI相同。

### 报告系统 — ❌ 严重缺失
报告生成（Excel导出）、历史报告访问、中间数据持久化三项能力完全缺失。这是原后处理工作流的重要产出，Agent工具无法替代。

### 润色独立入口 — ⚠️ 基础功能覆盖，高级功能缺失
`start_polish` 可以执行润色并写入条目，但缺少润色范围选择和预览确认流程（后者是GUI特有，Agent层不需要）。

### 总体评级: ⚠️ 流水线等价，报告系统待补全
AI助手工具能**执行**后处理（核心目标达成），但不能**报告**后处理结果（原工作流的另一半功能缺失）。如需达到"全部功能"等价，必须补全报告生成和中间数据保留。

## 签名
QA 审查完成。流水线等价性确认通过，报告系统标记为 Blocker 级别缺失。
