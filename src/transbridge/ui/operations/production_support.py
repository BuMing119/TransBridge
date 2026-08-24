"""Qt-free helpers used by the production operation facade composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import hashlib

from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import EntryRevision, SourceNamespace
from transbridge.application.projects import (
    ParaTranzTargetResolver,
    ParaTranzTargetStatus,
)
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
    target_source: str
    target_revision: str
    target_status: str
    target_endpoint: str
    target_account_user_id: int | None
    config_revision: int
    persist_as_default: bool


def context_factory(value: RequestContext | Callable[[object], RequestContext]):
    if isinstance(value, RequestContext):
        return lambda _ui_context: value
    if not callable(value):
        raise TypeError("runtime_context must be a RequestContext or callable")
    return value


def sync_request(context, kind: OperationKind, batch: bool, values=None):
    values = dict(values or {})
    explicit = values.get("paratranz_project_id")
    explicit_error = ""
    raw_persist = values.get("set_as_default", False)
    if isinstance(raw_persist, bool):
        persist_as_default = raw_persist
    else:
        normalized_persist = str(raw_persist).strip().casefold()
        persist_as_default = normalized_persist in {"1", "true", "yes", "是"}
        if normalized_persist not in {"", "0", "false", "no", "否", "1", "true", "yes", "是"}:
            explicit_error = "设为工程默认必须填写 true 或 false"
    try:
        explicit_project_id = None if explicit in (None, "") else int(explicit)
        if explicit_project_id is not None and explicit_project_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        explicit_project_id = None
        explicit_error = "ParaTranz 项目 ID 必须是正整数"
    resolve = getattr(context, "resolve_paratranz_target", None)
    if callable(resolve):
        target = resolve(
            explicit_project_id=explicit_project_id,
            explicit_verified=False,
        )
    else:
        config = getattr(context, "config", None)
        user = getattr(context, "current_user", None)
        account_id = user.get("id") if isinstance(user, dict) else getattr(config, "user_id", None)
        target = ParaTranzTargetResolver().resolve(
            binding=getattr(context, "paratranz_binding", None),
            binding_revision=getattr(context, "project_revision", None),
            explicit_project_id=explicit_project_id,
            endpoint=str(getattr(config, "base_url", "https://paratranz.cn")),
            account_user_id=account_id,
        )
    project_id = target.project_id or 0
    entries = ()
    namespace = SourceNamespace.legacy()
    reason = explicit_error
    try:
        entries = local_snapshots(context, project_id)
        namespaces = {item.entry_key.namespace for item in entries}
        if len(namespaces) != 1:
            raise ValueError("本地条目必须属于单一 source namespace")
        namespace = next(iter(namespaces))
    except (AttributeError, TypeError, ValueError) as exc:
        reason = str(exc)
    target_ready = target.status in {ParaTranzTargetStatus.UNVERIFIED, ParaTranzTargetStatus.AVAILABLE}
    ready = project_id > 0 and target_ready and bool(entries) and not batch and not reason
    if batch:
        reason = "批量 ParaTranz 计划需要显式逐槽位 scope，当前未开放"
    elif project_id <= 0:
        reason = target.reason or "当前本地工程尚未绑定 ParaTranz 项目"
    elif not target_ready:
        reason = target.reason or "当前 ParaTranz 目标不可用"
    elif persist_as_default and getattr(context, "active_project_id", None) is None:
        ready = False
        reason = "没有活动本地工程，当前目标只能用于本次操作，不能保存为默认绑定"
    config_revision = int(getattr(getattr(context, "config", None), "config_revision", 0))
    target_revision = (
        f"{target.source.value}:{target.binding_revision if target.binding_revision is not None else '-'}"
        f":config-{config_revision}"
    )
    request = SyncRequest(
        context,
        SyncOperation.UPLOAD if kind is OperationKind.UPLOAD else SyncOperation.DOWNLOAD,
        project_id,
        namespace,
        entries,
        batch,
        target.source.value,
        target_revision,
        target.status.value,
        target.endpoint,
        target.account_user_id,
        config_revision,
        persist_as_default,
    )
    return request, ready, reason


def sync_request_target_is_current(request: SyncRequest) -> tuple[bool, str]:
    """Check mutable target identity without consulting ParaTranz browse state."""
    values = (
        {"paratranz_project_id": str(request.project_id)}
        if request.target_source == "explicit" and request.project_id > 0
        else {}
    )
    refreshed, _ready, _reason = sync_request(
        request.ui_context,
        OperationKind.UPLOAD if request.operation is SyncOperation.UPLOAD else OperationKind.DOWNLOAD,
        request.batch,
        values,
    )
    expected = (
        request.project_id,
        request.target_source,
        request.target_revision,
        request.target_endpoint,
        request.target_account_user_id,
        request.config_revision,
    )
    actual = (
        refreshed.project_id,
        refreshed.target_source,
        refreshed.target_revision,
        refreshed.target_endpoint,
        refreshed.target_account_user_id,
        refreshed.config_revision,
    )
    if actual == expected:
        return True, ""
    return False, "本地工程绑定、ParaTranz 账号或服务配置已变化，请返回编辑并重新预检。"


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


def trim_cache(cache: dict, *, on_evict: Callable[[object], None] | None = None) -> None:
    while len(cache) > 100:
        value = cache.pop(next(iter(cache)))
        if on_evict is not None:
            on_evict(value)


__all__ = [
    "SyncRequest",
    "blocked",
    "context_factory",
    "local_hash",
    "local_snapshots",
    "operation_request",
    "replace_local_snapshots",
    "sync_request",
    "sync_request_target_is_current",
    "trim_cache",
]
