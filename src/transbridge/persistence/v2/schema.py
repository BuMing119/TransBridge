"""Strict JSON and semantic validation for persistence V2."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
import json
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from .ids import EntityKind, EntityRef, OpaqueId
from .models import (
    SCHEMA_VERSION,
    PersistenceDto,
    ProjectDto,
    SchemaEnvelope,
    SchemaValidationError,
    SessionDto,
    VariantDto,
)

_COMMON_PROPERTIES = {
    "schema_version": {"const": SCHEMA_VERSION},
    "entity_type": {"type": "string"},
    "id": {"type": "string", "minLength": 1, "maxLength": 64},
    "revision": {"type": "integer", "minimum": 0},
}

_DATA_SCHEMAS: dict[EntityKind, dict[str, Any]] = {
    EntityKind.PROJECT: {
        "type": "object",
        "required": ["name", "sources", "variant_ids", "active_variant_id"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "sources": {"type": "array", "items": {"type": "object"}},
            "variant_ids": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 64},
                "uniqueItems": True,
            },
            "active_variant_id": {"type": ["string", "null"]},
            "remote_bindings": {
                "type": "object",
                "properties": {
                    "paratranz": {
                        "type": "object",
                        "required": ["project_id", "project_name", "endpoint"],
                        "properties": {
                            "project_id": {"type": "integer", "minimum": 1},
                            "project_name": {"type": "string"},
                            "endpoint": {"type": "string", "format": "uri", "minLength": 1},
                            "account_user_id": {"type": ["integer", "null"], "minimum": 1},
                            "bound_at": {"type": ["string", "null"], "format": "date-time"},
                            "validated_at": {"type": ["string", "null"], "format": "date-time"},
                        },
                        "additionalProperties": False,
                    }
                },
                "additionalProperties": True,
            },
            "legacy": {"type": "object"},
        },
        "additionalProperties": True,
    },
    EntityKind.VARIANT: {
        "type": "object",
        "required": ["project_id", "translations", "labels", "label_library"],
        "properties": {
            "project_id": {"type": "string", "minLength": 1, "maxLength": 64},
            "translations": {"type": "object", "additionalProperties": {"type": "string"}},
            "labels": {
                "type": "object",
                "additionalProperties": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                },
            },
            "label_library": {"type": "object"},
            "snapshot_revision": {"type": "integer", "minimum": 0},
            "source_fingerprints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["namespace", "sha256"],
                    "properties": {
                        "namespace": {"type": "string", "minLength": 1},
                        "sha256": {
                            "type": ["string", "null"],
                            "pattern": "^[0-9a-f]{64}$",
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "entry_key",
                        "translation",
                        "stage",
                        "labels",
                        "provenance",
                        "revision",
                        "tombstone",
                    ],
                    "properties": {
                        "entry_key": {
                            "type": "object",
                            "required": ["namespace", "local_key"],
                            "properties": {
                                "namespace": {"type": "string", "minLength": 1},
                                "local_key": {"type": "string", "minLength": 1},
                            },
                            "additionalProperties": False,
                        },
                        "translation": {"type": "string"},
                        "stage": {"enum": [-1, 0, 1, 2, 3, 5, 9]},
                        "labels": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "uniqueItems": True,
                        },
                        "provenance": {"type": "array", "items": {"type": "object"}},
                        "revision": {"type": "integer", "minimum": 0},
                        "tombstone": {"type": "boolean"},
                        "inferred_fields": {
                            "type": "array",
                            "items": {"type": "string"},
                            "uniqueItems": True,
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "legacy": {"type": "object"},
        },
        "additionalProperties": True,
    },
    EntityKind.SESSION: {
        "type": "object",
        "required": ["name", "messages", "project_id", "variant_id"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "messages": {"type": "array", "items": {"type": "object"}},
            "project_id": {"type": ["string", "null"]},
            "variant_id": {"type": ["string", "null"]},
            "history": {"type": "array", "items": {"type": "object"}},
            "legacy": {"type": "object"},
        },
        "additionalProperties": True,
    },
}


def schema_for(kind: EntityKind) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["schema_version", "entity_type", "id", "revision", "data"],
        "properties": {
            **_COMMON_PROPERTIES,
            "entity_type": {"const": kind.value},
            "data": _DATA_SCHEMAS[kind],
        },
        "additionalProperties": False,
    }


def parse_json_bytes(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SchemaValidationError("INVALID_JSON", "Persistence document is not strict UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise SchemaValidationError("INVALID_ROOT", "Persistence document root must be an object.")
    return value


def version_of(document: dict[str, Any]) -> int:
    version = document.get("schema_version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise SchemaValidationError("INVALID_SCHEMA_VERSION", "schema_version must be a positive integer.")
    return version


def validate_v2(document: dict[str, Any], ref: EntityRef) -> PersistenceDto:
    validator = Draft202012Validator(schema_for(ref.kind))
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        pointer = _json_pointer(error.absolute_path)
        raise SchemaValidationError("SCHEMA_VALIDATION_FAILED", error.message, pointer=pointer)

    identity = str(document["id"])
    try:
        OpaqueId(identity)
    except ValueError as exc:
        raise SchemaValidationError("INVALID_INTERNAL_ID", str(exc), pointer="/id") from exc
    if identity != ref.identity.value:
        raise SchemaValidationError(
            "REFERENCE_ID_MISMATCH",
            "File identity does not match its requested reference.",
            pointer="/id",
        )

    data = document["data"]
    _validate_semantics(ref, data)
    envelope = SchemaEnvelope(
        schema_version=SCHEMA_VERSION,
        entity_type=ref.kind,
        identity=identity,
        revision=int(document["revision"]),
        data=data,
    )
    if ref.kind is EntityKind.PROJECT:
        return ProjectDto(envelope)
    if ref.kind is EntityKind.VARIANT:
        return VariantDto(envelope)
    return SessionDto(envelope)


def serialize_document(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _validate_semantics(ref: EntityRef, data: dict[str, Any]) -> None:
    if ref.kind is EntityKind.PROJECT:
        variant_ids = data["variant_ids"]
        for index, value in enumerate(variant_ids):
            try:
                OpaqueId(value)
            except ValueError as exc:
                raise SchemaValidationError(
                    "INVALID_VARIANT_REFERENCE",
                    str(exc),
                    pointer=f"/data/variant_ids/{index}",
                ) from exc
        active = data["active_variant_id"]
        if active is not None and active not in variant_ids:
            raise SchemaValidationError(
                "BROKEN_ACTIVE_VARIANT_REFERENCE",
                "active_variant_id must refer to a listed variant.",
                pointer="/data/active_variant_id",
            )
        _validate_paratranz_binding(data)
        return

    if ref.kind is EntityKind.VARIANT:
        project_id = data["project_id"]
        try:
            OpaqueId(project_id)
        except ValueError as exc:
            raise SchemaValidationError("INVALID_PROJECT_REFERENCE", str(exc), pointer="/data/project_id") from exc
        if project_id != ref.project_id.value:
            raise SchemaValidationError(
                "PROJECT_REFERENCE_MISMATCH",
                "Variant project reference does not match the requested parent.",
                pointer="/data/project_id",
            )
        fingerprints = data.get("source_fingerprints", ())
        namespaces = [item["namespace"] for item in fingerprints]
        if len(set(namespaces)) != len(namespaces):
            raise SchemaValidationError(
                "DUPLICATE_SOURCE_NAMESPACE",
                "Variant source namespaces must be unique.",
                pointer="/data/source_fingerprints",
            )
        entry_keys: set[tuple[str, str]] = set()
        for index, entry in enumerate(data.get("entries", ())):
            key = entry["entry_key"]
            identity = (key["namespace"], key["local_key"])
            if identity in entry_keys:
                raise SchemaValidationError(
                    "DUPLICATE_VARIANT_ENTRY",
                    "Variant entries must have unique EntryKeys.",
                    pointer=f"/data/entries/{index}/entry_key",
                )
            entry_keys.add(identity)
            if key["namespace"] not in namespaces:
                raise SchemaValidationError(
                    "UNDECLARED_SOURCE_NAMESPACE",
                    "Variant entry namespace must have a source fingerprint.",
                    pointer=f"/data/entries/{index}/entry_key/namespace",
                )
        return

    project_id = data["project_id"]
    variant_id = data["variant_id"]
    for key, value in (("project_id", project_id), ("variant_id", variant_id)):
        if value is not None:
            try:
                OpaqueId(value)
            except ValueError as exc:
                raise SchemaValidationError(
                    f"INVALID_{key.upper()}",
                    str(exc),
                    pointer=f"/data/{key}",
                ) from exc
    if variant_id is not None and project_id is None:
        raise SchemaValidationError(
            "BROKEN_SESSION_REFERENCE",
            "A Session variant reference requires a Project reference.",
            pointer="/data/variant_id",
        )


def _validate_paratranz_binding(data: dict[str, Any]) -> None:
    remote_bindings = data.get("remote_bindings") or {}
    binding = remote_bindings.get("paratranz")
    if binding is None:
        return

    endpoint = binding["endpoint"]
    try:
        parsed = urlsplit(endpoint)
    except ValueError as exc:
        raise SchemaValidationError(
            "INVALID_PARATRANZ_ENDPOINT",
            "ParaTranz endpoint must be an absolute HTTP(S) URL without credentials, query, or fragment.",
            pointer="/data/remote_bindings/paratranz/endpoint",
        ) from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SchemaValidationError(
            "INVALID_PARATRANZ_ENDPOINT",
            "ParaTranz endpoint must be an absolute HTTP(S) URL without credentials, query, or fragment.",
            pointer="/data/remote_bindings/paratranz/endpoint",
        )

    for field_name in ("bound_at", "validated_at"):
        value = binding.get(field_name)
        if value is None:
            continue
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SchemaValidationError(
                "INVALID_PARATRANZ_TIMESTAMP",
                f"ParaTranz {field_name} must be an ISO 8601 timestamp.",
                pointer=f"/data/remote_bindings/paratranz/{field_name}",
            ) from exc


def _reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite number: {value}")


def _json_pointer(path: Iterable[object]) -> str:
    parts = [str(part).replace("~", "~0").replace("/", "~1") for part in path]
    return "" if not parts else "/" + "/".join(parts)


__all__ = [
    "parse_json_bytes",
    "schema_for",
    "serialize_document",
    "validate_v2",
    "version_of",
]
