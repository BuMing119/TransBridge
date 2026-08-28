"""Production terminology commands and TaskRuntime workload handlers."""

from __future__ import annotations

from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.io import TranslationIoUseCase
from transbridge.application.tasks import OwnerRef
from transbridge.application.terminology.build import TerminologyFullBuilder, TranslationIoRegisteredSourceParser
from transbridge.application.terminology.conflicts import (
    ConflictResolutionCommand,
    ConflictResolutionOperation,
    ConflictService,
)
from transbridge.application.terminology.decisions import (
    DecisionCommand,
    DecisionOperation,
    DecisionService,
    ManualActor,
)
from transbridge.application.terminology.drafts import DraftLineState, DraftService, DraftWriteExpectation, new_draft
from transbridge.application.terminology.identity import build_key, canonical_digest, term_id
from transbridge.application.terminology.in_memory import InMemoryTerminologyRepository
from transbridge.application.terminology.input_capture import BuildInputSnapshot
from transbridge.application.terminology.models import (
    BuildResultRef,
    ConflictGroup,
    DecisionStatus,
    TermDecision,
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
    BuildCompleteness,
    BuildLlmStatus,
    BuildWorkloadRequest,
    ChangelogRenderWorkloadRequest,
    HistoryCompareWorkloadRequest,
    PublishWorkloadRequest,
    ReportRenderWorkloadRequest,
    TerminologyPhase,
    TerminologyProgress,
    TerminologyWorkloadResult,
    TerminologyWorkloadType,
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
        )
        return self._entrypoint.submit(request, _owner(context)).ref

    def publish(self, context: RequestContext) -> JobRef:
        project_id, variant_id = _line(context)
        repository = self._repositories.for_project(project_id)
        build_ref = self._latest_build_ref(repository, project_id, variant_id)
        snapshot = self._fresh_snapshot(context)
        expected = expected_state(snapshot, repository)
        draft = repository.active_draft(project_id, variant_id)
        if draft is None:
            raise RuntimeError("发布前需要先创建并确认术语草稿")
        version_id = self._ids.new_id()
        payload = {
            "build_ref": build_ref,
            "draft_ref": draft.ref,
            "version_id": version_id,
            "published_at": self._clock.now().isoformat(),
        }
        digest = canonical_digest(payload, namespace="terminology.production-publish-request.v1")
        self._state.put(self._state.publish_payloads, digest, payload)
        self._state.put(self._state.snapshots, expected.digest, snapshot)
        request = PublishWorkloadRequest(
            project_id=project_id,
            variant_id=variant_id,
            expected=expected,
            build_ref=build_ref.build_key,
            publish_digest=digest,
        )
        return self._entrypoint.submit(request, _owner(context)).ref

    def render_report(self, context: RequestContext) -> JobRef:
        project_id, variant_id = _line(context)
        repository = self._repositories.for_project(project_id)
        build_ref = self._latest_build_ref(repository, project_id, variant_id)
        snapshot = self._fresh_snapshot(context)
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

    def render_changelog(self, context: RequestContext) -> JobRef:
        return self._render_changelog(context, overwrite=False)

    def retry_changelog(self, context: RequestContext) -> JobRef:
        return self._render_changelog(context, overwrite=True)

    def apply_decision(
        self,
        operation: DecisionOperation | str,
        context: RequestContext,
        *,
        term_id: str | None = None,
        original: str | None = None,
        translation: str | None = None,
        notes: str | None = None,
        reason: str | None = None,
    ):
        """Apply one short, audited draft transaction on the active line."""

        service, expectation = self._decision_service(context)
        return service.apply(
            DecisionCommand(
                operation=DecisionOperation(operation),
                expectation=expectation,
                term_id=term_id,
                original=original,
                translation=translation,
                notes=notes,
                reason=reason,
            ),
            context,
        )

    def resolve_conflict(
        self,
        conflict: ConflictGroup,
        operation: ConflictResolutionOperation | str,
        context: RequestContext,
        *,
        translation: str | None = None,
        plugin_id: str | None = None,
        notes: str | None = None,
        reason: str | None = None,
    ):
        decisions, expectation = self._decision_service(context)
        return ConflictService(decisions).resolve(
            ConflictResolutionCommand(
                operation=ConflictResolutionOperation(operation),
                conflict=conflict,
                expectation=expectation,
                translation=translation,
                plugin_id=plugin_id,
                notes=notes,
                reason=reason,
            ),
            context,
        )

    def restore(self, version_ref: TerminologyVersionRef, context: RequestContext) -> JobRef:
        """Publish historical content as a new immutable version through TaskRuntime."""

        project_id, variant_id = _line(context)
        if (version_ref.project_id, version_ref.variant_id) != (project_id, variant_id):
            raise PermissionError("历史版本不属于当前工程和翻译版本")
        repository = self._repositories.for_project(project_id)
        repository.get_version(version_ref)
        if repository.active_draft(project_id, variant_id) is not None:
            raise RuntimeError("恢复历史内容前需要先发布或放弃当前草稿")
        build_ref = self._latest_build_ref(repository, project_id, variant_id)
        snapshot = self._fresh_snapshot(context)
        expected = expected_state(snapshot, repository)
        version_id = self._ids.new_id()
        payload = {
            "build_ref": build_ref,
            "rollback_from": version_ref,
            "version_id": version_id,
            "published_at": self._clock.now().isoformat(),
        }
        digest = canonical_digest(payload, namespace="terminology.production-restore-request.v1")
        self._state.put(self._state.publish_payloads, digest, payload)
        self._state.put(self._state.snapshots, expected.digest, snapshot)
        request = PublishWorkloadRequest(
            project_id=project_id,
            variant_id=variant_id,
            expected=expected,
            build_ref=build_ref.build_key,
            publish_digest=digest,
        )
        return self._entrypoint.submit(request, _owner(context)).ref

    def compare(self, version_ref: TerminologyVersionRef, context: RequestContext) -> JobRef:
        """Compare a history version with current effective content in TaskRuntime."""

        project_id, variant_id = _line(context)
        if (version_ref.project_id, version_ref.variant_id) != (project_id, variant_id):
            raise PermissionError("历史版本不属于当前工程和翻译版本")
        repository = self._repositories.for_project(project_id)
        current_ref = repository.effective_version_ref(project_id, variant_id)
        if current_ref is None:
            raise RuntimeError("当前翻译版本还没有已发布术语库")
        snapshot = self._fresh_snapshot(context)
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

    def _render_changelog(self, context: RequestContext, *, overwrite: bool) -> JobRef:
        project_id, variant_id = _line(context)
        repository = self._repositories.for_project(project_id)
        effective = repository.effective_version(project_id, variant_id)
        if effective is None or effective.changelog_ref is None:
            raise RuntimeError("当前翻译版本还没有可导出的术语更新日志")
        snapshot = self._fresh_snapshot(context)
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
        project_id, variant_id = _line(context)
        return self._repositories.for_project(project_id).active_draft(project_id, variant_id)

    def _decision_service(self, context: RequestContext) -> tuple[DecisionService, DraftWriteExpectation]:
        project_id, variant_id = _line(context)
        repository = self._repositories.for_project(project_id)
        active = self._lifecycle.active
        if active is None or active.variant is None:
            raise RuntimeError("人工术语调整需要当前工程和翻译版本")
        if active.project.envelope.identity != project_id or active.variant.ref.identity.value != variant_id:
            raise PermissionError("人工术语调整不属于当前工程和翻译版本")
        effective = repository.effective_version(project_id, variant_id)
        line = DraftLineState(
            project_id,
            variant_id,
            active.variant.revision,
            None if effective is None else effective.ref.version_id,
            empty_digest() if effective is None else effective.ref.content_digest,
        )
        transactions = repository.draft_transactions(_LifecycleDraftLineReader(self._lifecycle, repository))
        drafts = DraftService(transactions, self._ids)
        draft = drafts.active(project_id, variant_id)
        expectation = DraftWriteExpectation.from_draft(draft, line)
        return DecisionService(drafts, _RequestManualActor(), self._clock, self._ids), expectation

    def _fresh_snapshot(self, context: RequestContext) -> BuildInputSnapshot:
        result = self._build_inputs.capture_build_input(context, config={})
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


