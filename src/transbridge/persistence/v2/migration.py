"""Deterministic V1 to V2 document migrators."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from .ids import EntityKind, EntityRef, OpaqueId
from .models import SCHEMA_VERSION, MigrationDraft, SchemaValidationError


def migrate_v1(document: dict[str, Any], ref: EntityRef) -> MigrationDraft:
    """Build a V2 draft without mutating the source mapping."""

    source = deepcopy(document)
    if ref.kind is EntityKind.PROJECT:
        return _migrate_project(source, ref)
    if ref.kind is EntityKind.VARIANT:
        return _migrate_variant(source, ref)
    return _migrate_session(source, ref)


def _migrate_project(source: dict[str, Any], ref: EntityRef) -> MigrationDraft:
    _check_legacy_identity(source, ref, ("id", "project_id"))
    name = _required_text(source, "name")
    sources = source.get("sources", [])
    if not isinstance(sources, list) or not all(isinstance(item, dict) for item in sources):
        raise SchemaValidationError("INVALID_V1_SOURCES", "V1 project sources must be an array of objects.")
    variants = source.get("variants", [])
    if not isinstance(variants, list) or not all(isinstance(item, dict) for item in variants):
        raise SchemaValidationError("INVALID_V1_VARIANTS", "V1 project variants must be an array of objects.")

    names = [_required_text(item, "name") for item in variants]
    if len(set(names)) != len(names):
        raise SchemaValidationError("DUPLICATE_V1_VARIANT", "V1 project contains duplicate variant names.")
    mapped = {name: _legacy_opaque_id(name) for name in names}
    active_name = source.get("active_variant")
    if active_name is not None and (not isinstance(active_name, str) or active_name not in mapped):
        raise SchemaValidationError(
            "BROKEN_V1_ACTIVE_VARIANT",
            "V1 active_variant does not refer to a declared variant.",
        )

    known = {
        "schema_version",
        "version",
        "id",
        "project_id",
        "name",
        "created",
        "sources",
        "variants",
        "active_variant",
        "esp_key_format",
    }
    legacy = {
        "created": source.get("created"),
        "esp_key_format": source.get("esp_key_format", True),
        "variant_name_map": mapped,
    }
    data = {
        "name": name,
        "sources": deepcopy(sources),
        "variant_ids": [mapped[item] for item in names],
        "active_variant_id": mapped.get(active_name),
        "legacy": legacy,
    }
    return MigrationDraft(
        _document(ref, source, data),
        defaults=("revision=0",) if "revision" not in source else (),
        dropped_fields=tuple(sorted(set(source) - known - {"revision"})),
    )


def _migrate_variant(source: dict[str, Any], ref: EntityRef) -> MigrationDraft:
    _check_legacy_identity(source, ref, ("id", "variant_id"))
    _check_legacy_variant_name(source, ref)
    project_id = getattr(ref, "project_id", None)
    if project_id is None:
        raise SchemaValidationError("PROJECT_REFERENCE_REQUIRED", "Variant migration requires a Project reference.")

    translations = source.get("translations")
    if not isinstance(translations, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in translations.items()
    ):
        raise SchemaValidationError(
            "INVALID_V1_TRANSLATIONS",
            "V1 translations must be an object of string values.",
        )
    labels = source.get("labels", {})
    if not isinstance(labels, dict) or not all(
        isinstance(key, str) and isinstance(value, list) and all(isinstance(label, str) for label in value)
        for key, value in labels.items()
    ):
        raise SchemaValidationError("INVALID_V1_LABELS", "V1 labels must contain string arrays.")
    label_library = source.get("label_library", {})
    if not isinstance(label_library, dict):
        raise SchemaValidationError("INVALID_V1_LABEL_LIBRARY", "V1 label_library must be an object.")

    known = {
        "schema_version",
        "id",
        "variant_id",
        "variant",
        "updated",
        "translations",
        "labels",
        "label_library",
        "revision",
    }
    data = {
        "project_id": project_id.value,
        "translations": deepcopy(translations),
        "labels": {key: sorted(set(value)) for key, value in sorted(labels.items())},
        "label_library": deepcopy(label_library),
        "snapshot_revision": source.get("revision", 0),
        "source_fingerprints": [{"namespace": "legacy:v1", "sha256": None}],
        "entries": [
            {
                "entry_key": {"namespace": "legacy:v1", "local_key": key},
                "translation": translations.get(key, ""),
                "stage": 1 if translations.get(key, "") else 0,
                "labels": sorted(set(labels.get(key, ()))),
                "provenance": [],
                "revision": 0,
                "tombstone": False,
                "inferred_fields": ["provenance", "revision", "stage"],
            }
            for key in sorted(set(translations) | set(labels))
        ],
        "legacy": {
            "updated": source.get("updated"),
            "stage": "unknown",
            "provenance": "unknown",
            "source_fingerprint": "unknown-requires-explicit-remap",
        },
    }
    defaults = [
        "project_id=ref",
        "stage=inferred",
        "provenance=unknown",
        "entry_revision=0",
        "source_fingerprint=unknown",
    ]
    if "revision" not in source:
        defaults.append("revision=0")
    return MigrationDraft(
        _document(ref, source, data),
        defaults=tuple(defaults),
        dropped_fields=tuple(sorted(set(source) - known)),
    )


def _migrate_session(source: dict[str, Any], ref: EntityRef) -> MigrationDraft:
    _check_legacy_identity(source, ref, ("id", "session_id"))
    name = _required_text(source, "name")
    messages = source.get("messages")
    if not isinstance(messages, list) or not all(isinstance(message, dict) for message in messages):
        raise SchemaValidationError("INVALID_V1_MESSAGES", "V1 Session messages must be an array of objects.")

    project_id = source.get("project_id")
    variant_id = source.get("variant_id")
    for key, value in (("project_id", project_id), ("variant_id", variant_id)):
        if value is not None:
            if not isinstance(value, str):
                raise SchemaValidationError(f"INVALID_V1_{key.upper()}", f"V1 {key} must be a string or null.")
            try:
                OpaqueId(value)
            except ValueError as exc:
                raise SchemaValidationError(f"INVALID_V1_{key.upper()}", str(exc)) from exc
    if variant_id is not None and project_id is None:
        raise SchemaValidationError(
            "BROKEN_V1_SESSION_REFERENCE",
            "V1 variant_id requires project_id.",
        )

    known = {
        "schema_version",
        "id",
        "session_id",
        "name",
        "created_at",
        "last_active_at",
        "project_name",
        "project_id",
        "variant_id",
        "messages",
        "message_count",
        "revision",
    }
    data = {
        "name": name,
        "messages": deepcopy(messages),
        "project_id": project_id,
        "variant_id": variant_id,
        "history": [],
        "legacy": {
            "created_at": source.get("created_at"),
            "last_active_at": source.get("last_active_at"),
            "project_name": source.get("project_name"),
            "recovery": "degraded-history-unavailable",
        },
    }
    defaults = ["history=[]", "recovery=degraded-history-unavailable"]
    if "session_id" not in source and "id" not in source:
        defaults.append("session_id=trusted-ref")
    if "revision" not in source:
        defaults.append("revision=0")
    return MigrationDraft(
        _document(ref, source, data),
        defaults=tuple(defaults),
        dropped_fields=tuple(sorted(set(source) - known)),
    )


def _document(ref: EntityRef, source: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    revision = source.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise SchemaValidationError("INVALID_V1_REVISION", "V1 revision must be a non-negative integer.")
    return {
        "schema_version": SCHEMA_VERSION,
        "entity_type": ref.kind.value,
        "id": ref.identity.value,
        "revision": revision,
        "data": data,
    }


def _check_legacy_identity(source: dict[str, Any], ref: EntityRef, fields: tuple[str, ...]) -> None:
    present = [(field, source[field]) for field in fields if field in source and source[field] is not None]
    for field, value in present:
        if not isinstance(value, str):
            raise SchemaValidationError("INVALID_V1_INTERNAL_ID", f"V1 {field} must be a string.")
        try:
            OpaqueId(value)
        except ValueError as exc:
            raise SchemaValidationError("INVALID_V1_INTERNAL_ID", str(exc)) from exc
        if value != ref.identity.value:
            raise SchemaValidationError(
                "V1_REFERENCE_ID_MISMATCH",
                "V1 internal identity does not match the requested reference.",
            )


def _check_legacy_variant_name(source: dict[str, Any], ref: EntityRef) -> None:
    if "variant" not in source or source["variant"] is None:
        return
    value = source["variant"]
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError("INVALID_V1_INTERNAL_ID", "V1 variant must be a non-empty string.")
    if _legacy_opaque_id(value) != ref.identity.value:
        raise SchemaValidationError(
            "V1_REFERENCE_ID_MISMATCH",
            "V1 variant identity does not match the requested reference.",
        )


def _required_text(source: dict[str, Any], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError("MISSING_V1_FIELD", f"V1 {key} must be a non-empty string.")
    return value


def _legacy_opaque_id(value: str) -> str:
    try:
        return OpaqueId(value).value
    except ValueError:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
        return f"legacy-{digest}"


__all__ = ["migrate_v1"]
