"""Qt-free helpers used by the production operation facade composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import hashlib

from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import EntryRevision, SourceNamespace
from transbridge.application.projects import (
    EntryStatePatch,
    ParaTranzTargetResolver,
    ParaTranzTargetStatus,
)
from transbridge.application.sync import ConflictPolicy, DeletionPolicy, LocalEntrySnapshot, SyncOperation
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef

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
    target_project_name: str
    target_source: str
    target_revision: str
    target_status: str
    target_endpoint: str
    target_account_user_id: int | None
    config_revision: int
    persist_as_default: bool
    conflict_policy: ConflictPolicy
    deletion_policy: DeletionPolicy
    active_version_identity: tuple[str, str] | None = None
    project_revision: int | None = None
    variant_revision: int | None = None


def context_factory(value: RequestContext | Callable[[object], RequestContext]):
    if isinstance(value, RequestContext):
        return lambda _ui_context: value
    if not callable(value):
        raise TypeError("runtime_context must be a RequestContext or callable")
    return value


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "是"}


def sync_request(context, kind: OperationKind, batch: bool, values=None):
    values = dict(values or {})
    explicit = values.get("paratranz_project_id")
    explicit_project_name = str(values.get("paratranz_project_name", "")).strip()
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
    default_conflict = ConflictPolicy.PREFER_LOCAL if kind is OperationKind.UPLOAD else ConflictPolicy.PREFER_REMOTE
    try:
        raw_conflict = values.get("conflict_policy", default_conflict)
        conflict_policy = ConflictPolicy(raw_conflict)
    except (TypeError, ValueError):
        conflict_policy = default_conflict
        explicit_error = "同步处理方式无效"
    default_deletion = DeletionPolicy.APPLY if kind is OperationKind.UPLOAD else DeletionPolicy.PRESERVE
    raw_deletion = values.get("deletion_policy")
    if raw_deletion is not None:
        try:
            deletion_policy = DeletionPolicy(raw_deletion)
        except (TypeError, ValueError):
            deletion_policy = default_deletion
            explicit_error = "删除处理方式无效"
    else:
        deletion_policy = (
            DeletionPolicy.APPLY
            if _bool_value(values.get("apply_remote_deletions", default_deletion is DeletionPolicy.APPLY))
            else DeletionPolicy.PRESERVE
        )
    if kind is OperationKind.DOWNLOAD and bool(getattr(context, "uses_authoritative_projection", False)):
        deletion_policy = DeletionPolicy.PRESERVE
    resolve = getattr(context, "resolve_paratranz_target", None)
    if callable(resolve):
        target = resolve(
            explicit_project_id=explicit_project_id,
            explicit_project_name=explicit_project_name,
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
            explicit_project_name=explicit_project_name,
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
        ui_context=context,
        operation=SyncOperation.UPLOAD if kind is OperationKind.UPLOAD else SyncOperation.DOWNLOAD,
        project_id=project_id,
        namespace=namespace,
        local_entries=entries,
        batch=batch,
        target_project_name=target.project_name,
        target_source=target.source.value,
        target_revision=target_revision,
        target_status=target.status.value,
        target_endpoint=target.endpoint,
        target_account_user_id=target.account_user_id,
        config_revision=config_revision,
        persist_as_default=persist_as_default,
        conflict_policy=conflict_policy,
        deletion_policy=deletion_policy,
        active_version_identity=getattr(context, "active_version_identity", None),
        project_revision=getattr(context, "project_revision", None),
        variant_revision=getattr(context, "variant_revision", None),
    )
    return request, ready, reason


def sync_request_target_is_current(request: SyncRequest) -> tuple[bool, str]:
    """Check mutable target identity without consulting ParaTranz browse state."""
    values = {
        "conflict_policy": request.conflict_policy.value,
        "deletion_policy": request.deletion_policy.value,
        "set_as_default": request.persist_as_default,
    }
    if request.target_source == "explicit" and request.project_id > 0:
        values.update({
            "paratranz_project_id": str(request.project_id),
            "paratranz_project_name": request.target_project_name,
        })
    refreshed, _ready, _reason = sync_request(
        request.ui_context,
        OperationKind.UPLOAD if request.operation is SyncOperation.UPLOAD else OperationKind.DOWNLOAD,
        request.batch,
        values,
    )
    expected = (
        request.project_id,
        request.target_project_name,
        request.target_source,
        request.target_revision,
        request.target_endpoint,
        request.target_account_user_id,
        request.config_revision,
        request.conflict_policy,
        request.deletion_policy,
        request.active_version_identity,
        request.project_revision,
        request.variant_revision,
    )
    actual = (
        refreshed.project_id,
        refreshed.target_project_name,
        refreshed.target_source,
        refreshed.target_revision,
        refreshed.target_endpoint,
        refreshed.target_account_user_id,
        refreshed.config_revision,
        refreshed.conflict_policy,
        refreshed.deletion_policy,
        refreshed.active_version_identity,
        refreshed.project_revision,
        refreshed.variant_revision,
    )
    if actual == expected:
        return True, ""
    return False, "本地工程绑定、ParaTranz 账号或服务配置已变化，请按当前设置重新检查。"


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
    *,
    active_version_identity: tuple[str, str] | None = None,
    project_revision: int | None = None,
    variant_revision: int | None = None,
) -> None:
    original_collection = context.collection
    authoritative = bool(getattr(context, "uses_authoritative_projection", False))
    existing = {entry.identity: entry for entry in original_collection or ()}
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
            next_refs = (*unrelated, *remote_ref)
            changed = (item.translation, item.stage, next_refs) != (
                current.translation or "",
                current.stage,
                current.external_refs,
            )
            entries.append(
                replace(
                    current,
                    original=current.original if authoritative else item.original,
                    translation=item.translation,
                    stage=item.stage,
                    context=current.context if authoritative else item.context,
                    external_refs=next_refs,
                    revision=current.revision.next() if changed else current.revision,
                )
            )
    candidate = TranslationEntryCollection(entries)
    if authoritative:
        candidate_keys = {entry.identity for entry in candidate}
        if candidate_keys != set(existing):
            raise RuntimeError("当前 V2 工程尚不支持通过 ParaTranz 新增或删除本地词条；本地内容未更改。")
        if active_version_identity is None:
            raise RuntimeError("ParaTranz 同步缺少活动工程版本身份；本地内容未更改。")
        commands = getattr(context, "project_commands", None)
        runtime_context = getattr(context, "runtime_context", None)
        if commands is None or runtime_context is None:
            raise RuntimeError("权威 Variant 写入适配器不可用；本地内容未更改。")
        project_identity, variant_identity = active_version_identity
        result = commands.replace_entry_records(
            {
                entry.identity: EntryStatePatch(entry.translation or "", entry.stage, tuple(entry.external_refs))
                for entry in candidate
            },
            runtime_context,
            expected_project_revision=project_revision,
            expected_variant_revision=variant_revision,
            expected_variant_ref=VariantRef(VariantId(variant_identity), ProjectId(project_identity)),
        )
        if not result.is_success:
            message = result.diagnostics[0].message if result.diagnostics else "权威 Variant 写入失败"
            raise RuntimeError(f"ParaTranz 本地提交失败：{message}")
    if not bool(getattr(context, "uses_authoritative_projection", False)) or (
        getattr(context, "active_version_identity", active_version_identity) == active_version_identity
        and context.collection is original_collection
    ):
        context.collection = candidate


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
