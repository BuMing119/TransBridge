"""Explicit JSON contracts for complete, parsed project sources.

Only the types listed here may be constructed. Binary values are delegated to
the artifact store, including bytes nested inside localized source metadata.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from enum import Enum
import math
import ntpath
import posixpath
from typing import Any

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory
from transbridge.application.io import EntrySnapshot, FormatId, SourceDescriptor, SourceSnapshot, Stage
from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, Provenance, SourceNamespace
from transbridge.application.io.strings_adapter import LocalizedStringRecord
from transbridge.application.projects.provisioning_models import PreparedProjectSource, PreparedSourceHydration
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.v2.variant import SourceBaseline, SourceFingerprint, VariantEntryState

# Bump whenever parsing/identity/metadata semantics change incompatibly. The
# exact dataclass field check also rejects contracts from a different shape.
SOURCE_ARTIFACT_ABI = 1
_TYPES = {
    cls.__name__: cls
    for cls in (
        PreparedProjectSource,
        PreparedSourceHydration,
        SourceBaseline,
        SourceFingerprint,
        VariantEntryState,
        EntrySnapshot,
        SourceSnapshot,
        SourceDescriptor,
        SourceNamespace,
        EntryKey,
        EntryRevision,
        ExternalEntryRef,
        Provenance,
        LocalizedStringRecord,
        TranslationEntry,
        Diagnostic,
    )
}
_FIELDS = {name: tuple(field.name for field in fields(cls) if field.init) for name, cls in _TYPES.items()}
_FIELD_SETS = {name: frozenset(names) for name, names in _FIELDS.items()}
_ENUMS = {cls.__name__: cls for cls in (FormatId, Stage, DiagnosticSeverity, ErrorCategory)}


def encode_prepared_artifact(
    prepared: PreparedProjectSource, write_blob: Callable[[bytes], dict[str, Any]]
) -> dict[str, Any]:
    """Encode without asdict/deepcopy or base64, retaining all source contracts."""
    if prepared.hydration is None:
        raise ValueError("source artifact requires a complete prepared hydration")

    def encode(value):
        if isinstance(value, Enum):
            if type(value).__name__ not in _ENUMS or _ENUMS[type(value).__name__] is not type(value):
                raise TypeError(f"unsupported source artifact enum: {type(value).__name__}")
            return {"$enum": type(value).__name__, "value": value.value}
        if value is None or type(value) in (str, int, bool):
            return value
        if type(value) is float:
            if not math.isfinite(value):
                raise ValueError("source artifact numbers must be finite")
            return value
        if isinstance(value, bytes):
            return write_blob(value)
        if isinstance(value, tuple):
            return {"$tuple": [encode(item) for item in value]}
        if isinstance(value, list):
            return [encode(item) for item in value]
        if isinstance(value, dict):
            return {"$mapping": [[encode(key), encode(item)] for key, item in value.items()]}
        name = type(value).__name__
        if name not in _TYPES or _TYPES[name] is not type(value):
            raise TypeError(f"unsupported source artifact value: {name}")
        if isinstance(value, SourceSnapshot) and value.content is None:
            raise ValueError(f"source artifact requires captured bytes: {value.source.uri}")
        attributes = {}
        for field_name in _FIELDS[name]:
            item = getattr(value, field_name)
            if isinstance(value, PreparedProjectSource) and field_name == "descriptor":
                item = tuple((key, data) for key, data in item if key != "source_artifact_id")
            attributes[field_name] = encode(item)
        return {"$type": name, "fields": attributes}

    return encode(prepared)


def decode_prepared_artifact(
    payload: dict[str, Any],
    read_blob: Callable[[str], bytes],
    *,
    original_location: str,
    location: str | None = None,
) -> PreparedProjectSource:
    """Restore saved contracts; relocation only changes declared source paths."""
    relocate = _relocator(original_location, location)

    def decode(value):
        if value is None or type(value) in (str, int, bool):
            return value
        if type(value) is float and math.isfinite(value):
            return value
        if isinstance(value, list):
            return [decode(item) for item in value]
        if not isinstance(value, dict):
            raise ValueError("invalid source artifact value")
        key_count = len(value)
        if "$blob" in value:
            if key_count == 1:
                return read_blob(value["$blob"])
            if key_count != 3 or "offset" not in value or "size" not in value:
                raise ValueError("invalid source artifact binary reference")
            content = read_blob(value["$blob"])
            offset, size = value["offset"], value["size"]
            if (
                type(offset) is not int
                or type(size) is not int
                or offset < 0
                or size < 0
                or offset + size > len(content)
            ):
                raise ValueError("invalid source artifact binary slice")
            return content[offset : offset + size]
        if "$tuple" in value:
            if key_count != 1 or not isinstance(value["$tuple"], list):
                raise ValueError("invalid source artifact tuple")
            result = tuple(decode(item) for item in value["$tuple"])
            if len(result) == 2 and result[0] == "strings.origin_path" and isinstance(result[1], str):
                return result[0], relocate(result[1])
            return result
        if "$mapping" in value:
            if key_count != 1 or not isinstance(value["$mapping"], list):
                raise ValueError("invalid source artifact mapping")
            result = {}
            for pair in value["$mapping"]:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("invalid source artifact mapping pair")
                key, item = decode(pair[0]), decode(pair[1])
                if key in result:
                    raise ValueError("duplicate source artifact mapping key")
                result[key] = item
            return result
        if "$enum" in value:
            if key_count != 2 or "value" not in value or value["$enum"] not in _ENUMS:
                raise ValueError("unknown source artifact enum tag")
            return _ENUMS[value["$enum"]](value["value"])
        if key_count != 2 or "$type" not in value or "fields" not in value or value["$type"] not in _TYPES:
            raise ValueError("unknown source artifact type tag")
        name, attributes = value["$type"], value["fields"]
        if name == "PreparedSourceHydration" and isinstance(attributes, dict):
            attributes = {"source_id": None, "reference_location": None, **attributes}
        if not isinstance(attributes, dict) or attributes.keys() != _FIELD_SETS[name]:
            raise ValueError(f"source artifact contract fields differ: {name}")
        attributes = {key: decode(item) for key, item in attributes.items()}
        if name == "SourceSnapshot" and not isinstance(attributes["content"], bytes):
            raise ValueError("source artifact snapshot requires independently captured bytes")
        if name == "SourceDescriptor":
            attributes["uri"] = relocate(attributes["uri"])
        elif name == "PreparedSourceHydration" and location is not None:
            attributes["location"] = location
        elif name == "PreparedProjectSource":
            if dict(attributes["descriptor"]).get("location") != original_location:
                raise ValueError("source artifact manifest and descriptor locations differ")
            if location is not None:
                attributes["descriptor"] = tuple(
                    (key, location if key in {"location", "path"} else item) for key, item in attributes["descriptor"]
                )
        return _TYPES[name](**attributes)

    prepared = decode(payload)
    if not isinstance(prepared, PreparedProjectSource) or prepared.hydration is None:
        raise ValueError("source artifact does not contain a complete prepared source")
    descriptor, hydration = prepared.to_dict(), prepared.hydration
    if (
        descriptor["fingerprint"] != hydration.fingerprint
        or descriptor.get("format_id") != hydration.format_id.value
        or descriptor["location"] != hydration.location
        or hydration.source_snapshot.format_id != hydration.format_id
        or hydration.source_snapshot.content is None
    ):
        raise ValueError("source artifact descriptor, baseline and hydration differ")
    return prepared


def blob_references(value: Any) -> frozenset[str]:
    """Collect only typed blob references; unknown contracts fail during decode."""
    references: set[str] = set()

    def visit(item):
        if isinstance(item, dict):
            if "$blob" in item and (len(item) == 1 or (len(item) == 3 and "offset" in item and "size" in item)):
                digest = item["$blob"]
                if not isinstance(digest, str):
                    raise ValueError("invalid source artifact blob digest")
                if digest not in references:
                    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                        raise ValueError("invalid source artifact blob digest")
                    references.add(digest)
            else:
                for child in item.values():
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return frozenset(references)


def _relocator(original: str, location: str | None) -> Callable[[str], str]:
    if location is None:
        return lambda value: value
    if not isinstance(location, str) or not location.strip():
        raise ValueError("source artifact relocation requires a non-empty location")
    old_paths = ntpath if ntpath.splitdrive(original)[0] or "\\" in original else posixpath
    new_paths = ntpath if ntpath.splitdrive(location)[0] or "\\" in location else posixpath

    def relocate(value: str) -> str:
        if value.startswith("bsa:") and "!/" in value:
            archive, member = value[4:].split("!/", 1)
            return f"bsa:{relocate(archive)}!/{member}"
        if old_paths.normcase(old_paths.normpath(value)) == old_paths.normcase(old_paths.normpath(original)):
            return location
        if not old_paths.isabs(value):
            return value
        try:
            relative = old_paths.relpath(value, old_paths.dirname(original))
        except ValueError as exc:
            raise ValueError(f"localized source cannot be relocated: {value}") from exc
        return new_paths.normpath(new_paths.join(new_paths.dirname(location), *relative.replace("\\", "/").split("/")))

    return relocate
