# Story 10: P1 后处理全套工具 (proofreader namespace)

**所属方案**: `plans/agent-tool-expansion/plan.md`
**技术模块**: backend (smart_assistant/tools)
**状态**: 已确认 (v2)
**创建日期**: 2026-05-11
**更新日期**: 2026-05-11（v2: +LLM后处理require_confirmation(E10) +PostProcessor工厂函数(E9)）

## 前置依赖

### 上游 Story
- Story 01 → `ToolResult`
- Story 02 → `TaskManager`（long_running 任务管理）
- Story 07 → `tool_proofreader.py` 模块骨架（check_quality 已迁移至此）

### 跨 Plan 依赖
- `ai_translator/post_processor/post_processor.py` → `PostProcessor` 类（后处理入口）
- `ai_translator/post_processor/` → 各 checker/refiner/polisher/arbiter 类

### 引用的架构决策
- ADR-012: write 权限（产生 LLM 费用）+ long_running 任务管理

## 验收标准

- [ ] `run_consistency_check` — 执行术语一致性检查，permission: read
- [ ] `run_format_validation` — 执行格式校验，permission: read
- [ ] `run_llm_refinement` — LLM 修复，is_long_running，permission: write
- [ ] `run_llm_polish` — LLM 润色，is_long_running，permission: write
- [ ] `run_llm_arbitration` — LLM 裁决，is_long_running，permission: write
- [ ] `get_quality_report` — 获取最近报告摘要，permission: read
- [ ] 全部注册到 `proofreader` namespace

## 关键接口

```python
# tools/tool_proofreader.py

def _run_check_inline(processor_method_name: str, args, ctx, collection) -> ToolResult:
    """同步执行检查类后处理（consistency_check / format_validation）。"""
    processor = PostProcessor(PostProcessorConfig())
    entry_ids = args.get("entry_ids")
    method = getattr(processor, processor_method_name)
    result = method(collection, entry_ids)
    return ToolResult.ok(data=_summarize_check_result(result))

def _run_llm_async(processor_method_name: str, args, ctx, collection) -> ToolResult:
    """异步执行 LLM 类后处理（refinement / polish / arbitration）。"""
    entry_ids = args.get("entry_ids")
    stop_event = threading.Event()
    task_id = TaskManager().register(stop_event, metadata={"type": processor_method_name})
    thread = threading.Thread(target=_run_postprocess, args=(processor_method_name, collection, entry_ids, stop_event, task_id))
    thread.start()
    return ToolResult.ok(f"{processor_method_name} 已启动", data={"task_id": task_id})
```

## 实现步骤

### 步骤 1: 实现同步检查工具（consistency_check + format_validation）

**涉及文件**: `tools/tool_proofreader.py`（追加）

**实现要点**:
- 封装 `PostProcessor` 的 `check_consistency()` / `validate_format()` 方法
- 同步执行，直接返回结果（检查操作通常耗时短）
- 返回问题数/检查数/详情列表

**边界条件**:
- entry_ids 为空 → 检查全部 collection
- 无 collection → `@require_collection` 拦截

---

### 步骤 2: 实现异步 LLM 工具（refinement + polish + arbitration）

**涉及文件**: 同上

**实现要点**:
- 封装 `PostProcessor` 的 LLM 相关方法
- 后台线程执行 + TaskManager 管理生命周期
- 返回 task_id，通过 `get_task_status` 查询进度

**边界条件**:
- LLM API 失败 → try/except + TaskManager 状态更新为 failed
- 同样通过 Reflexion（现有 FR7.13.4）自动重试 3 次

---

### 步骤 3: 实现 `get_quality_report`

**涉及文件**: 同上

**实现要点**:
- 读取最近的质量检查/后处理报告文件（`data/ai_translator/*/reports/` 最新一份）
- 返回报告摘要（不返回完整报告避免超 context）
- 或直接调用 `PostProcessor` 的内存中的上一次检查结果

**边界条件**:
- 无历史报告 → `ToolResult.ok(message="暂无质量报告")`

---

### 步骤 4: 注册到 proofreader namespace

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/tools/tool_proofreader.py` | 追加 | 6 个后处理工具 + 注册 |

## 风险与注意事项

- **注意**: `run_llm_arbitration` 权限已修正为 write（产生 LLM 费用）
- **注意**: 现有 v1 `check_quality` 工具（已在 `tool_v1.py` / `tool_proofreader.py`）与新后处理工具共存于同一 namespace，LLM 按场景选择
