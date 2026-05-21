# AI 助手工具 vs 原后处理工作流 — 等价性 QA 审计

**日期**: 2026-05-20
**审计范围**: `run_postprocess` + `start_polish` + 相关工具 vs GUI `PostProcessor` 五阶段流水线
**前置审计**: [story-25-postprocess-unification-qa.md](story-25-postprocess-unification-qa.md) (2026-05-20，16 项问题)
**方案文档**: `plans/agent-tool-expansion/stories/story-25-postprocess-unification.md`

---

## 一、上轮审计修复确认

上一轮审计（2026-05-20）发现 5 Blocker + 3 Critical + 6 Major + 2 Minor = 16 项问题。本次复验确认：

| # | 问题 | 状态 |
|---|------|:---:|
| B1 | `PostProcessor()` 构造参数错误 | ✅ 已修复 |
| B2 | 未调用 `register_default_checkers()` | ✅ 已修复 |
| B3 | `LLMPolisher()` 构造参数错误 | ✅ 已修复 |
| B4 | `LLMPolisher` 导入路径 `llm_polisher` → `polisher` | ✅ 已修复 |
| B5 | `start_polish` 未创建 LLMClient | ✅ 已修复 |
| C5 | `start_polish` 不更新 `_last_report` | ✅ 已修复 |
| C6 | `intensity` 参数值映射 `medium` → `moderate` | ✅ 已修复 |
| N1 | `paratranz.api_client` 导入路径 (6处) | ✅ 已修复 |
| N2 | `parser.sst_parser` 导入路径 | ✅ 已修复 |
| N4 | orchestrator namespace `editor:get_statistics` → `default:get_statistics` | ✅ 已修复 |
| N3 | `import_strings` 模块不存在 | 🟡 已改为友好报错（非崩溃） |

**结论**: 上一轮的 11 个 Blocker/Critical 全部修复或降级。`run_postprocess` 的核心管线已可正常运行。

---

## 二、核心问题：AI 工具能否达到与原后处理工作流一样的效果？

### 答案：**基本可以，但有 4 个质量缺口**

### 2.1 已验证等价的部分

`_tool_run_postprocess` 直接包装 GUI 同款 `PostProcessor.process_entries()`，以下能力与 GUI 完全一致：

| 维度 | 实现方式 | 等价？ |
|------|---------|:---:|
| 五阶段流水线 | `PostProcessor.process_entries()` 内部串行协调 | ✅ 完全等价 |
| 术语一致性检查 | `ConsistencyChecker` via `register_default_checkers()` | ✅ 完全等价 |
| 格式校验 | `FormatValidator` via `register_default_checkers()` | ✅ 完全等价 |
| 质量关卡 (LLM) | `QualityGateChecker` via `register_default_checkers()` | ✅ 完全等价 |
| LLM 修复 | `LLMRefiner` via `register_default_checkers()` | ✅ 完全等价 |
| LLM 润色 (流水线内) | `LLMPolisher` via `register_default_checkers()` | ✅ 完全等价 |
| LLM 裁决 | `LLMArbiter` via `register_default_checkers()` | ✅ 完全等价 |
| 阶段选择控制 | `phases` 参数 → `config.enable_*` | ✅ 完全等价 |
| 裁决结果执行 | `_execute_decisions()` 更新 entry.stage | ✅ 完全等价 |
| 译文优先级 | polish > refine > original | ✅ 完全等价 |
| 规则回退裁决 | `_rule_based_decide()` 无 LLM 时自动回退 | ✅ 完全等价 |
| 停止支持 | `stop_event` → `stop_task` 工具 | ✅ 完全等价 |
| 并发批处理 | `ThreadPoolExecutor` (PostProcessor 内部) | ✅ 完全等价 |
| LLM 监控线程 | `register_default_checkers` 自动创建 monitor | ✅ 完全等价 |
| 配置同步 | `PostProcessorConfig.from_llm_config()` | ✅ 完全等价 |
| 术语库加载 | `TermDatabaseManager` → `register_default_checkers()` | ✅ 完全等价 |

### 2.2 已确认的功能缺口

#### G1: 无实时进度反馈 (Major)

**文件**: `tool_proofreader.py:88-144`

