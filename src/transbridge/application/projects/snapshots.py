"""Restore historical content through the current Variant's mutation boundary."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult, RequestContext
from transbridge.application.io.identity import EntryRevision
from transbridge.persistence.v2.ids import VariantRef
from transbridge.persistence.v2.variant import VariantChangeSet, VariantSnapshot

from .lifecycle import ProjectLifecycleService


@dataclass(frozen=True, slots=True)
class ProjectSnapshotInfo:
    identity: str
    name: str
    revision: int


class ProjectSnapshotPort(Protocol):
    def list(self, ref: VariantRef) -> tuple[ProjectSnapshotInfo, ...]: ...

    def load(self, identity: str, ref: VariantRef) -> VariantSnapshot: ...

    def delete(self, identity: str, ref: VariantRef) -> None: ...


class ProjectSnapshotCommands:
    def __init__(self, lifecycle: ProjectLifecycleService, repository: ProjectSnapshotPort) -> None:
        self._lifecycle = lifecycle
        self._repository = repository

    def list(self, context: RequestContext) -> tuple[ProjectSnapshotInfo, ...]:
        active = self._active(context)
        return self._repository.list(active.formal_variant_ref)

    def restore(self, identity: str, context: RequestContext) -> OperationResult:
        """Replace content, mark it dirty, and retain the formal Variant identity."""
        try:
            active = self._active(context)
            current = active.variant.snapshot()
            project_revision = active.project.envelope.revision
            snapshot = self._repository.load(identity, current.ref)
            if snapshot.source_fingerprints != current.source_fingerprints:
                raise ValueError("快照源文件与当前版本不同；请先恢复匹配的源文件")
            current_entries = {entry.entry_key: entry for entry in current.entries}
            if set(current_entries) != {entry.entry_key for entry in snapshot.entries}:
                raise ValueError("快照条目与当前版本不同，无法安全覆盖")
            entries = tuple(
                replace(
                    entry,
                    revision=EntryRevision(
                        max(entry.revision.value, current_entries[entry.entry_key].revision.value) + 1
                    ),
                )
                for entry in snapshot.entries
            )
            change_set = VariantChangeSet(
                current.ref,
                current.revision,
                snapshot.source_fingerprints,
                entries,
                snapshot.label_library,
                context.run_id or "",
            )
            return self._lifecycle.commit_active_variant(
                change_set, context, expected_project_revision=project_revision
            )
        except Exception as exc:
            error = DomainError(ErrorCategory.PREREQUISITE, "SNAPSHOT_RESTORE_FAILED", str(exc), cause=exc)
            return OperationResult.failed(error, run_id=context.run_id)

    def delete(self, identity: str, context: RequestContext) -> OperationResult[dict[str, str]]:
        try:
            active = self._active(context)
            self._repository.delete(identity, active.formal_variant_ref)
            return OperationResult.completed(
                {
                    "identity": identity,
                    "project_id": active.project_ref.identity.value,
                    "variant_id": active.formal_variant_ref.identity.value,
                },
                run_id=context.run_id,
            )
        except Exception as exc:
            error = DomainError(ErrorCategory.PREREQUISITE, "SNAPSHOT_DELETE_FAILED", str(exc), cause=exc)
            return OperationResult.failed(error, run_id=context.run_id)

    def _active(self, context: RequestContext):
        active = self._lifecycle.active
        if active is None or active.variant is None or active.formal_variant_ref is None:
            raise ValueError("请先打开一个翻译版本")
        if context.project_id not in (None, active.project_ref.identity.value):
            raise ValueError("快照请求的工程已改变")
        if context.variant_id not in (None, active.formal_variant_ref.identity.value):
            raise ValueError("快照请求的翻译版本已改变")
        return active
