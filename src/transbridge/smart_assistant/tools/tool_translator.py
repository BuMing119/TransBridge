"""P0 翻译执行控制工具 — 启动/停止/查询翻译任务 (translator namespace)。

Story 06 v2: 移除 pause_task(B5)，stop_task 必传 task_id(E7)，新增 stop_all_tasks(E7)。
Story 18: stop_task 合并 2→1，task_id 改为可选（None/""=停止全部）。
"""
from __future__ import annotations

import os
import threading
import logging

from .base import ToolResult, require_collection
from .task_manager import TaskManager

logger = logging.getLogger(__name__)

from src.transbridge.config.llm import LLMConfig as _LLMConfig


def _load_llm_config() -> _LLMConfig:
    """加载 LLMConfig，失败时返回默认实例。M28: 消除 get/set 配置工具中重复的 try/except 模式。"""
    try:
        return _LLMConfig.load_from_file()
    except Exception:
        return _LLMConfig()


# ── 启动翻译 ──────────────────────────────────────────────────

@require_collection
def _tool_start_translation(args: dict, ctx, collection) -> ToolResult:
    """启动 AI 翻译任务（后台线程 + TaskManager 管理）。"""
    mode = args.get("mode", "translate")
    if mode not in ("translate", "polish", "mixed"):
        return ToolResult.fail(f"无效模式: {mode}，可选: translate, polish, mixed")

    # C3: 前置条件检查
    try:
        from src.transbridge.paratranz.config_manager import LLMConfig
        llm_cfg = LLMConfig.load_from_file()
        if not llm_cfg.api_key:
            return ToolResult.fail("API Key 未配置",
                error_category="config", error_code="API_KEY_MISSING",
                recovery_action="请在 AI 翻译设置中配置 API Key")
        # MA11: 术语数据库来源检查
        term_sources = [s for s in llm_cfg.term_priority if s != "dynamic"]
        has_term_source = bool(term_sources)
        for src in term_sources:
            if src == "paratranz":
                from src.transbridge.paratranz.config_manager import ParatranzConfig
                pc = ParatranzConfig.load_from_file()
                if pc.token:
                    has_term_source = True
                    break
            elif src in ("json", "excel"):
                path = llm_cfg.local_json_path if src == "json" else llm_cfg.local_excel_path
                if path and os.path.exists(path):
                    has_term_source = True
                    break
        if not has_term_source:
            logger.warning("start_translation: 未检测到术语数据库来源，翻译质量可能受影响")
    except Exception:
        return ToolResult.fail("无法读取 LLM 配置，请检查设置",
            error_category="config", error_code="CONFIG_LOAD_FAILED")

    entry_ids = args.get("entry_ids")
    # Story 24-fix: 从 translation_scope 解析条目范围
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
            logger.info("start_translation: 从 translation_scope 解析出 %d 条条目 (stages=%s labels=%s categories=%s)",
                        len(entry_ids), scope.get("stages"), scope.get("labels"), scope.get("categories"))
        else:
            # 无 entry_ids 且无 scope → 默认全部未翻译
            ctx.translation_scope = {"stages": [0], "labels": [], "categories": [], "action": "include"}
            from .base import filter_entries
            filter_state = {"stage": [0]}
            scoped = filter_entries(collection, filter_state)
            entry_ids = [e.key for e in scoped]
            logger.info("start_translation: 未指定条目，默认作用域=全部未翻译(stage=0)，共 %d 条", len(entry_ids))

    stop_event = threading.Event()

    tm = TaskManager()
    task_id = tm.register(stop_event=stop_event, metadata={"mode": mode, "type": "translation"})

    # M4: 浅拷贝闭包捕获的可变引用，防止集合切换时读到错误数据
    _collection = collection
    _entry_ids = list(entry_ids) if entry_ids else None

    def _run():
        try:
            from src.transbridge.ai_translator.translator import AutoTranslator, TranslatorConfig
            from src.transbridge.paratranz.config_manager import LLMConfig

            llm_cfg = LLMConfig.load_from_file()
            cfg = TranslatorConfig(llm_config=llm_cfg, esp_path=ctx.esp_path, overwrite=False)

            # Story 24: 传入 paratranz_client 和 project_id，使 PT 术语来源生效
            paratranz_client = None
            project_id = None
            if hasattr(ctx, 'config') and ctx.config and getattr(ctx.config, 'token', None):
                from src.transbridge.paratranz import ParatranzClient
                paratranz_client = ParatranzClient(ctx.config)
                project_id = getattr(ctx, 'paratranz_project_id', None)
                if not project_id:
                    current = getattr(ctx, 'current_project', {}) or {}
                    project_id = current.get("id")

            translator = AutoTranslator(cfg, paratranz_client, project_id)

            def _progress(current, total, msg, succ, fail, new_terms):
                tm.update_progress(task_id, {
                    "current": current, "total": total, "message": msg,
                    "success_count": succ, "failed_count": fail,
                })
                if stop_event.is_set():
                    raise InterruptedError("任务已被用户停止")

            result = translator.translate(
                collection=_collection,
                target_entry_ids=_entry_ids,
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
            # B2: 通知完成
            tm.notify_completed(task_id, {
                "status": "completed",
                "success_count": result.success_count,
                "failed_count": result.failed_count,
                "skipped_count": result.skipped_count,
            })
            ctx.safe_mutate(lambda: ctx.notify_collection_modified())
        except InterruptedError:
            tm.set_status(task_id, "cancelled")
            tm.notify_failed(task_id, "任务已被用户停止")
        except Exception as exc:
            logger.exception("翻译任务异常: %s", exc)
            tm.set_status(task_id, "failed")
            tm.update_progress(task_id, {"error": str(exc)})
            tm.notify_failed(task_id, str(exc))

    thread = threading.Thread(target=_run, daemon=True)
    handle = tm.get_handle(task_id)
    if handle:
        handle._thread = thread
    thread.start()

    return ToolResult.ok(
        f"翻译任务已启动 (mode={mode})",
        data={"task_id": task_id, "mode": mode},
    )


@require_collection
def _tool_start_polish(args: dict, ctx, collection) -> ToolResult:
    """启动 AI 润色任务（后台线程 + TaskManager 管理）。

    entry_ids 和 scope 至少提供一个：
    - scope="all" / "passed" / "has_issues"（默认 "all"），自动筛选条目
    - 若同时提供 entry_ids，scope 被忽略
    """
    entry_ids = args.get("entry_ids")
    intensity = args.get("intensity", "medium")
    scope = args.get("scope", "all")

    if scope not in ("all", "passed", "has_issues"):
        return ToolResult.fail(f"无效 scope: {scope}，可选: all, passed, has_issues")

    if entry_ids is None:
        # 按 scope 筛选条目
        all_entries = list(collection)
        if scope == "all":
            targets = [e for e in all_entries if e.translation]
        elif scope == "passed":
            # stage 1(检查通过) 或 3+(审核通过及以上) → 已通过检查的条目
            targets = [e for e in all_entries if e.translation and e.stage in (1, 3, 4, 5, 6)]
        else:  # has_issues
            # stage 2(待审核) → 有问题的条目
            targets = [e for e in all_entries if e.translation and e.stage == 2]

        if not targets:
            scope_labels = {"all": "有译文", "passed": "已通过检查", "has_issues": "待审核"}
            return ToolResult.fail(f"没有符合 scope={scope}（{scope_labels.get(scope, scope)}）的条目")
        entry_ids = [e.key for e in targets]
    elif not entry_ids:
        return ToolResult.fail("请指定要润色的 entry_ids")
    else:
        targets = [collection.get(eid) for eid in entry_ids if collection.get(eid)]
        entry_ids = [e.key for e in targets]

    if not targets:
        return ToolResult.fail("所有指定的 entry_id 均无效，未找到匹配条目")

    stop_event = threading.Event()
    tm = TaskManager()
    task_id = tm.register(stop_event=stop_event, metadata={
        "intensity": intensity, "scope": scope, "type": "polish",
    })

    def _run():
        try:
            from src.transbridge.ai_translator.post_processor.polisher import LLMPolisher
            from src.transbridge.infra.llm_client import create_llm_client
            from src.transbridge.paratranz.config_manager import LLMConfig as _LLMCfg

            llm_cfg = _LLMCfg.load_from_file()
            llm_client = create_llm_client(llm_cfg)

            _level_map = {"light": "light", "medium": "moderate", "heavy": "aggressive"}
            polish_level = _level_map.get(intensity, "moderate")

            from src.transbridge.ai_translator.term_database import TermDatabaseManager
            term_mgr = TermDatabaseManager(
                config=llm_cfg,
                esp_path=getattr(ctx, 'esp_path', None) or "",
            )
            term_mgr.load_all()

            polisher = LLMPolisher(
                llm_client=llm_client,
                term_manager=term_mgr,
                game_profile=llm_cfg.game_profile,
                target_lang=llm_cfg.target_lang,
                polish_level=polish_level,
            )

            from src.transbridge.converter.translation_entry import TranslationEntry
            results = {}
            total = len(targets)
            for i, entry in enumerate(targets):
                if stop_event.is_set():
                    raise InterruptedError("任务已被用户停止")
                try:
                    result = polisher.polish(entry)
                    results[entry.key] = result
                    if result.polished_translation and result.confidence > 0:
                        updated = TranslationEntry(
                            id=entry.id, key=entry.key,
                            original=entry.original,
                            translation=result.polished_translation,
                            stage=entry.stage,
                            context=entry.context,
                        )
                        collection.add(updated, overwrite=True)
                except Exception as exc:
                    logger.warning("润色条目 %s 失败: %s", getattr(entry, 'key', '?'), exc)
                tm.update_progress(task_id, {"current": i + 1, "total": total})

            tm.set_status(task_id, "completed")

            import time
            from src.transbridge.smart_assistant.tools import tool_proofreader
            polished_count = sum(1 for r in results.values() if r.polished_translation and r.confidence > 0)
            tool_proofreader._last_report = {
                "phase": "polish",
                "entry_count": len(entry_ids),
                "polished_count": polished_count,
                "polish_level": polish_level,
                "scope": scope,
                "total": total,
                "timestamp": time.time(),
            }

            tm.notify_completed(task_id, {
                "status": "completed",
                "entry_count": len(entry_ids),
            })
            ctx.safe_mutate(lambda: ctx.notify_collection_modified())
        except InterruptedError:
            tm.set_status(task_id, "cancelled")
            tm.notify_failed(task_id, "任务已被用户停止")
        except Exception as exc:
            logger.exception("润色任务异常: %s", exc)
            tm.set_status(task_id, "failed")
            tm.notify_failed(task_id, str(exc))

    thread = threading.Thread(target=_run, daemon=True)
    handle = tm.get_handle(task_id)
    if handle:
        handle._thread = thread
    thread.start()

    return ToolResult.ok(
        f"润色任务已启动 (scope={scope}, intensity={intensity}, {len(entry_ids)}条)",
        data={"task_id": task_id, "intensity": intensity, "scope": scope, "entry_count": len(entry_ids)},
    )


# ── 停止/暂停/恢复 ──────────────────────────────────────────────

def _tool_stop_task(args: dict, ctx) -> ToolResult:
    """Story 18+26: 停止/暂停/恢复任务。task_id 可选，None/""=操作全部活跃任务。
    action: "stop"(默认)/"pause"/"resume"。"""
    task_id = args.get("task_id")
    action = args.get("action", "stop")
    if action not in ("stop", "pause", "resume"):
        return ToolResult.fail(f"无效 action: {action}，可选: stop, pause, resume")

    tm = TaskManager()

    if not task_id:
        active = tm.list_active()
        if not active:
            return ToolResult.ok("当前无运行中的任务", data={"affected_task_ids": []})
        affected, failed = [], []
        for tid in active:
            if action == "pause":
                ok = tm.pause(tid)
            elif action == "resume":
                ok = tm.resume(tid)
            else:
                ok = tm.cancel(tid)
            if ok:
                affected.append(tid)
            else:
                failed.append(tid)
        data = {"affected_task_ids": affected, "action": action}
        if failed:
            data["failed_task_ids"] = failed
            return ToolResult.partial_ok(f"已{_action_label(action)} {len(affected)} 个任务，{len(failed)} 失败",
                                        data=data)
        return ToolResult.ok(f"已{_action_label(action)}全部 {len(affected)} 个任务", data=data)

    if action == "pause":
        ok = tm.pause(task_id)
    elif action == "resume":
        ok = tm.resume(task_id)
    else:
        ok = tm.cancel(task_id)

    if ok:
        label = _action_label(action)
        return ToolResult.ok(f"任务 {task_id} 已{label}",
                            data={"task_id": task_id, "action": action})
    return ToolResult.fail(f"任务不存在或已结束: {task_id} (action={action})")


def _action_label(action: str) -> str:
    """action → 中文标签。"""
    return {"stop": "发送停止信号", "pause": "暂停", "resume": "恢复"}.get(action, action)


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


def _get_post_process_config(llm) -> dict:
    """M27: 从 LLMConfig 提取后处理开关配置。"""
    return {
        "enabled": getattr(llm, 'enable_post_process', True),
        "consistency_check": getattr(llm, 'pp_enable_consistency_check', True),
        "format_validation": getattr(llm, 'pp_enable_format_validation', True),
        "quality_gate": getattr(llm, 'pp_enable_quality_gate', True),
        "refinement": getattr(llm, 'pp_enable_refinement', True),
        "polish": getattr(llm, 'pp_enable_polish', False),
        "arbitration": getattr(llm, 'pp_enable_arbitration', True),
    }


def _get_term_db_info(ctx) -> dict:
    """M27: 读取当前 ESP 对应的术语数据库信息。"""
    term_db_info = {"path": None, "entry_count": 0}
    try:
        from pathlib import Path
        esp_stem = Path(ctx.esp_path).stem if ctx.esp_path else None
        if esp_stem:
            term_db_path = Path("data") / f"{esp_stem}_terms.json"
            if term_db_path.exists():
                import json
                with open(term_db_path, "r", encoding="utf-8") as f:
                    terms = json.load(f)
                term_db_info = {
                    "path": str(term_db_path),
                    "entry_count": len(terms) if isinstance(terms, dict) else 0,
                }
    except Exception as exc:
        logger.warning("术语数据库信息读取失败: %s", exc)
    return term_db_info


def _tool_get_translation_config(args: dict, ctx) -> ToolResult:
    """返回当前 LLM 翻译配置，含后处理、术语、ParaTranz 状态。"""
    llm = _load_llm_config()
    profiles = _get_profiles()

    # C4: 真实的后处理配置
    post_process = _get_post_process_config(llm)

    # C4: 术语数据库信息
    term_db_info = _get_term_db_info(ctx)

    # C5: ParaTranz 配置状态
    pt_config = {"token_configured": False, "api_url": None}
    try:
        from src.transbridge.paratranz.config_manager import ParatranzConfig
        pt_cfg = ParatranzConfig.load_from_file()
        pt_config = {
            "token_configured": bool(pt_cfg.token),
            "api_url": pt_cfg.api_url,
        }
    except Exception as exc:
        logger.warning("ParaTranz 配置读取失败: %s", exc)

    return ToolResult.ok(data={
        "provider": llm.provider,
        "model": llm.model,
        "api_key_configured": bool(llm.api_key),
        "temperature": getattr(llm, 'temperature', None),
        "max_tokens": getattr(llm, 'max_tokens', None),
        "target_lang": llm.target_lang,
        "game_profile": llm.game_profile,
        "term_priority": llm.term_priority,
        "local_json_path": llm.local_json_path or None,
        "local_excel_path": llm.local_excel_path or None,
        "post_process": post_process,
        "term_database": term_db_info,
        "paratranz": pt_config,
        "available_profiles": list(profiles.keys()) if profiles else None,
        "base_url_host": llm.base_url.split("://")[-1].split("/")[0] if llm.base_url else None,
    })


def _tool_set_translation_config(args: dict, ctx) -> ToolResult:
    """更新 LLM 翻译配置。H7: profile 预设方案切换替代 base_url 自由输入。"""
    import configparser
    from src.transbridge.config.paths import get_config_file_path

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

    llm = _load_llm_config()

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


def _tool_set_term_config(args: dict, ctx) -> ToolResult:
    """设置术语数据库配置"""
    term_sources = args.get("term_sources")
    json_path = args.get("json_path")
    excel_path = args.get("excel_path")

    llm = _load_llm_config()
    changed = []

    if term_sources is not None:
        valid = ["dynamic", "paratranz", "json", "excel"]
        invalid = [s for s in term_sources if s not in valid]
        if invalid:
            return ToolResult.fail(f"无效的术语来源: {invalid}。可选: {valid}")
        llm.term_priority = list(term_sources)
        changed.append(f"term_sources={term_sources}")

    if json_path is not None:
        llm.local_json_path = json_path
        changed.append(f"json_path={json_path}")

    if excel_path is not None:
        llm.local_excel_path = excel_path
        changed.append(f"excel_path={excel_path}")

    if not changed:
        return ToolResult.ok("未修改任何术语配置", data={"unchanged": True})

    llm.save_to_file()
    return ToolResult.ok(f"已更新术语配置: {', '.join(changed)}", data={"changed": changed})


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
    """预览当前作用域下匹配的条目数。m25: 复用 filter_entries 统一筛选逻辑。"""
    from .base import filter_entries
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return ToolResult.ok("当前无翻译集合", data={"matched": 0, "total": 0})
    scope = ctx.translation_scope
    # m25: 将 translation_scope 转为 filter_state 格式后调用 filter_entries
    filter_state = {
        "stage": scope.get("stages"),
        "category": scope.get("categories"),
        "labels": scope.get("labels"),
    }
    entry_labels = getattr(ctx, 'entry_labels', None)
    results = filter_entries(collection, filter_state, entry_labels=entry_labels)
    matched = len(results)
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
        "entry_ids": {"type": "list", "required": False, "description": "要润色的条目ID列表（与 scope 至少提供一个）"},
        "scope": {"type": "str", "required": False, "description": "润色范围: all(全部有译文)/passed(已通过检查,stage=1/3/4/5/6)/has_issues(待审核,stage=2)，默认 all"},
        "intensity": {"type": "str", "required": False, "description": "润色强度: light/medium/heavy，默认 medium"},
    },
    "stop_task": {
        "task_id": {"type": "str", "required": False, "description": "要操作的任务ID（不传则操作所有运行中任务）"},
        "action": {"type": "str", "required": False, "description": "操作类型: stop(停止，默认)/pause(暂停)/resume(恢复)"},
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
    # Story 24: 术语配置
    "set_term_config": {
        "term_sources": {"type": "list", "required": False,
            "description": "术语来源优先级列表。可选: dynamic/paratranz/json/excel"},
        "json_path": {"type": "str", "required": False, "description": "本地 JSON 术语库文件路径"},
        "excel_path": {"type": "str", "required": False, "description": "本地 Excel 术语库文件路径"},
    },
}


