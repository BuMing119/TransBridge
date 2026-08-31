"""Production ParaTranz upload/download operation-plan slice."""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from datetime import datetime
from threading import RLock

from transbridge.application.ports.paratranz import ExternalServiceError
from transbridge.application.projects import ParaTranzProjectBinding
from transbridge.application.sync import (
    AuthorizedSyncPlan,
    AuthorizeSyncPlanRequest,
    CallbackLocalSyncUnitOfWork,
    ConflictPolicy,
    CreateSyncPlanRequest,
    DeletionPolicy,
    ParaTranzSyncExecutor,
    ParaTranzSyncPlanningUseCase,
    ParaTranzSyncTaskDraft,
    ParaTranzSyncTaskEntrypoint,
    ParaTranzSyncTaskPreparation,
    SyncOperation,
    SyncPlanner,
)
from transbridge.application.tasks import OwnerRef, TaskRuntime
from transbridge.bootstrap.runtime import AppRuntime
from transbridge.paratranz.service import ParaTranzService
from transbridge.paratranz.sync_snapshot import ParaTranzRemoteSnapshotAdapter
from transbridge.ui.version_persistence import VersionPersistence

from .errors import OperationCompositionError
from .facade import OperationFeatureAdapter
from .mappers import (
    DomainPreflightResult,
    DownloadOperationMapper,
    OperationPlanDraft,
    UploadOperationMapper,
)
from .plan_view import EditableControl, EditableFieldState, OperationKind
from .preflight_view import PreflightCheckState, PreflightCheckStatus
from .production_support import (
    SyncRequest,
    blocked,
    local_hash,
    local_snapshots,
    operation_request,
    replace_local_snapshots,
    sync_request,
    sync_request_target_is_current,
    trim_cache,
)


@dataclass(frozen=True, slots=True)
class _PreparedSync:
    request: SyncRequest
    planning: ParaTranzSyncPlanningUseCase
    plan: object
    service: object
    project_name: str

    def close(self) -> None:
        close = getattr(self.service, "close", None)
        if callable(close):
            close()


