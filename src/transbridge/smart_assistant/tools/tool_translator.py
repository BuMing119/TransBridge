"""P0 翻译执行控制工具 — 启动/停止/查询翻译任务 (translator namespace)。

Story 06 v2: 移除 pause_task(B5)，stop_task 必传 task_id(E7)，新增 stop_all_tasks(E7)。
"""
from __future__ import annotations

import os
import threading
import logging

from .base import ToolResult
from .task_manager import TaskManager

logger = logging.getLogger(__name__)


# ── 启动翻译 ──────────────────────────────────────────────────

def _tool_start_translation(args: dict, ctx) -> ToolResult:
    """启动 AI 翻译任务（后台线程 + TaskManager 管理）。"""
    mode = args.get("mode", "translate")
    if mode not in ("translate", "polish", "mixed"):
        return ToolResult.fail(f"无效模式: {mode}，可选: translate, polish, mixed")

    collection = ctx.collection
    if not collection or len(collection) == 0:
        return ToolResult.fail("当前没有加载翻译集合")

    entry_ids = args.get("entry_ids")
    # C3: 空选择时默认全部未翻译条目，执行前由 C2 确认机制弹窗
    if not entry_ids and not getattr(ctx, 'translation_scope', None):
        ctx.translation_scope = {"stages": [0], "labels": [], "categories": [], "action": "include"}
        logger.info("start_translation: 未指定条目，默认作用域=全部未翻译(stage=0)")

    stop_event = threading.Event()

    tm = TaskManager()
    task_id = tm.register(stop_event=stop_event, metadata={"mode": mode, "type": "translation"})

    def _run():
        try:
            from src.transbridge.ai_translator.translator import AutoTranslator, TranslatorConfig
            from src.transbridge.paratranz.config_manager import LLMConfig

            llm_cfg = LLMConfig.load_from_file()
            cfg = TranslatorConfig(llm_config=llm_cfg, esp_path=ctx.esp_path, overwrite=False)
            translator = AutoTranslator(cfg)

            def _progress(current, total, msg, succ, fail, new_terms):
                tm.update_progress(task_id, {
                    "current": current, "total": total, "message": msg,
                    "success_count": succ, "failed_count": fail,
                })
                if stop_event.is_set():
                    raise InterruptedError("任务已被用户停止")

            result = translator.translate(
                collection=collection,
                target_entry_ids=entry_ids,
                progress_callback=_progress,
                stop_event=stop_event,
            )
            tm.update_progress(task_id, {
                "status": "completed",
                "success_count": result.success_count,
                "failed_count": result.failed_count,
                "skipped_count": result.skipped_count,
            })
            tm.set_status(task_id, "completed")
        except InterruptedError:
            tm.set_status(task_id, "cancelled")
        except Exception as exc:
            logger.exception("翻译任务异常: %s", exc)
            tm.set_status(task_id, "failed")
            tm.update_progress(task_id, {"error": str(exc)})

    thread = threading.Thread(target=_run, daemon=True)
    handle = tm.get_handle(task_id)
    if handle:
        handle._thread = thread
    thread.start()

    return ToolResult.ok(
        f"翻译任务已启动 (mode={mode})",
        data={"task_id": task_id, "mode": mode},
    )


def _tool_start_polish(args: dict, ctx) -> ToolResult:
    """启动 AI 润色任务（后台线程 + TaskManager 管理）。"""
    entry_ids = args.get("entry_ids", [])
    intensity = args.get("intensity", "medium")

    collection = ctx.collection
    if not collection or len(collection) == 0:
        return ToolResult.fail("当前没有加载翻译集合")

    if not entry_ids:
        return ToolResult.fail("请指定要润色的 entry_ids")

    stop_event = threading.Event()
    tm = TaskManager()
    task_id = tm.register(stop_event=stop_event, metadata={"intensity": intensity, "type": "polish"})

    def _run():
        try:
            from src.transbridge.ai_translator.post_processor.llm_polisher import LLMPolisher
            polisher = LLMPolisher(intensity=intensity)

            targets = [collection.get(eid) for eid in entry_ids if collection.get(eid)]
            total = len(targets)
            for i, entry in enumerate(targets):
                if stop_event.is_set():
                    raise InterruptedError("任务已被用户停止")
                try:
                    polisher.polish(entry)
                except Exception:
                    pass
                tm.update_progress(task_id, {"current": i + 1, "total": total})

            tm.set_status(task_id, "completed")
        except InterruptedError:
            tm.set_status(task_id, "cancelled")
        except Exception as exc:
            logger.exception("润色任务异常: %s", exc)
            tm.set_status(task_id, "failed")

    thread = threading.Thread(target=_run, daemon=True)
    handle = tm.get_handle(task_id)
    if handle:
        handle._thread = thread
    thread.start()

    return ToolResult.ok(
        f"润色任务已启动 (intensity={intensity}, {len(entry_ids)}条)",
        data={"task_id": task_id, "intensity": intensity, "entry_count": len(entry_ids)},
    )


# ── 停止翻译 ──────────────────────────────────────────────────

