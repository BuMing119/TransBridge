"""Production terminology commands and TaskRuntime workload handlers."""

from __future__ import annotations

from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.terminology.build_drafts import decisions_from_build
from transbridge.application.terminology.identity import build_key, canonical_digest
from transbridge.application.terminology.input_capture import BuildInputSnapshot
from transbridge.application.terminology.models import (
    BuildResultRef,
    TerminologyVersionRef,
)
from transbridge.application.terminology.renderers import ArtifactPublishPolicy
from transbridge.application.terminology.renderers.changelog_excel import ChangeLogExcelRenderer
from transbridge.application.terminology.renderers.changelog_markdown import ChangeLogMarkdownRenderer
from transbridge.application.terminology.renderers.quality_excel import QualityExcelRenderer
from transbridge.application.terminology.report_queries import TerminologyReportQueryService
from transbridge.application.terminology.reports import NoDraftIdentity, TerminologyReportSnapshotFactory
from transbridge.application.terminology.runtime import TerminologyExecutionContext, TerminologyWorkloadExecution
from transbridge.application.terminology.workloads import (
    BuildWorkloadRequest,
    ChangelogRenderWorkloadRequest,
    HistoryCompareWorkloadRequest,
    ReportRenderWorkloadRequest,
    TerminologyPhase,
    TerminologyProgress,
    TerminologyWorkloadResult,
    TerminologyWorkloadType,
)

from .terminology_build import BuildRunner
from .terminology_context import require_line as _line, task_owner as _owner
from .terminology_draft_access import LifecycleDraftLineReader as _LifecycleDraftLineReader  # noqa: F401
from .terminology_publication import PublishRunner, TerminologyPublicationCommands
from .terminology_review import (
    RequestManualActor as _RequestManualActor,  # noqa: F401
    TerminologyReviewCommands,
)
from .terminology_storage import empty_digest, expected_state