def build_paratranz_sync_features(runtime: AppRuntime) -> tuple[OperationFeatureAdapter, OperationFeatureAdapter]:
    """Build the two ParaTranz operation adapters without performing remote work."""

    sync_cache: dict[int, _PreparedSync] = {}
    cache_lock = RLock()

    def sync_checks(value: object) -> DomainPreflightResult:
        if not isinstance(value, SyncRequest):
            return DomainPreflightResult((blocked("SYNC_REQUEST_INVALID", "ParaTranz 请求无效"),))
        current, reason = sync_request_target_is_current(value)
        if not current:
            return DomainPreflightResult((blocked("PARATRANZ_TARGET_STALE", reason),))
        service = None
        try:
            service = ParaTranzService.from_config(value.ui_context.config)
            if value.target_account_user_id is None:
                raise PermissionError("当前 ParaTranz 账号尚未验证")
            mine = service.list_projects(uid=value.target_account_user_id)
            remote_project = next((item for item in mine if item.project_id == value.project_id), None)
            if remote_project is None:
                target_name = value.target_project_name or "所选 ParaTranz 项目"
                raise PermissionError(f"当前账号无权访问“{target_name}”")
            planning = ParaTranzSyncPlanningUseCase(
                ParaTranzRemoteSnapshotAdapter(service),
                planner=SyncPlanner(
                    local_state_only=bool(getattr(value.ui_context, "uses_authoritative_projection", False))
                ),
            )
            plan = planning.create_plan(
                CreateSyncPlanRequest(
                    value.project_id,
                    value.namespace,
                    value.local_entries,
                    value.operation,
                    value.conflict_policy,
                    value.deletion_policy,
                )
            )
        except Exception as exc:  # network/permission boundary becomes a blocked check
            if service is not None:
                service.close()
            if isinstance(exc, PermissionError):
                check = blocked("PARATRANZ_MEMBER_REQUIRED", str(exc))
            elif isinstance(exc, ExternalServiceError):
                check = blocked(
                    f"PARATRANZ_{exc.category.value.upper()}",
                    f"ParaTranz 检查失败：{_external_error_label(exc.category.value)}",
                )
            else:
                check = blocked("PARATRANZ_PREFLIGHT_FAILED", "无法完成 ParaTranz 检查，请稍后重试")
            return DomainPreflightResult((check,))
        prepared = _PreparedSync(value, planning, plan, service, remote_project.name)
        with cache_lock:
            previous = sync_cache.pop(id(value), None)
            sync_cache[id(value)] = prepared
            trim_cache(sync_cache, on_evict=lambda item: item.close())
        if previous is not None:
            previous.close()
        unresolved = int(getattr(plan, "conflicts", 0))
        check = PreflightCheckState(
            "PARATRANZ_PLAN_FRESH",
            "云端内容与更新计划",
            PreflightCheckStatus.PASSED if not unresolved else PreflightCheckStatus.BLOCKED,
            "" if not unresolved else f"发现 {unresolved} 条无法安全对应的数据，请查看变更明细",
        )
        checks = (check,)
        if value.operation is SyncOperation.DOWNLOAD and bool(
            getattr(value.ui_context, "uses_authoritative_projection", False)
        ):
            checks += (
                PreflightCheckState(
                    "PARATRANZ_LOCAL_SOURCE_SCOPE",
                    "当前来源的译文更新",
                    PreflightCheckStatus.WARNING,
                    "仅更新当前来源已有条目的译文和状态；云端独有条目跳过，本地原文、上下文和条目集合保持不变。",
                ),
            )
        return DomainPreflightResult(checks, tuple(plan.counts))

    def create_sync(kind: OperationKind, context, batch: bool, values):
        request, ready, reason = sync_request(context, kind, batch, values)
        entries = request.local_entries
        locked = sum(item.stage == 9 for item in entries)
        hidden = sum(item.stage == -1 for item in entries)
        config = getattr(context, "config", None)
        credentials = bool(config is not None and getattr(config, "token", None))
        permission = bool(
            request.project_id
            and request.target_account_user_id is not None
            and request.target_status in {"unverified", "available"}
        )
        target_name = request.target_project_name or (
            f"ParaTranz 项目 #{request.project_id}" if request.project_id else "尚未选择云端项目"
        )
        target_suffix = {
            "project_binding": " · 当前工程已绑定",
            "explicit": " · 本次选择",
        }.get(request.target_source, "")
        project_name = str(getattr(context, "project_name", "") or "当前本地工程")
        variant_name = str(getattr(context, "active_variant", "") or "当前版本")
        recovery_available = _recovery_available(context)
        local_state_only = kind is OperationKind.DOWNLOAD and bool(
            getattr(context, "uses_authoritative_projection", False)
        )
        conflict_summary = _conflict_summary(request)
        return OperationPlanDraft(
            request=request,
            target=f"{target_name}{target_suffix}",
            target_revision=f"remote:{request.project_id}:{request.target_revision}",
            input_fingerprint=local_hash(entries),
            scope_summary=f"{project_name} / {variant_name} / {len(entries):,} 条翻译内容",
            mode_summary="上传本地内容" if kind is OperationKind.UPLOAD else "使用 ParaTranz 内容更新本地",
            conflict_summary=conflict_summary,
            backup_summary=(
                "下载前自动创建历史还原点（包含未保存修改）"
                if kind is OperationKind.DOWNLOAD and recovery_available
                else "原子合并；失败不会改变本地内容"
                if kind is OperationKind.DOWNLOAD
                else "远端写入按同步计划执行"
            ),
            estimated_impact=(("local_entries", len(entries)),),
            editable_fields=(
                EditableFieldState(
                    "paratranz_project_id",
                    "云端项目",
                    "" if request.project_id <= 0 else str(request.project_id),
                    required=True,
                    control=EditableControl.REMOTE_PROJECT,
                    display_value=target_name,
                ),
                EditableFieldState(
                    "set_as_default",
                    "以后默认使用这个云端项目",
                    "true" if request.persist_as_default else "false",
                    enabled=(
                        request.target_source == "explicit" and getattr(context, "active_project_id", None) is not None
                    ),
                    control=EditableControl.BOOLEAN,
                ),
                EditableFieldState(
                    "conflict_policy",
                    "同步方式",
                    request.conflict_policy.value,
                    control=EditableControl.CHOICE,
                    options=_conflict_options(kind, local_state_only=local_state_only),
                ),
                EditableFieldState(
                    "apply_remote_deletions",
                    "同时删除云端已明确删除的本地条目",
                    "true" if request.deletion_policy is DeletionPolicy.APPLY else "false",
                    enabled=kind is OperationKind.DOWNLOAD and not local_state_only,
                    control=EditableControl.BOOLEAN,
                    help_text="开启后，云端明确删除的对应条目也会从本地删除。",
                ),
            ),
            credentials_ready=credentials,
            permission_ready=permission,
            input_ready=ready,
            output_ready=ready,
            locked_count=locked,
            hidden_count=hidden,
            warnings=(() if ready else (reason,)),
            expected_side_effects=(
                ("对 ParaTranz 执行幂等远端写入" if kind is OperationKind.UPLOAD else "在本地聚合事务中应用远端更新"),
            ),
        )

    def edit_sync(draft, fields):
        request = operation_request(draft, SyncRequest)
        values = dict(fields)
        raw_id = str(values.get("paratranz_project_id", "")).strip()
        raw_name = str(values.get("paratranz_project_name", request.target_project_name)).strip()
        if (
            raw_id == str(request.project_id)
            and raw_name == request.target_project_name
            and request.target_source != "explicit"
        ):
            values.pop("paratranz_project_id", None)
            values.pop("paratranz_project_name", None)
            values["set_as_default"] = False
        return create_sync(
            OperationKind.UPLOAD if request.operation is SyncOperation.UPLOAD else OperationKind.DOWNLOAD,
            request.ui_context,
            request.batch,
            values,
        )

    def discard_sync(draft) -> None:
        request = operation_request(draft, SyncRequest)
        with cache_lock:
            prepared = sync_cache.pop(id(request), None)
        if prepared is not None:
            prepared.close()

    def submit_sync(draft, _preflight, owner: OwnerRef, tasks: TaskRuntime):
        request = operation_request(draft, SyncRequest)
        with cache_lock:
            prepared = sync_cache.pop(id(request), None)
        if prepared is None:
            raise OperationCompositionError("ParaTranz 内容已变化，正在重新检查，请稍候")
        try:
            authority_revisions = {
                "project": request.project_revision,
                "variant": request.variant_revision,
            }
            local_uow = CallbackLocalSyncUnitOfWork(
                lambda: local_snapshots(request.ui_context, request.project_id),
                lambda values: replace_local_snapshots(
                    request.ui_context,
                    values,
                    request.project_id,
                    active_version_identity=request.active_version_identity,
                    project_revision=authority_revisions["project"],
                    variant_revision=authority_revisions["variant"],
                ),
            )
            snapshots = ParaTranzRemoteSnapshotAdapter(prepared.service)
            executor = ParaTranzSyncExecutor(prepared.service, snapshots, local_uow)
            recovery, recovery_name = _recovery_snapshot(request, prepared.plan)
            entrypoint = ParaTranzSyncTaskEntrypoint(tasks, executor, on_finished=prepared.close)

            def prepare(_run_id: str) -> ParaTranzSyncTaskPreparation:
                current_target, reason = sync_request_target_is_current(request)
                if not current_target:
                    raise OperationCompositionError(reason)
                if request.persist_as_default and request.target_source == "explicit":
                    now = datetime.now().astimezone().isoformat()
                    binding = ParaTranzProjectBinding(
                        request.project_id,
                        prepared.project_name,
                        request.target_endpoint,
                        request.target_account_user_id,
                        now,
                        now,
                    )
                    binding_result = request.ui_context.set_paratranz_binding(binding)
                    if not binding_result.is_success:
                        message = (
                            binding_result.diagnostics[0].message
                            if binding_result.diagnostics
                            else "无法保存工程默认绑定"
                        )
                        raise OperationCompositionError(message)
                    committed_revision = (
                        binding_result.value.get("project_revision") if isinstance(binding_result.value, dict) else None
                    )
                    authority_revisions["project"] = (
                        committed_revision
                        if committed_revision is not None
                        else getattr(request.ui_context, "project_revision", authority_revisions["project"])
                    )
                current = local_snapshots(request.ui_context, request.project_id)
                token = prepared.planning.issue_confirmation(prepared.plan, owner_id=owner.owner_id)
                authorized = prepared.planning.authorize(
                    AuthorizeSyncPlanRequest(
                        prepared.plan,
                        owner.owner_id,
                        request.project_id,
                        request.namespace,
                        current,
                        token,
                    )
                )
                return ParaTranzSyncTaskPreparation(authorized, current)

            pending_authorization = AuthorizedSyncPlan(prepared.plan, owner.owner_id, "PENDING")
            return entrypoint.submit(
                ParaTranzSyncTaskDraft(
                    pending_authorization,
                    request.project_id,
                    request.namespace,
                    request.local_entries,
                    recovery_snapshot=recovery,
                    recovery_snapshot_name=recovery_name,
                    preparation=prepare,
                ),
                owner,
            )
        except Exception:
            prepared.close()
            raise

    def supports_sync(context, batch: bool) -> bool:
        return not batch and getattr(context, "collection", None) is not None

    return (
        OperationFeatureAdapter(
            OperationKind.UPLOAD,
            UploadOperationMapper(sync_checks),
            lambda context, batch, values: create_sync(OperationKind.UPLOAD, context, batch, values),
            submit_sync,
            edit_sync,
            capability=supports_sync,
            draft_discarder=discard_sync,
        ),
        OperationFeatureAdapter(
            OperationKind.DOWNLOAD,
            DownloadOperationMapper(sync_checks),
            lambda context, batch, values: create_sync(OperationKind.DOWNLOAD, context, batch, values),
            submit_sync,
            edit_sync,
            capability=supports_sync,
            draft_discarder=discard_sync,
        ),
    )