def _tool_stop_task(args: dict, ctx) -> ToolResult:
    """停止指定翻译任务。E7: task_id 为必传参数（不再隐式停止所有）。"""
    task_id = args.get("task_id", "")
    if not task_id:
        return ToolResult.fail("请指定要停止的 task_id（使用 stop_all_tasks 停止所有任务）")

    tm = TaskManager()
    success = tm.cancel(task_id)
    if success:
        return ToolResult.ok(f"任务 {task_id} 已发送停止信号")
    return ToolResult.fail(f"任务不存在或已完成: {task_id}")


def _tool_stop_all_tasks(args: dict, ctx) -> ToolResult:
    """停止所有运行中的翻译/润色任务。E7: 显式独立工具。"""
    tm = TaskManager()
    active = tm.list_active()
    stopped = 0
    for tid in active:
        if tm.cancel(tid):
            stopped += 1
    return ToolResult.ok(f"已停止 {stopped} 个任务", data={"stopped_count": stopped})


# ── 查询状态 ──────────────────────────────────────────────────

def _tool_get_task_status(args: dict, ctx) -> ToolResult:
    """查询翻译任务状态。不传 task_id 时返回所有活跃任务摘要。"""
    task_id = args.get("task_id")
    tm = TaskManager()

    if task_id:
        status = tm.get_status(task_id)
        if "error" in status:
            return ToolResult.fail(status["error"])
        return ToolResult.ok(f"任务 {task_id}: {status['status']}", data=status)

    active = tm.list_active()
    all_tasks = tm.list_all()
    summaries = []
    for tid in all_tasks:
        s = tm.get_status(tid)
        summaries.append({"task_id": tid, "status": s.get("status", "unknown"),
                          "metadata": s.get("metadata", {})})

    return ToolResult.ok(
        f"活跃任务: {len(active)} / 总任务: {len(all_tasks)}",
        data={"active_count": len(active), "total_count": len(all_tasks), "tasks": summaries},
    )


# ── 翻译配置 (Story 09) ───────────────────────────────────────

def _get_profiles() -> dict[str, str]:
    """H7: 从 INI 文件读取 [llm_profiles] 预设端点方案。"""
    import configparser
    from src.transbridge.config.paths import get_config_file_path
    cp = configparser.ConfigParser()
    path = get_config_file_path()
    profiles = {}
    if os.path.exists(path):
        cp.read(path, encoding='utf-8')
        if cp.has_section('llm_profiles'):
            for k, v in cp.items('llm_profiles'):
                profiles[k] = v
    return profiles


def _tool_get_translation_config(args: dict, ctx) -> ToolResult:
    """返回当前 LLM 翻译配置。"""
    from src.transbridge.config.llm import LLMConfig
    try:
        llm = LLMConfig.load_from_file()
    except Exception:
        llm = LLMConfig()
    profiles = _get_profiles()
    return ToolResult.ok(data={
        "provider": llm.provider,
        "model": llm.model,
        "temperature": getattr(llm, 'temperature', None),
        "max_tokens": getattr(llm, 'max_tokens', None),
        "target_lang": llm.target_lang,
        "game_profile": llm.game_profile,
        "term_priority": llm.term_priority,
        "post_process_stages": getattr(llm, 'post_process_stages', None),  # m6
        "term_database": getattr(llm, 'term_database', None),  # m6
        "available_profiles": list(profiles.keys()) if profiles else None,
        "base_url_host": llm.base_url.split("://")[-1].split("/")[0] if llm.base_url else None,
    })


def _tool_set_translation_config(args: dict, ctx) -> ToolResult:
    """更新 LLM 翻译配置。H7: profile 预设方案切换替代 base_url 自由输入。"""
    import configparser
    from src.transbridge.config.paths import get_config_file_path
    from src.transbridge.config.llm import LLMConfig

    profile = args.get("profile")
    if profile:
        profiles = _get_profiles()
        if profile not in profiles:
            return ToolResult.fail(
                f"未知 profile: {profile}。可用方案: {list(profiles.keys())}"
            )
        # 将预设方案的 URL 写入 LLMConfig
        args = dict(args)
        args["base_url"] = profiles[profile]

    try:
        llm = LLMConfig.load_from_file()
    except Exception:
        llm = LLMConfig()

    changed = []
    # C1: base_url 仅通过 profile 预设方案间接设置，不允许直接修改
    for field_name in ["model", "temperature", "max_tokens", "target_lang", "game_profile"]:
        if field_name in args:
            old_val = getattr(llm, field_name, None)
            setattr(llm, field_name, args[field_name])
            changed.append(field_name)

    # m5: 检测并记录未知参数
    known_fields = {"profile", "model", "temperature", "max_tokens", "target_lang", "game_profile"}
    unknown = [k for k in args if k not in known_fields]
    if unknown:
        logger.warning("set_translation_config: 已忽略未知参数: %s", unknown)

    if not changed:
        return ToolResult.ok("未做任何修改")

    llm.save_to_file()
    return ToolResult.ok(
        f"已更新配置: {', '.join(changed)}" + (f" (profile={profile})" if profile else ""),
        data={"changed_fields": changed, "profile": profile},
    )


