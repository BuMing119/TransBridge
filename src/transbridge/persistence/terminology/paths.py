"""Root-confined locations for one Project's terminology assets."""

from __future__ import annotations

import os
from pathlib import Path
import re

from transbridge.persistence.v2.ids import OpaqueId, ProjectId
from transbridge.persistence.v2.models import PathBoundaryError

_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,12}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class TerminologyPaths:
    def __init__(self, root: str | Path) -> None:
        raw = Path(root)
        if not raw.is_absolute():
            raise PathBoundaryError("terminology persistence root must be absolute")
        self.root = raw.resolve(strict=False)

    def project_root(self, project_id: str) -> Path:
        encoded = ProjectId(project_id).encoded
        return self.guard(self.root / "projects" / encoded / "terminology")

    def database(self, project_id: str) -> Path:
        return self.guard(self.project_root(project_id) / "db" / "terminology.sqlite3")

    def backup_directory(self, project_id: str) -> Path:
        return self.guard(self.project_root(project_id) / "backup")

    def staging_directory(self, project_id: str) -> Path:
        return self.guard(self.project_root(project_id) / "staging")

    def artifact_directory(self, project_id: str) -> Path:
        return self.guard(self.project_root(project_id) / "artifacts")

    def backup(
        self,
        project_id: str,
        from_version: int,
        to_version: int,
        *,
        source_digest: str | None = None,
    ) -> Path:
        if min(from_version, to_version) < 0 or from_version >= to_version:
            raise ValueError("migration backup versions must be non-negative and increasing")
        if source_digest is not None and not _SHA256.fullmatch(source_digest):
            raise ValueError("migration backup source digest must be a lowercase SHA-256")
        digest_suffix = "" if source_digest is None else f"-{source_digest}"
        return self.guard(
            self.backup_directory(project_id) / f"schema-v{from_version}-to-v{to_version}{digest_suffix}.sqlite3"
        )

    def staging(self, project_id: str, token: str) -> Path:
        return self.guard(self.staging_directory(project_id) / f"{OpaqueId(token).encoded}.sqlite3")

    def artifact(self, project_id: str, artifact_id: str, suffix: str) -> Path:
        if not _SUFFIX.fullmatch(suffix):
            raise ValueError("artifact suffix must be a short ASCII extension")
        return self.guard(self.artifact_directory(project_id) / f"{OpaqueId(artifact_id).encoded}{suffix.lower()}")

    def guard(self, path: str | Path) -> Path:
        canonical = Path(path).resolve(strict=False)
        try:
            common = os.path.commonpath((self.root, canonical))
        except ValueError as exc:
            raise PathBoundaryError("terminology path is on a different root") from exc
        if os.path.normcase(common) != os.path.normcase(str(self.root)):
            raise PathBoundaryError("terminology path escapes its authorized root")
        return canonical


__all__ = ["TerminologyPaths"]
