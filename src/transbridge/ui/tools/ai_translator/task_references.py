"""Compose the task-scoped reference store from detached recovery identities."""

from __future__ import annotations

import logging
from pathlib import Path

from transbridge.ai_translator.project_reference_snapshot import restore_project_reference
from transbridge.ai_translator.task_reference_snapshot import TaskReferenceStore

logger = logging.getLogger(__name__)


def _store(task_id, directory, project_id, variant_id, target_lang):
    root = None if directory is None else Path(directory) / "ai-task-recovery" / "references"
    return TaskReferenceStore(task_id, root=root, identity=(project_id, variant_id, target_lang))


def reference_store_for_request(request):
    owner = getattr(getattr(request, "spec", None), "owner", None)
    # origin_task_id is historical provenance, never the reference-store owner.
    # Legacy continuation creates a new manifest; ordinary resume retains its ID.
    task_id = getattr(request, "recovery_task_id", None) or getattr(request, "run_id", "memory")
    return _store(
        task_id,
        getattr(request, "recovery_directory", None),
        str(getattr(owner, "project_id", "")),
        str(getattr(owner, "variant_id", "")),
        getattr(request.config, "target_lang", ""),
    )


def restored_reference_binding(record, directory, *, project_id, variant_id, target_lang):
    """Read only during launch; an old record without full references captures once."""
    if not record or directory is None:
        return None
    store = _store(
        record["task_id"],
        directory,
        str(project_id),
        str(variant_id),
        target_lang,
    )
    if store.published is None:
        logger.info("旧任务 %s 尚无完整参考快照，本次恢复将捕获一次并供后续阶段复用", record["task_id"])
        return None
    return restore_project_reference(store.published)
