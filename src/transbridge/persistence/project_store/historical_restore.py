"""Restore a named snapshot whose source revisions differ from the current project."""

from __future__ import annotations

from dataclasses import replace
import json
from uuid import uuid4

from transbridge.application.contracts import OperationResult
from transbridge.application.io.identity import EntryRevision
from transbridge.application.projects.models import LifecycleSnapshot
from transbridge.persistence.v2.ids import VariantId, VariantRef
from transbridge.persistence.v2.models import ProjectDto
from transbridge.persistence.v2.variant import VariantEntryState, VariantSnapshot

from .connection import project_connection
from .format import ProjectStorageConflict
from .history import snapshot_record, write_snapshot
from .lifecycle import _verify_project, _write_project
from .reads import require_variant
from .records import read_baselines, read_project, read_variant
from .variant_rows import write_variant


class HistoricalSourceRestore:
    def __init__(self, projects, lifecycle, baselines) -> None:
        self.projects = projects
        self.lifecycle = lifecycle
        self.baselines = baselines

    def required(self, identity: str, ref: VariantRef) -> bool:
        with project_connection(self.projects.store_for(ref.project_id).path) as connection:
            snapshot_record(connection, identity, ref)
            current = tuple(
                row[0] for row in connection.execute("SELECT source_id FROM project_source ORDER BY ordinal")
            )
            historical = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT source_id FROM snapshot_source WHERE snapshot_id = ? ORDER BY ordinal", (identity,)
                )
            )
        return current != historical

    def restore(self, identity: str, active, historical: VariantSnapshot, context) -> OperationResult:
        if active.dirty:
            raise ValueError("请先保存当前修改，再恢复包含旧来源修订的快照。")
        ref = active.project_ref
        path = self.projects.store_for(ref.identity).path
        with project_connection(path) as connection:
            saved = read_project(connection, ref)
            if saved.envelope.revision != active.persisted_project_revision:
                raise ProjectStorageConflict("project changed before restoring the snapshot")
            row = snapshot_record(connection, identity, historical.ref)
            settings = json.loads(row["source_settings"])
            old_variants = tuple(
                read_variant(connection, VariantRef(VariantId(value), ref.identity))[0]
                for value in saved.envelope.data["variant_ids"]
            )
        project = ProjectDto(
            replace(saved.envelope, revision=saved.envelope.revision + 1, data={**saved.envelope.data, **settings})
        )
        restored = {item.ref: _restore_variant(item, historical) for item in old_variants}
        target = restored[historical.ref]
        active_revision = next(item.revision for item in old_variants if item.ref == historical.ref)
        token = uuid4().hex

        def publish():
            with project_connection(path, write=True) as connection:
                _verify_project(connection, project, saved.envelope.revision)
                snapshot_record(connection, identity, historical.ref)
                for index, old in enumerate(old_variants):
                    if require_variant(connection, old.ref.identity)["revision"] != old.revision:
                        raise ProjectStorageConflict("a Variant changed before restoring the snapshot")
                    write_snapshot(
                        connection,
                        LifecycleSnapshot(ref, old.ref, old, "恢复快照前"),
                        f"{token}-{index}",
                    )
                connection.execute("DELETE FROM project_source")
                connection.execute(
                    """
                    INSERT INTO project_source (source_id, namespace, ordinal)
                    SELECT ss.source_id, s.namespace, ss.ordinal FROM snapshot_source AS ss
                    JOIN source AS s ON s.id = ss.source_id WHERE ss.snapshot_id = ?
                    """,
                    (identity,),
                )
                connection.execute("""
                    DELETE FROM entry_state WHERE entry_id IN (
                        SELECT e.id FROM source_entry AS e
                        WHERE e.source_id NOT IN (SELECT source_id FROM project_source)
                    )
                """)
                _write_project(connection, project)
                for old in old_variants:
                    version = restored[old.ref]
                    write_variant(connection, version, version.entries, old.revision)
                baseline = read_baselines(connection)
            self.baselines.replace_many(ref, tuple(restored), baseline, allow_empty=True)

        return self.lifecycle.commit_active_content(
            project,
            target,
            context,
            expected_project_revision=active.project.envelope.revision,
            expected_variant_revision=active_revision,
            before_publish=publish,
            persisted=True,
        )


def _restore_variant(current: VariantSnapshot, historical: VariantSnapshot) -> VariantSnapshot:
    current_by_key = {item.entry_key: item for item in current.entries}
    states = []
    is_target = current.ref == historical.ref
    for saved in historical.entries:
        now = current_by_key.get(saved.entry_key, VariantEntryState(saved.entry_key))
        if is_target:
            state = replace(saved, revision=EntryRevision(max(now.revision.value, saved.revision.value) + 1))
        else:
            state = now
        states.append(state)
    return VariantSnapshot(
        current.ref,
        historical.source_fingerprints,
        tuple(states),
        current.revision + 1,
        historical.label_library if is_target else current.label_library,
    )
