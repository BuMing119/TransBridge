"""P0 翻译执行控制工具 — 启动/停止/查询翻译任务 (translator namespace)。

Story 06 v2: 移除 pause_task(B5)，stop_task 必传 task_id(E7)，新增 stop_all_tasks(E7)。
Story 18: stop_task 合并 2→1，task_id 改为可选（None/""=停止全部）。
Story 03A: 重构为 TranslationController 类。
"""

from __future__ import annotations

import logging
import os
import threading

from .base import ToolResult, require_collection, require_runtime_context
from .task_manager import TaskManager
from .task_runtime_bridge import task_metadata

logger = logging.getLogger(__name__)


def _capture_run_entry_states(ctx, collection) -> dict[object, tuple[str, int]]:
    capture = getattr(ctx, "capture_entry_states", None)
    if callable(capture):
        return capture(collection)
    return {entry.identity: (entry.translation, entry.stage) for entry in collection}


def _rollback_run_entry_states(ctx, collection, states: dict[object, tuple[str, int]]) -> str | None:
    try:
        rollback = getattr(ctx, "rollback_entry_states", None)
        if callable(rollback):
            rollback(states, collection)
        else:
            from .types import ExecutionContext

            ExecutionContext(app_context=ctx).rollback_entry_states(states, collection)
    except Exception as exc:  # noqa: BLE001 - the original task failure must remain visible too
        logger.exception("助手任务回滚失败: %s", exc)
        return str(exc)
    return None


def _publish_run_entry_states(ctx, *, rollback_on_failure: bool = True) -> None:
    publish = getattr(ctx, "publish_collection_modified", None)
    if callable(publish):
        publish(rollback_on_failure=rollback_on_failure)
        return
    from .types import ExecutionContext

    ExecutionContext(app_context=ctx).publish_collection_modified(rollback_on_failure=rollback_on_failure)


def _paratranz_term_project_id(ctx) -> int | None:
    """Return only a binding that is safe to use with the current account/endpoint."""

    from transbridge.application.projects import (
        ParaTranzTargetResolver,
        ParaTranzTargetStatus,
    )

    resolve = getattr(ctx, "resolve_paratranz_target", None)
    if callable(resolve):
        target = resolve()
    else:
        config = getattr(ctx, "config", None)
        endpoint = getattr(config, "base_url", None)
        if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
            endpoint = "https://paratranz.cn"
        user = getattr(ctx, "current_user", None)
        account_id = user.get("id") if isinstance(user, dict) else getattr(config, "user_id", None)
        if not isinstance(account_id, int) or isinstance(account_id, bool) or account_id <= 0:
            account_id = None
        target = ParaTranzTargetResolver().resolve(
            binding=getattr(ctx, "paratranz_binding", None),
            binding_revision=getattr(ctx, "project_revision", None),
            endpoint=endpoint,
            account_user_id=account_id,
        )
    if target.status not in {ParaTranzTargetStatus.UNVERIFIED, ParaTranzTargetStatus.AVAILABLE}:
        return None
    return target.project_id


# ═══════════════════════════════════════════════════════════════════
#  TranslationController — 翻译任务控制
# ═══════════════════════════════════════════════════════════════════


