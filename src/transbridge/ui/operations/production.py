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
    HydratedWritePreflightService,
    HydratedWriteWorkload as HydratedWriteWorkload,
)
from transbridge.application.tasks import OwnerRef, TaskRuntime
from transbridge.application.terminology_profiles import TerminologyProfileWriteProjectionSource
from transbridge.bootstrap.runtime import AppRuntime
from transbridge.config.language_profiles import discover_language_profiles

from .batch_download_session import open_batch_download
from .categorized_upload_bridge import open_file_upload
from .errors import OperationCompositionError
from .execution_dialog import FomodPlanDialog
from .facade import OperationFeatureAdapter, OperationPlanFacade, RuntimeContextFactory
from .fomod_result import fomod_check_message, fomod_operation_result
from .mappers import (
    FomodOperationMapper,
    OperationPlanDraft,
)
from .paratranz_dialog import ParaTranzSyncDialog
from .paratranz_sync import build_paratranz_sync_features
from .plan_dialog import OperationPlanDialog
from .plan_view import EditableControl, EditableFieldState, OperationKind
from .preflight_view import PreflightCheckState, PreflightCheckStatus
from .production_support import (
    blocked as _blocked,
    context_factory as _context_factory,
    operation_request as _operation_request,
    trim_cache as _trim_cache,
)
from .runtime_adapter import OperationTaskAdapter
from .write_dialog import WritePlanDialog
from .write_feature import build_write_feature


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
    snapshots are fetched by the ParaTranz dialog's automatic check; writes
    and FOMOD publication are scheduled only after final confirmation.
    """

    context_factory = _context_factory(runtime_context)
    operation_tasks = OperationTaskAdapter(runtime.tasks)
    fomod_cache: dict[int, object] = {}
    retained_results: dict[str, object] = {}
    cache_lock = RLock()
    facade = None
    profile_projection = None
    use_cases = getattr(runtime, "use_cases", None)
    if use_cases is not None and "terminology_profile_service_factory" in use_cases.names():
        profile_services = use_cases.resolve("terminology_profile_service_factory")
        profile_projection = TerminologyProfileWriteProjectionSource(
            profile_services.profile_service_for,
            base_snapshot_for=profile_services.base_terminology_snapshot,
        )
    write_preflights = HydratedWritePreflightService(entry_projection=profile_projection)
    fomod_preflights = FomodTaskPreflightService()

    def retain_result(run_id, result):
        with cache_lock:
            retained_results[run_id] = result
            while len(retained_results) > 100:
                retained_results.pop(next(iter(retained_results)))
        if facade is not None:
            facade.notify_result_ready(run_id)

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
                "归档与输出位置",
                PreflightCheckStatus.PASSED if checked.ready else PreflightCheckStatus.BLOCKED,
                "" if checked.ready else "\n".join(fomod_check_message(code) for code in checked.diagnostics),
            )
        ]
        checks.extend(
            PreflightCheckState(code, "FOMOD 预检提示", PreflightCheckStatus.WARNING, code) for code in checked.warnings
        )
        return tuple(checks)

    def create_fomod(_context, _batch: bool, values):
        draft = values.get("draft")
        rules = values.get("rules")
        if not isinstance(draft, FomodTaskDraft) or rules is None:
            raise OperationCompositionError("FOMOD 构建选项不完整，请重新打开构建窗口。")
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

        def on_report(report):
            retain_result(report.run_id, fomod_operation_result(report))

        return FomodTaskEntrypoint(tasks, _fomod_engine_factory(request.rules)).submit(
            checked, owner, on_report=on_report
        )

    def read_result(ref, owner):
        snapshot = runtime.tasks.get(ref, owner)
        if snapshot.specification.job_type in {
            "operation.fomod",
            "operation.paratranz.upload",
            "operation.paratranz.download",
        }:
            with cache_lock:
                return retained_results.get(ref.run_id)
        return operation_tasks.result(ref, owner)

    features = (
        *build_paratranz_sync_features(runtime, on_result=retain_result),
        build_write_feature(context_factory, operation_tasks, write_preflights),
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
            OperationKind.WRITE: WritePlanDialog,
            OperationKind.FOMOD: FomodPlanDialog,
        }
    facade = OperationPlanFacade(
        runtime,
        context_factory,
        features,
        dialog_factory=dialog_factory,
        dialog_factories=dialog_factories,
        execution_result_reader=read_result,
        upload_launcher=open_file_upload if dialog_factory is OperationPlanDialog else None,
        download_launcher=(lambda context: open_batch_download(context, runtime))
        if dialog_factory is OperationPlanDialog
        else None,
    )
    return facade


def _fomod_plan(request: _FomodRequest) -> OperationPlanDraft:
    target = Path(request.draft.output_archive)
    locale = request.draft.target_locale
    language = next((item.display_name for item in discover_language_profiles() if item.locale == locale), locale)
    return OperationPlanDraft(
        request=request,
        target=str(target),
        target_revision="existing" if target.exists() else "missing",
        input_fingerprint=hashlib.sha256(request.draft.new_archive.encode()).hexdigest(),
        scope_summary="新版归档" + (" + 旧版归档" if request.draft.old_archive else ""),
        mode_summary=f"生成 {request.draft.output_format.upper()} · {language}",
        conflict_summary="覆盖已确认" if request.draft.overwrite_confirmed else "目标存在时停止",
        backup_summary="覆盖时创建校验备份",
        estimated_impact=(("archives", 1 + int(request.draft.old_archive is not None)), ("files", 1)),
        editable_fields=(
            EditableFieldState("output_archive", "输出归档", request.draft.output_archive, required=True),
            EditableFieldState(
                "overwrite_confirmed",
                "覆盖确认",
                "true" if request.draft.overwrite_confirmed else "false",
                control=EditableControl.BOOLEAN,
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
