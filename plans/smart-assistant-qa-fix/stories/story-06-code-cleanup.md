# Story 06: 代码清理与架构修复

**所属方案**: `plans/smart-assistant-qa-fix/plan.md`
**技术模块**: 跨模块（smart_assistant/、ui/tools/smart_assistant/、infra/）
**状态**: 已确认
**创建日期**: 2026-05-12
**覆盖问题**: C1（ADR-008违规）、M1（RetryHandler死代码）、M2（v1/namespace重复）、M3（collection检查重复）、M6（ReAct无重试）、m1-m21（21项Minor）

## 前置依赖

### 上游 Story
- **Story-01**（安全护栏）: 已完成 → ReAct 路径已接入 `execute_with_guardrails`，本 Story 的 M6 在此基础上接入 RetryHandler
- **Story-05**（线程资源）: 已完成 → MemoryStore 异步写入，本 Story 的 m13/m14 联动

### 引用的架构决策
- **ADR-008 §2**（Import 规范）: backend 不依赖 UI，修复 C1
- **ADR-009 §3**（Reflexion 自纠错）: RetryHandler 注入模式，修复 M1/M6
- **ADR-012 §1.3**（ExecutionEngine 注入）: 工具注册与权限分级

## 验收标准

- [ ] `context_builder.py` 不再 `from src.transbridge.ui.context import AppContext`，改用依赖注入
- [ ] `RetryHandler` 在 `ExecutionEngine.__init__` 中实例化，或删除死代码
- [ ] `collection-is-None` 检查 6 处统一改用 `@require_collection` 装饰器
- [ ] v1 同步工具标记为 `deprecated`，namespace 异步工具为推荐替代
- [ ] ReAct 模式的 `_handle_tool_result` 接入 `RetryHandler`
- [ ] 21 项 Minor 问题全部修复

## 实现步骤

### 步骤 1: C1 — 修复 ADR-008 违规

**涉及文件**: `src/transbridge/smart_assistant/context_builder.py`（修改）、`src/transbridge/ui/tools/smart_assistant/chat_widget.py`（修改）

**实现要点**:
- 移除 `context_builder.py:4` 中的 `from src.transbridge.ui.context import AppContext`
- `ContextBuilder.__init__` 接受 `app_context` 参数（依赖注入）
- `chat_widget.py` 中创建 `ContextBuilder(app_context=self._ctx)`

**边界条件**:
- AppContext 未传入 → `ContextBuilder` 的 `build_system_prompt` 抛出明确错误

---

### 步骤 2: M1+M6 — RetryHandler 实例化 + ReAct 接入

**涉及文件**: `src/transbridge/smart_assistant/execution_engine.py`（修改）、`src/transbridge/ui/tools/smart_assistant/chat_widget.py`（修改）

**实现要点**:
- M1: `execution_engine.py:39` — `self._retry_handler = RetryHandler()` 而非 `None`
- M6: `chat_widget.py:502` — `_handle_tool_result` 中检测 `FAIL` 结果 → 调用 `RetryHandler.should_retry()` → 若可重试则重新执行

**边界条件**:
- RetryHandler 导入失败 → 降级为 `None`，跳过重试（向后兼容）
- 重试耗尽 → 保持当前 `[FAIL]` 消息

**伪代码**:
```python
def _handle_tool_result(self, step, result):
    tool_name = step.get("tool", "?")
    if not result.get("success") and self._engine and self._engine._retry_handler:
        err_msg = result.get("message", "")
        if self._engine._retry_handler.should_retry(err_msg):
            adjusted = self._engine._retry_handler.analyze_and_adjust(step, err_msg, 0)
            if adjusted:
                result = self._engine._run_single(adjusted)
    # ... existing display logic
```

---

### 步骤 3: M3 — 统一 @require_collection 装饰器

**涉及文件**: `tool_translator.py`, `tool_v1.py`, `tool_writer.py`, `tool_paratranz.py`, `tool_proofreader.py`（修改 5 文件）

**实现要点**:
- 已在 `tool_editor.py` 中定义的 `@require_collection` 装饰器移到 `tools/base.py`
- 5 个文件中的手动 `if self._ctx.collection is None: return ToolResult.fail(...)` 替换为 `@require_collection`
- 装饰器统一错误消息格式

**伪代码**:
```python
# tools/base.py
def require_collection(func):
    @functools.wraps(func)
    def wrapper(args, ctx, *a, **kw):
        if ctx.collection is None:
            return ToolResult.fail("当前没有加载翻译集合", error_category="input", error_code="COLLECTION_NOT_LOADED")
        return func(args, ctx, *a, **kw)
    return wrapper

# 使用
@require_collection
def _tool_start_translation(args, ctx) -> ToolResult:
    ...
```

---

### 步骤 4: M2 — v1 工具标记 deprecated

**涉及文件**: `src/transbridge/smart_assistant/tools/tool_v1.py`（修改）

