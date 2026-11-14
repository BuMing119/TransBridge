# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Indexed conflict review reads without materializing an entire build."""

import json
from types import SimpleNamespace

from transbridge.application.terminology.conflict_review import TerminologyConflictDetails
from transbridge.application.terminology.conflict_usage import evidence_record_identity
from transbridge.application.terminology.errors import CursorStaleError, RepositoryConflictError
from transbridge.application.terminology.models import (
    BilingualEvidence,
    ConflictGroup,
    DraftRef,
    TermCandidate,
    TermDecision,
    TerminologyVersionRef,
)

from .conflict_queries import review_conflict_page
from .read_cancellation import cancellable_read, checked_rows


class ConflictReads:
    def __init__(self, repository):
        self.repository = repository
        self.connection = repository._connection

    def _line(self, ref):
        self.repository._require_build_ref(ref)
        row = self.connection.execute(
            "SELECT project_id, variant_id FROM builds WHERE build_key = ?", (ref.build_key,)
        ).fetchone()
        return row["project_id"], row["variant_id"]

    def _records(self, table, ref, identities, kind, cancellation=None):
        # JSON's table function avoids SQLite's bound-variable limit for large groups.
        return tuple(
            self.repository._decode(row[0], kind)
            for row in checked_rows(
                self.connection.execute(
                    f"SELECT payload_json FROM {table} WHERE build_key = ? "
                    "AND stable_id IN (SELECT value FROM json_each(?)) ORDER BY stable_id",
                    (ref.build_key, json.dumps(sorted(set(identities)))),
                ),
                cancellation,
            )
        )

    def latest_conflicts(self, project, variant):
        with self.repository._lock:
            ref = self.repository.latest_build_ref(project, variant)
            if ref is None:
                return ()
            return tuple(
                self.repository._decode(row[0], ConflictGroup)
                for row in self.connection.execute(
                    "SELECT payload_json FROM build_conflicts WHERE build_key = ? ORDER BY stable_id", (ref.build_key,)
                )
            )

    def evidence(self, ref, conflict, *, cancellation=None):
        with self.repository._lock, cancellable_read(self.connection, cancellation):
            self._line(ref)
            return self._records(
                "build_evidence",
                ref,
                (identity for variant in conflict.variants for identity in variant.evidence_ids),
                BilingualEvidence,
                cancellation,
            )

    def usage_counts(self, ref, conflicts, *, cancellation=None):
        with self.repository._lock, cancellable_read(self.connection, cancellation):
            self._line(ref)
            ids = {identity for group in conflicts for variant in group.variants for identity in variant.evidence_ids}
            rows = self.connection.execute(
                "SELECT stable_id, json_extract(payload_json, '$.fields.entry_key'), "
                "json_extract(payload_json, '$.fields.namespace'), "
                "json_extract(payload_json, '$.fields.source_chain.\"$tuple\"') FROM build_evidence "
                "WHERE build_key = ? AND stable_id IN (SELECT value FROM json_each(?))",
                (ref.build_key, json.dumps(sorted(ids))),
            )
            identities = {
                row[0]: evidence_record_identity(
                    SimpleNamespace(entry_key=row[1], namespace=row[2] or "", source_chain=json.loads(row[3]))
                )
                for row in checked_rows(rows, cancellation)
            }
            return {
                (group.conflict_group_id, variant.normalized_translation): len({
                    identities[key] for key in variant.evidence_ids if key in identities
                })
                for group in checked_rows(conflicts, cancellation)
                for variant in group.variants
            }

    def _draft_overlay(self, project, variant):
        row = self.connection.execute(
            "SELECT json_extract(payload_json, '$.fields.ref'), "
            "json_extract(payload_json, '$.fields.conflict_resolutions.\"$tuple\"') "
            "FROM drafts WHERE project_id = ? AND variant_id = ?",
            (project, variant),
        ).fetchone()
        if row is None:
            return None
        return SimpleNamespace(
            ref=self.repository._decode(row[0], DraftRef),
            conflict_resolutions=tuple(
                self.repository._decode(json.dumps(item), ConflictGroup) for item in json.loads(row[1] or "[]")
            ),
        )

    def page(self, ref, request, filters):
        with self.repository._lock:
            project, variant = self._line(ref)
            if self.repository.latest_build_ref(project, variant) != ref:
                raise CursorStaleError("术语构建已变化，请刷新异译列表")
            draft = self._draft_overlay(project, variant)
            effective = None
            if draft is None:
                row = self.connection.execute(
                    "SELECT v.version_key, json_extract(v.payload_json, '$.fields.ref'), "
                    "json_extract(v.payload_json, '$.fields.conflicts.\"$tuple\"') FROM versions v "
                    "JOIN effective_versions e ON v.version_key = e.version_key "
                    "WHERE e.project_id = ? AND e.variant_id = ? AND v.build_key = ?",
                    (project, variant, ref.build_key),
                ).fetchone()
                if row is not None:
                    conflicts = tuple(
                        self.repository._decode(json.dumps(item), ConflictGroup) for item in json.loads(row[2] or "[]")
                    )
                    effective = SimpleNamespace(
                        ref=self.repository._decode(row[1], TerminologyVersionRef), build_ref=ref, conflicts=conflicts
                    )
            return review_conflict_page(self.connection, SimpleNamespace(ref=ref), draft, effective, request, filters)

    def details(self, conflict, project, variant, *, cancellation=None):
        with self.repository._lock, cancellable_read(self.connection, cancellation):
            self.repository._require_project(project)
            if (conflict.project_id, conflict.variant_id) != (project, variant):
                raise PermissionError("异译不属于当前工程和翻译版本")
            ref = self.repository.latest_build_ref(project, variant)
            pinned = (
                ()
                if ref is None
                else self._records("build_conflicts", ref, (conflict.conflict_group_id,), ConflictGroup, cancellation)
            )
            if not pinned or pinned[0].variants != conflict.variants:
                raise RepositoryConflictError("异译证据已变化，请刷新后重新审核")
            pinned = pinned[0]
            draft = self._draft_overlay(project, variant)
            if draft is None:
                raise RepositoryConflictError("当前没有可审核的草稿，请先构建术语库")
            projected = next(
                (item for item in draft.conflict_resolutions if item.conflict_group_id == pinned.conflict_group_id),
                pinned,
            )
            candidates = self._records(
                "build_candidates",
                ref,
                (key for variant in pinned.variants for key in variant.candidate_ids),
                TermCandidate,
                cancellation,
            )
            evidence = self._records(
                "build_evidence",
                ref,
                (key for variant in pinned.variants for key in variant.evidence_ids),
                BilingualEvidence,
                cancellation,
            )
            decisions = tuple(
                self.repository._decode(row[0], TermDecision)
                for row in checked_rows(
                    self.connection.execute(
                        "SELECT item.value FROM drafts d, "
                        "json_each(d.payload_json, '$.fields.decisions.\"$tuple\"') item "
                        "WHERE d.project_id = ? AND d.variant_id = ? "
                        "AND json_extract(item.value, '$.fields.normalized_original') = ?",
                        (project, variant, pinned.normalized_original),
                    ),
                    cancellation,
                )
            )
            plugins = tuple(
                sorted({
                    *(item.plugin_scope for item in evidence if item.plugin_scope),
                    *(item.scope.plugin_id for item in candidates if item.scope.plugin_id),
                    *(item.scope.plugin_id for item in decisions if item.scope.plugin_id),
                })
            )
            return TerminologyConflictDetails(ref, draft.ref, projected, candidates, evidence, plugins, decisions)
