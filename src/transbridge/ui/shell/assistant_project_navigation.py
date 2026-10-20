"""Exact local-Project navigation and display metadata for assistant sessions."""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path

from transbridge.application.contracts import RequestContext
from transbridge.application.projects.catalog import variant_catalog
from transbridge.persistence.v2 import ProjectId, ProjectRef, VariantId, VariantRef
from transbridge.persistence.v2.models import PersistenceV2Error

logger = logging.getLogger(__name__)


def switch_assistant_project(host, context: RequestContext, on_finished: Callable[[bool], None]) -> None:
    """Switch only after upper-level confirmation; complete after Qt installs the view."""
    busy = (
        getattr(host.context, "project_opening", False)
        or getattr(host, "project_open_worker", None) is not None
        or getattr(host, "save_worker", None) is not None
        or getattr(host, "foreground_worker", None) is not None
        or getattr(host, "close_pending", False)
        or getattr(getattr(host.workbench, "ai_tasks", None), "busy", False)
    )
    if busy:
        host.show_message("当前工程仍有后台操作，请等待完成后再切换对话所属工程。")
        on_finished(False)
        return
    if (
        not context.project_id
        or not context.variant_id
        or not getattr(host.context, "uses_authoritative_projection", False)
        or getattr(host, "current_project_opener", None) is None
        or getattr(host, "runtime_context", None) is None
    ):
        host.show_message("此对话没有可打开的工程版本绑定，或当前工程服务不可用。")
        on_finished(False)
        return
    if host.context.active_version_identity == (context.project_id, context.variant_id) and not getattr(
        host.context, "project_view_failed", False
    ):
        on_finished(True)
        return
    host.project_coordinator.open_project_identity(context, on_finished)


def session_project_labels(host, project_id: str | None, variant_id: str | None) -> dict:
    """Read only Project metadata; never load a Variant's translation entries."""
    labels = {
        "project_name": project_id or "未归属对话",
        "variant_name": variant_id or "未绑定版本",
        "available": False,
        "reason": "此对话没有绑定工程和版本。",
    }
    if not project_id or not variant_id:
        return labels
    runtime = getattr(host, "app_runtime", None)
    if runtime is None or "persistence_v2" not in runtime.use_cases.names():
        labels["reason"] = "工程目录服务不可用。"
        return labels
    try:
        persistence = runtime.use_cases.resolve("persistence_v2")
        ref = ProjectRef(ProjectId(project_id))
        project = persistence.projects.read_snapshot(ref)
        labels["project_name"] = str(project.envelope.data["name"])
        variant = next((item for item in variant_catalog(project) if item.variant_id == variant_id), None)
        if variant is None:
            labels["reason"] = "此对话绑定的版本已不存在。"
            return labels
        labels["variant_name"] = variant.name
        variant_ref = VariantRef(VariantId(variant_id), ref.identity)
        if not Path(persistence.variants.path_for(variant_ref)).is_file():
            labels["reason"] = "此对话绑定的版本文件已不存在。"
            return labels
        labels.update(available=True, reason=None)
    except (OSError, KeyError, TypeError, ValueError, PersistenceV2Error) as exc:
        logger.warning("Could not read assistant session Project metadata: %s", exc)
        labels["reason"] = "此对话绑定的工程记录不可用，请检查本地工程文件。"
    return labels