class TranslationController:
    """翻译任务控制器 — 管理翻译/润色任务的启动、停止、查询和配置。

    self._ctx (AppContext) 仅用于初始化路径。
    运行时数据访问通过方法的 ctx (ExecutionContext) 参数，
    该参数由 ExecutionEngine 在每次工具调用时注入。
    """

    def __init__(self, app_context=None, task_manager=None):
        self._ctx = app_context
        self._task_mgr = task_manager

    # ── 启动翻译 ──────────────────────────────────────────────────

    def start_translation(self, args: dict, ctx, collection) -> ToolResult:
        """启动 AI 翻译任务（后台线程 + TaskManager 管理）。"""
        mode = args.get("mode", "translate")
        if mode not in ("translate", "polish", "mixed"):
            return ToolResult.fail(f"无效模式: {mode}，可选: translate, polish, mixed")

        # C3: 前置条件检查
        try:
            from transbridge.paratranz.config_manager import LLMConfig

            llm_cfg = LLMConfig.load_from_file()
            if not llm_cfg.api_key:
                return ToolResult.fail(
                    "API Key 未配置",
                    error_category="config",
                    error_code="API_KEY_MISSING",
                    recovery_action="请在 AI 翻译设置中配置 API Key",
                )
            # MA11: 术语数据库来源检查
            term_sources = [s for s in llm_cfg.term_priority if s != "dynamic"]
            has_term_source = bool(term_sources)
            for src in term_sources:
                if src == "paratranz":
                    from transbridge.paratranz.config_manager import ParatranzConfig

                    pc = ParatranzConfig.load_from_file()
                    if pc.token:
                        has_term_source = True
                        break
                elif src in ("json", "csv", "excel"):
                    path = {
                        "json": llm_cfg.local_json_path,
                        "csv": getattr(llm_cfg, "local_csv_path", ""),
                        "excel": llm_cfg.local_excel_path,
                    }[src]
                    if path and os.path.exists(path):
                        has_term_source = True
                        break
            if not has_term_source:
                logger.warning("start_translation: 未检测到术语数据库来源，翻译质量可能受影响")
        except Exception:
            return ToolResult.fail(
                "无法读取 LLM 配置，请检查设置", error_category="config", error_code="CONFIG_LOAD_FAILED"
            )

        entry_ids = args.get("entry_ids")
        # M3: 复用 resolve_scope_to_entry_ids 消除与 tool_proofreader 的重复代码
        if not entry_ids:
            from .base import resolve_scope_to_entry_ids

            scoped_ids = resolve_scope_to_entry_ids(ctx, collection)
            if scoped_ids:
                entry_ids = scoped_ids
                logger.info("start_translation: 从 translation_scope 解析出 %d 条条目", len(entry_ids))
            else:
                # 无 entry_ids 且无 scope → 默认全部未翻译
                ctx.translation_scope = {"stages": [0], "labels": [], "categories": [], "action": "include"}
                from .base import filter_entries

                scoped = filter_entries(collection, {"stage": [0]})
                entry_ids = [e.key for e in scoped]
                logger.info("start_translation: 未指定条目，默认作用域=全部未翻译(stage=0)，共 %d 条", len(entry_ids))

        # M4: 浅拷贝闭包捕获的可变引用，防止集合切换时读到错误数据
        _collection = collection
        _entry_ids = list(entry_ids) if entry_ids else None
        from .terminology_run import freeze_terminology_binding

        try:
            terminology = freeze_terminology_binding(ctx)
        except ValueError as exc:
            return ToolResult.fail(str(exc), error_category="terminology", error_code="SNAPSHOT_UNAVAILABLE")

        stop_event = threading.Event()
        tm = TaskManager()
        task_id = tm.register(stop_event=stop_event, metadata=task_metadata(ctx, {"mode": mode, "type": "translation"}))
        handle = tm.get_handle(task_id)
        run_entry_states = _capture_run_entry_states(ctx, _collection)

        def _run():
            paratranz_client = None
            try:
                from transbridge.ai_translator.translator import AutoTranslator, TranslatorConfig
                from transbridge.paratranz.config_manager import LLMConfig

                llm_cfg = LLMConfig.load_from_file()
                cfg = TranslatorConfig(llm_config=llm_cfg, esp_path=ctx.esp_path, overwrite=False)

                # Story 24: 传入 paratranz_client 和 project_id，使 PT 术语来源生效
                project_id = _paratranz_term_project_id(ctx)
                if hasattr(ctx, "config") and ctx.config and getattr(ctx.config, "token", None):
                    if project_id is not None:
                        from transbridge.paratranz import ParatranzClient

                        paratranz_client = ParatranzClient(ctx.config)

                translator = AutoTranslator(
                    cfg,
                    paratranz_client,
                    project_id,
                    **terminology.translator_kwargs(),
                )

                def _progress(current, total, msg, succ, fail, new_terms):
                    tm.update_progress(
                        task_id,
                        {
                            "current": current,
                            "total": total,
                            "message": msg,
                            "success_count": succ,
                            "failed_count": fail,
                        },
                    )
                    if stop_event.is_set():
                        raise InterruptedError("任务已被用户停止")

                result = translator.translate(
                    collection=_collection,
                    target_entry_ids=_entry_ids,
                    progress_callback=_progress,
                    stop_event=stop_event,
                    pause_event=handle.pause_event,
                )
                if stop_event.is_set():
                    raise InterruptedError("任务已被用户停止")
                decision = handle.execution.commit(
                    task_id, lambda: _publish_run_entry_states(ctx, rollback_on_failure=False)
                )
                if not decision.accepted:
                    raise InterruptedError("任务已被用户停止；结果未提交")
                tm.update_progress(
                    task_id,
                    {
                        "status": "completed",
                        "success_count": result.success_count,
                        "failed_count": result.failed_count,
                        "skipped_count": result.skipped_count,
                    },
                )
                tm.set_status(task_id, "completed")
                # B2: 通知完成
                tm.notify_completed(
                    task_id,
                    {
                        "status": "completed",
                        "success_count": result.success_count,
                        "failed_count": result.failed_count,
                        "skipped_count": result.skipped_count,
                    },
                )
            except InterruptedError:
                rollback_error = _rollback_run_entry_states(ctx, _collection, run_entry_states)
                if rollback_error is None:
                    tm.set_status(task_id, "cancelled")
                    tm.notify_failed(task_id, "任务已被用户停止；本次修改已回滚")
                else:
                    message = f"任务已停止，但回滚失败：{rollback_error}"
                    tm.set_status(task_id, "failed")
                    tm.update_progress(task_id, {"error": message})
                    tm.notify_failed(task_id, message)
            except Exception as exc:
                logger.exception("翻译任务异常: %s", exc)
                rollback_error = _rollback_run_entry_states(ctx, _collection, run_entry_states)
                message = str(exc) if rollback_error is None else f"{exc}；回滚失败：{rollback_error}"
                tm.set_status(task_id, "failed")
                tm.update_progress(task_id, {"error": message})
                tm.notify_failed(task_id, message)
            finally:
                if paratranz_client is not None:
                    try:
                        paratranz_client.close()
                    except Exception:  # noqa: BLE001 - cleanup must not hide the task result
                        logger.exception("关闭 AI 翻译 ParaTranz 客户端失败")

        tm.start_thread(task_id, _run)  # M2: 复用 TaskManager.start_thread
        # m6: 无全局线程池，每次翻译/润色/后处理创建独立 Thread。并发上限由 TaskManager 调用方控制。

        return ToolResult.ok(
            f"翻译任务已启动 (mode={mode})",
            data={"task_id": task_id, "mode": mode},
        )

    def start_polish(self, args: dict, ctx, collection) -> ToolResult:
        """启动 AI 润色任务（后台线程 + TaskManager 管理）。

        entry_ids 和 scope 至少提供一个：
        - scope="all" / "passed" / "has_issues"（默认 "all"），自动筛选条目
        - 若同时提供 entry_ids，scope 被忽略
        """
        entry_ids = args.get("entry_ids")
        intensity = args.get("intensity", "medium")
        scope = args.get("scope", "all")
        strategy = args.get("strategy", "proofread")
        if strategy == "combined":  # compatibility with saved assistant calls
            strategy = "proofread"

        if scope not in ("all", "passed", "has_issues"):
            return ToolResult.fail(f"无效 scope: {scope}，可选: all, passed, has_issues")
        if intensity not in ("light", "medium", "heavy"):
            return ToolResult.fail(f"无效 intensity: {intensity}，可选: light, medium, heavy")
        if strategy not in ("proofread", "strict"):
            return ToolResult.fail(f"无效 strategy: {strategy}，可选: proofread, strict")

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

        from .terminology_run import freeze_terminology_binding

        try:
            terminology = freeze_terminology_binding(ctx)
        except ValueError as exc:
            return ToolResult.fail(str(exc), error_category="terminology", error_code="SNAPSHOT_UNAVAILABLE")

        stop_event = threading.Event()
        tm = TaskManager()
        task_id = tm.register(
            stop_event=stop_event,
            metadata=task_metadata(
                ctx,
                {
                    "intensity": intensity,
                    "scope": scope,
                    "strategy": strategy,
                    "type": "polish",
                },
            ),
        )
        handle = tm.get_handle(task_id)
        run_entry_states = _capture_run_entry_states(ctx, collection)

        def _run():
            llm_runtime = None
            try:
                from transbridge.paratranz.config_manager import LLMConfig as _LLMCfg

                from ._polish_execution import execute_polish
                from ._polish_llm_runtime import create_polish_llm_runtime

                llm_cfg = _LLMCfg.load_from_file()
                llm_runtime = create_polish_llm_runtime(
                    llm_cfg,
                    esp_path=getattr(ctx, "esp_path", None) or "",
                    stop_event=stop_event,
                    pause_event=handle.pause_event,
                )

                from transbridge.ai_translator.term_database import TermDatabaseManager

                term_mgr = TermDatabaseManager(
                    config=llm_cfg,
                    esp_path=getattr(ctx, "esp_path", None) or "",
                    **terminology.term_database_kwargs(),
                )
                term_mgr.load_all()

                total = len(targets)
                summary = execute_polish(
                    strategy=strategy,
                    intensity=intensity,
                    llm_config=llm_cfg,
                    llm_client=llm_runtime.client,
                    term_manager=term_mgr,
                    targets=targets,
                    collection=collection,
                    stop_event=stop_event,
                    progress_callback=lambda _stage, current, count, message: tm.update_progress(
                        task_id,
                        {"current": current, "total": count, "message": message},
                    ),
                    log_callback=lambda line: llm_runtime.log_store.write_line("workflow", line),
                )

                if stop_event.is_set():
                    raise InterruptedError("任务已被用户停止")

                decision = handle.execution.commit(
                    task_id, lambda: _publish_run_entry_states(ctx, rollback_on_failure=False)
                )
                if not decision.accepted:
                    raise InterruptedError("任务已被用户停止；结果未提交")

                tm.set_status(task_id, "completed")

                import time

                from transbridge.smart_assistant.tools.tool_proofreader import set_last_report

                polish_level = {"light": "light", "medium": "moderate", "heavy": "aggressive"}[intensity]
                set_last_report({
                    "phase": "polish",
                    "strategy": strategy,
                    "entry_count": len(entry_ids),
                    "polished_count": summary.polished_count,
                    "failed_count": summary.failed_count,
                    "polish_level": polish_level,
                    "scope": scope,
                    "total": total,
                    "timestamp": time.time(),
                })

                tm.notify_completed(
                    task_id,
                    {
                        "status": "completed",
                        "entry_count": len(entry_ids),
                        "polished_count": summary.polished_count,
                        "failed_count": summary.failed_count,
                        "strategy": strategy,
                    },
                )
            except InterruptedError:
                rollback_error = _rollback_run_entry_states(ctx, collection, run_entry_states)
                if rollback_error is None:
                    tm.set_status(task_id, "cancelled")
                    tm.notify_failed(task_id, "任务已被用户停止；本次修改已回滚")
                else:
                    message = f"任务已停止，但回滚失败：{rollback_error}"
                    tm.set_status(task_id, "failed")
                    tm.update_progress(task_id, {"error": message})
                    tm.notify_failed(task_id, message)
            except Exception as exc:
                logger.exception("润色任务异常: %s", exc)
                rollback_error = _rollback_run_entry_states(ctx, collection, run_entry_states)
                message = str(exc) if rollback_error is None else f"{exc}；回滚失败：{rollback_error}"
                tm.set_status(task_id, "failed")
                tm.update_progress(task_id, {"error": message})
                tm.notify_failed(task_id, message)
            finally:
                if llm_runtime is not None:
                    llm_runtime.close()

        tm.start_thread(task_id, _run)  # M2: 复用 TaskManager.start_thread

        return ToolResult.ok(
            f"润色任务已启动 (strategy={strategy}, scope={scope}, intensity={intensity}, {len(entry_ids)}条)",
            data={
                "task_id": task_id,
                "strategy": strategy,
                "intensity": intensity,
                "scope": scope,
                "entry_count": len(entry_ids),
            },
        )

    # ── 停止/暂停/恢复 ──────────────────────────────────────────────

    def stop_task(self, args: dict, ctx) -> ToolResult:
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
                return ToolResult.partial_ok(
                    f"已{self._action_label(action)} {len(affected)} 个任务，{len(failed)} 失败", data=data
                )
            return ToolResult.ok(f"已{self._action_label(action)}全部 {len(affected)} 个任务", data=data)

        if action == "pause":
            ok = tm.pause(task_id)
        elif action == "resume":
            ok = tm.resume(task_id)
        else:
            ok = tm.cancel(task_id)

        if ok:
            label = self._action_label(action)
            return ToolResult.ok(f"任务 {task_id} 已{label}", data={"task_id": task_id, "action": action})
        return ToolResult.fail(f"任务不存在或已结束: {task_id} (action={action})")

    def _action_label(self, action: str) -> str:
        """action → 中文标签。"""
        return {"stop": "发送停止信号", "pause": "暂停", "resume": "恢复"}.get(action, action)

    # ── 查询状态 ──────────────────────────────────────────────────

    def get_task_status(self, args: dict, ctx) -> ToolResult:
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
            summaries.append({"task_id": tid, "status": s.get("status", "unknown"), "metadata": s.get("metadata", {})})

        return ToolResult.ok(
            f"活跃任务: {len(active)} / 总任务: {len(all_tasks)}",
            data={"active_count": len(active), "total_count": len(all_tasks), "tasks": summaries},
        )

    # ── 翻译配置 (Story 09) ───────────────────────────────────────

    def _get_post_process_config(self, llm) -> dict:
        """M27: 从 LLMConfig 提取后处理开关配置。"""
        return {
            "enabled": getattr(llm, "enable_post_process", True),
            "consistency_check": getattr(llm, "pp_enable_consistency_check", True),
            "format_validation": getattr(llm, "pp_enable_format_validation", True),
            "quality_gate": getattr(llm, "pp_enable_quality_gate", True),
            "refinement": getattr(llm, "pp_enable_refinement", True),
            "polish": getattr(llm, "pp_enable_polish", False),
            "arbitration": getattr(llm, "pp_enable_arbitration", True),
        }

    def _get_term_db_info(self, ctx) -> dict:
        """M27: 读取当前 ESP 对应的术语数据库信息。"""
        term_db_info = {"path": None, "entry_count": 0}
        try:
            from pathlib import Path

            esp_stem = Path(ctx.esp_path).stem if ctx.esp_path else None
            if esp_stem:
                term_db_path = Path("data") / f"{esp_stem}_terms.json"
                if term_db_path.exists():
                    import json

                    with open(term_db_path, encoding="utf-8") as f:
                        terms = json.load(f)
                    term_db_info = {
                        "path": str(term_db_path),
                        "entry_count": len(terms) if isinstance(terms, dict) else 0,
                    }
        except Exception as exc:
            logger.warning("术语数据库信息读取失败: %s", exc)
        return term_db_info

    def get_translation_config(self, args: dict, ctx) -> ToolResult:
        """返回当前 LLM 翻译配置，含后处理、术语、ParaTranz 状态。"""
        from ._common import load_llm_config

        llm = load_llm_config()

        # C4: 真实的后处理配置
        post_process = self._get_post_process_config(llm)

        # C4: 术语数据库信息
        term_db_info = self._get_term_db_info(ctx)

        # C5: ParaTranz 配置状态
        pt_config = {"token_configured": False, "api_url": None}
        try:
            from transbridge.paratranz.config_manager import ParatranzConfig

            pt_cfg = ParatranzConfig.load_from_file()
            pt_config = {
                "token_configured": bool(pt_cfg.token),
                "api_url": pt_cfg.base_url,
            }
        except Exception as exc:
            logger.warning("ParaTranz 配置读取失败: %s", exc)

        return ToolResult.ok(
            data={
                "provider": llm.provider,
                "model": llm.model,
                "api_key_configured": bool(llm.api_key),
                "temperature": getattr(llm, "temperature", None),
                "max_tokens": llm.max_output_tokens,
                "target_lang": llm.target_lang,
                "game_profile": llm.game_profile,
                "term_priority": llm.term_priority,
                "local_json_path": llm.local_json_path or None,
                "local_csv_path": getattr(llm, "local_csv_path", "") or None,
                "local_excel_path": llm.local_excel_path or None,
                "post_process": post_process,
                "term_database": term_db_info,
                "paratranz": pt_config,
                "config_revision": llm.config_revision,
                "base_url_host": llm.base_url.split("://")[-1].split("/")[0] if llm.base_url else None,
            }
        )

    def set_translation_config(self, args: dict, ctx) -> ToolResult:
        """Update one configuration snapshot; endpoint identity is atomic."""

        from transbridge.config.language_profiles import LanguageProfileError, load_language_profile

        from ._common import load_llm_config

        if "target_lang" in args:
            try:
                load_language_profile(args["target_lang"])
            except LanguageProfileError as exc:
                return ToolResult.fail(str(exc))

        llm = load_llm_config()

        changed = []
        endpoint_fields = {"provider", "base_url", "model"}
        touched_endpoint = endpoint_fields.intersection(args)
        if touched_endpoint and touched_endpoint != endpoint_fields:
            return ToolResult.fail("provider/base_url/model 必须在同一次调用中完整提供")
        for field_name in ["provider", "base_url", "model", "temperature", "target_lang", "game_profile"]:
            if field_name in args:
                setattr(llm, field_name, args[field_name])
                changed.append(field_name)
        if "max_tokens" in args:
            llm.max_output_tokens = args["max_tokens"]
            changed.append("max_tokens")

        # m5: 检测并记录未知参数
        known_fields = {
            "provider",
            "base_url",
            "model",
            "temperature",
            "max_tokens",
            "target_lang",
            "game_profile",
        }
        unknown = [k for k in args if k not in known_fields]
        if unknown:
            logger.warning("set_translation_config: 已忽略未知参数: %s", unknown)

        if not changed:
            return ToolResult.ok("未做任何修改")

        llm.save_to_file()
        return ToolResult.ok(
            f"已更新配置: {', '.join(changed)}",
            data={"changed_fields": changed, "config_revision": llm.config_revision},
        )

    def set_term_config(self, args: dict, ctx) -> ToolResult:
        """设置术语数据库配置"""
        term_sources = args.get("term_sources")
        json_path = args.get("json_path")
        csv_path = args.get("csv_path")
        excel_path = args.get("excel_path")

        from ._common import load_llm_config

        llm = load_llm_config()
        changed = []

        if term_sources is not None:
            valid = ["dynamic", "paratranz", "json", "csv", "excel"]
            invalid = [s for s in term_sources if s not in valid]
            if invalid:
                return ToolResult.fail(f"无效的术语来源: {invalid}。可选: {valid}")
            llm.term_priority = list(term_sources)
            changed.append(f"term_sources={term_sources}")

        if json_path is not None:
            llm.local_json_path = json_path
            changed.append(f"json_path={json_path}")

        if csv_path is not None:
            llm.local_csv_path = csv_path
            changed.append(f"csv_path={csv_path}")

        if excel_path is not None:
            llm.local_excel_path = excel_path
            changed.append(f"excel_path={excel_path}")

        if not changed:
            return ToolResult.ok("未修改任何术语配置", data={"unchanged": True})

        llm.save_to_file()
        return ToolResult.ok(f"已更新术语配置: {', '.join(changed)}", data={"changed": changed})

    def set_scope(self, args: dict, ctx) -> ToolResult:
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

    def get_scope_preview(self, args: dict, ctx) -> ToolResult:
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
        entry_labels = getattr(ctx, "entry_labels", None)
        results = filter_entries(collection, filter_state, entry_labels=entry_labels)
        matched = len(results)
        total = len(collection)
        return ToolResult.ok(
            f"作用域匹配: {matched}/{total} 条",
            data={"matched": matched, "total": total, "scope": scope},
        )


