"""Production build execution and guarded handoff into terminology review."""

from __future__ import annotations

from transbridge.application.io import TranslationIoUseCase
from transbridge.application.terminology.build import TerminologyFullBuilder, TranslationIoRegisteredSourceParser
from transbridge.application.terminology.build_drafts import BuildDraftCoordinator
from transbridge.application.terminology.errors import RepositoryConflictError
from transbridge.application.terminology.in_memory import InMemoryTerminologyRepository
from transbridge.application.terminology.runtime import TerminologyExecutionContext, TerminologyWorkloadExecution
from transbridge.application.terminology.workloads import (
    BuildCompleteness,
    BuildLlmStatus,
    BuildWorkloadRequest,
    TerminologyPhase,
    TerminologyProgress,
    TerminologyWorkloadResult,
    TerminologyWorkloadType,
)

from .terminology_draft_access import LifecycleDraftLineReader
from .terminology_storage import sha256_part


class BuildRunner:
    def __init__(self, repositories, state, catalog, ids, lifecycle) -> None:
        self._repositories = repositories
        self._state = state
        self._catalog = catalog
        self._ids = ids
        self._lifecycle = lifecycle

    def __call__(self, request, runtime: TerminologyExecutionContext) -> TerminologyWorkloadExecution:
        if not isinstance(request, BuildWorkloadRequest):
            raise TypeError("build runner received another workload")
        snapshot = self._state.get(self._state.snapshots, request.build_key)
        context = self._state.get(self._state.contexts, request.build_key)
        runtime.progress.update(TerminologyProgress(TerminologyPhase.PARSE, 0, len(snapshot.sources)))
        builder = TerminologyFullBuilder(
            TranslationIoRegisteredSourceParser(TranslationIoUseCase(self._catalog)),
            InMemoryTerminologyRepository(),
        )
        repository = self._repositories.for_project(request.project_id)
        baseline = repository.active_draft(request.project_id, request.variant_id)
        if baseline is None:
            baseline = repository.effective_version(request.project_id, request.variant_id)
        outcome = builder.build(
            snapshot,
            context,
            baseline_decisions=() if baseline is None else baseline.decisions,
            cancellation=runtime.cancellation,
        )
        runtime.checkpoint()
        runtime.progress.update(
            TerminologyProgress(TerminologyPhase.PERSIST, len(snapshot.sources), len(snapshot.sources))
        )

        def mutation() -> None:
            ensure_draft(repository, outcome.result, snapshot, self._ids.new_id(), self._lifecycle)
            self._state.put(self._state.latest_builds, (request.project_id, request.variant_id), outcome.result.ref)

        result = TerminologyWorkloadResult(
            TerminologyWorkloadType.BUILD,
            output_ref=outcome.result.ref.build_key,
            completeness=BuildCompleteness(outcome.result.completeness.value),
            llm_status=BuildLlmStatus(outcome.result.llm_status.value),
            diagnostics=outcome.result.diagnostics,
        )
        return TerminologyWorkloadExecution(result, mutation)


def ensure_draft(repository, build, snapshot, draft_id: str, lifecycle) -> None:
    current = repository.active_draft(snapshot.project_id, snapshot.variant_id)
    if current is None:
        if snapshot.draft_id != "no-draft":
            raise RepositoryConflictError("captured terminology draft is no longer active")
    elif (current.ref.draft_id, current.ref.revision, sha256_part(current.ref.decision_set_digest)) != (
        snapshot.draft_id,
        snapshot.draft_revision,
        snapshot.decision_digest,
    ):
        raise RepositoryConflictError("terminology draft changed after build input capture")
    reader = LifecycleDraftLineReader(lifecycle, repository)
    line = reader.read_line(None, snapshot.project_id, snapshot.variant_id)
    if (line.variant_revision, line.effective_version_id, sha256_part(line.effective_content_digest)) != (
        snapshot.variant_revision,
        snapshot.effective_version_id,
        snapshot.effective_content_digest,
    ):
        raise RepositoryConflictError("terminology base changed after build input capture")
    BuildDraftCoordinator(repository.draft_transactions(reader, build=build)).reconcile(
        build,
        line=line,
        draft_id=draft_id,
        current=current,
        effective=repository.effective_version(snapshot.project_id, snapshot.variant_id),
    )