`process_entries()` 支持 `progress_callback(phase, current, total, message)` 参数，但工具未传入。后果：
- TaskManager 在整个后处理期间 progress 始终为空
- 用户通过 `get_task_status` 查询时看不到当前阶段和进度
- GUI 版本在每个阶段/批次完成时都会更新进度

**影响**: 对于大量条目（如 500+ 条），用户可能需要等待数分钟无任何反馈，无法判断是否卡死。

**修复方案**: 在 `_run()` 中传入 `progress_callback`：
```python
def _progress(phase, current, total, message):
    tm.update_progress(task_id, {
        "phase": phase, "current": current, "total": total, "message": message,
    })
result = processor.process_entries(
    entries, stop_event=stop_event,
    esp_path=getattr(ctx, 'esp_path', None),
    progress_callback=_progress,
)
```

---

#### G2: 无断点续传 (Medium)

**文件**: `tool_proofreader.py:96-98`

`process_entries()` 支持 `checkpoint: PostProcessCheckpoint` 参数，工具未传入。后果：
- 任务被 `stop_task` 取消后，所有已完成批次的中间结果丢失
- 重新运行需从头开始，产生重复 LLM API 费用
- GUI 版本支持断点保存和恢复

**影响**: 大批量后处理被中断后经济损失较大（重复 LLM 调用）。

**修复方案**: 创建 `PostProcessCheckpoint` 并传入：
```python
from src.transbridge.ai_translator.post_processor.checkpoint import PostProcessCheckpoint
checkpoint = PostProcessCheckpoint()
result = processor.process_entries(
    entries, stop_event=stop_event,
    esp_path=getattr(ctx, 'esp_path', None),
    checkpoint=checkpoint,
    progress_callback=_progress,
)
```

---

#### G3: 完成后不通知 UI 刷新 (Medium)

**文件**: `tool_proofreader.py:116-128`

`process_entries()` 完成后会修改 `entry.translation` 和 `entry.stage`（通过 `_execute_decisions()`）。这些修改发生在后台线程中，但工具未调用 `ctx.safe_mutate(lambda: ctx.notify_collection_modified())` 通知 UI。

对比 `edit_translation` (`tool_editor.py:193`) 和 `set_stage` (`tool_editor.py:238`)，两者都正确调用了 `safe_mutate` + `notify_collection_modified`。

**影响**: 后处理完成后，Step2 表格不会自动刷新，用户看到的是旧数据。

**修复方案**: 在 `_run()` 完成分支中（`tm.set_status(task_id, "completed")` 之后）添加：
```python
ctx.safe_mutate(lambda: ctx.notify_collection_modified())
```

---

#### G4: 缺少 API Key 前置检查 (Minor)

**文件**: `tool_proofreader.py:57-62`

`run_postprocess` 直接调用 `LLMConfig.load_from_file()` + `create_llm_client()`，当 API Key 未配置时，异常信息是底层 SDK 错误（如 `AuthenticationError`），而非友好的 "请先配置 API Key"。

对比 `start_translation` (`tool_translator.py:38-64`)，它有完整的 API Key + 术语源前置检查。

**影响**: 用户体验差，错误信息不友好。

**修复方案**: 在 `_tool_run_postprocess` 开头添加 API Key 检查（复用 `start_translation` 的模式）。

---

## 三、独立润色工具 (`start_polish`) 状态

`start_polish` 与流水线内的润色是**两个独立的代码路径**：

| 维度 | 流水线内润色 (`run_postprocess`) | 独立润色 (`start_polish`) |
|------|:---:|:---:|
| 入口 | `PostProcessor.process_entries()` | `LLMPolisher` 直接调用 |
| LLMClient 创建 | ✅ | ✅ (已修复) |
| TermDatabaseManager | ✅ 传入 `LLMPolisher` | ❌ 未传入 |
| game_profile | ✅ 从 LLMConfig 读取 | ❌ 未传入（用默认值） |
| target_lang | ✅ 从 LLMConfig 读取 | ❌ 未传入（用默认值） |
| 条目范围解析 | ✅ 支持 translation_scope | ❌ 不支持，必须手动传 entry_ids |
| 结果报告 | ✅ `_last_report` | ✅ (已修复) |

