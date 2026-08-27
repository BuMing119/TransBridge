from __future__ import annotations

from dataclasses import dataclass

import pytest

from transbridge.ai_translator.term_vector_manifest import create_index_metadata, validate_index_metadata


@dataclass
class _Client:
    identity: dict[str, object]
    dimension: int = 8

    @property
    def index_identity(self) -> dict[str, object]:
        return self.identity


def _metadata(client: _Client) -> dict[str, object]:
    return create_index_metadata(
        term_hash="terms-v1",
        client=client,
        dimension=client.dimension,
        terms=[{"text": "Dragon", "term": "Dragon", "translation": "龙"}],
    )


def test_manifest_accepts_same_embedding_identity_without_persisting_raw_endpoint() -> None:
    client = _Client({"mode": "api", "provider": "openai", "model": "m1", "base_url": "https://api.test/v1"})

    metadata = _metadata(client)
    valid, reason, dimension = validate_index_metadata(metadata, expected_term_hash="terms-v1", client=client)

    assert valid is True
    assert reason == ""
    assert dimension == 8
    assert "https://api.test/v1" not in str(metadata)


@pytest.mark.parametrize(
    "changed_identity",
    (
        {"mode": "local", "provider": "local", "model": "m1", "base_url": ""},
        {"mode": "api", "provider": "custom", "model": "m1", "base_url": "https://api.test/v1"},
        {"mode": "api", "provider": "openai", "model": "m2", "base_url": "https://api.test/v1"},
        {"mode": "api", "provider": "openai", "model": "m1", "base_url": "https://other.test/v1"},
    ),
)
def test_manifest_rejects_provider_model_mode_or_endpoint_changes(changed_identity: dict[str, object]) -> None:
    original = _Client({"mode": "api", "provider": "openai", "model": "m1", "base_url": "https://api.test/v1"})

    valid, reason, _dimension = validate_index_metadata(
        _metadata(original),
        expected_term_hash="terms-v1",
        client=_Client(changed_identity),
    )

    assert valid is False
    assert reason == "Embedding configuration changed"


def test_manifest_rejects_dimension_and_legacy_schema_changes() -> None:
    client = _Client({"mode": "local", "model": "m1"}, dimension=8)
    metadata = _metadata(client)

    valid_dimension, _, _ = validate_index_metadata(
        metadata,
        expected_term_hash="terms-v1",
        client=_Client({"mode": "local", "model": "m1"}, dimension=16),
    )
    metadata.pop("schema_version")
    valid_schema, reason, _ = validate_index_metadata(
        metadata,
        expected_term_hash="terms-v1",
        client=client,
    )

    assert valid_dimension is False
    assert valid_schema is False
    assert "legacy" in reason
