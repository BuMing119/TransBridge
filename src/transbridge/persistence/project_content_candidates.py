"""Known, bounded source evidence used only while upgrading incomplete Projects."""

from __future__ import annotations

import logging
import os

from transbridge.application.contracts import DomainError, ErrorCategory

from .source_update_archive import backup_sources
from .v2.filesystem import RepositoryPaths
from .v2.ids import ProjectId, ProjectRef, VariantId, VariantRef
from .v2.migration import migrate_to_current
from .v2.models import PersistenceV2Error
from .v2.schema import parse_json_bytes, validate_v2
from .v2.variant import VariantSnapshot

_LOGGER = logging.getLogger(__name__)


class ProjectContentCandidates:
    """Inspect existing store manifests and declared references, never search a disk."""

    def __init__(self, root, filesystem, artifacts, project_ref):
        self._filesystem = filesystem
        self._paths = RepositoryPaths(root, filesystem)
        self._artifacts = artifacts
        self._project_ref = project_ref
        self._prepared = None
        self._sources = None
        self._snapshots = None
        self.failures = []

    def saved(self):
        if self._prepared is None:
            self._prepared = []
            directory = self._path("source_artifacts", "manifests")
            for path in self._filesystem.list_files(directory):
                identity, extension = os.path.splitext(os.path.basename(path))
                if extension != ".json":
                    continue
                try:
                    self._prepared.append((identity, self._artifacts.get(identity)))
                except (OSError, ValueError) as exc:
                    self.unavailable(path, exc)
        return tuple(self._prepared)

    def references(self):
        if self._sources is None:
            self._sources = []
            ref = self._project_ref
            # Historical Project preimages remain inert; no old journal is
            # activated while discovering their declared source references.
            directories = (
                (self._path("projects"), False),
                (self._paths.project_backup_data(ref), True),
                (self._path("projects", ref.identity.encoded, "source-updates"), True),
            )
            for directory, recursive in directories:
                paths = (
                    self._filesystem.list_tree_files(directory) if recursive else self._filesystem.list_files(directory)
                )
                for path in paths:
                    if not path.endswith(".json"):
                        continue
                    try:
                        raw = self._filesystem.read_bytes(self._paths.guard(path))
                        document = parse_json_bytes(raw)
                        if "records" in document and "transaction_id" in document:
                            if document.get("project_id") == ref.identity.value:
                                self._sources.extend(backup_sources(raw))
                        elif document.get("entity_type") == "project":
                            self._sources.extend(self._project_sources(document))
                    except (OSError, ValueError, KeyError, TypeError, PersistenceV2Error) as exc:
                        self.unavailable(path, exc)
        return tuple(self._sources)

    def snapshots(self):
        """Read real lifecycle snapshots as identity evidence, never as templates."""
        if self._snapshots is None:
            self._snapshots = []
            for path in self._filesystem.list_files(self._path("snapshots")):
                if not path.endswith(".json"):
                    continue
                try:
                    document = parse_json_bytes(self._filesystem.read_bytes(self._paths.guard(path)))
                    if document.get("project_id") != self._project_ref.identity.value:
                        continue
                    if document.get("schema_version") != 1:
                        raise ValueError("Unsupported Project snapshot format")
                    variant = document["variant"]
                    ref = VariantRef(VariantId(variant["id"]), self._project_ref.identity)
                    dto = validate_v2(migrate_to_current(variant, ref).document, ref)
                    self._snapshots.append(VariantSnapshot.from_dto(dto, ref))
                except (OSError, ValueError, KeyError, TypeError, PersistenceV2Error) as exc:
                    self.unavailable(path, exc)
        return tuple(self._snapshots)

    @staticmethod
    def _project_sources(document):
        ref = ProjectRef(ProjectId(document["id"]))
        project = validate_v2(migrate_to_current(document, ref).document, ref)
        return project.envelope.data.get("sources", ())

    def _path(self, *parts):
        return self._paths.guard(os.path.join(self._paths.root, *parts))

    def unavailable(self, path, exc):
        if isinstance(exc, DomainError) and exc.category in {ErrorCategory.INTERNAL, ErrorCategory.CANCELLED}:
            raise exc
        self.failures.append({"candidate": path, "reason": str(exc)})
        _LOGGER.warning("Skipping unavailable Project source recovery evidence: %s (%s)", path, exc)
