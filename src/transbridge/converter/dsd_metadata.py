# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""DSD record fields that travel with translation entries and source snapshots."""

from collections.abc import Mapping
from typing import Any, Protocol

from transbridge.application.io.mutation import VALID_STAGES

DSD_NATIVE_FIELDS = "transbridge.io.dsd.native_fields"
DSD_CONDITIONS = "transbridge.io.dsd.conditions"
DSD_EMPTY_REPLACEMENT = "transbridge.io.dsd.empty_replacement"
DSD_QUEST_INDEX = "transbridge.io.dsd.quest_index"
DSD_REGION_INDEX = "transbridge.io.dsd.region_index"
DSD_RECORD_METADATA = frozenset({
    DSD_NATIVE_FIELDS,
    DSD_CONDITIONS,
    DSD_EMPTY_REPLACEMENT,
    DSD_QUEST_INDEX,
    DSD_REGION_INDEX,
})

_OPTIONAL_FIELDS = ("conditions", "editor_id", "index", "original")
_INDEX_FIELDS = {"QUST CNAM": DSD_QUEST_INDEX, "REGN RDMP": DSD_REGION_INDEX}
_MAX_INDEX = 0xFFFFFFFF
_REPLACEMENT_STAGES = VALID_STAGES - {-1, 0}


class _DsdMetadataEntry(Protocol):
    metadata: tuple[tuple[str, Any], ...]


class _DsdReplacementEntry(_DsdMetadataEntry, Protocol):
    translation: str
    stage: int


def build_dsd_metadata(data: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    """Record field presence so native rows retain their explicitly supplied selectors."""
    fields = tuple(field for field in _OPTIONAL_FIELDS if field in data)
    metadata: dict[str, Any] = {DSD_NATIVE_FIELDS: fields}
    if data.get("string") == "":
        metadata[DSD_EMPTY_REPLACEMENT] = True
    if "conditions" in fields:
        conditions = data["conditions"]
        if not isinstance(conditions, list | tuple) or not all(isinstance(item, str) for item in conditions):
            raise TypeError("DSD conditions must be an array of strings")
        metadata[DSD_CONDITIONS] = tuple(conditions)
    if "index" in fields:
        index = data["index"]
        if type(index) is not int:
            raise TypeError("DSD index must be an integer")
        if not 0 <= index <= _MAX_INDEX:
            raise ValueError("DSD index must be between 0 and 4294967295")
        index_field = _INDEX_FIELDS.get(data["type"])
        if index_field is not None:
            metadata[index_field] = index
    return tuple(sorted(metadata.items()))


def dsd_native_fields(entry: _DsdMetadataEntry) -> frozenset[str] | None:
    """Return the optional properties present on import, including an empty field set."""
    fields = dict(entry.metadata).get(DSD_NATIVE_FIELDS)
    if isinstance(fields, list | tuple) and all(field in _OPTIONAL_FIELDS for field in fields):
        return frozenset(fields)
    return None


def dsd_conditions(entry: _DsdMetadataEntry) -> tuple[str, ...] | None:
    """Return conditions in their original order, or None if absent."""
    conditions = dict(entry.metadata).get(DSD_CONDITIONS)
    if isinstance(conditions, list | tuple) and all(isinstance(item, str) for item in conditions):
        return tuple(conditions)
    return None


def dsd_has_replacement(entry: _DsdReplacementEntry) -> bool:
    """Treat an empty string as a replacement only when declared by the native source."""
    return bool(entry.translation) or (
        entry.stage in _REPLACEMENT_STAGES and dict(entry.metadata).get(DSD_EMPTY_REPLACEMENT) is True
    )


def dsd_quest_index(entry: _DsdMetadataEntry) -> int | None:
    """Return the explicit quest log index, including zero."""
    return _dsd_index(entry, DSD_QUEST_INDEX)


def dsd_region_index(entry: _DsdMetadataEntry) -> int | None:
    """Return the explicit region data index, including zero."""
    return _dsd_index(entry, DSD_REGION_INDEX)


def _dsd_index(entry: _DsdMetadataEntry, metadata_key: str) -> int | None:
    value = dict(entry.metadata).get(metadata_key)
    return value if type(value) is int and 0 <= value <= _MAX_INDEX else None
