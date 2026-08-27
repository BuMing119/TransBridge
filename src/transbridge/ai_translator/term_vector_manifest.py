"""Versioned, secret-free manifest helpers for persisted term vector indexes."""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

INDEX_METADATA_SCHEMA = 2


class EmbeddingIdentitySource(Protocol):
    @property
    def dimension(self) -> int: ...


def embedding_fingerprint(client: EmbeddingIdentitySource) -> str:
    identity = getattr(client, "index_identity", None)
    if callable(identity):
        identity = identity()
    if not isinstance(identity, dict):
        identity = {
            "backend": f"{type(client).__module__}.{type(client).__qualname__}",
            "dimension": client.dimension,
        }
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_index_metadata(
    *,
    term_hash: str,
    client: EmbeddingIdentitySource,
    dimension: int,
    terms: list[dict],
) -> dict[str, object]:
    return {
        "schema_version": INDEX_METADATA_SCHEMA,
        "hash": term_hash,
        "embedding_fingerprint": embedding_fingerprint(client),
        "dimension": int(dimension),
        "terms": terms,
    }


def validate_index_metadata(
    metadata: dict,
    *,
    expected_term_hash: str,
    client: EmbeddingIdentitySource,
) -> tuple[bool, str, int]:
    if metadata.get("schema_version") != INDEX_METADATA_SCHEMA:
        return False, "Vector index metadata is legacy or unsupported", 0
    if metadata.get("hash") != expected_term_hash:
        return False, "Term content changed", 0
    if metadata.get("embedding_fingerprint") != embedding_fingerprint(client):
        return False, "Embedding configuration changed", 0

    expected_dimension = int(metadata.get("dimension") or 0)
    client_dimension = int(client.dimension or 0)
    if expected_dimension <= 0 or (client_dimension > 0 and expected_dimension != client_dimension):
        return False, "Embedding dimension changed", expected_dimension
    return True, "", expected_dimension


__all__ = [
    "INDEX_METADATA_SCHEMA",
    "create_index_metadata",
    "embedding_fingerprint",
    "validate_index_metadata",
]
