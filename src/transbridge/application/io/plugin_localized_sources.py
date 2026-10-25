"""Capture complete localized plugin inputs, including raw BSA member bytes."""

from dataclasses import replace
import hashlib
import logging
import os
from pathlib import Path

from sse_plugin_interface.flags import RecordFlags

from transbridge.application.contracts import Diagnostic, OperationOutcome
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.localized_resources import capture_plugin_resources, capture_strings_directory
from transbridge.parser.strings_file import PluginStringsWriter

from .contracts import FormatId, ParseRequest, SourceDescriptor, SourceSnapshot
from .strings_adapter import LocalizedStringsAdapter

_logger = logging.getLogger(__name__)
_FORMATS = {".strings": FormatId.STRINGS, ".dlstrings": FormatId.DLSTRINGS, ".ilstrings": FormatId.ILSTRINGS}


class LocalizedSnapshotCancelled(ValueError):
    """The parent plugin parse must report cancellation rather than a bad source."""


def _localized_source_snapshots(
    plugin_path: Path,
    language: str,
    request: ParseRequest,
    *,
    lookup=None,
    plugin_content: bytes | None = None,
) -> tuple[tuple[SourceSnapshot, tuple[TranslationEntry, ...]], ...]:
    resources = getattr(lookup, "resources", ()) or _capture_plugin_templates(plugin_path, language, plugin_content)
    sources = []
    for resource in resources:
        format_id = _FORMATS[resource.suffix]
        physical = str(resource.source_path.resolve())
        uri = physical if resource.member_path is None else f"bsa:{physical}!/{resource.member_path}"
        parsed = LocalizedStringsAdapter(format_id).parse_bytes(
            ParseRequest(
                SourceDescriptor(uri, resource.name, len(resource.content)),
                request.context,
                format_id,
                cancellation=request.cancellation,
            ),
            resource.content,
        )
        if parsed.outcome is OperationOutcome.CANCELLED:
            raise LocalizedSnapshotCancelled("localized source capture cancelled")
        if parsed.outcome is not OperationOutcome.COMPLETED or parsed.source_snapshot is None:
            codes = ", ".join(item.code for item in parsed.diagnostics)
            raise ValueError(f"localized source snapshot is invalid: {uri} ({codes})")
        snapshot = replace(
            parsed.source_snapshot,
            metadata=(
                *parsed.source_snapshot.metadata,
                ("strings.storage", "loose" if resource.member_path is None else "bsa"),
                ("strings.origin_path", physical),
                ("strings.member_path", resource.member_path),
            ),
        )
        sources.append((snapshot, parsed.entries))
    return tuple(sources)


def _capture_plugin_templates(plugin_path: Path, language: str, plugin_content: bytes | None):
    if plugin_content is not None and not (int.from_bytes(plugin_content[8:12], "little") & RecordFlags.Localized):
        return capture_strings_directory(plugin_path.parent / "Strings", plugin_path.stem, language)
    return capture_plugin_resources(plugin_path, language)


def localized_id_variants(sources) -> dict[int, tuple[FormatId, ...]]:
    variants = {}
    for snapshot, entries in sources:
        for entry in entries:
            variants.setdefault(entry.string_id, []).append(snapshot.format_id)
    return {identifier: tuple(formats) for identifier, formats in variants.items()}


def localized_entry_identity(entry, variants) -> tuple[FormatId, int]:
    """Reuse the established writer's field routing only when an ID spans tables."""
    formats = variants.get(entry.string_id, ())
    if len(formats) == 1:
        return formats[0], entry.string_id
    subrecord_type = entry.dsd_type.rsplit(" ", 1)[-1]
    if not subrecord_type:
        raise ValueError("captured localized source has no library subrecord type")
    format_id = _FORMATS[PluginStringsWriter._ext_for(subrecord_type)]
    if format_id not in formats:
        raise ValueError("localized string ID has no source table for its subrecord type")
    return format_id, entry.string_id


def _snapshot_localized_sources(
    snapshot: SourceSnapshot,
) -> tuple[tuple[SourceSnapshot, tuple[TranslationEntry, ...]], ...]:
    value = dict(snapshot.metadata).get("localized_sources", ())
    if not isinstance(value, tuple):
        raise ValueError("plugin localized snapshot metadata is invalid")
    for item in value:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], SourceSnapshot)
            or not isinstance(item[1], tuple)
        ):
            raise ValueError("plugin localized snapshot metadata is invalid")
    return value


def localized_physical_inputs(snapshot: SourceSnapshot) -> tuple[tuple[str, SourceSnapshot | None], ...]:
    """Physical leases use archive fingerprints, never a member's virtual URI/hash."""
    inputs = {}
    for companion, _entries in _snapshot_localized_sources(snapshot):
        metadata = dict(companion.metadata)
        path = str(metadata.get("strings.origin_path", companion.source.uri))
        inputs[path] = None if metadata.get("strings.storage") == "bsa" else companion
    return tuple(inputs.items())


def validate_localized_snapshot(snapshot: SourceSnapshot) -> Diagnostic | None:
    """Strict live-source callers verify selection and bytes; hydration uses captured bytes."""
    sources = _snapshot_localized_sources(snapshot)
    if not sources:
        return None
    language = str(dict(snapshot.metadata).get("localized_language", "english"))
    expected = tuple(
        (
            {value: key for key, value in _FORMATS.items()}[item.format_id],
            _path(dict(item.metadata).get("strings.origin_path", item.source.uri)),
            _member(dict(item.metadata).get("strings.member_path")),
            item.sha256,
        )
        for item, _entries in sources
    )
    try:
        resources = _capture_plugin_templates(Path(snapshot.source.uri), language, snapshot.content)
    except (OSError, ValueError) as exc:
        _logger.exception("Unable to verify captured localized resources for %s", snapshot.source.uri)
        return Diagnostic(
            "SOURCE_SNAPSHOT_UNAVAILABLE",
            f"无法重新读取插件的本地化字符串资源：{Path(snapshot.source.uri).name}（{type(exc).__name__}）。",
        )
    actual = tuple(
        (item.suffix, _path(item.source_path), _member(item.member_path), hashlib.sha256(item.content).hexdigest())
        for item in resources
    )
    if actual != expected:
        return Diagnostic(
            "SOURCE_FINGERPRINT_CONFLICT" if actual else "SOURCE_SNAPSHOT_UNAVAILABLE",
            "插件依赖的本地化字符串资源在读取后发生变化；本次未写回，请重新读取源文件。",
            details=(
                ("source", snapshot.source.uri),
                ("expected_resources", len(expected)),
                ("actual_resources", len(actual)),
            ),
        )
    return None


def _path(value) -> str:
    return os.path.normcase(str(Path(value).resolve()))


def _member(value) -> str:
    return str(value or "").replace("\\", "/").casefold()
