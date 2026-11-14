# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Bind a newly selected plugin to one write operation."""

from __future__ import annotations

from collections.abc import Iterable

from transbridge.application.contracts import DomainError, ErrorCategory, RequestContext
from transbridge.application.io.contracts import FormatId, SourceSnapshot
from transbridge.application.io.mutation import EntrySnapshot
from transbridge.application.io.text_source_options import snapshot_read_options

from .provisioning_models import ProjectSourcePreparationPort, ProjectSourceRequest


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