def _register_translator_tools():
    from src.transbridge.smart_assistant.tool_registry import ToolRegistry
    ToolRegistry.register_tools("translator", [
        {"name": "start_translation", "display_name": "启动翻译", "description": "①启动AI翻译后台任务。②参数: mode=translate(默认)/polish/mixed(mixed同translate), entry_ids=key列表(可选,不传则用set_scope作用域,默认stage=0未翻译)。③返回: {task_id, mode}。④规则: 前置需API key已配,先调get_translation_config确认配置;允许并行多任务。",
         "execute": _tool_start_translation, "permission": "write", "is_long_running": True,
         "require_confirmation": True, "parameters": _PARAM_SCHEMAS.get("start_translation", {})},
        {"name": "start_polish", "display_name": "启动润色", "description": "①启动AI润色后台任务。②参数: entry_ids或scope至少传一(同时传entry_ids优先), scope=all(全部有译文)/passed(stage=1/3/4/5/6)/has_issues(2), intensity=light/medium/heavy(默认medium)。③返回: {task_id, intensity, scope, entry_count}。④规则: 前置需API key已配,先调get_translation_config确认。",
         "execute": _tool_start_polish, "permission": "write", "is_long_running": True,
         "require_confirmation": True, "parameters": _PARAM_SCHEMAS.get("start_polish", {})},
        {"name": "stop_task", "display_name": "停止/暂停/恢复", "description": "①控制后台任务(停止/暂停/恢复)。②参数: task_id(可选,不传则操作所有活跃任务), action=stop(默认,不可恢复)/pause(等当前批次完成)/resume。③单个: {task_id, action},全部: {affected_task_ids, action}。④规则: stop不可逆;活跃=仅running+paused;需用户确认。",
         "execute": _tool_stop_task, "permission": "write", "require_confirmation": True,
         "parameters": _PARAM_SCHEMAS.get("stop_task", {})},
        {"name": "get_task_status", "display_name": "查询任务状态", "description": "①查询任务进度。②参数: task_id(可选)。③单个返回{task_id, status, progress{current,total,message}, created_at, metadata},全部返回{active_count, total_count, tasks[{task_id, status, metadata}]}(不含progress/created_at)。④status: running/paused/completed/cancelled/failed。",
         "execute": _tool_get_task_status, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("get_task_status", {})},
        # Story 09: 翻译配置
        {"name": "get_translation_config", "display_name": "翻译配置", "description": "①返回LLM翻译配置完整快照(只读)。②无参数。③返回: provider/model/api_key_configured/temperature/max_tokens/target_lang/game_profile/term_priority/本地路径/post_process各项开关/available_profiles。④规则: game_profile无预定义值;调set_translation_config/set_term_config前先调此工具获取available_profiles和当前状态。",
         "execute": _tool_get_translation_config, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("get_translation_config", {})},
        {"name": "set_translation_config", "display_name": "设置翻译配置", "description": "①更新LLM翻译参数(只传需修改项)。②参数: profile(必须从available_profiles选), model(provider相关,不可枚举), temperature(0.0-2.0), max_tokens, target_lang(如chinese), game_profile。③返回: {changed_fields, profile}。④规则: 先调get_translation_config获取available_profiles列表;profile不可自由输入。",
         "execute": _tool_set_translation_config, "permission": "write",
         "parameters": _PARAM_SCHEMAS.get("set_translation_config", {})},
        {"name": "set_scope", "display_name": "设置作用域", "description": "①设置翻译作用域(start_translation不传entry_ids时的默认范围)。②参数: stages[阶段号], labels[标签名], categories[分类名], action=include/exclude/only。③返回作用域快照。④规则: 维度间AND维度内OR;labels/categories需先用list_labels/get_statistics确认存在;include/only当前行为相同。",
         "execute": _tool_set_scope, "permission": "write",
         "parameters": _PARAM_SCHEMAS.get("set_scope", {})},
        {"name": "get_scope_preview", "display_name": "作用域预览", "description": "①预览当前作用域匹配条目统计。②无参数。③返回: {matched, total, scope{stages,labels,categories,action}}。④规则: 仅返回计数非条目列表;默认作用域=全部未翻译(stage=0);先调set_scope再调本工具确认范围。",
         "execute": _tool_get_scope_preview, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("get_scope_preview", {})},        {"name": "set_term_config", "display_name": "术语配置",
         "description": "①设置术语来源优先级与本地路径。②参数: term_sources优先级列表(dynamic/paratranz/json/excel,顺序决定优先级), json_path, excel_path。dynamic=AI翻译中自动提取,始终可用。③返回: {changed}。④规则: 先调get_translation_config查看当前配置;空列表禁用所有来源;无效来源名被拒绝。",
         "execute": _tool_set_term_config, "permission": "write",
         "parameters": _PARAM_SCHEMAS.get("set_term_config", {})},

    ])


_register_translator_tools()
