"""Serializable, already parsed source candidates; recovery never reopens the input."""

from base64 import b64decode, b64encode
from dataclasses import asdict, replace

from transbridge.application.io import FormatId, SourceDescriptor, SourceSnapshot, Stage
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.io.strings_adapter import LocalizedStringRecord
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.v2.variant import SourceBaseline, SourceFingerprint, VariantEntryState

from .provisioning_models import PreparedProjectSource, PreparedSourceHydration


def prepare_parsed_source(path, entries, snapshot, format_id, options=(), *, role="primary", diagnostics=()):
    entries = tuple(entries)
    namespaces = {entry.identity.namespace for entry in entries}
    if len(namespaces) > 1:
        raise ValueError("来源没有唯一的内容身份。")
    namespace = (
        next(iter(namespaces))
        if namespaces
        else SourceNamespace(
            dict(snapshot.metadata).get("source_namespace")
            or SourceNamespace.from_fingerprint(format_id.value, snapshot.sha256).value
        )
    )
    options = dict(options)
    options.setdefault("skip_empty", False)
    if format_id is FormatId.PLUGIN_SSE:
        options.setdefault("discover_sibling_strings", True)
    hydration = (
        PreparedSourceHydration(
            str(path), snapshot.sha256, format_id, snapshot, tuple(entry.snapshot() for entry in entries)
        )
        if all(hasattr(entry, "snapshot") for entry in entries)
        else None
    )

    def baseline_entry(entry):
        translation, stage = entry.translation, entry.stage
        if role == "migration" and format_id is FormatId.PLUGIN_SSE:
            translation = translation or entry.original
            stage = Stage.TRANSLATED if translation.strip() else Stage.UNTRANSLATED
        return VariantEntryState(
            entry.identity,
            translation,
            stage,
            revision=entry.revision,
            provenance=entry.provenance,
            external_refs=entry.external_refs,
        )

    return PreparedProjectSource(
        tuple(
            {
                "source_id": namespace.value,
                "enabled": True,
                "format_id": format_id.value,
                "format_options": dict(options),
                "location": str(path),
                "path": str(path),
                "fingerprint": snapshot.sha256,
                "role": role,
            }.items()
        ),
        SourceBaseline(
            SourceFingerprint(namespace, snapshot.sha256),
            tuple(baseline_entry(entry) for entry in entries),
        ),
        diagnostics=diagnostics,
        hydration=hydration,
    )


def encode_prepared_source(prepared, entries):
    hydration = prepared.hydration
    snapshot = hydration.source_snapshot
    return {
        **encode_source_snapshot(snapshot),
        "descriptor": prepared.to_dict(),
        "entries": [_encode_metadata(entry) for entry in entries],
        "entries_codec": 1,
    }


def encode_source_snapshot(snapshot):
    if snapshot.content is None:
        raise ValueError("来源快照没有独立内容，不能保存可恢复成果。")
    return {
        "source": asdict(snapshot.source),
        "content": b64encode(snapshot.content).decode("ascii"),
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
        "lease_id": snapshot.lease_id,
        "format_id": snapshot.format_id.value,
        "encoding": snapshot.encoding,
        "bom": b64encode(snapshot.bom).decode("ascii"),
        "metadata": _encode_metadata(snapshot.metadata),
        "metadata_codec": 1,
    }


def decode_source_snapshot(payload):
    if payload.get("metadata_codec") != 1:
        raise ValueError("来源快照元数据编码版本不受支持。")
    snapshot = SourceSnapshot.from_bytes(
        SourceDescriptor(**payload["source"]),
        FormatId(payload["format_id"]),
        b64decode(payload["content"], validate=True),
        encoding=payload["encoding"],
        bom=b64decode(payload["bom"], validate=True),
        metadata=_decode_metadata(payload["metadata"]),
    )
    if payload["sha256"] != snapshot.sha256 or payload["size_bytes"] != snapshot.size_bytes:
        raise ValueError("来源快照内容与保存的指纹或长度不一致。")
    return replace(snapshot, lease_id=payload.get("lease_id"))


def decode_prepared_source(payload):
    if payload.get("entries_codec") != 1:
        raise ValueError("来源条目编码版本不受支持。")
    snapshot = decode_source_snapshot(payload)
    descriptor = payload["descriptor"]
    if descriptor["fingerprint"] != snapshot.sha256:
        raise ValueError("来源成果内容与指纹不一致。")
    return prepare_parsed_source(
        descriptor["location"],
        tuple(_decode_metadata(row) for row in payload["entries"]),
        snapshot,
        FormatId(payload["format_id"]),
        tuple(descriptor["format_options"].items()),
    )


def _encode_metadata(value):
    """Use explicit source/entry contracts, retaining binary companions and container types."""
    if isinstance(value, SourceSnapshot):
        return {"$source_snapshot": encode_source_snapshot(value)}
    if isinstance(value, TranslationEntry):
        return {"$translation_entry": _encode_metadata(value.to_dict()), "$metadata": _encode_metadata(value.metadata)}
    if isinstance(value, LocalizedStringRecord):
        return {"$localized_string_record": _encode_metadata(asdict(value))}
    if isinstance(value, bytes):
        return {"$bytes": b64encode(value).decode("ascii")}
    if isinstance(value, tuple):
        return {"$tuple": [_encode_metadata(item) for item in value]}
    if isinstance(value, list):
        return [_encode_metadata(item) for item in value]
    if isinstance(value, dict):
        return {"$mapping": [[_encode_metadata(key), _encode_metadata(item)] for key, item in value.items()]}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"来源快照元数据类型不受支持：{type(value).__name__}")


def _decode_metadata(value):
    if isinstance(value, list):
        return [_decode_metadata(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"$source_snapshot"}:
        return decode_source_snapshot(value["$source_snapshot"])
    if set(value) == {"$translation_entry", "$metadata"}:
        entry = TranslationEntry.from_dict(_decode_metadata(value["$translation_entry"]))
        return replace(entry, metadata=_decode_metadata(value["$metadata"]))
    if set(value) == {"$localized_string_record"}:
        return LocalizedStringRecord(**_decode_metadata(value["$localized_string_record"]))
    if set(value) == {"$bytes"}:
        return b64decode(value["$bytes"], validate=True)
    if set(value) == {"$tuple"} and isinstance(value["$tuple"], list):
        return tuple(_decode_metadata(item) for item in value["$tuple"])
    if set(value) == {"$mapping"} and isinstance(value["$mapping"], list):
        result = {}
        for key, item in value["$mapping"]:
            key = _decode_metadata(key)
            if key in result:
                raise ValueError("来源快照元数据包含重复映射键。")
            result[key] = _decode_metadata(item)
        return result
    raise ValueError("来源快照元数据类型标签无效。")