# ═══════════════════════════════════════════════════════════════════
#  无状态 controller + 模块级 wrapper（保持向后兼容）
# ═══════════════════════════════════════════════════════════════════

_translator_ctrl = TranslationController()


# ── 模块级 wrapper 函数（@require_collection 装饰在此处，因装饰器不支持实例方法） ──


@require_runtime_context
@require_collection
def _tool_start_translation(args: dict, ctx, collection) -> ToolResult:
    return _translator_ctrl.start_translation(args, ctx, collection)


@require_runtime_context
@require_collection
def _tool_start_polish(args: dict, ctx, collection) -> ToolResult:
    return _translator_ctrl.start_polish(args, ctx, collection)


@require_runtime_context
def _tool_stop_task(args: dict, ctx) -> ToolResult:
    return _translator_ctrl.stop_task(args, ctx)


@require_runtime_context
def _tool_get_task_status(args: dict, ctx) -> ToolResult:
    return _translator_ctrl.get_task_status(args, ctx)


@require_runtime_context
def _tool_get_translation_config(args: dict, ctx) -> ToolResult:
    return _translator_ctrl.get_translation_config(args, ctx)


@require_runtime_context
def _tool_set_translation_config(args: dict, ctx) -> ToolResult:
    return _translator_ctrl.set_translation_config(args, ctx)


