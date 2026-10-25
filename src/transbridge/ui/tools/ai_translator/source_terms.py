"""Project terminology composition and final cross-source review callbacks."""

from __future__ import annotations

from copy import copy
from hashlib import sha256
from pathlib import Path


def source_run_id(run_id: str, source_key: str) -> str:
    """Distinct sources must not share candidate or postprocess checkpoints."""
    digest = sha256(source_key.encode()).hexdigest()[:20]
    return f"{run_id}-{digest}"


def source_terminology_path(path: str | None, source_key: str | None) -> str:
    """Give non-plugin sources a stable, filesystem-safe local cache identity."""
    return path or f"project-source-{sha256(str(source_key).encode()).hexdigest()[:20]}.esp"


def project_term_pool(request):
    from transbridge.ai_translator.shared_term_pool import SharedTermPool

    owner = getattr(getattr(request, "spec", None), "owner", None)
    project = getattr(owner, "project_id", None)
    variant = getattr(owner, "variant_id", None)
    if not project or not variant or not getattr(request.config, "retrieval_enabled", True):
        return None
    return SharedTermPool(str(project), str(variant), request.config.target_lang)


def close_source_terms(executor, outcomes):
    from transbridge.ai_translator.shared_term_lookup import shared_terms_for_entry
    from transbridge.ai_translator.term_database import TermDatabaseManager

    from .source_reference_review import review_source_references, source_review_evidence
    from .task_term_closure import close_task_terms

    pool = executor.shared_term_pool
    if pool is None:
        return tuple(outcomes)
    managers = {}
    snapshots = {}

    def resolve(task, entry):
        if task.key not in managers:
            executor.progress(task.key, "term_closure_load", 0, 0, "正在准备本来源的任务参考快照")
            config = copy(executor.config)
            # Final review matches source spans only. It must not rebuild an
            # embedding index or call a model while loading reference data.
            config.enable_semantic_match = False
            manager = TermDatabaseManager(
                config,
                source_terminology_path(task.esp_path, task.key),
                executor.client,
                executor.project_id,
                shared_term_pool=pool,
                generated_term_plugin_id=Path(task.esp_path).name if task.esp_path else None,
                reference_store=getattr(executor, "reference_store", None),
                reference_source=task.key,
                **executor.request.terminology_binding.term_database_kwargs(),
            )
            manager.load_all()
            managers[task.key] = manager
            executor.progress(task.key, "term_closure_scan", 0, 0, "任务参考已就绪，正在扫描未评估的差异")
        manager = managers[task.key]
        context = manager.lookup_context_for_entry(entry)
        plugin = None if context is None else context.plugin_id
        if plugin not in snapshots:
            snapshots[plugin] = pool.snapshot(plugin)
        return shared_terms_for_entry(manager, entry, snapshot=snapshots[plugin])

    evidence = {outcome.task.key: source_review_evidence(outcome) for outcome in outcomes}

    def repair(task, references):
        if not getattr(executor.config, "enable_post_process", True):
            executor.log(task.key, "后处理已关闭，参考差异保留为待复核项")
            return {}
        executor.log(task.key, f"项目参考差异：综合校对 {len(references)} 条")
        return review_source_references(executor, task, references, evidence[task.key])

    closed = close_task_terms(
        outcomes,
        resolve_terms=resolve,
        repair=repair,
        stop_event=executor.stop,
        pause_event=getattr(executor, "pause", None),
        progress=executor.progress,
        conflicts=pool.conflicts_since(executor.initial_term_revision),
    )
    if executor.consistency:
        executor.consistency.require_current(executor.request)
    return closed