class ProductionTerminologyCommands:
    def __init__(self, entrypoint, repositories, paths, state, ids, clock, build_inputs, lifecycle) -> None:
        self._entrypoint = entrypoint
        self._repositories = repositories
        self._paths = paths
        self._state = state
        self._ids = ids
        self._clock = clock
        self._build_inputs = build_inputs
        self._lifecycle = lifecycle
        self._publication = TerminologyPublicationCommands(entrypoint, repositories, state, ids, clock, build_inputs)
        self._review = TerminologyReviewCommands(repositories, ids, clock, lifecycle, build_inputs)

    def start_build(self, snapshot: BuildInputSnapshot, context: RequestContext) -> JobRef:
        expected = expected_state(snapshot, self._repositories.for_project(snapshot.project_id))
        key = build_key(snapshot)
        self._state.put(self._state.snapshots, expected.digest, snapshot)
        self._state.put(self._state.snapshots, key, snapshot)
        self._state.put(self._state.contexts, key, context)
        request = BuildWorkloadRequest(
            project_id=snapshot.project_id,
            variant_id=snapshot.variant_id,
            expected=expected,
            build_key=key,
            config_digest=snapshot.config_digest,
            input_snapshot=snapshot,
        )
        return self._entrypoint.submit(request, _owner(context)).ref

    def preview_publish(self, context: RequestContext, config=None):
        return self._publication.preview_publish(context, config)

    def publish_preview(self, preview, context: RequestContext) -> JobRef:
        return self._publication.publish_preview(preview, context)

    def publish(self, context: RequestContext, config=None) -> JobRef:
        return self._publication.publish(context, config)

    def render_report(self, context: RequestContext, config=None) -> JobRef:
        project_id, variant_id = _line(context)
        repository = self._repositories.for_project(project_id)
        build_ref = self._latest_build_ref(repository, project_id, variant_id)
        snapshot = self._fresh_snapshot(context, config)
        expected = expected_state(snapshot, repository)
        target = self._paths.artifact(project_id, self._ids.new_id(), ".xlsx")
        payload = {"build_ref": build_ref, "target": target}
        digest = canonical_digest(
            {"build_ref": build_ref, "target": str(target)},
            namespace="terminology.production-report-request.v1",
        )
        self._state.put(self._state.report_payloads, digest, payload)
        self._state.put(self._state.snapshots, expected.digest, snapshot)
        request = ReportRenderWorkloadRequest(
            project_id=project_id,
            variant_id=variant_id,
            expected=expected,
            report_snapshot_ref=build_ref.build_key,
            report_snapshot_digest=digest,
        )
        return self._entrypoint.submit(request, _owner(context)).ref

    def render_changelog(self, context: RequestContext, config=None) -> JobRef:
        return self._render_changelog(context, config=config, overwrite=False)

    def retry_changelog(self, context: RequestContext, config=None) -> JobRef:
        return self._render_changelog(context, config=config, overwrite=True)

    def apply_decision(self, operation, context: RequestContext, **values):
        return self._review.apply_decision(operation, context, **values)

    def prepare_draft(self, context: RequestContext, config=None):
        return self._review.prepare_draft(context, config)

    def edit_term(self, term_id, context: RequestContext, **values):
        return self._review.edit_term(term_id, context, **values)

    def batch_terms(self, term_ids, operation, context: RequestContext, **values):
        return self._review.batch_terms(term_ids, operation, context, **values)

    def resolve_conflict(self, conflict, operation, context: RequestContext, **values):
        return self._review.resolve_conflict(conflict, operation, context, **values)

    def conflict_details(self, conflict, context: RequestContext):
        return self._review.conflict_details(conflict, context)

    def resolve_review(self, details, operation, context: RequestContext, **values):
        return self._review.resolve_review(details, operation, context, **values)

    def discard_draft(self, expected_ref, context: RequestContext) -> None:
        self._review.discard_draft(expected_ref, context)

    def restore(self, version_ref: TerminologyVersionRef, context: RequestContext, config=None) -> JobRef:
        return self._publication.restore(version_ref, context, config)

    def compare(self, version_ref: TerminologyVersionRef, context: RequestContext, config=None) -> JobRef:
        """Compare a history version with current effective content in TaskRuntime."""

        project_id, variant_id = _line(context)
        if (version_ref.project_id, version_ref.variant_id) != (project_id, variant_id):
            raise PermissionError("历史版本不属于当前工程和翻译版本")
        repository = self._repositories.for_project(project_id)
        current_ref = repository.effective_version_ref(project_id, variant_id)
        if current_ref is None:
            raise RuntimeError("当前翻译版本还没有已发布术语库")
        snapshot = self._fresh_snapshot(context, config)
        expected = expected_state(snapshot, repository)
        payload = {"historical": version_ref, "current": current_ref}
        digest = canonical_digest(payload, namespace="terminology.production-compare-request.v1")
        self._state.put(self._state.compare_payloads, digest, payload)
        self._state.put(self._state.snapshots, expected.digest, snapshot)
        request = HistoryCompareWorkloadRequest(
            project_id=project_id,
            variant_id=variant_id,
            expected=expected,
            version_ref=version_ref.version_id,
            compare_digest=digest,
        )
        return self._entrypoint.submit(request, _owner(context)).ref

    def latest_comparison(self, project_id: str, variant_id: str):
        return self._state.latest_comparisons.get((project_id, variant_id))

    def task_failure(self, run_id: str, context: RequestContext) -> str | None:
        return self._entrypoint.failure(run_id, _owner(context))

    def task_result(self, run_id: str, context: RequestContext):
        return self._entrypoint.result_for_run(run_id, _owner(context))

    def _render_changelog(self, context: RequestContext, *, overwrite: bool, config=None) -> JobRef:
        project_id, variant_id = _line(context)
        repository = self._repositories.for_project(project_id)
        effective = repository.effective_version(project_id, variant_id)
        if effective is None or effective.changelog_ref is None:
            raise RuntimeError("当前翻译版本还没有可导出的术语更新日志")
        snapshot = self._fresh_snapshot(context, config)
        expected = expected_state(snapshot, repository)
        base = self._paths.artifact_directory(project_id) / effective.ref.version_id
        payload = {
            "ref": effective.changelog_ref,
            "markdown": base.with_suffix(".md"),
            "excel": base.with_suffix(".xlsx"),
            "overwrite": overwrite,
        }
        digest = canonical_digest(
            {
                "ref": effective.changelog_ref,
                "markdown": str(payload["markdown"]),
                "excel": str(payload["excel"]),
                "overwrite": overwrite,
            },
            namespace="terminology.production-changelog-request.v1",
        )
        self._state.put(self._state.changelog_payloads, digest, payload)
        self._state.put(self._state.snapshots, expected.digest, snapshot)
        request = ChangelogRenderWorkloadRequest(
            project_id=project_id,
            variant_id=variant_id,
            expected=expected,
            changelog_document_ref=effective.changelog_ref.document_id,
            changelog_document_digest=digest,
        )
        return self._entrypoint.submit(request, _owner(context)).ref

    def latest_build_ref(self, project_id: str, variant_id: str) -> BuildResultRef | None:
        build = self._repositories.for_project(project_id).latest_build(project_id, variant_id)
        return None if build is None else build.ref

    def latest_build_result(self, project_id: str, variant_id: str):
        return self._repositories.for_project(project_id).latest_build(project_id, variant_id)

    def latest_version_ref(self, project_id: str, variant_id: str):
        effective = self._repositories.for_project(project_id).effective_version(project_id, variant_id)
        return None if effective is None else effective.ref

    def active_draft(self, context: RequestContext):
        return self._review.active_draft(context)

    def review_counts(self, context: RequestContext):
        return self._review.review_counts(context)

    def _fresh_snapshot(self, context: RequestContext, config=None) -> BuildInputSnapshot:
        result = self._build_inputs.capture_build_input(context, config={} if config is None else config)
        if result.value is None:
            diagnostic = result.diagnostics[0] if result.diagnostics else None
            raise RuntimeError("无法确认当前工程术语状态" if diagnostic is None else diagnostic.message)
        return result.value

    @staticmethod
    def _latest_build_ref(repository, project_id: str, variant_id: str) -> BuildResultRef:
        build = repository.latest_build(project_id, variant_id)
        if build is None:
            raise RuntimeError("当前翻译版本还没有术语构建结果")
        return build.ref


