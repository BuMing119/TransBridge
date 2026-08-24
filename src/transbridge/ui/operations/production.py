"""Concrete production feature adapters for the operation-plan facade."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from pathlib import Path
from threading import RLock

from transbridge.application.contracts import RequestContext
from transbridge.application.fomod import (
    FomodTaskDraft,
    FomodTaskEntrypoint,
    FomodTaskPreflightService,
    PipelineEngine,
)
from transbridge.application.io.operation_write import (
    HydratedWriteDraft,
    HydratedWritePreflightService,
    HydratedWriteWorkload,
)
from transbridge.application.io.publish import BackupPolicy, ConflictPolicy as PublishConflictPolicy
from transbridge.application.sync import (
    AuthorizeSyncPlanRequest,
    CallbackLocalSyncUnitOfWork,
    ConflictPolicy,
    CreateSyncPlanRequest,
    ParaTranzSyncExecutor,
    ParaTranzSyncPlanningUseCase,
    ParaTranzSyncTaskDraft,
    ParaTranzSyncTaskEntrypoint,
)
from transbridge.application.tasks import OwnerRef, TaskRuntime
from transbridge.bootstrap.runtime import AppRuntime
from transbridge.paratranz.service import ParaTranzService
from transbridge.paratranz.sync_snapshot import ParaTranzRemoteSnapshotAdapter

from .facade import OperationFeatureAdapter, OperationPlanFacade, RuntimeContextFactory
from .mappers import (
    DownloadOperationMapper,
    FomodOperationMapper,
    OperationPlanDraft,
    UploadOperationMapper,
    WriteOperationMapper,
)
from .plan_dialog import OperationPlanDialog
from .plan_view import EditableFieldState, OperationKind
from .preflight_view import PreflightCheckState, PreflightCheckStatus
from .production_support import (
    SyncRequest as _SyncRequest,
    blocked as _blocked,
    context_factory as _context_factory,
    local_hash as _local_hash,
    local_snapshots as _local_snapshots,
    operation_request as _operation_request,
    replace_local_snapshots as _replace_local_snapshots,
    sync_request as _sync_request,
    trim_cache as _trim_cache,
)
from .runtime_adapter import OperationTaskAdapter, OperationTaskRequest


class OperationCompositionError(RuntimeError):
    """A required production capability is unavailable at facade construction/use."""


@dataclass(frozen=True, slots=True)
class _PreparedSync:
    request: _SyncRequest
    planning: ParaTranzSyncPlanningUseCase
    plan: object
    service: object


@dataclass(frozen=True, slots=True)
class _FomodRequest:
    draft: FomodTaskDraft
    rules: object


def build_operation_plan_facade(
    runtime: AppRuntime,
    runtime_context: RequestContext | RuntimeContextFactory,
    *,
    dialog_factory=OperationPlanDialog,
) -> OperationPlanFacade:
    """Build all four concrete GUI operation adapters from process services.

    This constructor performs no remote or formal-file side effect.  ParaTranz
    snapshots are fetched only when the user runs preflight; writes and FOMOD
    publication are scheduled only after the shared final confirmation.
    """

    context_factory = _context_factory(runtime_context)
    operation_tasks = OperationTaskAdapter(runtime.tasks)
    sync_cache: dict[int, _PreparedSync] = {}
    fomod_cache: dict[int, object] = {}
    cache_lock = RLock()
    write_preflights = HydratedWritePreflightService()
    fomod_preflights = FomodTaskPreflightService()

    def sync_checks(value: object) -> tuple[PreflightCheckState, ...]:
        if not isinstance(value, _SyncRequest):
            return (_blocked("SYNC_REQUEST_INVALID", "ParaTranz 请求无效"),)
        try:
            service = ParaTranzService.from_config(value.ui_context.config)
            planning = ParaTranzSyncPlanningUseCase(ParaTranzRemoteSnapshotAdapter(service))
            plan = planning.create_plan(
                CreateSyncPlanRequest(
                    value.project_id,
                    value.namespace,
                    value.local_entries,
                    value.operation,
                    ConflictPolicy.ABORT,
                )
            )
        except Exception as exc:  # network/permission boundary becomes a blocked check
            return (_blocked("PARATRANZ_PREFLIGHT_FAILED", f"ParaTranz 预检失败：{type(exc).__name__}"),)
        with cache_lock:
            sync_cache[id(value)] = _PreparedSync(value, planning, plan, service)
            _trim_cache(sync_cache)
        unresolved = int(getattr(plan, "conflicts", 0))
        return (
            PreflightCheckState(
                "PARATRANZ_PLAN_FRESH",
                "远端快照与同步计划",
                PreflightCheckStatus.PASSED if not unresolved else PreflightCheckStatus.BLOCKED,
                "" if not unresolved else f"存在 {unresolved} 个未解决冲突",
            ),
        )

    def write_checks(value: object) -> tuple[PreflightCheckState, ...]:
        if not isinstance(value, HydratedWriteDraft):
            return (_blocked("WRITE_REQUEST_INVALID", "写回请求无效"),)
        checked = write_preflights.preflight(value)
        return tuple(
            PreflightCheckState(
                item.code,
                item.code.replace("_", " "),
                (
                    PreflightCheckStatus.WARNING
                    if item.warning and not item.passed
                    else PreflightCheckStatus.PASSED
                    if item.passed
                    else PreflightCheckStatus.BLOCKED
                ),
                "" if item.passed else item.message,
            )
            for item in checked.checks
        )

    def fomod_checks(value: object) -> tuple[PreflightCheckState, ...]:
        if not isinstance(value, _FomodRequest):
            return (_blocked("FOMOD_REQUEST_INVALID", "FOMOD 请求无效"),)
        checked = fomod_preflights.preflight(value.draft)
        with cache_lock:
            fomod_cache[id(value)] = checked
            _trim_cache(fomod_cache)
        checks = [
            PreflightCheckState(
                "FOMOD_TYPED_PREFLIGHT",
                "归档、路径和发布预算",
                PreflightCheckStatus.PASSED if checked.ready else PreflightCheckStatus.BLOCKED,
                "" if checked.ready else "、".join(checked.diagnostics),
            )
        ]
        checks.extend(
            PreflightCheckState(code, "FOMOD 预检提示", PreflightCheckStatus.WARNING, code) for code in checked.warnings
        )
        return tuple(checks)

    def create_sync(kind: OperationKind, context, batch: bool, _values):
        request, ready, reason = _sync_request(context, kind, batch)
        entries = request.local_entries
        locked = sum(item.stage == 9 for item in entries)
        hidden = sum(item.stage == -1 for item in entries)
        config = getattr(context, "config", None)
        credentials = bool(config is not None and getattr(config, "token", None))
        permission = bool(request.project_id and getattr(context, "is_member", lambda: True)())
        local_hash = _local_hash(entries)
        return OperationPlanDraft(
            request=request,
            target=f"ParaTranz 项目 {request.project_id}",
            target_revision="remote:preflight-required",
            input_fingerprint=local_hash,
            scope_summary=f"{len(entries)} 个本地对象" + ("（批量）" if batch else ""),
            mode_summary="上传本地变更" if kind is OperationKind.UPLOAD else "下载并原子合并",
            conflict_summary="遇到冲突时停止",
            backup_summary="远端版本/本地快照用于回滚证据",
            estimated_impact=(("objects", len(entries)),),
            credentials_ready=credentials,
            permission_ready=permission,
            input_ready=ready,
            output_ready=ready,
            locked_count=locked,
            hidden_count=hidden,
            dirty_target=kind is OperationKind.DOWNLOAD and bool(getattr(context, "dirty", False)),
            warnings=(() if ready else (reason,)),
            expected_side_effects=(
                "对 ParaTranz 执行幂等远端写入" if kind is OperationKind.UPLOAD else "在本地聚合事务中合并远端快照",
            ),
        )

    def submit_sync(draft, _preflight, owner: OwnerRef, tasks: TaskRuntime):
        request = _operation_request(draft, _SyncRequest)
        with cache_lock:
            prepared = sync_cache.pop(id(request), None)
        if prepared is None:
            raise OperationCompositionError("ParaTranz 计划缺少当前预检；请返回并重新预检")
        current = _local_snapshots(request.ui_context, request.project_id)
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
        local_uow = CallbackLocalSyncUnitOfWork(
            lambda: _local_snapshots(request.ui_context, request.project_id),
            lambda values: _replace_local_snapshots(request.ui_context, values, request.project_id),
        )
        snapshots = ParaTranzRemoteSnapshotAdapter(prepared.service)
        executor = ParaTranzSyncExecutor(prepared.service, snapshots, local_uow)
        entrypoint = ParaTranzSyncTaskEntrypoint(tasks, executor)
        return entrypoint.submit(
            ParaTranzSyncTaskDraft(
                authorized,
                request.project_id,
                request.namespace,
                current,
            ),
            owner,
        )

    def create_write(context, batch: bool, values):
        if batch:
            raise OperationCompositionError("批量写回需要逐槽位生成独立写回计划，目前未开放")
        slot = getattr(context, "active_slot", None)
        if slot is None or slot.source_snapshot is None or slot.format_id is None:
            raise OperationCompositionError("当前集合没有 S04 hydration source_snapshot/format_id")
        collection = slot.collection
        entries = tuple(item.snapshot() for item in collection)
        source = Path(slot.esp_path or slot.source_snapshot.source.uri)
        target = str(values.get("target_path") or source.with_name(f"{source.stem}_translated{source.suffix}"))
        request_context = context_factory(context)
        overwrite = Path(target).exists() and bool(values.get("overwrite_confirmed", False))
        request = HydratedWriteDraft(
            slot.source_snapshot,
            slot.format_id,
            entries,
            target,
            collection.collection_revision.value,
            request_context,
            conflict_policy=(PublishConflictPolicy.EXPLICIT_OVERWRITE if overwrite else PublishConflictPolicy.FAIL),
            backup_policy=BackupPolicy.REQUIRED_IF_EXISTS,
        )
        return _write_plan(request)

    def edit_write(draft, fields):
        request = _operation_request(draft, HydratedWriteDraft)
        edits = dict(fields)
        target = edits.get("target_path", request.target_path).strip()
        overwrite = edits.get("overwrite_confirmed", "false").strip().casefold() in {"1", "true", "yes", "是"}
        edited = replace(
            request,
            target_path=target,
            conflict_policy=(PublishConflictPolicy.EXPLICIT_OVERWRITE if overwrite else PublishConflictPolicy.FAIL),
        )
        return _write_plan(edited)

    def submit_write(draft, preflight, owner: OwnerRef, tasks: TaskRuntime):
        request = _operation_request(draft, HydratedWriteDraft)
        checked = write_preflights.preflight(request)
        if not checked.ready:
            raise OperationCompositionError("写回预检在提交前已失效")
        workload = HydratedWriteWorkload(checked)
        return operation_tasks.submit(
            OperationTaskRequest(
                OperationKind.WRITE,
                preflight.request_digest,
                request.source_snapshot.sha256,
                "写回翻译文件",
                workload,
                True,
                (request.target_path,),
            ),
            owner,
        )

    def create_fomod(_context, _batch: bool, values):
        draft = values.get("draft")
        rules = values.get("rules")
        if not isinstance(draft, FomodTaskDraft) or rules is None:
            raise OperationCompositionError("FOMOD 计划缺少 typed draft 或过滤规则")
        request = _FomodRequest(draft, rules)
        return _fomod_plan(request)

    def edit_fomod(draft, fields):
        request = _operation_request(draft, _FomodRequest)
        edits = dict(fields)
        edited = replace(
            request.draft,
            output_archive=edits.get("output_archive", request.draft.output_archive).strip(),
            overwrite_confirmed=edits.get("overwrite_confirmed", "false").strip().casefold()
            in {"1", "true", "yes", "是"},
        )
        return _fomod_plan(_FomodRequest(edited, request.rules))

    def submit_fomod(draft, _preflight, owner: OwnerRef, tasks: TaskRuntime):
        request = _operation_request(draft, _FomodRequest)
        with cache_lock:
            checked = fomod_cache.pop(id(request), None)
        if checked is None:
            checked = fomod_preflights.preflight(request.draft)
        if not checked.ready:
            raise OperationCompositionError("FOMOD 预检在提交前已失效")
        return FomodTaskEntrypoint(tasks, _fomod_engine_factory(request.rules)).submit(checked, owner)

    def supports_sync(context, batch: bool) -> bool:
        if batch or getattr(context, "collection", None) is None:
            return False
        project = getattr(context, "paratranz_project_id", None) or getattr(context, "current_project", None)
        return bool(project)

    def supports_write(context, batch: bool) -> bool:
        slot = getattr(context, "active_slot", None)
        return bool(
            not batch
            and slot is not None
            and slot.source_snapshot is not None
            and slot.format_id is not None
            and slot.collection is not None
        )

    features = (
        OperationFeatureAdapter(
            OperationKind.UPLOAD,
            UploadOperationMapper(sync_checks),
            lambda context, batch, values: create_sync(OperationKind.UPLOAD, context, batch, values),
            submit_sync,
            capability=supports_sync,
        ),
        OperationFeatureAdapter(
            OperationKind.DOWNLOAD,
            DownloadOperationMapper(sync_checks),
            lambda context, batch, values: create_sync(OperationKind.DOWNLOAD, context, batch, values),
            submit_sync,
            capability=supports_sync,
        ),
        OperationFeatureAdapter(
            OperationKind.WRITE,
            WriteOperationMapper(write_checks),
            create_write,
            submit_write,
            edit_write,
            supports_write,
        ),
        OperationFeatureAdapter(
            OperationKind.FOMOD,
            FomodOperationMapper(fomod_checks),
            create_fomod,
            submit_fomod,
            edit_fomod,
            lambda _context, batch: not batch,
        ),
    )
    return OperationPlanFacade(runtime, context_factory, features, dialog_factory=dialog_factory)


def _write_plan(request: HydratedWriteDraft) -> OperationPlanDraft:
    target_exists = Path(request.target_path).exists()
    stages = tuple(item.stage for item in request.entries)
    return OperationPlanDraft(
        request=request,
        target=request.target_path,
        target_revision="existing" if target_exists else "missing",
        input_fingerprint=request.source_snapshot.sha256,
        scope_summary=f"{len(request.entries)} 个 hydration 条目",
        mode_summary=f"{request.format_id.value} staging → validate → atomic commit",
        conflict_summary=request.conflict_policy.value,
        backup_summary=request.backup_policy.value,
        estimated_impact=(("objects", len(request.entries)), ("files", 1)),
        editable_fields=(
            EditableFieldState("target_path", "输出路径", request.target_path, required=True),
            EditableFieldState(
                "overwrite_confirmed",
                "确认覆盖（true/false）",
                "true" if request.conflict_policy is PublishConflictPolicy.EXPLICIT_OVERWRITE else "false",
            ),
        ),
        locked_count=sum(stage == 9 for stage in stages),
        hidden_count=sum(stage == -1 for stage in stages),
        overwrite_risk=target_exists,
        overwrite_confirmed=request.conflict_policy is PublishConflictPolicy.EXPLICIT_OVERWRITE,
        backup_required=target_exists,
        backup_enabled=request.backup_policy is not BackupPolicy.NONE,
        expected_side_effects=("验证通过后原子替换一个正式输出文件",),
    )


def _fomod_plan(request: _FomodRequest) -> OperationPlanDraft:
    target = Path(request.draft.output_archive)
    return OperationPlanDraft(
        request=request,
        target=str(target),
        target_revision="existing" if target.exists() else "missing",
        input_fingerprint=hashlib.sha256(request.draft.new_archive.encode()).hexdigest(),
        scope_summary="新版归档" + (" + 旧版归档" if request.draft.old_archive else ""),
        mode_summary=f"typed pipeline / {request.draft.output_format} / {request.draft.target_locale}",
        conflict_summary="覆盖已确认" if request.draft.overwrite_confirmed else "目标存在时停止",
        backup_summary="覆盖时创建校验备份",
        estimated_impact=(("archives", 1 + int(request.draft.old_archive is not None)), ("files", 1)),
        editable_fields=(
            EditableFieldState("output_archive", "输出归档", request.draft.output_archive, required=True),
            EditableFieldState(
                "overwrite_confirmed",
                "确认覆盖（true/false）",
                "true" if request.draft.overwrite_confirmed else "false",
            ),
        ),
        overwrite_risk=target.exists(),
        overwrite_confirmed=request.draft.overwrite_confirmed,
        backup_required=target.exists(),
        backup_enabled=True,
        expected_side_effects=("发布一个经校验的 FOMOD 归档及 manifest",),
    )


def _fomod_engine_factory(rules):
    def build(spec, run_guard, commit_guard):
        del spec
        from transbridge.config.llm import LLMConfig
        from transbridge.fomod.pipeline import FomodPipeline, _LegacyPluginPort, _LegacyXmlPort
        from transbridge.fomod.stages import default_stages

        pipeline = FomodPipeline(rules=rules, llm_config=LLMConfig.load_from_file())
        return PipelineEngine(
            default_stages(
                rules=rules,
                plugin_port=_LegacyPluginPort(pipeline, None),
                xml_port=_LegacyXmlPort(pipeline),
            ),
            run_guard=run_guard,
            commit_guard=commit_guard,
        )

    return build


__all__ = ["OperationCompositionError", "build_operation_plan_facade"]