@require_runtime_context
def _tool_set_term_config(args: dict, ctx) -> ToolResult:
    return _translator_ctrl.set_term_config(args, ctx)


@require_runtime_context
def _tool_set_scope(args: dict, ctx) -> ToolResult:
    return _translator_ctrl.set_scope(args, ctx)


@require_runtime_context
def _tool_get_scope_preview(args: dict, ctx) -> ToolResult:
    return _translator_ctrl.get_scope_preview(args, ctx)


# ═══════════════════════════════════════════════════════════════════
#  参数 Schema
# ═══════════════════════════════════════════════════════════════════

_PARAM_SCHEMAS = {
    "start_translation": {
        "mode": {
            "type": "str",
            "required": False,
            "description": "Translation mode: translate/polish/mixed; default translate",
        },
        "entry_ids": {
            "type": "list",
            "required": False,
            "description": "Target entry IDs; defaults to all untranslated entries",
        },
    },
    "start_polish": {
        "entry_ids": {"type": "list", "required": False, "description": "Entry IDs to polish; provide this or scope"},
        "scope": {
            "type": "str",
            "required": False,
            "description": (
                "Polish scope: all (all translated), passed (stage 1/3/4/5/6), or has_issues (stage 2); default all"
            ),
        },
        "intensity": {
            "type": "str",
            "required": False,
            "description": "Polish intensity: light/medium/heavy; default medium",
        },
        "strategy": {
            "type": "str",
            "required": False,
            "description": "Proofreading strategy: proofread (default) or strict (multi-stage compatible)",
        },
    },
    "stop_task": {
        "task_id": {"type": "str", "required": False, "description": "Task ID; omitted targets all active tasks"},
        "action": {"type": "str", "required": False, "description": "Action: stop (default), pause, or resume"},
    },
    "get_task_status": {
        "task_id": {
            "type": "str",
            "required": False,
            "description": "Task ID; omitted returns summaries for all tasks",
        },
    },
    # Story 09: 翻译配置
    "get_translation_config": {},
    "set_translation_config": {
        "provider": {
            "type": "str",
            "required": False,
            "description": "Must be provided together with base_url and model",
        },
        "base_url": {
            "type": "str",
            "required": False,
            "description": "Must be provided together with provider and model",
        },
        "model": {"type": "str", "required": False, "description": "Model name"},
        "temperature": {"type": "float", "required": False, "description": "Generation temperature"},
        "max_tokens": {"type": "int", "required": False, "description": "Maximum output tokens"},
        "target_lang": {"type": "str", "required": False, "description": "Target language code"},
        "game_profile": {"type": "str", "required": False, "description": "Game profile"},
    },
    "set_scope": {
        "stages": {"type": "list", "required": False, "description": "Target stages"},
        "labels": {"type": "list", "required": False, "description": "Target labels"},
        "categories": {"type": "list", "required": False, "description": "Target categories"},
        "action": {
            "type": "str",
            "required": False,
            "description": "Scope action: include/exclude/only; default include",
        },
    },
    "get_scope_preview": {},
    # Story 24: 术语配置
    "set_term_config": {
        "term_sources": {
            "type": "list",
            "required": False,
            "description": "Terminology source priority list: dynamic/paratranz/json/csv/excel",
        },
        "json_path": {"type": "str", "required": False, "description": "Local JSON glossary path"},
        "csv_path": {"type": "str", "required": False, "description": "Local CSV glossary path"},
        "excel_path": {"type": "str", "required": False, "description": "Local Excel glossary path"},
    },
}


