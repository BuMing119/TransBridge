"""Render SSE plugins and their localized strings from confirmed source snapshots."""

from __future__ import annotations

from dataclasses import replace
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from transbridge.application.contracts import Diagnostic, OperationCounts, OperationOutcome, OperationResult
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.plugin_parser import PluginParser
from transbridge.writer.plugin_writer import PluginWriter

from .contracts import FormatId, SourceDescriptor, SourceSnapshot, WriteRequest
from .identity import EntryKey
from .plugin_bsa_output import (
    bsa_output_enabled,
    bsa_output_path,
    localized_loose_paths,
    localized_render_target,
    pack_localized_strings,
    validate_bsa_destination,
)
from .plugin_localized_sources import (
    _localized_source_snapshots as _localized_source_snapshots,
    _snapshot_localized_sources,
    localized_entry_identity,
    localized_id_variants,
)
from .plugin_publication_source import prepared_plugin_source
from .stage_policy import DEFAULT_STAGE_POLICY, StageOperation, StagePolicyPort
from .strings_adapter import LocalizedStringsAdapter


def write_plugin(request: WriteRequest) -> OperationResult[tuple[str, ...]]:
    from .legacy_adapters import (
        _cancelled,
        _copy_failure,
        _failed_diagnostics,
        _failed_operation,
        _local_path,
        _snapshot_namespace,
    )

    snapshot = request.source_snapshot
    if snapshot is None:
        return _failed_operation(
            Diagnostic("SOURCE_SNAPSHOT_REQUIRED", "Plugin writes require their source snapshot."),
            request.context.run_id,
        )
    try:
        localized_sources = _snapshot_localized_sources(snapshot)
        if dict(snapshot.metadata).get("localized_lookup_detected") and not localized_sources:
            return _failed_diagnostics(
                (
                    Diagnostic(
                        "LOCALIZED_SOURCE_SNAPSHOT_UNAVAILABLE",
                        "插件的本地化字符串快照不完整，无法安全写回。",
                    ),
                ),
                request.context.run_id,
            )
        options = dict(request.options)
        options.setdefault("language", dict(snapshot.metadata).get("localized_language", "english"))
        request = replace(request, options=tuple(options.items()))
        source_path = _local_path(snapshot.source.uri)
        prepared = prepared_plugin_source(snapshot, options, for_write=True)
        parser, source_entries = prepared.parser, prepared.entries
        if parser is None:
            raise ValueError("plugin source parser was retired before rendering")
        plugin = parser.get_plugin()
        if plugin is None:
            raise ValueError("plugin parser did not produce a source plugin")
        expected_namespace = _snapshot_namespace(snapshot)
        invalid = tuple(
            (index, getattr(entry, "entry_key", None))
            for index, entry in enumerate(request.entries)
            if not isinstance(getattr(entry, "entry_key", None), EntryKey)
            or entry.entry_key.namespace != expected_namespace
        )
        if invalid:
            return _failed_diagnostics(
                tuple(
                    Diagnostic(
                        "SOURCE_IDENTITY_CONFLICT",
                        "A plugin entry belongs to a different source snapshot.",
                        details=(("record_index", index), ("entry_key", str(entry_key))),
                    )
                    for index, entry_key in invalid
                ),
                request.context.run_id,
            )
        known = {(entry.key, entry.identity.original): entry for entry in source_entries}
        missing = tuple(
            entry.entry_key.local_key
            for entry in request.entries
            if (entry.key, entry.identity.original) not in known
            or entry.original != known[(entry.key, entry.identity.original)].original
            or entry.requires_original_match != known[(entry.key, entry.identity.original)].requires_original_match
        )
        if missing:
            return _failed_diagnostics(
                (
                    Diagnostic(
                        "SOURCE_LOCATOR_NOT_FOUND",
                        "One or more plugin entries cannot be uniquely located.",
                        details=(("locators", missing),),
                    ),
                ),
                request.context.run_id,
            )
        if any(entry.string_id != known[(entry.key, entry.identity.original)].string_id for entry in request.entries):
            return _failed_operation(
                Diagnostic("SOURCE_LOCATOR_CONFLICT", "Localized string IDs differ from the confirmed source."),
                request.context.run_id,
            )
        # Hydrated DTOs do not carry the legacy dsd_type facade. Reuse the
        # library-derived field type from the already-verified source locator.
        request = _source_typed_request(request, known)
        projected_entries = _project_stage_entries(
            request.entries,
            request.stage_policy or DEFAULT_STAGE_POLICY,
        )
        writer = PluginWriter(
            plugin,
            strings_lookup=parser.get_strings_lookup(),
            language=str(options.get("language", "english")),
        )
        writer.apply_collection(TranslationEntryCollection(projected_entries))
        if _cancelled(request.cancellation):
            return OperationResult.cancelled(run_id=request.context.run_id)
        target = _local_path(request.target.uri)
        target.parent.mkdir(parents=True, exist_ok=True)
        if localized_sources:
            string_artifacts: list[str] = []
            localized_diagnostics: list[Diagnostic] = []
            packed = bsa_output_enabled(snapshot, request.options)
            validate_bsa_destination(snapshot, target, request.options)
            with localized_render_target(target, packed=packed) as render_target:
                localized_requests = _localized_write_requests(request, render_target, localized_sources)
                localized_validations = tuple(
                    adapter.validate_write(localized_request) for adapter, localized_request in localized_requests
                )
                failed_validation = next(
                    (item for item in localized_validations if item.outcome is not OperationOutcome.COMPLETED), None
                )
                if failed_validation is not None:
                    return _copy_failure(failed_validation)
                for adapter, localized_request in localized_requests:
                    localized_result = adapter.write(localized_request)
                    if localized_result.outcome is not OperationOutcome.COMPLETED:
                        return _copy_failure(localized_result)
                    string_artifacts.extend(localized_result.artifact_refs)
                    localized_diagnostics.extend(localized_result.diagnostics)
                if packed:
                    string_artifacts = [pack_localized_strings(target, render_target, string_artifacts)]
            plugin_saved = writer._inline_count > 0 or target != source_path
            if plugin_saved:
                if writer._inline_count:
                    plugin.save(target)
                else:
                    # External Strings changed; the plugin's records and IDs did
                    # not. Preserve the confirmed bytes instead of reserializing
                    # and recompressing every record just to change its filename.
                    target.write_bytes(snapshot.content)
            artifacts = tuple(([str(target)] if plugin_saved else []) + string_artifacts)
            if not artifacts:
                raise ValueError("localized plugin writer produced no artifact")
            return OperationResult.completed(
                artifacts,
                diagnostics=tuple(localized_diagnostics),
                counts=OperationCounts(succeeded=len(request.entries)),
                artifact_refs=artifacts,
                run_id=request.context.run_id,
            )
        result = writer.write(target)
        artifacts = tuple(
            str(path)
            for path in ([target] if result.get("esp_saved") else []) + list(result.get("strings_written", ()))
        )
        if not artifacts:
            raise ValueError("plugin writer produced no artifact")
    except (OSError, TypeError, ValueError) as exc:
        logging.getLogger(__name__).exception("Plugin snapshot rendering failed for %s", snapshot.source.uri)
        return _failed_operation(
            Diagnostic("PLUGIN_WRITE_FAILED", f"SSE plugin write failed ({type(exc).__name__}): {exc}"),
            request.context.run_id,
        )
    return OperationResult.completed(
        artifacts,
        counts=OperationCounts(succeeded=len(request.entries)),
        artifact_refs=artifacts,
        run_id=request.context.run_id,
    )


