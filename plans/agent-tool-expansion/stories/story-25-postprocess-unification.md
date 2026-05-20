# Story 25: 后处理工具统一 — run_postprocess 替代 5 个独立工具

**Epic**: agent-tool-expansion
**对应需求**: FR9.4（后处理独立操作工具）
**优先级**: P0（修复全部运行时崩溃）
**状态**: 已方案
**创建日期**: 2026-05-18
**涉及文件**: `tools/tool_proofreader.py`（主修改）、`agents/agent_registry.py`（工具列表更新）、`plans/agent-tool-expansion/plan.md`（Story追加）

## 问题背景

QA审计发现 `tool_proofreader.py` 中 5 个执行工具全部在运行时崩溃：

1. 所有工具调用不存在的 `processor.process(collection)` — 单个处理器类只有 `check(entry)`/`refine()`/`polish()`/`arbitrate()` 方法，没有统一的 `process` 方法
2. LLM 工具（refinement/polish/arbitration）的 `__init__` 需要 `llm_client: LLMClient` 必填参数，工厂函数未传入
3. 缺少 QualityGate 检查阶段
4. 工具独立运行无法复现 GUI 的五阶段串行管线

## 验收标准

- [ ] 新增 `run_postprocess` 工具，直接包装 `PostProcessor.process_entries()`，与 GUI 后处理行为完全一致
- [ ] `phases` 参数支持选择运行阶段：`["consistency","format","quality_gate","refinement","polish","arbitration"]`，默认全部
- [ ] `entry_ids` 可选参数，指定条目 key 列表，不传则从 scope 解析或处理全部
- [ ] 工具在后台线程运行（`is_long_running`），通过 TaskManager 管理，支持 `stop_task` 取消
- [ ] permission: `write`，`require_confirmation: true`（产生 LLM API 费用）
- [ ] 废弃 5 个旧函数（`_tool_run_consistency_check` / `_tool_run_format_validation` / `_tool_run_llm_refinement` / `_tool_run_llm_polish` / `_tool_run_llm_arbitration`），函数保留但取消注册
- [ ] 废弃 `_run_postprocess_phase` 工厂函数（不再需要）
- [ ] `get_quality_report` 保留，适配 `_last_report` 为新格式
- [ ] proofreader namespace 工具数从 6 减为 2
- [ ] 更新 Agent 注册中 proofreader 的工具列表

## 实现步骤

### 步骤 1: 新增 `_tool_run_postprocess` + 废弃旧工具

**文件**: `tools/tool_proofreader.py`

