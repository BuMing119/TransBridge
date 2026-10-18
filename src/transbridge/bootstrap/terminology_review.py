"""Short guarded draft edits and evidence-bound conflict review commands."""

from __future__ import annotations

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.terminology.batch_edits import batch_commands
from transbridge.application.terminology.build_freshness import build_input_digest, require_current_build
from transbridge.application.terminology.conflict_review import TerminologyConflictDetails, TerminologyReviewCounts
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
from transbridge.application.terminology.drafts import DraftService, DraftWriteExpectation, OpenDraftCommand
from transbridge.application.terminology.errors import RepositoryConflictError, TerminologyNotFoundError
from transbridge.application.terminology.models import ConflictStatus, DecisionStatus, DraftRef, TermScope

from .terminology_context import require_line
from .terminology_draft_access import LifecycleDraftLineReader


class TerminologyReviewCommands:
    def __init__(self, repositories, ids, clock, lifecycle, build_inputs=None) -> None:
        self._repositories = repositories
        self._ids = ids
        self._clock = clock
        self._lifecycle = lifecycle
        self._build_inputs = build_inputs

    def active_draft(self, context: RequestContext):
        project_id, variant_id = require_line(context)
        return self._repositories.for_project(project_id).active_draft(project_id, variant_id)

    def review_counts(self, context: RequestContext) -> TerminologyReviewCounts:
        project_id, variant_id = require_line(context)
        repository = self._repositories.for_project(project_id)
        build = repository.latest_build(project_id, variant_id)
        draft = repository.active_draft(project_id, variant_id)
        effective = repository.effective_version(project_id, variant_id)
        decisions = () if draft is None else draft.decisions
        resolutions = () if draft is None else draft.conflict_resolutions
        if draft is None and effective is not None:
            decisions = effective.decisions
            if build is not None and effective.build_ref == build.ref:
                resolutions = effective.conflicts
        resolved = {item.conflict_group_id: item for item in resolutions}
        conflicts = (
            () if build is None else tuple(resolved.get(item.conflict_group_id, item) for item in build.conflicts)
        )
        return TerminologyReviewCounts(
            sum(not item.suppressed and item.status is DecisionStatus.REVIEW_REQUIRED for item in decisions),
            sum(item.status is ConflictStatus.UNRESOLVED for item in conflicts),
        )

    def prepare_draft(self, context: RequestContext, config=None):
        project_id, variant_id = require_line(context)
        repository = self._repositories.for_project(project_id)
        reader = LifecycleDraftLineReader(self._lifecycle, repository)
        line = reader.read_line(None, project_id, variant_id)
        current = repository.active_draft(project_id, variant_id)
        if current is not None:
            if (current.ref.base_version_id, current.ref.base_content_digest) != (
                line.effective_version_id,
                line.effective_content_digest,
            ):
                raise RepositoryConflictError("草稿基线已经变化，请重新生成草稿")
            return current
        effective = repository.effective_version(project_id, variant_id)
        if effective is None:
            return None
        build = repository.latest_build(project_id, variant_id)
        if build is None or build.ref != effective.build_ref:
            raise RepositoryConflictError("来源整理结果已经变化，请重新生成草稿后编辑")
        if self._build_inputs is None:
            raise RuntimeError("无法确认当前来源状态，请重新打开工程后重试")
        captured = self._build_inputs.capture_build_input(context, config=config or {})
        if captured.outcome is not OperationOutcome.COMPLETED or captured.value is None:
            message = captured.diagnostics[0].message if captured.diagnostics else "无法读取当前工程来源"
            raise RuntimeError(message)
        if (captured.value.project_id, captured.value.variant_id) != (project_id, variant_id):
            raise RepositoryConflictError("当前工程或翻译版本已变化，请重新打开术语窗口")
        require_current_build(build, build_input_digest(captured.value))
        return DraftService(repository.draft_transactions(reader), self._ids).open(
            OpenDraftCommand(
                line,
                effective.ref.version_id,
                effective.ref.content_digest,
                effective.decisions,
                tuple(item for item in effective.conflicts if item.status is not ConflictStatus.UNRESOLVED),
            )
        )

    def apply_decision(self, operation, context: RequestContext, *, expected_ref=None, **values):
        service, expectation = self._decision_service(context, expected_ref=expected_ref)
        return service.apply(
            DecisionCommand(operation=DecisionOperation(operation), expectation=expectation, **values), context
        )

    def edit_term(self, term_id, context: RequestContext, *, expected_ref=None, **values):
        service, expectation = self._decision_service(context, expected_ref=expected_ref)
        draft, _ = self._edit_state(context)
        if not any(item.term_id == term_id for item in draft.decisions):
            raise ValueError("术语已变化或不存在，请刷新后重试")
        return service.apply(
            DecisionCommand(operation=DecisionOperation.EDIT, expectation=expectation, term_id=term_id, **values),
            context,
        )

    def batch_terms(self, term_ids, operation, context: RequestContext, *, expected_ref=None):
        service, expectation = self._decision_service(context, expected_ref=expected_ref)
        draft, conflicts = self._edit_state(context)
        return service.apply_many(batch_commands(draft, expectation, tuple(term_ids), operation, conflicts), context)

    def _edit_state(self, context):
        project_id, variant_id = require_line(context)
        repository = self._repositories.for_project(project_id)
        draft = repository.active_draft(project_id, variant_id)
        if draft is None:
            raise TerminologyNotFoundError("当前没有可编辑的草稿，请先生成草稿")
        build = repository.latest_build(project_id, variant_id)
        return draft, () if build is None else build.conflicts

    def resolve_conflict(self, conflict, operation, context: RequestContext, **values):
        details = self.conflict_details(conflict, context)
        return self.resolve_review(details, operation, context, **values)

    def conflict_details(self, conflict, context: RequestContext) -> TerminologyConflictDetails:
        project_id, variant_id = require_line(context)
        if (conflict.project_id, conflict.variant_id) != (project_id, variant_id):
            raise PermissionError("异译不属于当前工程和翻译版本")
        repository = self._repositories.for_project(project_id)
        build = repository.latest_build(project_id, variant_id)
        pinned = (
            None
            if build is None
            else next((item for item in build.conflicts if item.conflict_group_id == conflict.conflict_group_id), None)
        )
        if pinned is None or pinned.variants != conflict.variants:
            raise RepositoryConflictError("异译证据已变化，请刷新后重新审核")
        draft = self.active_draft(context)
        if draft is None:
            raise RepositoryConflictError("当前没有可审核的草稿，请先构建术语库")
        resolutions = {item.conflict_group_id: item for item in draft.conflict_resolutions}
        projected = resolutions.get(pinned.conflict_group_id, pinned)
        evidence_ids = {identity for variant in pinned.variants for identity in variant.evidence_ids}
        candidate_ids = {identity for variant in pinned.variants for identity in variant.candidate_ids}
        candidates = tuple(item for item in build.candidates if item.candidate_id in candidate_ids)
        evidence = tuple(item for item in build.evidence if item.evidence_id in evidence_ids)
        decisions = tuple(item for item in draft.decisions if item.normalized_original == pinned.normalized_original)
        plugin_ids = tuple(
            sorted({
                *(item.plugin_scope for item in evidence if item.plugin_scope),
                *(item.scope.plugin_id for item in candidates if item.scope.plugin_id),
                *(item.scope.plugin_id for item in decisions if item.scope.plugin_id),
            })
        )
        return TerminologyConflictDetails(build.ref, draft.ref, projected, candidates, evidence, plugin_ids, decisions)

    def resolve_review(self, details: TerminologyConflictDetails, operation, context: RequestContext, **values):
        current = self.conflict_details(details.conflict, context)
        if current != details:
            raise RepositoryConflictError("草稿或异译证据已变化，请重新审核")
        operation = ConflictResolutionOperation(operation)
        plugin_id = values.get("plugin_id")
        if operation is ConflictResolutionOperation.PLUGIN_EXCEPTION:
            if plugin_id not in details.plugin_ids:
                raise ValueError("请选择此异译证据中已有的插件")
            scope = TermScope.plugin(plugin_id)
        else:
            scope = TermScope.project()
        existing = next((item for item in details.decisions if item.scope == scope), None)
        service, expectation = self._decision_service(context, expected_ref=details.draft_ref)
        return ConflictService(service).resolve(
            ConflictResolutionCommand(
                operation=operation,
                conflict=details.conflict,
                expectation=expectation,
                term_id=None if existing is None else existing.term_id,
                **values,
            ),
            context,
        )

    def discard_draft(self, expected_ref: DraftRef, context: RequestContext) -> None:
        if (expected_ref.project_id, expected_ref.variant_id) != require_line(context):
            raise PermissionError("待放弃草稿不属于当前工程和翻译版本")
        repository = self._repositories.for_project(expected_ref.project_id)
        reader = LifecycleDraftLineReader(self._lifecycle, repository)
        line = reader.read_line(None, expected_ref.project_id, expected_ref.variant_id)
        expectation = DraftWriteExpectation(
            expected_ref.draft_id, expected_ref.revision, expected_ref.decision_set_digest, line
        )
        repository.draft_transactions(reader).abandon_draft(expected_ref, expectation=expectation)

    def _decision_service(self, context, *, expected_ref=None):
        project_id, variant_id = require_line(context)
        repository = self._repositories.for_project(project_id)
        reader = LifecycleDraftLineReader(self._lifecycle, repository)
        line = reader.read_line(None, project_id, variant_id)
        drafts = DraftService(repository.draft_transactions(reader), self._ids)
        ref = repository.active_ref(project_id, variant_id)
        if ref is None:
            raise TerminologyNotFoundError("active terminology draft was not found")
        if expected_ref is not None and ref != expected_ref:
            raise RepositoryConflictError("草稿已变化，请重新审核")
        expectation = DraftWriteExpectation.from_ref(ref, line)
        build = repository.latest_build(project_id, variant_id)
        conflicts = () if build is None else build.conflicts
        return DecisionService(drafts, RequestManualActor(), self._clock, self._ids, conflicts=conflicts), expectation


class RequestManualActor:
    @staticmethod
    def resolve(context: RequestContext) -> ManualActor:
        actor = dict(context.metadata).get("manual_actor_id", "").strip()
        if not actor:
            raise PermissionError("人工术语调整需要可信的操作人身份")
        return ManualActor(actor, trusted=True)


__all__ = ["TerminologyReviewCommands"]