def _blocking_stage_diagnostics(
    entries: tuple[object, ...],
    policy: StagePolicyPort,
) -> tuple[Diagnostic, ...]:
    diagnostics: list[Diagnostic] = []
    for index, entry in enumerate(entries):
        decision = policy.evaluate(
            getattr(entry, "stage", None),
            getattr(entry, "translation", ""),
            StageOperation.PUBLISH,
            original=getattr(entry, "original", ""),
        )
        if not decision.blocks_publish:
            continue
        diagnostic = decision.diagnostic or Diagnostic(
            "STAGE_PUBLISH_BLOCKED",
            "The entry stage blocks plugin publication.",
        )
        diagnostics.append(
            replace(
                diagnostic,
                details=(
                    *diagnostic.details,
                    ("record_index", index),
                    ("entry_key", str(getattr(entry, "entry_key", None))),
                    ("string_id", getattr(entry, "string_id", None)),
                ),
            )
        )
    return tuple(diagnostics)


def _project_stage_entries(
    entries: tuple[object, ...],
    policy: StagePolicyPort,
) -> tuple[TranslationEntry, ...]:
    projected: list[TranslationEntry] = []
    for entry in entries:
        if not isinstance(entry, TranslationEntry):
            raise TypeError("plugin writes require TranslationEntry values")
        decision = policy.evaluate(
            entry.stage,
            entry.translation,
            StageOperation.PUBLISH,
            original=entry.original,
        )
        if decision.publish_text is None:
            raise ValueError("blocking stage decision passed validation")
        projected.append(replace(entry, translation=decision.publish_text))
    return tuple(projected)