```python
def _tool_run_postprocess(args: dict, ctx) -> ToolResult:
    """运行完整的后处理流水线（与 GUI PostProcessor 一致）。"""
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return ToolResult.fail("当前没有加载翻译集合")

    phases = args.get("phases", ["consistency", "format", "quality_gate",
                                  "refinement", "polish", "arbitration"])
    entry_ids = args.get("entry_ids")

    # 从 scope 解析条目（复用 start_translation 的模式）
    if not entry_ids:
        scope = getattr(ctx, 'translation_scope', None)
        if scope and any(scope.get(k) for k in ('stages', 'labels', 'categories')):
            from .base import filter_entries
            filter_state = {
                "stage": scope.get("stages"),
                "category": scope.get("categories"),
                "labels": scope.get("labels"),
            }
            entry_labels = getattr(ctx, 'entry_labels', None)
            scoped = filter_entries(collection, filter_state, entry_labels=entry_labels)
            entry_ids = [e.key for e in scoped]

    entries = [collection.get(eid) for eid in entry_ids] if entry_ids else list(collection)
    entries = [e for e in entries if e is not None]

    # 创建 LLMClient（复用 LLMConfig）
    from src.transbridge.paratranz.config_manager import LLMConfig
    from src.transbridge.infra.llm_client import create_llm_client
    llm_cfg = LLMConfig.load_from_file()
    llm_client = create_llm_client(llm_cfg)

    # 加载术语管理器
    from src.transbridge.ai_translator.term_database import TermDatabaseManager
    term_mgr = TermDatabaseManager(
        config=llm_cfg,
        esp_path=getattr(ctx, 'esp_path', None) or "",
    )
    term_mgr.load_all()

    # 构建 PostProcessorConfig（从 LLMConfig 加载，与 GUI 一致）
    from src.transbridge.ai_translator.post_processor.post_processor import PostProcessor, PostProcessorConfig
    config = PostProcessorConfig.from_llm_config(llm_cfg)

    # 按 phases 参数覆盖开关
    phase_names = {"consistency", "format", "quality_gate", "refinement", "polish", "arbitration"}
    for p in phase_names:
        setattr(config, f"enable_{p}" if p != "quality_gate" else "enable_quality_gate",
                p in phases if p in phases else getattr(config, f"enable_{p}" if p != "quality_gate" else "enable_quality_gate", True))

    # 启动后台线程
    stop_event = threading.Event()
    tm = TaskManager()
    task_id = tm.register(stop_event=stop_event, metadata={"phases": phases, "type": "postprocess"})

    def _run():
        global _last_report
        try:
            processor = PostProcessor(
                llm_client=llm_client,
                config=config,
                term_manager=term_mgr,
                esp_path=getattr(ctx, 'esp_path', None),
            )
            result = processor.process_entries(
                entries, stop_event=stop_event,
                esp_path=getattr(ctx, 'esp_path', None),
            )

            _last_report = {
                "phase": "postprocess",
                "total_checked": result.total_checked,
                "issue_count": result.issue_count,
                "auto_fixed": result.auto_fixed,
                "needs_review": list(result.needs_review),
                "issues": [{"entry_id": iss.entry_id, "issue_type": iss.issue_type,
                            "severity": iss.severity, "message": iss.message}
                           for iss in result.issues[:50]],
                "timestamp": time.time(),
            }

            tm.update_progress(task_id, {"status": "completed", "summary": str(result)})
            tm.set_status(task_id, "completed")
            tm.notify_completed(task_id, {"status": "completed", "total_checked": result.total_checked,
                                          "issue_count": result.issue_count, "auto_fixed": result.auto_fixed})
        except Exception as exc:
            logger.exception("后处理异常: %s", exc)
            tm.set_status(task_id, "failed")
            tm.update_progress(task_id, {"error": str(exc)})
            tm.notify_failed(task_id, str(exc))

    thread = threading.Thread(target=_run, daemon=True)
    handle = tm.get_handle(task_id)
    if handle:
        handle._thread = thread
    thread.start()

    return ToolResult.ok(f"后处理已启动 (phases={phases})", data={"task_id": task_id, "phases": phases, "entry_count": len(entries)})
```

### 步骤 2: 清理旧函数和注册

**文件**: `tools/tool_proofreader.py`

- 删除 `_run_postprocess_phase` 工厂函数
- 5 个旧工具函数保留但添加 `warnings.warn()` deprecated 提示
- 注册列表仅保留 `run_postprocess` 和 `get_quality_report`

### 步骤 3: `get_quality_report` 适配

保持现有实现不变（读取模块级 `_last_report`），`_last_report` 格式已在上方 `_run()` 中适配。

### 步骤 4: 更新 Agent 注册

**文件**: `agents/agent_registry.py`

proofreader Agent 的工具列表更新为 `["run_postprocess", "get_quality_report"]`

### 步骤 5: 更新 plan.md

**文件**: `plans/agent-tool-expansion/plan.md`

追加 Story 25 条目，更新 proofreader 工具数：6 → 2

## 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 统一入口 vs 独立工具 | 统一 `run_postprocess` | GUI 的 PostProcessor 是五阶段串行管线，独立工具无法复现 |
| LLMClient 创建 | 工具内从 LLMConfig 创建 | 与 `start_translation` 一致，复用配置 |
| TermDatabaseManager | 工具内创建 | 一致性检查需要术语缓存 |
| 旧工具处理 | 保留函数 + 取消注册 | 避免外部引用断裂，LLM 不再看到 |
| phases 参数 | 字符串列表，默认全部 | LLM 可根据需求选择阶段组合 |

## 架构依赖

- `PostProcessor` (`post_processor/post_processor.py`) — GUI 同款五阶段管线
- `LLMConfig.load_from_file()` — 配置共享
- `create_llm_client()` — LLM 客户端工厂
- `TermDatabaseManager` — 术语加载（与 `start_translation` 一致）
- `TaskManager` — 后台任务管理
