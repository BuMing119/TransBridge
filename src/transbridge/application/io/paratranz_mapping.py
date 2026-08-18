"""Pure ParaTranz JSON record mapping with explicit local and remote identity."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any

from transbridge.application.contracts import Diagnostic

from .identity import EntryKey, ExternalEntryRef, SourceNamespace
from .mutation import VALID_STAGES

PARATRANZ_SYSTEM = "paratranz"
PARATRANZ_CORE_FIELDS = frozenset({"id", "key", "original", "translation", "stage", "context"})


@dataclass(frozen=True, slots=True)
class ParatranzEntry:
    """Transport DTO whose local key and optional remote id remain independent."""

    entry_key: EntryKey
    original: str
    translation: str = ""
    stage: int = 0
    context: str | None = None
    external_refs: tuple[ExternalEntryRef, ...] = ()
    extensions: tuple[tuple[str, Any], ...] = ()
    source_index: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.original, str):
            raise TypeError("ParaTranz original must be a string")
        if not isinstance(self.translation, str):
            raise TypeError("ParaTranz translation must be a string")
        if self.context is not None and not isinstance(self.context, str):
            raise TypeError("ParaTranz context must be a string or None")
        if isinstance(self.stage, bool) or not isinstance(self.stage, int) or self.stage not in VALID_STAGES:
            raise ValueError(f"ParaTranz stage must be one of {sorted(VALID_STAGES)}")
        if self.source_index is not None and (
            isinstance(self.source_index, bool) or not isinstance(self.source_index, int) or self.source_index < 0
        ):
            raise ValueError("ParaTranz source index must be a non-negative integer")
        if len({reference.index_key for reference in self.external_refs}) != len(self.external_refs):
            raise ValueError("ParaTranz external references must be unique")
        extension_names = tuple(name for name, _ in self.extensions)
        if len(extension_names) != len(set(extension_names)):
            raise ValueError("ParaTranz extension names must be unique")
        conflicts = PARATRANZ_CORE_FIELDS.intersection(extension_names)
        if conflicts:
            raise ValueError(f"ParaTranz extensions cannot replace core fields: {sorted(conflicts)}")

    @property
    def key(self) -> str:
        return self.entry_key.local_key

    def paratranz_ref(self) -> ExternalEntryRef | None:
        references = tuple(reference for reference in self.external_refs if reference.system == PARATRANZ_SYSTEM)
        if len(references) > 1:
            raise ValueError("an entry cannot have more than one ParaTranz external reference")
        return references[0] if references else None


@dataclass(frozen=True, slots=True)
class ParatranzMappingBatch:
    entries: tuple[ParatranzEntry, ...]
    diagnostics: tuple[Diagnostic, ...]
    failed_indices: tuple[int, ...]


def map_paratranz_records(
    payload: Any,
    namespace: SourceNamespace,
    *,
    external_scope: str = "offline",
) -> ParatranzMappingBatch:
    """Validate an array and map all non-conflicting records without overwrites."""

    if not isinstance(payload, list):
        return ParatranzMappingBatch(
            (),
            (_diagnostic("PARATRANZ_ROOT_INVALID", "ParaTranz JSON root must be an array.", -1, payload),),
            (-1,),
        )

    candidates: dict[int, ParatranzEntry] = {}
    diagnostics: list[Diagnostic] = []
    failed: set[int] = set()
    for index, record in enumerate(payload):
        entry, diagnostic = map_paratranz_record(
            record,
            index,
            namespace,
            external_scope=external_scope,
        )
        if diagnostic is not None:
            diagnostics.append(diagnostic)
            failed.add(index)
        elif entry is not None:
            candidates[index] = entry

    key_groups: dict[EntryKey, list[int]] = {}
    id_groups: dict[tuple[str, str, str, str | int | float | bool | None], list[int]] = {}
    for index, entry in candidates.items():
        key_groups.setdefault(entry.entry_key, []).append(index)
        reference = entry.paratranz_ref()
        if reference is not None:
            id_groups.setdefault(reference.index_key, []).append(index)

    for entry_key, indices in key_groups.items():
        if len(indices) < 2:
            continue
        for index in indices:
            failed.add(index)
            diagnostics.append(
                _diagnostic(
                    "PARATRANZ_KEY_DUPLICATE",
                    "The ParaTranz key occurs more than once; no record in the conflict was selected.",
                    index,
                    payload[index],
                    details=(("conflicting_indices", tuple(indices)), ("entry_key", entry_key.serialize())),
                )
            )

    for index_key, indices in id_groups.items():
        distinct_keys = {candidates[index].entry_key for index in indices}
        if len(distinct_keys) < 2:
            continue
        for index in indices:
            failed.add(index)
            diagnostics.append(
                _diagnostic(
                    "PARATRANZ_ID_CONFLICT",
                    "The ParaTranz id points to different local keys.",
                    index,
                    payload[index],
                    details=(
                        ("conflicting_indices", tuple(indices)),
                        ("external_ref", repr(index_key)),
                    ),
                )
            )

    entries = tuple(candidates[index] for index in sorted(candidates) if index not in failed)
    return ParatranzMappingBatch(entries, tuple(diagnostics), tuple(sorted(failed)))


def map_paratranz_record(
    record: Any,
    index: int,
    namespace: SourceNamespace,
    *,
    external_scope: str,
) -> tuple[ParatranzEntry | None, Diagnostic | None]:
    if not isinstance(record, dict):
        return None, _diagnostic("PARATRANZ_RECORD_INVALID", "ParaTranz records must be objects.", index, record)
    duplicate_fields = _duplicate_fields(record)
    if duplicate_fields:
        return None, _diagnostic(
            "PARATRANZ_FIELD_CONFLICT",
            "ParaTranz record contains duplicate object fields.",
            index,
            record,
            details=(("duplicate_fields", duplicate_fields),),
        )

    key = record.get("key")
    if not isinstance(key, str) or not key.strip():
        return None, _diagnostic("PARATRANZ_KEY_INVALID", "ParaTranz key must be a non-empty string.", index, record)
    if "original" not in record or not isinstance(record["original"], str):
        return None, _diagnostic(
            "PARATRANZ_ORIGINAL_INVALID",
            "ParaTranz original is required and must be a string.",
            index,
            record,
        )

    translation = record.get("translation", "")
    if not isinstance(translation, str):
        return None, _diagnostic(
            "PARATRANZ_TRANSLATION_INVALID",
            "ParaTranz translation must be a string.",
            index,
            record,
        )
    context = record.get("context")
    if context is not None and not isinstance(context, str):
        return None, _diagnostic(
            "PARATRANZ_CONTEXT_INVALID",
            "ParaTranz context must be a string or null.",
            index,
            record,
        )
    stage = record.get("stage", 0)
    if isinstance(stage, bool) or not isinstance(stage, int) or stage not in VALID_STAGES:
        return None, _diagnostic(
            "PARATRANZ_STAGE_INVALID",
            f"ParaTranz stage must be one of {sorted(VALID_STAGES)}.",
            index,
            record,
            details=(("stage", stage),),
        )

    external_refs: tuple[ExternalEntryRef, ...] = ()
    if "id" in record:
        opaque_id = record["id"]
        if not isinstance(opaque_id, (str, int, float, bool, type(None))) or (
            isinstance(opaque_id, float) and not math.isfinite(opaque_id)
        ):
            return None, _diagnostic(
                "PARATRANZ_ID_INVALID",
                "ParaTranz id must be a finite JSON scalar.",
                index,
                record,
            )
        external_refs = (ExternalEntryRef(PARATRANZ_SYSTEM, external_scope, opaque_id),)

    extensions = tuple(
        (name, _json_clone(value)) for name, value in record.items() if name not in PARATRANZ_CORE_FIELDS
    )
    return (
        ParatranzEntry(
            EntryKey(namespace, key),
            record["original"],
            translation,
            stage,
            context,
            external_refs,
            extensions,
            index,
        ),
        None,
    )


def paratranz_record_from_entry(entry: Any, *, preserve_extensions: bool = True) -> dict[str, Any]:
    """Map a V2 DTO/snapshot/compatible entry without synthesizing a remote id."""

    entry_key = _entry_key(entry)
    original = getattr(entry, "original", None)
    translation = getattr(entry, "translation", "")
    stage = getattr(entry, "stage", 0)
    context = getattr(entry, "context", None)
    ParatranzEntry(entry_key, original, translation, stage, context)

    references = tuple(
        reference for reference in getattr(entry, "external_refs", ()) if reference.system == PARATRANZ_SYSTEM
    )
    if len(references) > 1:
        raise ValueError("an entry cannot have more than one ParaTranz external reference")

    record: dict[str, Any] = {"key": entry_key.local_key, "original": original}
    if references:
        record["id"] = references[0].opaque_id
    record["translation"] = translation
    record["stage"] = stage
    if context is not None:
        record["context"] = context

    if preserve_extensions:
        extensions = getattr(entry, "extensions", ())
        for name, value in extensions:
            if name in PARATRANZ_CORE_FIELDS:
                raise ValueError(f"ParaTranz extension conflicts with core field: {name}")
            record[name] = _json_clone(value)
    return record


def _entry_key(entry: Any) -> EntryKey:
    entry_key = getattr(entry, "entry_key", None)
    if entry_key is None:
        entry_key = getattr(entry, "identity", None)
    if not isinstance(entry_key, EntryKey):
        raise TypeError("ParaTranz writer entries require an EntryKey")
    return entry_key


def _json_clone(value: Any) -> Any:
    try:
        return json.loads(
            json.dumps(value, ensure_ascii=False, allow_nan=False),
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("ParaTranz extension values must be JSON-serializable") from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number is not permitted: {value}")


def _duplicate_fields(value: Any, path: str = "$") -> tuple[str, ...]:
    duplicates = tuple(f"{path}.{name}" for name in getattr(value, "duplicate_keys", ()))
    if isinstance(value, dict):
        nested = tuple(item for name, child in value.items() for item in _duplicate_fields(child, f"{path}.{name}"))
        return (*duplicates, *nested)
    if isinstance(value, list):
        nested = tuple(
            item for index, child in enumerate(value) for item in _duplicate_fields(child, f"{path}[{index}]")
        )
        return (*duplicates, *nested)
    return duplicates


def _diagnostic(
    code: str,
    message: str,
    index: int,
    record: Any,
    *,
    details: tuple[tuple[str, Any], ...] = (),
) -> Diagnostic:
    key = record.get("key") if isinstance(record, dict) else None
    opaque_id = record.get("id") if isinstance(record, dict) else None
    return Diagnostic(
        code,
        message,
        details=(("record_index", index), ("key", key), ("id", opaque_id), *details),
    )
