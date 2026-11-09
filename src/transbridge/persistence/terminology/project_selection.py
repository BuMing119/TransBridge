"""Project-owned selection references to immutable terminology revisions."""

from __future__ import annotations

from dataclasses import asdict
import json

from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology_profiles.models import TerminologyProfileSelection
from transbridge.persistence.project_store.connection import project_connection
from transbridge.persistence.project_store.records import WorkspaceProjects
from transbridge.persistence.v2.ids import ProjectId, VariantId
from transbridge.persistence.v2.schema import serialize_document

from .effective import SqliteEffectiveTerminologySnapshotPort
from .paths import TerminologyPaths
from .repository import SqliteTerminologyRepository


class ProjectTerminologySelection:
    def __init__(self, root):
        self._projects = WorkspaceProjects(root)
        self._paths = TerminologyPaths(root)

    def selection(self, project_id, variant_id):
        path = self._projects.store_for(ProjectId(project_id)).path
        with project_connection(path) as connection:
            row = connection.execute(
                "SELECT terminology_selection FROM variant WHERE identity = ?", (VariantId(variant_id).value,)
            ).fetchone()
        return None if row is None else decode_selection(row[0], project_id, variant_id)

    def selections(self, project_id):
        path = self._projects.store_for(ProjectId(project_id)).path
        return read_project_selections(path, project_id)

    def variant_targets(self, project_id):
        """Read the project catalog revision and overwrite evidence in one snapshot."""
        path = self._projects.store_for(ProjectId(project_id)).path
        with project_connection(path) as connection:
            row = connection.execute("SELECT identity, revision FROM project").fetchone()
            if row["identity"] != project_id:
                raise ValueError("Terminology target lookup returned a different project")
            variants = {
                item["identity"]: (
                    item["revision"],
                    decode_selection(item["terminology_selection"], project_id, item["identity"]),
                )
                for item in connection.execute("SELECT identity, revision, terminology_selection FROM variant")
            }
        return row["revision"], variants

    def read(self, project_id, variant_id):
        return self.encode(self.selection(project_id, variant_id))

    def cache_token(self, project_id):
        path = self._projects.store_for(ProjectId(project_id)).path
        with project_connection(path) as connection:
            return connection.execute("SELECT revision FROM project").fetchone()[0]

    @staticmethod
    def encode(selection):
        return None if selection is None else serialize_document(asdict(selection))

    def published_revision(self, project_id, profile_id, revision):
        if not self._paths.database(project_id).is_file():
            return None
        repository = SqliteTerminologyRepository.open(str(self._paths.root), project_id, writable=False)
        try:
            published = repository.localization_profiles.get_published(profile_id, revision)
            return published if published is not None and published.project_id == project_id else None
        finally:
            repository.close()

    def base_snapshot(self, project_id, variant_id):
        if not self._paths.database(project_id).is_file():
            return EffectiveTerminologySnapshot(project_id, variant_id, EffectiveSnapshotStatus.NO_PROJECT_VERSION)
        repository = SqliteTerminologyRepository.open(str(self._paths.root), project_id, writable=False)
        try:
            return SqliteEffectiveTerminologySnapshotPort(repository).snapshot(project_id, variant_id)
        finally:
            repository.close()


def read_project_selections(path, project_id):
    with project_connection(path) as connection:
        return tuple(
            decode_selection(row[1], project_id, row[0])
            for row in connection.execute(
                "SELECT identity, terminology_selection FROM variant "
                "WHERE terminology_selection IS NOT NULL ORDER BY identity"
            )
        )


def decode_selection(payload, project_id, variant_id):
    if payload is None:
        return None
    values = json.loads(payload)
    if not isinstance(values, dict) or values.keys() != {
        "project_id",
        "variant_id",
        "profile_id",
        "revision",
        "selected_at",
    }:
        raise ValueError("Invalid project terminology selection")
    selection = TerminologyProfileSelection(**values)
    if (selection.project_id, selection.variant_id) != (project_id, variant_id):
        raise ValueError("Terminology selection belongs to another project or version")
    return selection