**实现要点**:
- 每个 v1 同步工具的 docstring 首行添加 `@deprecated: 请使用 namespace 对应工具`
- 在 `ToolRegistry` 注册时 v1 工具添加 `deprecated=True` 标记
- 系统提示词中提示 LLM 优先使用 namespace 工具

**v1 → namespace 映射**:
| v1 工具 | namespace 替代 |
|---------|---------------|
| `translate_entries` | `start_translation` |
| `check_quality` | `check_consistency` / `validate_format` |
| `write_back` | `write_to_esp` / `write_to_eet` / `write_to_xt` |
| `export_json` | `export_collection_json` |

---

### 步骤 5: m1-m21 — 21 项 Minor 修复

**涉及文件**: 多个（见下表）

| # | 文件 | 修复内容 |
|---|------|---------|
| m1 | `tools/base.py:210`, `__init__.py:50` | 移除 `_filter_entries` 的 `__all__` 导出，或去掉前导下划线 |
| m2 | `tool_registry.py:118-124` | 删除 deprecated `get_collection_summary` 注册 |
| m3 | `chat_widget.py:498` | 删除 `_on_skill` 方法 |
| m4 | `infra/__init__.py` | 确认 `markdown_renderer` 被导出且有引用 |
| m5 | `tool_parser.py:128-143` | 统一为 5 元组注册格式 |
| m6 | `tool_parser.py:138` | display_name 使用实际名称，不粗暴截断 |
| m7 | `tool_registry.py:25-70` | 添加简短 docstring |
| m8 | `tool_parser.py:140` | 参数 schema 添加 description |
| m9 | `execution_engine.py:93-105, 235-240` | 忙等轮询改为 `threading.Condition.wait(timeout)` |
| m10 | `task_manager.py:85-90` | progress 修改移到锁内 |
| m11 | `execution_engine.py:267` | 复用 ThreadPoolExecutor |
| m12 | `observability/collector.py:22-23` | 会话切换时重置 `_session_tokens` |
| m13 | `chat_widget.py:39, 531-536` | `_clear_conversation` 释放 `_uploaded_docs` |
| m14 | `infra/vector_store.py:56-62` | 软删除时从 FAISS 索引移除向量 |
| m15 | `observability/collector.py:51` | 对话清除时重置 `_active.tools_called` |
| m16 | `chat_widget.py:264-265` | 删除空 `_on_llm_chunk`（S08-4 已实现流式） |
| m17 | `infra/markdown_renderer.py:348-383` | 复用 QWidget 组件降低创建开销 |
| m18 | `tools/task_manager.py:29-41` | 添加 `reset()` 类方法供会话切换 |
| m19 | `chat_widget.py:467-474` | `_on_retry` 3s 超时添加超时处理 |
| m20 | `tools/tool_editor.py:367` | `clear_all_filters` 权限 `write` → `read` |
| m21 | `tools/tool_default.py:131` | `list_local_projects` 仅返回项目名 |

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/context_builder.py` | 修改 | C1: 移除 UI 依赖 |
| `smart_assistant/execution_engine.py` | 修改 | M1: RetryHandler 实例化 + m9/m11 |
| `ui/tools/smart_assistant/chat_widget.py` | 修改 | C1 适配 + M6 + m3/m13/m16/m19 |
| `smart_assistant/tools/base.py` | 修改 | M3: @require_collection + m1 |
| `smart_assistant/tools/tool_translator.py` | 修改 | M3: @require_collection |
| `smart_assistant/tools/tool_v1.py` | 修改 | M2: deprecated + M3 |
| `smart_assistant/tools/tool_writer.py` | 修改 | M3: @require_collection |
| `smart_assistant/tools/tool_paratranz.py` | 修改 | M3: @require_collection |
| `smart_assistant/tools/tool_proofreader.py` | 修改 | M3: @require_collection |
| `smart_assistant/tools/tool_parser.py` | 修改 | m5/m6/m8 |
| `smart_assistant/tools/tool_registry.py` | 修改 | m2/m7 |
| `smart_assistant/tools/task_manager.py` | 修改 | m10/m18 |
| `smart_assistant/tools/tool_editor.py` | 修改 | m20 |
| `smart_assistant/tools/tool_default.py` | 修改 | m21 |
| `smart_assistant/observability/collector.py` | 修改 | m12/m15 |
| `infra/__init__.py` | 修改 | m4 |
| `infra/markdown_renderer.py` | 修改 | m17 |
| `infra/vector_store.py` | 修改 | m14 |

## 风险与注意事项

- **风险**: `@require_collection` 装饰器改变函数签名（接收 `(args, ctx)` → 返回 `ToolResult`），不是所有工具都遵循此签名 → **缓解**: 逐个文件替换前确认函数签名，不匹配的手动保留
- **风险**: 删除 `_on_skill`（m3）可能影响 Skill 系统 → **缓解**: 先 grep 确认无调用者
- **注意**: Minor 修复虽小但散，建议逐文件批量修复以减少切换成本；每个文件修复后运行 lint 检查
- **注意**: M1 RetryHandler 实例化后，需验证 `_run_single` 中的重试循环不再因 `self._retry_handler is None` 短路