def _localized_write_requests(
    request: WriteRequest,
    plugin_target: Path,
    sources: tuple[tuple[SourceSnapshot, tuple[TranslationEntry, ...]], ...],
) -> tuple[tuple[LocalizedStringsAdapter, WriteRequest], ...]:
    variants = localized_id_variants(sources)
    plugin_entries: dict[tuple[FormatId, int], TranslationEntry] = {}
    for entry in request.entries:
        if not isinstance(entry, TranslationEntry) or entry.string_id is None:
            continue
        identity = localized_entry_identity(entry, variants)
        existing = plugin_entries.get(identity)
        if existing is not None and (
            existing.translation,
            existing.stage,
            existing.original,
        ) != (entry.translation, entry.stage, entry.original):
            raise ValueError("plugin entries disagree for the same localized string ID")
        plugin_entries[identity] = entry

    mapped_ids: set[tuple[FormatId, int]] = set()
    localized_requests: list[tuple[LocalizedStringsAdapter, WriteRequest]] = []
    language = str(dict(request.options).get("language", "english"))
    for snapshot, base_entries in sources:
        adapter = LocalizedStringsAdapter(snapshot.format_id)
        mapped_entries: list[TranslationEntry] = []
        for base_entry in base_entries:
            identity = (snapshot.format_id, base_entry.string_id)
            plugin_entry = plugin_entries.get(identity)
            if plugin_entry is None:
                mapped_entries.append(base_entry)
                continue
            mapped_ids.add(identity)
            mapped_entries.append(
                replace(
                    base_entry,
                    original=plugin_entry.original,
                    translation=plugin_entry.translation,
                    stage=plugin_entry.stage,
                )
            )
        suffix = _strings_suffix(snapshot.format_id)
        target = plugin_target.parent / "Strings" / f"{plugin_target.stem}_{language.capitalize()}{suffix}"
        localized_requests.append((
            adapter,
            WriteRequest(
                SourceDescriptor(str(target), target.name),
                snapshot.format_id,
                tuple(mapped_entries),
                request.variant_revision,
                request.context,
                source_snapshot=snapshot,
                options=(
                    tuple({**dict(request.options), "source_authority": "hydration-v2"}.items())
                    if dict(snapshot.metadata).get("strings.storage") == "bsa"
                    else request.options
                ),
                cancellation=request.cancellation,
                stage_policy=request.stage_policy,
            ),
        ))
    missing = tuple(sorted(set(plugin_entries).difference(mapped_ids)))
    if missing:
        raise ValueError("plugin localized string IDs are absent from the complete source snapshots")
    return tuple(localized_requests)


def _parser_from_snapshot(snapshot: SourceSnapshot, options: dict):
    """The parser sees only materialized snapshot bytes, never mutable source paths."""
    if snapshot.content is None:
        raise ValueError("plugin writing requires captured source bytes")
    language = str(dict(snapshot.metadata).get("localized_language", options.get("language", "english")))
    with TemporaryDirectory(prefix="transbridge-plugin-snapshot-") as directory:
        source = Path(directory) / Path(snapshot.source.uri).name
        source.write_bytes(snapshot.content)
        sources = _snapshot_localized_sources(snapshot)
        if sources:
            strings = source.parent / "Strings"
            strings.mkdir()
            for localized_snapshot, _entries in sources:
                if localized_snapshot.content is None:
                    raise ValueError("localized writing requires captured source bytes")
                suffix = _strings_suffix(localized_snapshot.format_id)
                (strings / f"{source.stem}_{language.capitalize()}{suffix}").write_bytes(localized_snapshot.content)
        parser = PluginParser()
        entries = parser.parse_plugin(
            source,
            # Write locators require the complete source, regardless of read/display filters.
            skip_empty=False,
            language=language,
            discover_sibling_strings=bool(sources),
        )
        return parser, entries


def plugin_artifact_paths(snapshot: SourceSnapshot, target: str, options=()) -> tuple[str, ...]:
    """Enumerate final outputs before any staging or overwrite can begin."""
    if snapshot.format_id is not FormatId.PLUGIN_SSE:
        return (target,)
    companions = localized_loose_paths(snapshot, target, options)
    if bsa_output_enabled(snapshot, options):
        return (target, str(bsa_output_path(Path(target))))
    return (target, *companions)


def localized_write_requests(request: WriteRequest):
    if request.source_snapshot is None:
        return ()
    sources = _snapshot_localized_sources(request.source_snapshot)
    variants = localized_id_variants(sources)
    if any(
        entry.string_id is not None and len(variants.get(entry.string_id, ())) > 1 and not entry.dsd_type
        for entry in request.entries
    ):
        # The bundle validator receives the original hydrated DTOs. Only when
        # an ID is shared do we need the library's source field type again.
        source_entries = prepared_plugin_source(request.source_snapshot, dict(request.options)).entries
        request = _source_typed_request(
            request, {(entry.key, entry.identity.original): entry for entry in source_entries}
        )
    return _localized_write_requests(request, Path(request.target.uri), sources)


def _source_typed_request(request, known):
    return replace(
        request,
        entries=tuple(
            replace(entry, dsd_type=known[(entry.key, entry.identity.original)].dsd_type) for entry in request.entries
        ),
    )


def _strings_suffix(format_id: FormatId) -> str:
    return {FormatId.STRINGS: ".strings", FormatId.DLSTRINGS: ".dlstrings", FormatId.ILSTRINGS: ".ilstrings"}[format_id]
