"""Versioned normalization, canonical serialization, and stable identities."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields, is_dataclass
from enum import Enum
import hashlib
import json
import math
from threading import RLock
from typing import Any, Protocol
import unicodedata

from .errors import DigestCollisionError
from .models import ExtractionMethod, TermScope

NORMALIZATION_SCHEMA = "terminology.normalization.v1"
CANONICAL_SCHEMA = "terminology.canonical-json.v1"
IDENTITY_SCHEMA = "terminology.identity.v1"
BUILD_KEY_SCHEMA = "terminology.build-key.v1"

_VOLATILE_BUILD_FIELDS = frozenset({
    "captured_at",
    "created_at",
    "lease",
    "lease_path",
    "recorded_at",
    "run_id",
    "source_lease",
    "temporary_path",
    "timestamp",
    "ui_order",
})


class HashProvider(Protocol):
    def __call__(self, payload: bytes) -> str | bytes: ...


def normalize_original(value: str) -> str:
    """Normalize source terms without deleting punctuation or changing semantics."""

    return _normalize_whitespace(value).casefold()


def normalize_translation(value: str) -> str:
    """Normalize translated terms while preserving case and punctuation."""

    return _normalize_whitespace(value)


def _normalize_whitespace(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("term text must be a string")
    return " ".join(unicodedata.normalize("NFKC", value).split())


def canonical_bytes(value: Any, *, schema: str = CANONICAL_SCHEMA) -> bytes:
    """Encode a value using deterministic, finite JSON with an explicit schema."""

    payload = {"schema": _non_empty(schema, "canonical schema"), "value": _canonical_value(value)}
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def canonical_digest(
    value: Any,
    *,
    namespace: str,
    hash_provider: HashProvider | None = None,
    collision_guard: DigestCollisionGuard | None = None,
) -> str:
    """Hash and optionally collision-check a canonical payload."""

    namespace = _non_empty(namespace, "digest namespace")
    payload = canonical_bytes(value, schema=namespace)
    digest = _hash(payload, hash_provider)
    identity = f"{namespace}:{digest}"
    if collision_guard is not None:
        collision_guard.verify(identity, payload)
    return identity


class DigestCollisionGuard:
    """Repository-neutral digest collision detector based on canonical bytes."""

    def __init__(self) -> None:
        self._payloads: dict[str, bytes] = {}
        self._lock = RLock()

    def verify(self, digest: str, payload: bytes) -> None:
        with self._lock:
            existing = self._payloads.get(digest)
            if existing is not None and existing != payload:
                raise DigestCollisionError(digest)
            self._payloads[digest] = payload


def evidence_id(
    *,
    project_id: str,
    variant_id: str,
    source_chain: Sequence[str],
    entry_key: str,
    original: str,
    translation: str,
    hash_provider: HashProvider | None = None,
    collision_guard: DigestCollisionGuard | None = None,
) -> str:
    payload = {
        "project_id": _non_empty(project_id, "project ID"),
        "variant_id": _non_empty(variant_id, "variant ID"),
        "source_chain": sorted(_non_empty(value, "source identity") for value in source_chain),
        "entry_key": _non_empty(entry_key, "entry key"),
        "normalized_original": normalize_original(original),
        "normalized_translation": normalize_translation(translation),
    }
    return canonical_digest(
        payload,
        namespace=f"{IDENTITY_SCHEMA}.evidence",
        hash_provider=hash_provider,
        collision_guard=collision_guard,
    )


def candidate_id(
    *,
    evidence_ids: Sequence[str],
    original: str,
    translation: str,
    scope: TermScope,
    extraction_method: ExtractionMethod | str,
    algorithm_version: str,
    hash_provider: HashProvider | None = None,
    collision_guard: DigestCollisionGuard | None = None,
) -> str:
    payload = {
        "evidence_ids": sorted(_non_empty(value, "evidence ID") for value in evidence_ids),
        "normalized_original": normalize_original(original),
        "normalized_translation": normalize_translation(translation),
        "scope": scope.canonical_key,
        "extraction_method": ExtractionMethod(extraction_method).value,
        "algorithm_version": _non_empty(algorithm_version, "algorithm version"),
    }
    return canonical_digest(
        payload,
        namespace=f"{IDENTITY_SCHEMA}.candidate",
        hash_provider=hash_provider,
        collision_guard=collision_guard,
    )


def term_id(
    *,
    project_id: str,
    variant_id: str,
    scope: TermScope,
    original: str,
    hash_provider: HashProvider | None = None,
    collision_guard: DigestCollisionGuard | None = None,
) -> str:
    payload = {
        "project_id": _non_empty(project_id, "project ID"),
        "variant_id": _non_empty(variant_id, "variant ID"),
        "scope": scope.canonical_key,
        "normalized_original": normalize_original(original),
    }
    return canonical_digest(
        payload,
        namespace=f"{IDENTITY_SCHEMA}.term",
        hash_provider=hash_provider,
        collision_guard=collision_guard,
    )


def conflict_group_id(
    *,
    project_id: str,
    variant_id: str,
    original: str,
    hash_provider: HashProvider | None = None,
    collision_guard: DigestCollisionGuard | None = None,
) -> str:
    payload = {
        "project_id": _non_empty(project_id, "project ID"),
        "variant_id": _non_empty(variant_id, "variant ID"),
        "normalized_original": normalize_original(original),
    }
    return canonical_digest(
        payload,
        namespace=f"{IDENTITY_SCHEMA}.conflict-group",
        hash_provider=hash_provider,
        collision_guard=collision_guard,
    )


def build_key(
    snapshot: Any,
    *,
    hash_provider: HashProvider | None = None,
    collision_guard: DigestCollisionGuard | None = None,
) -> str:
    """Create a stable build identity, excluding observation-only fields.

    BuildInputSnapshot is owned by Story 01. Accepting its immutable value
    structurally keeps this module from defining a competing input DTO.
    """

    payload = _canonical_value(snapshot, excluded_fields=_VOLATILE_BUILD_FIELDS, sort_sequences=True)
    return canonical_digest(
        payload,
        namespace=BUILD_KEY_SCHEMA,
        hash_provider=hash_provider,
        collision_guard=collision_guard,
    )


def _canonical_value(
    value: Any,
    *,
    excluded_fields: frozenset[str] = frozenset(),
    sort_sequences: bool = False,
) -> Any:
    canonical_payload = getattr(value, "canonical_payload", None)
    if callable(canonical_payload):
        return _canonical_value(canonical_payload(), excluded_fields=excluded_fields, sort_sequences=sort_sequences)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonical_value(
                getattr(value, field.name), excluded_fields=excluded_fields, sort_sequences=sort_sequences
            )
            for field in fields(value)
            if field.name not in excluded_fields
        }
    if isinstance(value, Enum):
        return _canonical_value(value.value, excluded_fields=excluded_fields, sort_sequences=sort_sequences)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical mapping keys must be strings")
            if key not in excluded_fields:
                result[key] = _canonical_value(item, excluded_fields=excluded_fields, sort_sequences=sort_sequences)
        return result
    if isinstance(value, (tuple, list, set, frozenset)):
        items = [
            _canonical_value(item, excluded_fields=excluded_fields, sort_sequences=sort_sequences) for item in value
        ]
        if sort_sequences or isinstance(value, (set, frozenset)):
            items.sort(key=_canonical_sort_key)
        return items
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON numbers must be finite")
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def _canonical_sort_key(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash(payload: bytes, provider: Callable[[bytes], str | bytes] | None) -> str:
    result = hashlib.sha256(payload).hexdigest() if provider is None else provider(payload)
    if isinstance(result, bytes):
        result = result.hex()
    return _non_empty(result, "hash result")


def _non_empty(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value.strip()


__all__ = [
    "BUILD_KEY_SCHEMA",
    "CANONICAL_SCHEMA",
    "IDENTITY_SCHEMA",
    "NORMALIZATION_SCHEMA",
    "DigestCollisionGuard",
    "HashProvider",
    "build_key",
    "candidate_id",
    "canonical_bytes",
    "canonical_digest",
    "conflict_group_id",
    "evidence_id",
    "normalize_original",
    "normalize_translation",
    "term_id",
]