def _conflict_options(kind: OperationKind, *, local_state_only: bool = False) -> tuple[tuple[str, str], ...]:
    if kind is OperationKind.UPLOAD:
        return (
            (ConflictPolicy.PREFER_LOCAL.value, "使用本地内容更新 ParaTranz（推荐）"),
            (ConflictPolicy.PREFER_REMOTE.value, "保留 ParaTranz 已有内容"),
        )
    return (
        (ConflictPolicy.PREFER_REMOTE.value, "使用 ParaTranz 内容更新本地（推荐）"),
        (
            ConflictPolicy.PREFER_LOCAL.value,
            "保留本地已有内容" if local_state_only else "保留本地已有内容，只补充云端新增内容",
        ),
    )


def _conflict_summary(request: SyncRequest) -> str:
    if request.operation is SyncOperation.UPLOAD:
        return "本地内容优先" if request.conflict_policy is ConflictPolicy.PREFER_LOCAL else "保留云端已有内容"
    return "云端内容优先" if request.conflict_policy is ConflictPolicy.PREFER_REMOTE else "保留本地已有内容"


def _recovery_available(context: object) -> bool:
    if not getattr(context, "active_version_identity", None):
        return False
    if bool(getattr(context, "uses_authoritative_projection", False)):
        return bool(
            getattr(context, "project_commands", None) is not None
            and getattr(context, "runtime_context", None) is not None
        )
    return bool(
        getattr(context, "active_project", None) is not None
        and getattr(context, "variant_store", None) is not None
        and getattr(context, "active_variant", None) is not None
    )


