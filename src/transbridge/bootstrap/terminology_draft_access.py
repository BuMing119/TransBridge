"""Read authoritative lifecycle state for short terminology draft transactions."""

from __future__ import annotations

from transbridge.application.terminology.drafts import DraftLineState

from .terminology_storage import empty_digest


class LifecycleDraftLineReader:
    def __init__(self, lifecycle, repository) -> None:
        self._lifecycle = lifecycle
        self._repository = repository

    def read_line(self, _connection, project_id: str, variant_id: str) -> DraftLineState:
        active = self._lifecycle.active
        if active is None or active.variant is None:
            raise RuntimeError("术语草稿需要当前工程和翻译版本")
        if active.project.envelope.identity != project_id or active.variant.ref.identity.value != variant_id:
            raise PermissionError("术语草稿不属于当前工程和翻译版本")
        effective = self._repository.effective_version_ref(project_id, variant_id)
        return DraftLineState(
            project_id,
            variant_id,
            active.variant.revision,
            None if effective is None else effective.version_id,
            empty_digest() if effective is None else effective.content_digest,
        )
