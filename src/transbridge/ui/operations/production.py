"""Concrete production feature adapters for the operation-plan facade."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from pathlib import Path
from threading import RLock

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    DomainError,
    ErrorCategory,
    OperationCounts,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
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
from transbridge.application.io.plugin_write import plugin_artifact_paths
from transbridge.application.io.publish import BackupPolicy, ConflictPolicy as PublishConflictPolicy
from transbridge.application.tasks import OwnerRef, TaskRuntime
from transbridge.bootstrap.runtime import AppRuntime

from .errors import OperationCompositionError
from .facade import OperationFeatureAdapter, OperationPlanFacade, RuntimeContextFactory
from .mappers import (
    FomodOperationMapper,
    OperationPlanDraft,
    WriteOperationMapper,
)
from .paratranz_dialog import ParaTranzSyncDialog
from .paratranz_sync import build_paratranz_sync_features
from .plan_dialog import OperationPlanDialog
from .plan_view import EditableFieldState, OperationKind
from .preflight_view import PreflightCheckState, PreflightCheckStatus
from .production_support import (
    blocked as _blocked,
    context_factory as _context_factory,
    operation_request as _operation_request,
    trim_cache as _trim_cache,
)
from .runtime_adapter import OperationTaskAdapter, OperationTaskRequest


@dataclass(frozen=True, slots=True)
class _FomodRequest:
    draft: FomodTaskDraft
    rules: object


@dataclass(frozen=True, slots=True)
class _HydratedWriteBatch:
    drafts: tuple[HydratedWriteDraft, ...]

    def __post_init__(self) -> None:
        if len(self.drafts) < 2:
            raise ValueError("批量写回至少需要两个 hydration 来源")


class _HydratedWriteBatchWorkload:
    supports_cancel = True

    def __init__(self, preflights) -> None:
        self._preflights = tuple(preflights)

    def __call__(self, run_context):
        outcomes = []
        diagnostics = []
        artifacts = []
        succeeded = failed = cancelled = 0
        for checked in self._preflights:
            if bool(getattr(run_context.cancellation, "is_cancelled", False)):
                cancelled += 1
                outcomes.append(_write_object_outcome(checked.draft, "cancelled", "WRITE_CANCELLED"))
                continue
            result = HydratedWriteWorkload(checked)(run_context)
            if result.outcome is OperationOutcome.COMPLETED:
                succeeded += 1
                status = "succeeded"
            elif result.outcome is OperationOutcome.CANCELLED:
                cancelled += 1
                status = "cancelled"
            else:
                failed += 1
                status = "failed"
                diagnostics.extend(result.diagnostics)
            outcomes.append(
                _write_object_outcome(
                    checked.draft,
                    status,
                    result.diagnostics[0].code if result.diagnostics else result.outcome.value,
                )
            )
            artifacts.extend(result.artifact_refs)
        value = {"outcomes": tuple(outcomes)}
        counts = OperationCounts(succeeded=succeeded, failed=failed, cancelled=cancelled)
        if failed == 0 and cancelled == 0:
            return OperationResult.completed(
                value,
                counts=counts,
                artifact_refs=tuple(artifacts),
                run_id=run_context.ref.run_id,
            )
        if succeeded:
            if not diagnostics:
                diagnostics.append(
                    Diagnostic(
                        "WRITE_BATCH_INCOMPLETE",
                        "批量写回未完成全部来源。",
                        severity=DiagnosticSeverity.ERROR,
                        category=ErrorCategory.CANCELLED if cancelled else ErrorCategory.EXTERNAL,
                    )
                )
            return OperationResult.partial(
                value,
                counts=counts,
                diagnostics=tuple(diagnostics),
                artifact_refs=tuple(artifacts),
                run_id=run_context.ref.run_id,
            )
        if cancelled and not failed:
            return OperationResult.cancelled(run_id=run_context.ref.run_id)
        first = diagnostics[0] if diagnostics else None
        return OperationResult.failed(
            DomainError(
                ErrorCategory.EXTERNAL,
                "WRITE_BATCH_FAILED" if first is None else first.code,
                "批量写回没有发布任何来源。" if first is None else first.message,
            ),
            run_id=run_context.ref.run_id,
        )


def build_operation_plan_facade(
    runtime: AppRuntime,
    runtime_context: RequestContext | RuntimeContextFactory,
    *,
    dialog_factory=OperationPlanDialog,
) -> OperationPlanFacade:
    """Build all four concrete GUI operation adapters from process services.

    This constructor performs no remote or formal-file side effect.  ParaTranz
    snapshots are fetched by the ParaTranz dialog's automatic check; writes
    and FOMOD publication are scheduled only after final confirmation.
    """

    context_factory = _context_factory(runtime_context)
    operation_tasks = OperationTaskAdapter(runtime.tasks)
    fomod_cache: dict[int, object] = {}
    write_cache: dict[int, object] = {}
    cache_lock = RLock()
    write_preflights = HydratedWritePreflightService()
    fomod_preflights = FomodTaskPreflightService()

    def write_checks(value: object) -> tuple[PreflightCheckState, ...]:
        if isinstance(value, _HydratedWriteBatch):
            checked_batch = tuple(write_preflights.preflight(item) for item in value.drafts)
            with cache_lock:
                write_cache[id(value)] = checked_batch
                _trim_cache(write_cache)
            checks = []
            for index, checked in enumerate(checked_batch, 1):
                for item in checked.checks:
                    checks.append(
                        PreflightCheckState(
                            f"SOURCE_{index}_{item.code}",
                            f"来源 {index} · {item.code.replace('_', ' ')}",
                            (
                                PreflightCheckStatus.WARNING
                                if item.warning and not item.passed
                                else PreflightCheckStatus.PASSED
                                if item.passed
                                else PreflightCheckStatus.BLOCKED
                            ),
                            "" if item.passed else item.message,
                        )
                    )
            return tuple(checks)
        if not isinstance(value, HydratedWriteDraft):
            return (_blocked("WRITE_REQUEST_INVALID", "写回请求无效"),)
        checked = write_preflights.preflight(value)
        with cache_lock:
            write_cache[id(value)] = checked
            _trim_cache(write_cache)
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

    def create_write(context, batch: bool, values):
        if batch:
            drafts = []
            overwrite = bool(values.get("overwrite_confirmed", False))
            request_context = context_factory(context)
            for slot in getattr(context, "slots", {}).values():
                if slot.source_snapshot is None or slot.format_id is None or slot.collection is None:
                    continue
                source = Path(slot.esp_path or slot.eet_path or slot.xt_path or slot.source_snapshot.source.uri)
                target = source.with_name(f"{source.stem}_translated{source.suffix}")
                drafts.append(
                    HydratedWriteDraft(
                        slot.source_snapshot,
                        slot.format_id,
                        tuple(item.snapshot() for item in slot.collection),
                        str(target),
                        int(getattr(context, "variant_revision", slot.collection.collection_revision.value) or 0),
                        request_context,
                        conflict_policy=(
                            PublishConflictPolicy.EXPLICIT_OVERWRITE if overwrite else PublishConflictPolicy.FAIL
                        ),
                        backup_policy=BackupPolicy.REQUIRED_IF_EXISTS,
                    )
                )
            if len(drafts) < 2:
                raise OperationCompositionError("批量写回至少需要两个具有正式 hydration 快照的来源")
            return _write_batch_plan(_HydratedWriteBatch(tuple(drafts)))
        slot = getattr(context, "active_slot", None)
        if slot is None or slot.source_snapshot is None or slot.format_id is None:
            raise OperationCompositionError("当前集合没有 S04 hydration source_snapshot/format_id")
        collection = slot.collection
        entries = tuple(item.snapshot() for item in collection)
        source = Path(slot.esp_path or slot.source_snapshot.source.uri)
        target = str(values.get("target_path") or source.with_name(f"{source.stem}_translated{source.suffix}"))
        request_context = context_factory(context)
        overwrite = bool(values.get("overwrite_confirmed", False))
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
        if isinstance(draft, OperationPlanDraft) and isinstance(draft.request, _HydratedWriteBatch):
            edits = dict(fields)
            overwrite = edits.get("overwrite_confirmed", "false").strip().casefold() in {
                "1",
                "true",
                "yes",
                "是",
            }
            request = _HydratedWriteBatch(
                tuple(
                    replace(
                        item,
                        conflict_policy=(
                            PublishConflictPolicy.EXPLICIT_OVERWRITE if overwrite else PublishConflictPolicy.FAIL
                        ),
                    )
                    for item in draft.request.drafts
                )
            )
            return _write_batch_plan(request)
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
        if isinstance(draft, OperationPlanDraft) and isinstance(draft.request, _HydratedWriteBatch):
            request = draft.request
            with cache_lock:
                checked_batch = write_cache.pop(id(request), None)
            current = tuple(write_preflights.preflight(item) for item in request.drafts)
            if (
                checked_batch is None
                or any(not item.ready for item in current)
                or tuple(item.artifact_fingerprints for item in current)
                != tuple(item.artifact_fingerprints for item in checked_batch)
            ):
                raise OperationCompositionError("批量写回预检在提交前已失效")
            return operation_tasks.submit(
                OperationTaskRequest(
                    OperationKind.WRITE,
                    preflight.request_digest,
                    ":".join(item.source_snapshot.sha256 for item in request.drafts),
                    f"批量写回 {len(request.drafts)} 个来源",
                    _HydratedWriteBatchWorkload(current),
                    True,
                    tuple(item.target_path for item in request.drafts),
                ),
                owner,
            )
        request = _operation_request(draft, HydratedWriteDraft)
        with cache_lock:
            checked = write_cache.pop(id(request), None)
        current = write_preflights.preflight(request)
        if checked is None or not current.ready or current.artifact_fingerprints != checked.artifact_fingerprints:
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

    def supports_write(context, batch: bool) -> bool:
        if batch:
            return (
                sum(
                    slot.source_snapshot is not None and slot.format_id is not None and slot.collection is not None
                    for slot in getattr(context, "slots", {}).values()
                )
                >= 2
            )
        slot = getattr(context, "active_slot", None)
        return bool(
            not batch
            and slot is not None
            and slot.source_snapshot is not None
            and slot.format_id is not None
            and slot.collection is not None
        )

    def discard_write(draft) -> None:
        if isinstance(draft, OperationPlanDraft) and isinstance(draft.request, _HydratedWriteBatch):
            request = draft.request
        else:
            request = _operation_request(draft, HydratedWriteDraft)
        with cache_lock:
            write_cache.pop(id(request), None)

    features = (
        *build_paratranz_sync_features(runtime),
        OperationFeatureAdapter(
            OperationKind.WRITE,
            WriteOperationMapper(write_checks),
            create_write,
            submit_write,
            edit_write,
            supports_write,
            discard_write,
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
    dialog_factories = None
    if dialog_factory is OperationPlanDialog:
        dialog_factories = {
            OperationKind.UPLOAD: ParaTranzSyncDialog,
            OperationKind.DOWNLOAD: ParaTranzSyncDialog,
        }
    return OperationPlanFacade(
        runtime,
        context_factory,
        features,
        dialog_factory=dialog_factory,
        dialog_factories=dialog_factories,
    )


def _write_plan(request: HydratedWriteDraft) -> OperationPlanDraft:
    artifact_paths = plugin_artifact_paths(request.source_snapshot, request.target_path, request.options)
    target_exists = any(Path(path).exists() for path in artifact_paths)
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
        estimated_impact=(("objects", len(request.entries)), ("files", len(artifact_paths))),
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
        expected_side_effects=(
            "验证通过后提交插件及全部 Strings；提交异常时恢复原有输出"
            if len(artifact_paths) > 1
            else "验证通过后原子替换一个正式输出文件",
        ),
    )


def _write_batch_plan(request: _HydratedWriteBatch) -> OperationPlanDraft:
    targets = tuple(item.target_path for item in request.drafts)
    existing = tuple(path for path in targets if Path(path).exists())
    entries = tuple(entry for item in request.drafts for entry in item.entries)
    overwrite = all(item.conflict_policy is PublishConflictPolicy.EXPLICIT_OVERWRITE for item in request.drafts)
    fingerprint = hashlib.sha256(
        "\0".join(f"{item.source_snapshot.sha256}:{item.variant_revision}" for item in request.drafts).encode()
    ).hexdigest()
    return OperationPlanDraft(
        request=request,
        target="；".join(targets),
        target_revision=f"{len(existing)} existing / {len(targets) - len(existing)} missing",
        input_fingerprint=fingerprint,
        scope_summary=f"{len(request.drafts)} 个 hydration 来源，{len(entries)} 个条目",
        mode_summary="逐来源 staging → validate → atomic commit",
        conflict_summary="全部覆盖已确认" if overwrite else "任一目标存在时停止并要求确认",
        backup_summary="每个被覆盖的来源都创建校验备份",
        estimated_impact=(("objects", len(entries)), ("files", len(targets))),
        editable_fields=(
            EditableFieldState(
                "overwrite_confirmed",
                "确认覆盖所有已存在目标（true/false）",
                "true" if overwrite else "false",
            ),
        ),
        locked_count=sum(getattr(item, "stage", 0) == 9 for item in entries),
        hidden_count=sum(getattr(item, "stage", 0) == -1 for item in entries),
        overwrite_risk=bool(existing),
        overwrite_confirmed=overwrite,
        backup_required=bool(existing),
        backup_enabled=True,
        expected_side_effects=("所有来源先通过预检，再按来源原子发布并返回逐来源结果",),
    )


def _write_object_outcome(draft: HydratedWriteDraft, status: str, code: str) -> dict[str, object]:
    return {
        "object_ref": draft.target_path,
        "label": Path(draft.target_path).name,
        "status": status,
        "code": code,
        "retryable": False,
    }


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
