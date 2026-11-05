"""Recoverable SQLite row participant for Project activation journals."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import json

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology_profiles.models import TerminologyProfileSelection
from transbridge.persistence.terminology.connection import TerminologyConnectionFactory
from transbridge.persistence.terminology.paths import TerminologyPaths
from transbridge.persistence.terminology.profiles import SqliteTerminologyProfileRepository

from .ids import ProjectId, VariantId
from .schema import serialize_document


class TerminologySelectionParticipant:
    """Change only a selection row; existing SQLite handles observe data_version."""

    def __init__(self, root):
        self._paths = TerminologyPaths(root)
        self._factory = TerminologyConnectionFactory(self._paths)

    @contextmanager
    def repository(self, project_id):
        opened = self._factory.open(project_id)
        try:
            yield SqliteTerminologyProfileRepository(opened.connection)
        finally:
            opened.connection.close()

    def selection(self, project_id, variant_id):
        if not self._paths.database(project_id).is_file():
            return None
        with self.repository(project_id) as repository:
            return repository.get_selection(project_id, variant_id)

    def read(self, project_id, variant_id):
        return self.encode(self.selection(project_id, variant_id))

    def published_revision(self, project_id, profile_id, revision):
        if not self._paths.database(project_id).is_file():
            return None
        with self.repository(project_id) as repository:
            return repository.get_published(profile_id, revision)

    def base_snapshot(self, project_id, variant_id):
        if not self._paths.database(project_id).is_file():
            return EffectiveTerminologySnapshot(project_id, variant_id, EffectiveSnapshotStatus.NO_PROJECT_VERSION)
        from transbridge.persistence.terminology.effective import SqliteEffectiveTerminologySnapshotPort
        from transbridge.persistence.terminology.repository import SqliteTerminologyRepository

        repository = SqliteTerminologyRepository.open(str(self._paths.root), project_id)
        try:
            return SqliteEffectiveTerminologySnapshotPort(repository).snapshot(project_id, variant_id)
        finally:
            repository.close()

    @staticmethod
    def encode(selection):
        return None if selection is None else serialize_document(asdict(selection))

    @staticmethod
    def decode(payload, project_id, variant_id):
        ProjectId(project_id)
        VariantId(variant_id)
        if payload is None:
            return None
        value = json.loads(payload)
        if set(value) != {"project_id", "variant_id", "profile_id", "revision", "selected_at"}:
            raise ValueError("invalid terminology selection journal payload")
        selection = TerminologyProfileSelection(**value)
        if (selection.project_id, selection.variant_id) != (project_id, variant_id):
            raise ValueError("terminology selection journal identity mismatch")
        if TerminologySelectionParticipant.encode(selection) != payload:
            raise ValueError("noncanonical terminology selection journal payload")
        return selection

    def replace(self, project_id, variant_id, payload, *, known):
        selection = self.decode(payload, project_id, variant_id)
        if payload is None and not self._paths.database(project_id).is_file():
            if None not in known:
                raise self.conflict()
            return
        with self.repository(project_id) as repository:
            # BEGIN IMMEDIATE makes the preimage check and row mutation one CAS.
            connection = repository._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = self.encode(repository.get_selection(project_id, variant_id))
                if current not in known:
                    raise self.conflict()
                if current != payload:
                    if selection is None:
                        repository.clear_selection(project_id, variant_id)
                    else:
                        repository.set_selection(selection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def conflict():
        return DomainError(
            ErrorCategory.CONFLICT,
            "PROJECT_ACTIVATION_RECOVERY_REQUIRED",
            "工程译名方案关联已被其他操作修改，请保留恢复日志并重新打开工程。",
        )