class BuildRunner:
    def __init__(self, repositories, state, catalog, ids) -> None:
        self._repositories = repositories
        self._state = state
        self._catalog = catalog
        self._ids = ids

    def __call__(self, request, runtime: TerminologyExecutionContext) -> TerminologyWorkloadExecution:
        if not isinstance(request, BuildWorkloadRequest):
            raise TypeError("build runner received another workload")
        snapshot = self._state.get(self._state.snapshots, request.build_key)
        context = self._state.get(self._state.contexts, request.build_key)
        runtime.progress.update(TerminologyProgress(TerminologyPhase.PARSE, 0, len(snapshot.sources)))
        staging = InMemoryTerminologyRepository()
        builder = TerminologyFullBuilder(
            TranslationIoRegisteredSourceParser(TranslationIoUseCase(self._catalog)),
            staging,
        )
        repository = self._repositories.for_project(request.project_id)
        effective = repository.effective_version(request.project_id, request.variant_id)
        outcome = builder.build(
            snapshot,
            context,
            baseline_decisions=() if effective is None else effective.decisions,
            cancellation=runtime.cancellation,
        )
        runtime.checkpoint()
        runtime.progress.update(
            TerminologyProgress(TerminologyPhase.PERSIST, len(snapshot.sources), len(snapshot.sources))
        )

        def mutation() -> None:
            repository.put_build(outcome.result)
            self._state.put(self._state.latest_builds, (request.project_id, request.variant_id), outcome.result.ref)
            ensure_draft(repository, outcome.result, snapshot, self._ids.new_id())

        result = TerminologyWorkloadResult(
            TerminologyWorkloadType.BUILD,
            output_ref=outcome.result.ref.build_key,
            completeness=BuildCompleteness(outcome.result.completeness.value),
            llm_status=BuildLlmStatus(outcome.result.llm_status.value),
            diagnostics=outcome.result.diagnostics,
        )
        return TerminologyWorkloadExecution(result, mutation)


