"""Purpose-specific source reads for sync and task conflict validation."""

from __future__ import annotations

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.io.plugin_dialogue_order import dialogue_order_from_snapshot
from transbridge.application.projects.source_extraction import hydrate_extracted, reconstructed_source_snapshot
from transbridge.application.projects.source_hydration_cache import SourceReadHydration
from transbridge.application.translation.result_merge import source_locator_signature
from transbridge.parser.plugin.dialogue_order_binary import DialogueOrderCancelled
from transbridge.persistence.v2.ids import ProjectId
from transbridge.persistence.v2.variant import SourceFingerprint

from .connection import project_connection
from .format import ProjectStorageConflict
from .reads import iter_source_entries, read_entries, read_sources


class ProjectSourceReads:
    def __init__(self, projects, lifecycle) -> None:
        self.projects = projects
        self.lifecycle = lifecycle

    def source(self, variant, namespace, *, include_dialogue_order=False, cancellation=None) -> SourceReadHydration:
        _check_cancelled(cancellation)
        fingerprint = next(item for item in variant.source_fingerprints if item.namespace == namespace)
        draft = self._drafts(variant).get(namespace)
        if draft is not None:
            hydration = hydrate_extracted(draft, None)
            entries = hydration.entries
            metadata = hydration.source_snapshot
        else:
            path = self.projects.store_for(variant.ref.project_id).path
            with project_connection(path) as connection:
                sources = read_sources(connection, namespace)
                if len(sources) != 1 or sources[0].fingerprint != fingerprint.sha256:
                    raise ProjectStorageConflict("source changed before its translation facts could be loaded")
                entries = []
                for row in iter_source_entries(connection, variant.ref.identity, namespace, include_removed=True):
                    _check_cancelled(cancellation)
                    entries.append(row.snapshot)
            metadata = (
                reconstructed_source_snapshot(sources[0], ())
                if include_dialogue_order and sources[0].format_id.value == "plugin.sse"
                else None
            )
        try:
            dialogue = (
                dialogue_order_from_snapshot(metadata, cancellation=cancellation)
                if include_dialogue_order and metadata is not None
                else None
            )
        except DialogueOrderCancelled as exc:
            raise DomainError(
                ErrorCategory.CANCELLED, "DIALOGUE_ORDER_CANCELLED", "已取消对话排序准备。", cause=exc
            ) from exc
        return SourceReadHydration(SourceFingerprint(namespace, fingerprint.sha256), tuple(entries), dialogue)

    def result_texts(self, project, variant, keys, _context):
        fingerprints = {item.namespace: item.sha256 for item in variant.source_fingerprints}
        selected = set(keys)
        if not selected:
            return {}
        drafts = self._drafts(variant)
        draft_rows = {
            item.snapshot.entry_key: item.snapshot
            for source in drafts.values()
            for item in source.entries
            if item.snapshot.entry_key in selected
        }
        path = self.projects.store_for(ProjectId(project.envelope.identity)).path
        with project_connection(path) as connection:
            saved_rows = read_entries(
                connection,
                variant.ref.identity,
                (key for key in selected if key.namespace not in drafts),
            )
        entries = {row.snapshot.entry_key: row.snapshot for row in saved_rows}
        entries.update(draft_rows)
        return {
            key: (
                entry.original,
                entry.context or "",
                source_locator_signature(entry.metadata, entry.string_id, fingerprints[key.namespace]),
            )
            for key, entry in entries.items()
            if key.namespace in fingerprints
        }

    def _drafts(self, variant):
        active = self.lifecycle.active
        if active is None or active.formal_variant_ref != variant.ref:
            return {}
        fingerprints = {item.namespace: item.sha256 for item in variant.source_fingerprints}
        return {
            item.namespace: item
            for item in active.source_additions
            if fingerprints.get(item.namespace) == item.fingerprint
        }


def _check_cancelled(cancellation):
    if cancellation is not None and cancellation.is_cancelled:
        raise DomainError(ErrorCategory.CANCELLED, "DIALOGUE_ORDER_CANCELLED", "已取消来源读取。")
