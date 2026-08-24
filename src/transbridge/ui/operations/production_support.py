"""Qt-free helpers used by the production operation facade composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import hashlib

from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import EntryRevision, SourceNamespace
from transbridge.application.sync import LocalEntrySnapshot, SyncOperation
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection

from .mappers import OperationPlanDraft
from .plan_view import OperationKind
from .preflight_view import PreflightCheckState, PreflightCheckStatus


@dataclass(frozen=True, slots=True)
class SyncRequest:
    ui_context: object
    operation: SyncOperation
    project_id: int
    namespace: SourceNamespace
    local_entries: tuple[LocalEntrySnapshot, ...]
    batch: bool


def context_factory(value: RequestContext | Callable[[object], RequestContext]):
    if isinstance(value, RequestContext):
        return lambda _ui_context: value
    if not callable(value):
        raise TypeError("runtime_context must be a RequestContext or callable")
    return value


def sync_request(context, kind: OperationKind, batch: bool):
    project_id = getattr(context, "paratranz_project_id", None)
    if project_id is None and getattr(context, "current_project", None):
        project_id = context.current_project.get("id")
    try:
        project_id = int(project_id)
    except (TypeError, ValueError):
        project_id = 0
    entries = ()
    namespace = SourceNamespace.legacy()
    reason = ""
    try:
        entries = local_snapshots(context, project_id)
        namespaces = {item.entry_key.namespace for item in entries}
        if len(namespaces) != 1:
            raise ValueError("本地条目必须属于单一 source namespace")
        namespace = next(iter(namespaces))
    except (AttributeError, TypeError, ValueError) as exc:
        reason = str(exc)
    ready = project_id > 0 and bool(entries) and not batch and not reason
    if batch:
        reason = "批量 ParaTranz 计划需要显式逐槽位 scope，当前未开放"
    elif project_id <= 0:
        reason = "未选择 ParaTranz 项目"
    request = SyncRequest(
        context,
        SyncOperation.UPLOAD if kind is OperationKind.UPLOAD else SyncOperation.DOWNLOAD,
        project_id,
        namespace,
        entries,
        batch,
    )
    return request, ready, reason


def local_snapshots(context, project_id: int) -> tuple[LocalEntrySnapshot, ...]:
    collection = context.collection
    if collection is None:
        raise ValueError("当前没有已加载集合")
    scope = f"project:{project_id}"
    output = []
    for entry in collection:
        refs = tuple(ref for ref in entry.external_refs if ref.system == "paratranz" and ref.scope == scope)
        if len(refs) > 1:
            raise ValueError(f"条目 {entry.key} 有重复远端引用")
        output.append(
            LocalEntrySnapshot(
                entry.identity,
                entry.revision if isinstance(entry.revision, EntryRevision) else EntryRevision(entry.revision),
                entry.original,
                entry.translation or "",
                entry.context or "",
                entry.stage,
                refs[0] if refs else None,
            )
        )
    return tuple(output)


def replace_local_snapshots(
    context,
    snapshots: tuple[LocalEntrySnapshot, ...],
    project_id: int,
) -> None:
    existing = {entry.identity: entry for entry in context.collection or ()}
    active_scope = f"project:{project_id}"
    entries = []
    for item in snapshots:
        if item.deleted:
            continue
        current = existing.get(item.entry_key)
        remote_ref = () if item.external_ref is None else (item.external_ref,)
        if current is None:
            entries.append(
                TranslationEntry(
                    id=item.entry_key.local_key,
                    key=item.entry_key.local_key,
                    original=item.original,
                    translation=item.translation,
                    stage=item.stage,
                    context=item.context,
                    entry_key=item.entry_key,
                    external_refs=remote_ref,
                    revision=item.revision,
                )
            )
        else:
            unrelated = tuple(
                ref for ref in current.external_refs if not (ref.system == "paratranz" and ref.scope == active_scope)
            )
            entries.append(
                replace(
                    current,
                    original=item.original,
                    translation=item.translation,
                    stage=item.stage,
                    context=item.context,
                    external_refs=(*unrelated, *remote_ref),
                    revision=item.revision,
                )
            )
    context.collection = TranslationEntryCollection(entries)


def local_hash(entries: tuple[LocalEntrySnapshot, ...]) -> str:
    material = "\0".join(
        f"{item.entry_key.serialize()}:{item.revision.value}:{item.stage}:{item.translation}" for item in entries
    )
    return hashlib.sha256(material.encode()).hexdigest()


def operation_request(draft, expected_type):
    if not isinstance(draft, OperationPlanDraft) or not isinstance(draft.request, expected_type):
        raise TypeError(f"operation draft must contain {expected_type.__name__}")
    return draft.request


def blocked(code: str, reason: str) -> PreflightCheckState:
    return PreflightCheckState(code, code.replace("_", " "), PreflightCheckStatus.BLOCKED, reason)


def trim_cache(cache: dict) -> None:
    while len(cache) > 100:
        cache.pop(next(iter(cache)))


__all__ = [
    "SyncRequest",
    "blocked",
    "context_factory",
    "local_hash",
    "local_snapshots",
    "operation_request",
    "replace_local_snapshots",
    "sync_request",
    "trim_cache",
]