class PublishRunner:
    def __init__(self, repositories, state) -> None:
        self._repositories = repositories
        self._state = state

    def __call__(self, request, runtime: TerminologyExecutionContext) -> TerminologyWorkloadExecution:
        if not isinstance(request, PublishWorkloadRequest):
            raise TypeError("publish runner received another workload")
        payload = self._state.get(self._state.publish_payloads, request.publish_digest)
        runtime.progress.update(TerminologyProgress(TerminologyPhase.VALIDATE, 1, 2))

        def mutation() -> None:
            from transbridge.application.terminology.publish import PublishTerminologyRequest, VersionPublisher

            repository = self._repositories.for_project(request.project_id)
            publisher = VersionPublisher(
                repository.publisher,
                StaticPublishState(request.expected),
                Permit(),
            )
            publisher.publish(
                PublishTerminologyRequest(
                    project_id=request.project_id,
                    variant_id=request.variant_id,
                    expected=request.expected,
                    build_ref=payload["build_ref"],
                    version_id=payload["version_id"],
                    published_at=payload["published_at"],
                    draft_ref=payload.get("draft_ref"),
                    rollback_from=payload.get("rollback_from"),
                )
            )

        runtime.progress.update(TerminologyProgress(TerminologyPhase.PUBLISH, 2, 2))
        return TerminologyWorkloadExecution(
            TerminologyWorkloadResult(TerminologyWorkloadType.PUBLISH, output_ref=payload["version_id"]),
            mutation,
        )


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


def decisions_from_build(build) -> tuple[TermDecision, ...]:
    conflicted = {
        candidate_id
        for group in build.conflicts
        for variant in group.variants
        for candidate_id in variant.candidate_ids
    }
    return tuple(
        TermDecision(
            term_id(build.project_id, build.variant_id, candidate.scope, candidate.original),
            build.project_id,
            build.variant_id,
            candidate.original,
            candidate.normalized_original,
            candidate.translation,
            scope=candidate.scope,
            status=DecisionStatus.ADOPTED,
            evidence_ids=candidate.evidence_ids,
        )
        for candidate in build.candidates
        if candidate.candidate_id not in conflicted
    )


def ensure_draft(repository, build, snapshot, draft_id: str) -> None:
    if repository.active_draft(snapshot.project_id, snapshot.variant_id) is not None:
        return
    effective = repository.effective_version(snapshot.project_id, snapshot.variant_id)
    repository.create_draft(
        new_draft(
            draft_id=draft_id,
            project_id=snapshot.project_id,
            variant_id=snapshot.variant_id,
            base_version_id=None if effective is None else effective.ref.version_id,
            base_content_digest=empty_digest() if effective is None else effective.ref.content_digest,
            decisions=decisions_from_build(build),
        )
    )


class StaticPublishState:
    def __init__(self, expected) -> None:
        self._expected = expected

    def current(self, project_id: str, variant_id: str):
        del project_id, variant_id
        return self._expected


class Permit:
    @staticmethod
    def is_permitted() -> bool:
        return True


class _RequestManualActor:
    @staticmethod
    def resolve(context: RequestContext) -> ManualActor:
        actor = dict(context.metadata).get("manual_actor_id", "").strip()
        if not actor:
            raise PermissionError("人工术语调整需要可信的操作人身份")
        return ManualActor(actor, trusted=True)


class _LifecycleDraftLineReader:
    def __init__(self, lifecycle, repository) -> None:
        self._lifecycle = lifecycle
        self._repository = repository

    def read_line(self, _connection, project_id: str, variant_id: str) -> DraftLineState:
        active = self._lifecycle.active
        if active is None or active.variant is None:
            raise RuntimeError("术语草稿需要当前工程和翻译版本")
        if active.project.envelope.identity != project_id or active.variant.ref.identity.value != variant_id:
            raise PermissionError("术语草稿不属于当前工程和翻译版本")
        effective = self._repository.effective_version(project_id, variant_id)
        return DraftLineState(
            project_id,
            variant_id,
            active.variant.revision,
            None if effective is None else effective.ref.version_id,
            empty_digest() if effective is None else effective.ref.content_digest,
        )


def _owner(context: RequestContext) -> OwnerRef:
    project_id, variant_id = _line(context)
    return OwnerRef(
        context.owner_id,
        dict(context.metadata).get("entrypoint", "gui"),
        project_id=project_id,
        variant_id=variant_id,
        permissions=context.permissions,
    )


def _line(context: RequestContext) -> tuple[str, str]:
    if context.project_id is None or context.variant_id is None:
        raise RuntimeError("术语操作需要当前工程和翻译版本")
    return context.project_id, context.variant_id


__all__ = [
    "BuildRunner",
    "ChangelogRunner",
    "HistoryCompareRunner",
    "ProductionTerminologyCommands",
    "PublishRunner",
    "ReportRunner",
]