def _recovery_snapshot(request: SyncRequest, plan: object):
    counts = dict(getattr(plan, "counts", ()))
    changes_local = sum(counts.get(key, 0) for key in ("create_local", "update_local", "delete_local"))
    if request.operation is not SyncOperation.DOWNLOAD or not changes_local:
        return None, None
    context = request.ui_context
    identity = getattr(context, "active_version_identity", None)
    if identity is None or not _recovery_available(context):
        return None, None
    persistence = VersionPersistence(context, identity)
    name = f"ParaTranz-下载前-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{getattr(plan, 'plan_id', 'plan')[-8:]}"

    def capture(_run_id: str):
        entries = tuple(copy(entry) for entry in (getattr(context, "collection", None) or ()))
        result = persistence.create_snapshot(name, entries)
        if hasattr(result, "is_success") and not result.is_success:
            diagnostics = tuple(getattr(result, "diagnostics", ()))
            message = diagnostics[0].message if diagnostics else "无法创建下载前历史还原点"
            raise OperationCompositionError(message)
        return result

    return capture, name


def _external_error_label(category: str) -> str:
    return {
        "authentication": "账号认证失败",
        "permission": "当前账号没有访问权限",
        "rate_limited": "请求过于频繁，请稍后重试",
        "timeout": "连接超时",
        "unavailable": "服务暂时不可用",
        "transport": "网络连接失败",
        "cancelled": "检查已取消",
    }.get(category, "远端服务返回错误")


__all__ = ["build_paratranz_sync_features"]
