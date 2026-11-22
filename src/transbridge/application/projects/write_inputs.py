# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Bind a newly selected plugin to one write operation."""

from __future__ import annotations

from collections.abc import Iterable

from transbridge.application.contracts import DomainError, ErrorCategory, RequestContext
from transbridge.application.io.contracts import FormatId, SourceSnapshot
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.io.mutation import EntrySnapshot
from transbridge.application.io.text_source_options import snapshot_read_options

from .provisioning_models import ProjectSourcePreparationPort, ProjectSourceRequest


def native_write_template(slot):
    reader = getattr(slot.collection, "native_template", None)
    return reader() if reader is not None else slot.source_snapshot


class ProjectPluginReferences:
    """Resolve saved and pending input references against one project/version read token."""

    def __init__(self, references, project_id, variant_id, project_revision, variant_revision, *, expected_token=None):
        self.references = references
        self.store = references.store_for(project_id)
        self.token = self.store.read_token(variant_id)
        self._revisions = project_revision, variant_revision
        self._saved = {info.namespace: info for info in self.store.sources(native_fields=False)}
        self._pending = None
        self._pending_sources = {}
        if (
            expected_token is not None
            and self.token != expected_token
            or self.token.project_revision > project_revision
            or self.token.variant_revision > variant_revision
        ):
            raise ValueError("工程版本已变化，请重新发起导出。")

    def find(self, snapshot, namespace):
        if not namespace:
            return None
        namespace = SourceNamespace(namespace)
        if self._pending is None and (
            self.token.project_revision != self._revisions[0] or namespace not in self._saved
        ):
            self._pending = self.references.capture_pending_sources(
                self.token.project_id,
                self.token.variant_id,
                project_revision=self._revisions[0],
                variant_revision=self._revisions[1],
            )
            if self._pending is None:
                raise ValueError("工程来源已变化，请重新发起导出。")
            self._pending_sources = {info.namespace: info for info in self._pending.sources}
        info = self._pending_sources.get(namespace) or self._saved.get(namespace)
        return info if info and info.format_id is snapshot.format_id and info.fingerprint == snapshot.sha256 else None

    def remember(self, source_id, info, path):
        values = dict(expected_fingerprint=info.fingerprint, expected_path=info.reference_path, reference_path=path)
        if info.namespace in self._pending_sources:
            return self.references.update_pending_source_reference(self._pending, source_id, info.namespace, **values)
        return self.references.update_source_reference(
            self.token.project_id, source_id, info.namespace, token=self.token, **values
        )


def bind_plugin_write_input(
    expected: SourceSnapshot,
    entries: Iterable[EntrySnapshot],
    path: str,
    context: RequestContext,
    preparer: ProjectSourcePreparationPort,
    *,
    input_encoding: str | None = None,
) -> SourceSnapshot:
    if expected.format_id is not FormatId.PLUGIN_SSE:
        raise ValueError("this operation input must match a project plugin source")
    metadata = dict(expected.metadata)
    options = {
        "skip_empty": False,
        "discover_sibling_strings": True,
        **snapshot_read_options(expected),
    }
    if "localized_language" in metadata:
        options["language"] = metadata["localized_language"]
    if "source_namespace" in metadata:
        options["source_namespace"] = metadata["source_namespace"]
    try:
        prepared = preparer.prepare_source(
            ProjectSourceRequest(path, FormatId.PLUGIN_SSE, expected.sha256, tuple(options.items())),
            context,
            role="primary",
            common_options=(),
        )
    except DomainError as exc:
        if exc.code != "PLUGIN_LOCALIZED_INPUT_REQUIRED":
            raise
        raise DomainError(
            ErrorCategory.PREREQUISITE, "WRITE_STRINGS_INPUT_REQUIRED", "所选插件需要本次操作可读取的配套 Strings。"
        ) from exc
    hydration = prepared.hydration
    if hydration is None or hydration.source_snapshot.content is None:
        raise DomainError(ErrorCategory.PREREQUISITE, "WRITE_PLUGIN_INPUT_REQUIRED", "本次写回需要可读取的插件。")
    chosen = hydration.source_snapshot
    chosen_metadata = dict(chosen.metadata)
    if metadata.get("localized_lookup_detected") and not chosen_metadata.get("localized_sources"):
        raise DomainError(
            ErrorCategory.PREREQUISITE, "WRITE_STRINGS_INPUT_REQUIRED", "所选插件需要本次操作可读取的配套 Strings。"
        )
    actual = {entry.entry_key: entry for entry in hydration.entries}
    for entry in entries:
        matched = actual.get(entry.entry_key)
        if matched is None or (matched.original, matched.context, matched.string_id) != (
            entry.original,
            entry.context,
            entry.string_id,
        ):
            raise DomainError(
                ErrorCategory.CONFLICT,
                "WRITE_PLUGIN_ENTRY_MISMATCH",
                "所选插件或配套 Strings 与工程保存的词条不匹配。",
            )
    return chosen