**结论**: 独立润色可用但功能弱于流水线内润色。建议 LLM 优先使用 `run_postprocess phases=["polish"]` 替代 `start_polish`。

---

## 四、工具注册完整性验证

所有 namespace 工具注册与 Agent 绑定正确：

| Namespace | 工具数 | Agent | 状态 |
|-----------|:---:|-------|:---:|
| default | 7 | orchestrator | ✅ |
| editor | 7 | editor | ✅ |
| translator | 9 | translator | ✅ |
| proofreader | 2 | proofreader | ✅ |
| parser | 6 | parser | ✅ |
| paratranz | 9 | paratranz | ✅ |
| writer | 1 | writer | ✅ |
| **总计** | **41** | **7 Agents** | ✅ |

---

## 五、预置测试结果

运行 `tests/test_agent_tool_integration.py` (89 用例)：

| 结果 | 数量 | 说明 |
|------|:---:|------|
| ✅ PASSED | 87 | 全部功能测试通过 |
| ❌ FAILED | 2 | 预置测试设计缺陷（非工具实现问题） |

### 失败详情

**F1**: `test_execute_with_guardrails_input_validation` — 测试假设 `parse_esp` 在 ToolRegistry 中不存在或 permission=read，但 Story 24 后 permission=write。PermissionGuard 在 InputValidationGuard 之前即返回确认请求，测试的断言 `"路径遍历" in result.message` 不匹配 `"需要写入权限确认"`。**这是测试编写时的假设过时，不是工具 bug。**

**F2**: `test_namespace_wildcard_expansion` — 测试尝试 `from agent_registry import _expand_wildcard` 作为模块级函数导入，但该函数已重构为 `AgentRegistry._expand_wildcard` @staticmethod。**这是测试未随重构更新。**

---

## 六、审查结论

| 维度 | 评分 | 说明 |
|------|:---:|------|
| 核心等价性 | ✅ | 五阶段流水线通过 `PostProcessor.process_entries()` 完全复用 GUI 实现 |
| 配置一致性 | ✅ | `PostProcessorConfig.from_llm_config()` + `register_default_checkers()` |
| 安全正确性 | ✅ | stop_event 正确传递，LLM monitor thread 由 PostProcessor 内部创建 |
| 实时反馈 | ❌ | 缺 progress_callback，长时间运行无进度可见 (G1) |
| 断点续传 | ❌ | 缺 checkpoint 支持，中断后无法恢复 (G2) |
| UI 刷新 | ❌ | 完成后不通知 UI 刷新 (G3) |
| 错误提示 | ⚠️ | 缺 API Key 前置检查 (G4) |
| 测试覆盖 | ⚠️ | 缺 `run_postprocess` 专项测试用例 |

### 最终判断

**QA 基本通过 — 后处理核心功能等价，4 项改善建议待处理。**

AI 助手的 `run_postprocess` 工具**已经可以**达到与原后处理工作流一样的效果：
- 五阶段流水线完全由 GUI 同款 `PostProcessor` 执行
- 配置、术语库、LLM 客户端创建方式与 GUI 一致
- 停止功能和并发批处理与 GUI 一致

但用户体验存在差距：无实时进度条、无断点续传、完成后 UI 不自动刷新。这 3 项属于体验改善而非功能缺失，不影响"能否完成同样的翻译后处理工作"这个核心问题的答案。

---

## 七、改善建议（优先级排序）

| 优先级 | 问题 | 修复工作量 | 修复文件 |
|:---:|------|:---:|------|
| P0 | G1: 添加 progress_callback | ~10 行 | `tool_proofreader.py` |
| P1 | G3: 添加 collection_changed 通知 | ~3 行 | `tool_proofreader.py` |
| P1 | G4: 添加 API Key 前置检查 | ~10 行 | `tool_proofreader.py` |
| P2 | G2: 添加 checkpoint 断点续传 | ~15 行 | `tool_proofreader.py` |
| P2 | F1+F2: 修复预置测试 | ~5 行 | `test_agent_tool_integration.py` |
| P3 | `start_polish` 功能补全 (term_manager/game_profile/target_lang) | ~15 行 | `tool_translator.py` |

### 签名

**QA 基本通过** — 核心后处理管线等价，建议改善 4 项体验缺口后复验。