class ReportRunner:
    def __init__(self, repositories, state) -> None:
        self._repositories = repositories
        self._state = state

    def __call__(self, request, runtime: TerminologyExecutionContext) -> TerminologyWorkloadExecution:
        if not isinstance(request, ReportRenderWorkloadRequest):
            raise TypeError("report runner received another workload")
        payload = self._state.get(self._state.report_payloads, request.report_snapshot_digest)
        repository = self._repositories.for_project(request.project_id)
        build = repository.get_build(payload["build_ref"])
        draft = repository.active_draft(request.project_id, request.variant_id)
        effective = repository.effective_version(request.project_id, request.variant_id)
        factory = TerminologyReportSnapshotFactory(repository)
        if draft is None:
            report = factory.freeze(
                build.ref,
                no_draft=NoDraftIdentity(
                    request.project_id,
                    request.variant_id,
                    None if effective is None else effective.ref.version_id,
                    empty_digest() if effective is None else effective.ref.content_digest,
                ),
                terms=decisions_from_build(build),
            )
        else:
            report = factory.freeze(build.ref, draft=draft)
        runtime.progress.update(TerminologyProgress(TerminologyPhase.RENDER, 1, 2))

        def mutation() -> None:
            ref = repository.put_report_snapshot(report)
            QualityExcelRenderer(TerminologyReportQueryService(repository)).render(ref, payload["target"])
            self._state.put(self._state.latest_reports, (request.project_id, request.variant_id), ref)

        return TerminologyWorkloadExecution(
            TerminologyWorkloadResult(TerminologyWorkloadType.REPORT_RENDER, output_ref=str(payload["target"])),
            mutation,
        )


class ChangelogRunner:
    def __init__(self, repositories, state) -> None:
        self._repositories = repositories
        self._state = state

    def __call__(self, request, runtime: TerminologyExecutionContext) -> TerminologyWorkloadExecution:
        if not isinstance(request, ChangelogRenderWorkloadRequest):
            raise TypeError("changelog runner received another workload")
        payload = self._state.get(self._state.changelog_payloads, request.changelog_document_digest)
        repository = self._repositories.for_project(request.project_id)
        policy = ArtifactPublishPolicy.OVERWRITE if payload["overwrite"] else ArtifactPublishPolicy.FAIL_IF_EXISTS
        runtime.progress.update(TerminologyProgress(TerminologyPhase.RENDER, 1, 2))

        def mutation() -> None:
            ChangeLogMarkdownRenderer(repository.changelogs).render(payload["ref"], payload["markdown"], policy=policy)
            ChangeLogExcelRenderer(repository.changelogs).render(payload["ref"], payload["excel"], policy=policy)

        return TerminologyWorkloadExecution(
            TerminologyWorkloadResult(TerminologyWorkloadType.CHANGELOG_RENDER, output_ref=str(payload["markdown"])),
            mutation,
        )


class HistoryCompareRunner:
    def __init__(self, repositories, state) -> None:
        self._repositories = repositories
        self._state = state

    def __call__(self, request, runtime: TerminologyExecutionContext) -> TerminologyWorkloadExecution:
        if not isinstance(request, HistoryCompareWorkloadRequest):
            raise TypeError("history compare runner received another workload")
        from transbridge.application.terminology.diff import CanonicalDiffEngine

        payload = self._state.get(self._state.compare_payloads, request.compare_digest)
        repository = self._repositories.for_project(request.project_id)
        historical_ref = payload["historical"]
        current_ref = payload["current"]
        runtime.progress.update(TerminologyProgress(TerminologyPhase.VALIDATE, 1, 2))
        comparison = repository.direct_canonical_diff(historical_ref, current_ref)
        if comparison is None:
            historical = repository.get_version(historical_ref)
            current = repository.get_version(current_ref)
            comparison = CanonicalDiffEngine().compare(
                historical,
                target_version_id=current.ref.version_id,
                decisions=current.decisions,
                conflicts=current.conflicts,
                manual_actions=current.manual_actions,
            )
        runtime.checkpoint()

        def mutation() -> None:
            self._state.put(
                self._state.latest_comparisons,
                (request.project_id, request.variant_id),
                comparison,
            )

        runtime.progress.update(TerminologyProgress(TerminologyPhase.FINALIZE, 2, 2))
        return TerminologyWorkloadExecution(
            TerminologyWorkloadResult(TerminologyWorkloadType.HISTORY_COMPARE, output_ref=comparison.content_digest),
            mutation,
        )


__all__ = [
    "BuildRunner",
    "ChangelogRunner",
    "HistoryCompareRunner",
    "ProductionTerminologyCommands",
    "PublishRunner",
    "ReportRunner",
]
