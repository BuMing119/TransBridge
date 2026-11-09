# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Extract durable translation facts from one ephemeral parser result."""

from __future__ import annotations

from dataclasses import replace
from pathlib import PureWindowsPath

from transbridge.application.io.contracts import FormatId, SourceDescriptor, SourceSnapshot
from transbridge.application.io.plugin_dialogue_order import METADATA_KEY
from transbridge.application.io.sst_export import PLUGIN_SST_MASTERS, PLUGIN_SST_UNAVAILABLE
from transbridge.converter.eet_entries import EET_CODEC_VERSION
from transbridge.converter.sst_entries import SST_CODEC_VERSION

from .native_source_formats import SST_FORMATS, extract_native_fields, reconstruct_native
from .provisioning_models import PreparedProjectSource, PreparedSourceHydration
from .source_fields import entry_fields, format_fields, parse_options, source_name
from .storage_contracts import ExtractedSource, SourceInfo, StoredEntry
from .translation_templates import STRINGS_FORMATS, XML_FORMATS, extract_xml_records, rebuild_strings, rebuild_xml


def extract_source(prepared: PreparedProjectSource) -> ExtractedSource:
    """Preserve the parser's physical entry order and discard its source lease."""
    hydration = prepared.hydration
    if hydration is None:
        raise ValueError("source extraction requires parsed source entries")
    source = hydration.source_snapshot
    descriptor = prepared.to_dict()
    fingerprint = prepared.baseline.fingerprint
    if fingerprint.sha256 is None:
        raise ValueError("source extraction requires a verified fingerprint")
    fields = {"source_role": descriptor.get("role", "primary")}
    if source.encoding is not None:
        fields["encoding"] = source.encoding
    if source.bom:
        fields["bom"] = source.bom.hex()
    if source.format_id is FormatId.PLUGIN_SSE:
        metadata = dict(source.metadata)
        for name in (
            METADATA_KEY,
            "localized_language",
            "localized_lookup_detected",
            "stage_policy_version",
            PLUGIN_SST_MASTERS,
            PLUGIN_SST_UNAVAILABLE,
        ):
            if name in metadata:
                fields[name] = metadata[name]
    native_key = None
    native_records = ()
    format_records = ()
    if source.format_id is FormatId.BINARY_EET or source.format_id in SST_FORMATS:
        if source.content is None:
            raise ValueError("native translation fields require the current operation's parsed content")
        native_key, header, native_records = extract_native_fields(source.format_id, source.content)
        fields[f"{native_key}_header"] = header
        if len(native_records) != len(hydration.entries):
            raise ValueError("native translation records and parsed entry count differ")
    elif source.format_id in XML_FORMATS:
        if source.content is None:
            raise ValueError("XML translation records require the current operation's parsed content")
        fields["xml_header"], format_records = extract_xml_records(source.content)
    states = {entry.entry_key: entry for entry in prepared.baseline.entries}
    entries = []
    for index, entry in enumerate(hydration.entries):
        metadata = dict(entry_fields(entry.metadata))
        if native_key is not None:
            metadata[f"{native_key}.native"] = native_records[index]
        state = states.pop(entry.entry_key, None)
        if state is None:
            raise ValueError("parsed source entry is absent from the source baseline")
        entries.append(
            StoredEntry(
                replace(
                    entry, translation="", stage=0, external_refs=(), provenance=(), metadata=tuple(metadata.items())
                ),
                state,
            )
        )
    if states:
        raise ValueError("source baseline includes entries absent from the parsed source")
    return ExtractedSource(
        fingerprint.namespace,
        source_name(source.source.display_name or PureWindowsPath(hydration.location).name),
        hydration.format_id,
        fingerprint.sha256,
        tuple(entries),
        tuple(parse_options((descriptor.get("format_options") or {}).items()).items()),
        tuple(format_fields(fields.items()).items()),
        format_records,
    )


def reconstructed_source_snapshot(
    info: SourceInfo, entries: tuple[StoredEntry, ...], format_records: tuple[tuple[str, str | None], ...] = ()
) -> SourceSnapshot:
    """Build a temporary native translation document or an input-free plugin description."""
    descriptor = SourceDescriptor(f"project-source:{info.namespace.value}", info.name)
    fields = dict(info.format_fields)
    metadata = {"source_namespace": info.namespace.value}
    encoding = fields.get("encoding") or "utf-8"
    bom = bytes.fromhex(fields.get("bom", ""))
    if info.format_id is FormatId.BINARY_EET or info.format_id in SST_FORMATS:
        prefix, codec = (
            ("eet", EET_CODEC_VERSION) if info.format_id is FormatId.BINARY_EET else ("sst", SST_CODEC_VERSION)
        )
        content = reconstruct_native(info.format_id, fields[f"{prefix}_header"], entries)
        metadata[f"{prefix}_codec_version"] = codec
        snapshot = SourceSnapshot.from_bytes(descriptor, info.format_id, content, metadata=tuple(metadata.items()))
        if snapshot.sha256 != info.fingerprint:
            raise ValueError("native translation fields do not reconstruct the verified source")
        return snapshot
    if info.format_id in XML_FORMATS:
        content = rebuild_xml(fields["xml_header"], format_records, encoding, bom)
        metadata["translation_source_fingerprint"] = info.fingerprint
        return SourceSnapshot.from_bytes(
            descriptor, info.format_id, content, encoding=encoding, bom=bom, metadata=tuple(metadata.items())
        )
    if info.format_id in STRINGS_FORMATS:
        content, records = rebuild_strings(info.format_id, entries)
        metadata.update({
            "translation_source_fingerprint": info.fingerprint,
            "strings.variant": descriptor.suffix,
            "strings.records": records,
        })
        return SourceSnapshot.from_bytes(
            descriptor, info.format_id, content, encoding="per-entry", metadata=tuple(metadata.items())
        )
    if info.format_id is FormatId.PLUGIN_SSE:
        metadata.update({
            key: fields[key]
            for key in (
                METADATA_KEY,
                "localized_language",
                "localized_lookup_detected",
                PLUGIN_SST_MASTERS,
                PLUGIN_SST_UNAVAILABLE,
            )
            if key in fields
        })
    return SourceSnapshot(
        descriptor,
        info.format_id,
        info.fingerprint,
        0,
        lease_id="project-translation-content",
        encoding=fields.get("encoding"),
        metadata=tuple(metadata.items()),
    )


def hydrate_extracted(source: ExtractedSource, source_id: str | None) -> PreparedSourceHydration:
    return PreparedSourceHydration(
        f"project-source:{source.name}",
        source.fingerprint,
        source.format_id,
        reconstructed_source_snapshot(source.info, source.entries, source.format_records),
        tuple(
            replace(
                entry.snapshot,
                translation=entry.state.translation,
                stage=entry.state.stage.value,
                revision=entry.state.revision,
                provenance=entry.state.provenance,
                external_refs=entry.state.external_refs,
            )
            for entry in source.entries
        ),
        source_id,
        reference_location="",
    )