def _tool_set_scope(args: dict, ctx) -> ToolResult:
    """设置翻译作用域。E8: 操作 ctx.translation_scope 正式属性（带类型校验）。"""
    scope = {
        "stages": args.get("stages", []),
        "labels": args.get("labels", []),
        "categories": args.get("categories", []),
        "action": args.get("action", "include"),
    }
    try:
        ctx.translation_scope = scope
    except (TypeError, ValueError) as exc:
        return ToolResult.fail(str(exc))
    return ToolResult.ok(f"翻译作用域已更新: action={scope['action']}", data=ctx.translation_scope)


def _tool_get_scope_preview(args: dict, ctx) -> ToolResult:
    """预览当前作用域下匹配的条目数。"""
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return ToolResult.ok("当前无翻译集合", data={"matched": 0, "total": 0})
    scope = ctx.translation_scope
    matched = 0
    for e in collection:
        stages_ok = not scope["stages"] or e.stage in scope["stages"]
        cat_ok = not scope["categories"] or (
            e.context and any(e.context.startswith(c + ":") or e.context.startswith(c + "_") for c in scope["categories"])
        )
        if stages_ok and cat_ok:
            matched += 1

    total = len(collection)
    return ToolResult.ok(
        f"作用域匹配: {matched}/{total} 条",
        data={"matched": matched, "total": total, "scope": scope},
    )


# ── 注册 ──────────────────────────────────────────────────────

_PARAM_SCHEMAS = {
    "start_translation": {
        "mode": {"type": "str", "required": False, "description": "翻译模式: translate/polish/mixed，默认 translate"},
        "entry_ids": {"type": "list", "required": False, "description": "目标条目ID列表，默认全部未翻译"},
    },
    "start_polish": {
        "entry_ids": {"type": "list", "required": True, "description": "要润色的条目ID列表"},
        "intensity": {"type": "str", "required": False, "description": "润色强度: light/medium/heavy，默认 medium"},
    },
    "stop_task": {
        "task_id": {"type": "str", "required": True, "description": "要停止的任务ID"},
    },
    "get_task_status": {
        "task_id": {"type": "str", "required": False, "description": "任务ID（不传则返回所有任务摘要）"},
    },
    # Story 09: 翻译配置
    "get_translation_config": {},
    "set_translation_config": {
        "profile": {"type": "str", "required": False, "description": "H7: INI [llm_profiles] 中预设的端点方案名（如 openai/anthropic），替代直接输入 URL"},
        "model": {"type": "str", "required": False, "description": "模型名"},
        "temperature": {"type": "float", "required": False, "description": "生成温度"},
        "max_tokens": {"type": "int", "required": False, "description": "最大输出 token 数"},
        "target_lang": {"type": "str", "required": False, "description": "目标语言代码"},
        "game_profile": {"type": "str", "required": False, "description": "游戏 profile"},
    },
    "set_scope": {
        "stages": {"type": "list", "required": False, "description": "目标 stage 列表"},
        "labels": {"type": "list", "required": False, "description": "目标标签列表"},
        "categories": {"type": "list", "required": False, "description": "目标分类列表"},
        "action": {"type": "str", "required": False, "description": "作用域动作: include/exclude/only，默认 include"},
    },
    "get_scope_preview": {},
}


def _register_translator_tools():
    from src.transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec

    tools = [
        ("start_translation", "启动翻译", "启动AI翻译任务（后台运行），返回task_id用于查询进度和停止", _tool_start_translation, "write", True),
        ("start_polish", "启动润色", "启动AI润色任务（后台运行），返回task_id", _tool_start_polish, "write", True),
        ("stop_task", "停止任务", "停止指定翻译/润色任务（E7：task_id必传）", _tool_stop_task, "write", False),
        ("stop_all_tasks", "停止所有任务", "停止所有运行中的翻译/润色任务（E7：显式独立工具）", _tool_stop_all_tasks, "write", False),
        ("get_task_status", "查询任务状态", "查询指定任务或所有任务的进度状态", _tool_get_task_status, "read", False),
        # Story 09: 翻译配置
        ("get_translation_config", "翻译配置", "返回当前LLM翻译配置（provider/model/profile等）", _tool_get_translation_config, "read", False),
        ("set_translation_config", "设置翻译配置", "更新LLM翻译参数。H7: profile切换预设端点方案（非自由输入URL）", _tool_set_translation_config, "write", False),
        ("set_scope", "设置作用域", "设置翻译作用域（stages/labels/categories/action）", _tool_set_scope, "write", False),
        ("get_scope_preview", "作用域预览", "预览当前作用域下匹配的条目统计", _tool_get_scope_preview, "read", False),
    ]

    for name, display_name, description, execute, permission, is_long_running in tools:
        ToolRegistry.register(ToolSpec(
            name=name, display_name=display_name, description=description,
            parameters=_PARAM_SCHEMAS.get(name, {}),
            execute=execute, permission=permission,
            is_long_running=is_long_running,
            require_confirmation=(name in ("start_translation", "start_polish", "stop_task")),
        ), namespace="translator")


_register_translator_tools()
