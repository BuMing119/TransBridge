"""Prepare, approve, and run publications against the exact reviewed content."""

from __future__ import annotations

from dataclasses import replace
import json

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.terminology.build_freshness import require_current_build
from transbridge.application.terminology.identity import canonical_digest
from transbridge.application.terminology.publication_review import TerminologyPublishPreview
from transbridge.application.terminology.publish import (
    PublishGuardRejectedError,
    PublishTerminologyRequest,
    VersionPublisher,
)
from transbridge.application.terminology.runtime import TerminologyExecutionContext, TerminologyWorkloadExecution
from transbridge.application.terminology.workloads import (
    PublishWorkloadRequest,
    TerminologyPhase,
    TerminologyProgress,
    TerminologyWorkloadResult,
    TerminologyWorkloadType,
)

from .terminology_context import require_line, task_owner
from .terminology_storage import expected_state


class TerminologyPublicationCommands:
    def __init__(self, entrypoint, repositories, state, ids, clock, build_inputs) -> None:
        self._entrypoint = entrypoint
        self._repositories = repositories
        self._state = state
        self._ids = ids
        self._clock = clock
        self._build_inputs = build_inputs

    def preview_publish(self, context: RequestContext, config=None) -> TerminologyPublishPreview:
        config_json = json.dumps(
            config or {}, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        project_id, variant_id = require_line(context)
        repository = self._repositories.for_project(project_id)
        snapshot = self._capture(context, config_json)
        expected = expected_state(snapshot, repository)
        build = self._latest_build(repository, project_id, variant_id)
        require_current_build(build, expected.build_freshness_digest)
        draft = repository.active_draft(project_id, variant_id)
        if draft is None:
            raise RuntimeError("发布前需要先创建并确认术语草稿")
        request = PublishTerminologyRequest(
            project_id=project_id,
            variant_id=variant_id,
            expected=expected,
            build_ref=build.ref,
            version_id=self._ids.new_id(),
            published_at=self._clock.now().isoformat(),
            draft_ref=draft.ref,
        )
        candidate = (
            VersionPublisher(repository.publisher, StaticPublishState(expected), Permit()).prepare(request).version
        )
        request = replace(request, reviewed_content_digest=candidate.ref.content_digest)
        return TerminologyPublishPreview(request, candidate, config_json)

    def publish_preview(self, preview: TerminologyPublishPreview, context: RequestContext):
        project_id, variant_id = require_line(context)
        request = preview.request
        if (request.project_id, request.variant_id) != (project_id, variant_id):
            raise PermissionError("发布预览不属于当前工程和翻译版本")
        repository = self._repositories.for_project(project_id)
        snapshot = self._capture(context, preview.config_json)
        if expected_state(snapshot, repository) != preview.expected:
            raise PublishGuardRejectedError("来源、草稿或当前版本已变化，请重新检查发布影响")
        if self._latest_build(repository, project_id, variant_id).ref != preview.build_ref:
            raise PublishGuardRejectedError("术语构建已变化，请重新检查发布影响")
        candidate = VersionPublisher(repository.publisher, StaticPublishState(preview.expected), Permit()).prepare(
            request
        )
        if candidate.version != preview.candidate_version:
            raise PublishGuardRejectedError("发布内容与预览不一致，请重新检查发布影响")
        return self._submit(request, snapshot, context)

    def publish(self, context: RequestContext, config=None):
        return self.publish_preview(self.preview_publish(context, config), context)

    def restore(self, version_ref, context: RequestContext, config=None):
        project_id, variant_id = require_line(context)
        if (version_ref.project_id, version_ref.variant_id) != (project_id, variant_id):
            raise PermissionError("历史版本不属于当前工程和翻译版本")
        repository = self._repositories.for_project(project_id)
        repository.get_version(version_ref)
        if repository.active_draft(project_id, variant_id) is not None:
            raise RuntimeError("恢复历史内容前需要先发布或放弃当前草稿")
        snapshot = self._capture(context, json.dumps(config or {}, ensure_ascii=False, allow_nan=False))
        expected = expected_state(snapshot, repository)
        build = self._latest_build(repository, project_id, variant_id)
        require_current_build(build, expected.build_freshness_digest)
        request = PublishTerminologyRequest(
            project_id=project_id,
            variant_id=variant_id,
            expected=expected,
            build_ref=build.ref,
            version_id=self._ids.new_id(),
            published_at=self._clock.now().isoformat(),
            rollback_from=version_ref,
        )
        return self._submit(request, snapshot, context)

    def _capture(self, context, config_json):
        result = self._build_inputs.capture_build_input(context, config=json.loads(config_json))
        if result.outcome is not OperationOutcome.COMPLETED or result.value is None:
            diagnostic = result.diagnostics[0] if result.diagnostics else None
            raise RuntimeError("无法确认当前工程术语状态" if diagnostic is None else diagnostic.message)
        if (result.value.project_id, result.value.variant_id) != require_line(context):
            raise PermissionError("当前工程和翻译版本已经变化")
        return result.value

    def _submit(self, publication, snapshot, context):
        digest = canonical_digest(publication, namespace="terminology.production-publication-request.v2")
        self._state.put(self._state.publish_payloads, digest, publication)
        self._state.put(self._state.snapshots, publication.expected.digest, snapshot)
        request = PublishWorkloadRequest(
            project_id=publication.project_id,
            variant_id=publication.variant_id,
            expected=publication.expected,
            build_ref=publication.build_ref.build_key,
            publish_digest=digest,
        )
        return self._entrypoint.submit(request, task_owner(context)).ref

    @staticmethod
    def _latest_build(repository, project_id, variant_id):
        build = repository.latest_build(project_id, variant_id)
        if build is None:
            raise RuntimeError("当前翻译版本还没有术语构建结果")
        return build


class PublishRunner:
    def __init__(self, repositories, state) -> None:
        self._repositories = repositories
        self._state = state

    def __call__(self, request, runtime: TerminologyExecutionContext) -> TerminologyWorkloadExecution:
        if not isinstance(request, PublishWorkloadRequest):
            raise TypeError("publish runner received another workload")
        publication = self._state.get(self._state.publish_payloads, request.publish_digest)
        runtime.progress.update(TerminologyProgress(TerminologyPhase.VALIDATE, 1, 2))

        def mutation() -> None:
            repository = self._repositories.for_project(request.project_id)
            VersionPublisher(repository.publisher, StaticPublishState(request.expected), Permit()).publish(publication)

        runtime.progress.update(TerminologyProgress(TerminologyPhase.PUBLISH, 2, 2))
        return TerminologyWorkloadExecution(
            TerminologyWorkloadResult(TerminologyWorkloadType.PUBLISH, output_ref=publication.version_id), mutation
        )


class StaticPublishState:
    def __init__(self, expected) -> None:
        self._expected = expected

    def current(self, project_id: str, variant_id: str):
        return self._expected


class Permit:
    @staticmethod
    def is_permitted() -> bool:
        return True


__all__ = ["PublishRunner", "TerminologyPublicationCommands"]