# ═══════════════════════════════════════════════════════════════════
#  工具注册
# ═══════════════════════════════════════════════════════════════════


def _register_translator_tools():
    from ..tool_registry import ToolRegistry

    ToolRegistry.register_tools(
        "translator",
        [
            {
                "name": "start_translation",
                "display_name": "启动翻译",
                "description": (
                    "①Start an AI translation background task. ②Arguments: mode=translate (default)/polish/mixed, "
                    "optional entry_ids; without entry_ids use set_scope, defaulting to stage 0. "
                    "③Returns {task_id, mode}. "
                    "④Rules: configure the API key and inspect get_translation_config first; "
                    "parallel tasks are allowed."
                ),
                "execute": _tool_start_translation,
                "permission": "write",
                "is_long_running": True,
                "require_confirmation": True,
                "parameters": _PARAM_SCHEMAS.get("start_translation", {}),
            },
            {
                "name": "start_polish",
                "display_name": "启动校对",
                "description": (
                    "①Start an AI proofreading background task. ②Provide entry_ids or scope; entry_ids wins. "
                    "scope=all/passed(stage 1/3/4/5/6)/has_issues(stage 2), intensity=light/medium/heavy, "
                    "strategy=proofread/strict. "
                    "③Returns {task_id, strategy, intensity, scope, entry_count}. "
                    "④Rule: configure "
                    "the API key and inspect get_translation_config first."
                ),
                "execute": _tool_start_polish,
                "permission": "write",
                "is_long_running": True,
                "require_confirmation": True,
                "parameters": _PARAM_SCHEMAS.get("start_polish", {}),
            },
            {
                "name": "stop_task",
                "display_name": "停止/暂停/恢复",
                "description": (
                    "①Stop, pause, or resume background tasks. ②Arguments: optional task_id "
                    "(omitted targets all active "
                    "tasks), action=stop (default and irreversible)/pause/resume. "
                    "③Returns {task_id, action} for one or "
                    "{affected_task_ids, action} for all. ④Rules: active means running or paused; user confirmation is "
                    "required."
                ),
                "execute": _tool_stop_task,
                "permission": "write",
                "require_confirmation": True,
                "parameters": _PARAM_SCHEMAS.get("stop_task", {}),
            },
            {
                "name": "get_task_status",
                "display_name": "查询任务状态",
                "description": (
                    "①Query task progress. ②Optional task_id. ③One task returns "
                    "{task_id,status,progress{current,total,message},created_at,metadata}; all tasks return "
                    "{active_count,total_count,tasks[{task_id,status,metadata}]}. ④status is "
                    "running/paused/completed/cancelled/failed."
                ),
                "execute": _tool_get_task_status,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("get_task_status", {}),
            },
            # Story 09: 翻译配置
            {
                "name": "get_translation_config",
                "display_name": "翻译配置",
                "description": (
                    "①Return a read-only unified LLM configuration snapshot. ②No arguments. "
                    "③Returns non-secret fields such as provider/base_url_host/model/config_revision. "
                    "④Rule: never returns plaintext credentials."
                ),
                "execute": _tool_get_translation_config,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("get_translation_config", {}),
            },
            {
                "name": "set_translation_config",
                "display_name": "设置翻译配置",
                "description": (
                    "①Update the unified LLM configuration. ②provider/base_url/model must be supplied together; "
                    "temperature/max_tokens/target_lang/game_profile are optional. "
                    "③Returns changed_fields/config_revision."
                ),
                "execute": _tool_set_translation_config,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS.get("set_translation_config", {}),
            },
            {
                "name": "set_scope",
                "display_name": "设置作用域",
                "description": (
                    "①Set the default translation scope used by start_translation without entry_ids. "
                    "②Arguments: stages, "
                    "labels, categories, action=include/exclude/only. ③Returns a scope snapshot. ④Rules: AND across "
                    "dimensions, OR within one; verify labels/categories with "
                    "list_labels/get_statistics; include and only "
                    "currently behave the same."
                ),
                "execute": _tool_set_scope,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS.get("set_scope", {}),
            },
            {
                "name": "get_scope_preview",
                "display_name": "作用域预览",
                "description": (
                    "①Preview counts for the current scope. ②No arguments. ③Returns "
                    "{matched,total,scope{stages,labels,categories,action}}. "
                    "④Rules: returns counts, not entries; default "
                    "scope is untranslated stage 0; call after set_scope to confirm the range."
                ),
                "execute": _tool_get_scope_preview,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("get_scope_preview", {}),
            },
            {
                "name": "set_term_config",
                "display_name": "术语配置",
                "description": (
                    "①Set terminology-source priority and local paths. ②Arguments: ordered term_sources "
                    "(dynamic/paratranz/json/csv/excel), json_path, csv_path, excel_path. "
                    "dynamic is always available for extraction during AI translation. ③Returns {changed}. ④Rules: "
                    "inspect get_translation_config first; an empty list disables all sources; "
                    "invalid source names are "
                    "rejected."
                ),
                "execute": _tool_set_term_config,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS.get("set_term_config", {}),
            },
        ],
    )


_register_translator_tools()
