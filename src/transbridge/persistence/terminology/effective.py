"""Read-only effective terminology projection backed by project SQLite."""

from __future__ import annotations

import sqlite3

from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologySnapshot,
)
from transbridge.application.terminology.errors import TerminologyNotFoundError
from transbridge.application.terminology.ports import PageRequest
from transbridge.application.terminology.publish import terminology_version_content_digest

from .connection import StorageMode, TerminologyStorageError
from .repository import SqliteTerminologyRepository


class SqliteEffectiveTerminologySnapshotPort:
    """Expose only verified, current immutable version membership to consumers."""

    def __init__(self, repository: SqliteTerminologyRepository) -> None:
        self._repository = repository

    def snapshot(
        self,
        local_project_id: str,
        local_variant_id: str,
        version_id: str | None = None,
    ) -> EffectiveTerminologySnapshot:
        if local_project_id != self._repository.project_id:
            raise ValueError("effective terminology repository belongs to another Project")
        state = self._repository.storage_state
        if state.mode is StorageMode.READ_ONLY:
            return EffectiveTerminologySnapshot(
                local_project_id,
                local_variant_id,
                EffectiveSnapshotStatus.UNAVAILABLE,
                diagnostics=(_storage_diagnostic("terminology repository is read-only", state.diagnostic),),
            )
        if not state.integrity_ok:
            return EffectiveTerminologySnapshot(
                local_project_id,
                local_variant_id,
                EffectiveSnapshotStatus.CORRUPT,
                diagnostics=(_storage_diagnostic("terminology repository integrity check failed", state.diagnostic),),
            )
        try:
            version = self._read_version(local_project_id, local_variant_id, version_id)
        except TerminologyNotFoundError:
            if version_id is None:
                return EffectiveTerminologySnapshot(
                    local_project_id,
                    local_variant_id,
                    EffectiveSnapshotStatus.CORRUPT,
                    diagnostics=("effective terminology pointer or version content digest is inconsistent",),
                )
            return EffectiveTerminologySnapshot(
                local_project_id,
                local_variant_id,
                EffectiveSnapshotStatus.UNAVAILABLE,
                diagnostics=(f"terminology version was not found: {version_id}",),
            )
        except (TerminologyStorageError, sqlite3.Error) as exc:
            diagnostic = getattr(getattr(exc, "state", None), "diagnostic", None)
            return EffectiveTerminologySnapshot(
                local_project_id,
                local_variant_id,
                EffectiveSnapshotStatus.CORRUPT,
                diagnostics=(_storage_diagnostic("terminology version could not be read safely", diagnostic),),
            )
        if version is None:
            return EffectiveTerminologySnapshot(
                local_project_id,
                local_variant_id,
                EffectiveSnapshotStatus.NO_PROJECT_VERSION,
            )
        if terminology_version_content_digest(version) != version.ref.content_digest:
            return EffectiveTerminologySnapshot(
                local_project_id,
                local_variant_id,
                EffectiveSnapshotStatus.CORRUPT,
                diagnostics=("terminology version content digest mismatch",),
            )
        return EffectiveTerminologySnapshot(
            local_project_id,
            local_variant_id,
            EffectiveSnapshotStatus.READY,
            version_id=version.ref.version_id,
            content_digest=version.ref.content_digest,
            decisions=version.decisions,
        )

    def _read_version(self, project_id: str, variant_id: str, version_id: str | None):
        if version_id is None:
            return self._repository.effective_version(project_id, variant_id)
        page = self._repository.list_versions(project_id, variant_id, PageRequest(limit=1000))
        ref = next((item for item in page.items if item.version_id == version_id), None)
        if ref is None:
            raise TerminologyNotFoundError("terminology version was not found")
        return self._repository.get_version(ref)


def _storage_diagnostic(message: str, detail: str | None) -> str:
    return message if not detail else f"{message}: {detail}"


__all__ = ["SqliteEffectiveTerminologySnapshotPort"]
