# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read and publish Project settings for future game text operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
import secrets
from threading import RLock
from typing import TYPE_CHECKING

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.config.text_encoding import TextEncodings
from transbridge.persistence.v2.models import ProjectDto

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from transbridge.application.contracts import RequestContext
    from transbridge.persistence.v2.ids import ProjectRef

    from .source_update_models import SourceUpdatePreview


@dataclass(frozen=True, slots=True)
class ProjectTextEncodingPreview:
    token: str
    encodings: TextEncodings
    sources: tuple[SourceUpdatePreview, ...]
    variant_count: int


@dataclass(frozen=True, slots=True)
class ProjectTextEncodingResult:
    project_path: str
    encodings: TextEncodings
    backup_path: str | None = None


@dataclass(frozen=True, slots=True)
class _PendingEncoding:
    owner: str
    generation: int
    ref: ProjectRef
    path: str
    project_revision: int
    project: ProjectDto
    preview: ProjectTextEncodingPreview


class ProjectTextEncodingService:
    def __init__(self, lifecycle, projects, store) -> None:
        self._lifecycle, self._projects, self._store = lifecycle, projects, store
        self._pending: dict[str, _PendingEncoding] = {}
        self._lock = RLock()

    def active_project_path(self, context: RequestContext) -> str | None:
        active = self._lifecycle.active
        if active is None:
            return None
        _check_context(active.project_ref, context)
        return self._projects.path_for(active.project_ref)

    def active_encodings(self, context: RequestContext) -> TextEncodings | None:
        active = self._lifecycle.active
        if active is None:
            return None
        _check_context(active.project_ref, context)
        return TextEncodings.from_project_data(active.project.envelope.data)

    def read(self, project_path: str, context: RequestContext) -> OperationResult[TextEncodings]:
        try:
            ref = self._store.resolve(project_path)
            _check_context(ref, context)
            data = self._projects.read_snapshot(ref).envelope.data
            return OperationResult.completed(TextEncodings.from_project_data(data), run_id=context.run_id)
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def prepare(
        self,
        project_path: str,
        encodings: TextEncodings,
        context: RequestContext,
        *,
        source_paths: Mapping[str, str] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> OperationResult[ProjectTextEncodingPreview]:
        try:
            _check_cancelled(cancelled)
            ref = self._store.resolve(project_path)
            _check_context(ref, context)
            generation = self._lifecycle.generation
            project = self._projects.read_snapshot(ref)
            data = project.envelope.data
            active = self._lifecycle.active
            if (
                active is not None
                and active.project_ref == ref
                and active.project.envelope.revision != active.persisted_project_revision
            ):
                raise DomainError(ErrorCategory.CONFLICT, "TEXT_ENCODING_UNSAVED_CHANGES", "请先保存工程设置。")
            token = secrets.token_hex(24)
            settings = {**data, **encodings.to_project_data()}
            updated = ProjectDto(
                replace(project.envelope, revision=project.envelope.revision + (settings != data), data=settings)
            )
            preview = ProjectTextEncodingPreview(token, encodings, (), len(data["variant_ids"]))
            candidate = _PendingEncoding(
                context.owner_id,
                generation,
                ref,
                self._projects.path_for(ref),
                project.envelope.revision,
                updated,
                preview,
            )
            _check_cancelled(cancelled)
            with self._lock:
                self._pending[token] = candidate
            return OperationResult.completed(preview, run_id=context.run_id)
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def commit(
        self, token: str, context: RequestContext, *, cancelled: Callable[[], bool] | None = None
    ) -> OperationResult[ProjectTextEncodingResult]:
        try:
            with self._lock:
                candidate = self._pending.get(token)
                if candidate is None:
                    raise _stale()
                if candidate.owner != context.owner_id:
                    raise DomainError(
                        ErrorCategory.PERMISSION, "TEXT_ENCODING_OWNER_MISMATCH", "编码设置不属于当前操作。"
                    )
                _check_context(candidate.ref, context)
                del self._pending[token]
            _check_cancelled(cancelled)
            result = self._commit_settings(candidate, context, cancelled)
            if not result.is_success:
                return replace(result, value=None)
            return OperationResult.completed(
                ProjectTextEncodingResult(candidate.path, candidate.preview.encodings),
                diagnostics=result.diagnostics,
                run_id=context.run_id,
            )
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def _commit_settings(self, candidate, context, cancelled):
        current = self._projects.read_snapshot(candidate.ref)
        if (
            self._lifecycle.generation != candidate.generation
            or current.envelope.revision != candidate.project_revision
        ):
            raise _stale()
        _check_cancelled(cancelled)
        if candidate.project.envelope.revision == candidate.project_revision:
            if candidate.project.envelope.data != current.envelope.data:
                raise _stale()
            return OperationResult.completed(run_id=context.run_id)
        active = self._lifecycle.active
        if active is not None and active.project_ref == candidate.ref:
            return self._lifecycle.commit_project_update(
                candidate.project,
                candidate.project_revision,
                context,
                expected_generation=candidate.generation,
            )
        self._lifecycle.commit_detached_content(
            candidate.ref,
            candidate.generation,
            lambda: self._store.write_project(candidate.ref, candidate.project, candidate.project_revision),
        )
        return OperationResult.completed(run_id=context.run_id)

    def discard(self, token: str, context: RequestContext) -> None:
        with self._lock:
            candidate = self._pending.get(token)
            if candidate is not None and candidate.owner == context.owner_id:
                del self._pending[token]


def _check_context(ref, context):
    if context.project_id is not None and context.project_id != ref.identity.value:
        raise DomainError(ErrorCategory.PERMISSION, "PROJECT_CONTEXT_MISMATCH", "编码设置与请求工程不一致。")


def _check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise DomainError(ErrorCategory.CANCELLED, "TEXT_ENCODING_CANCELLED", "已取消保存编码设置。")


def _stale():
    return DomainError(ErrorCategory.CONFLICT, "TEXT_ENCODING_PREVIEW_STALE", "工程设置已变化，请重新打开设置。")
