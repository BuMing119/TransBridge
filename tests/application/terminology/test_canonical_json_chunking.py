import json

import pytest

from transbridge.application.terminology import canonical_json
from transbridge.application.terminology.errors import DigestCollisionError
from transbridge.application.terminology.identity import (
    CANONICAL_SCHEMA,
    DigestCollisionGuard,
    canonical_bytes,
    canonical_digest,
)


@pytest.mark.parametrize("size", [0, 127, 128, 129, 257])
def test_sequence_boundaries_keep_exact_canonical_bytes(size: int) -> None:
    value = {
        "outer": [{"text": '龙𐐷\n"\\', "numbers": [None, True, -0.0, 1e-8, index]} for index in range(size)],
        "nested": [[list(range(size))]],
    }
    expected = json.dumps(
        {"schema": CANONICAL_SCHEMA, "value": value},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert canonical_bytes(value) == expected


def test_large_sequence_reaches_c_encoder_in_bounded_batches(monkeypatch) -> None:
    sizes = []
    encode = canonical_json._ENCODER.encode

    def observe(value):
        if isinstance(value, list):
            sizes.append(len(value))
        return encode(value)

    monkeypatch.setattr(canonical_json._ENCODER, "encode", observe)
    value = list(range(1000))
    assert json.loads(canonical_bytes(value))["value"] == value
    assert sizes and max(sizes) <= 128


def test_small_payload_keeps_one_encoder_call(monkeypatch) -> None:
    calls = []
    encode = canonical_json._ENCODER.encode

    def observe(value):
        calls.append(value)
        return encode(value)

    monkeypatch.setattr(canonical_json._ENCODER, "encode", observe)
    assert json.loads(canonical_bytes({"z": [1, 2], "a": "龙"}))["value"] == {"a": "龙", "z": [1, 2]}
    assert len(calls) == 1


def test_custom_payloads_run_in_original_insertion_order_before_chunking() -> None:
    calls = []

    class CounterHook:
        def canonical_payload(self):
            calls.append(len(calls) + 1)
            return calls[-1]

    value = {"z": CounterHook(), "a": CounterHook(), "large": [None] * 129}
    encoded = json.loads(canonical_bytes(value))
    assert encoded["value"] == {"a": 2, "large": [None] * 129, "z": 1}
    assert calls == [1, 2]


def test_custom_sequence_payload_precedence_is_unchanged() -> None:
    class Sequence(list):
        def canonical_payload(self):
            return "custom"

    assert json.loads(canonical_bytes(Sequence(range(129))))["value"] == "custom"


def test_large_payload_preserves_injected_hash_bytes_and_collision_guard() -> None:
    value = {"rows": list(range(257))}
    encoded = canonical_bytes(value, schema="custom.v1")
    seen = []

    def provider(payload):
        seen.append(payload)
        return b"same"

    guard = DigestCollisionGuard()
    assert canonical_digest(value, namespace=" custom.v1 ", hash_provider=provider, collision_guard=guard).startswith(
        "custom.v1:"
    )
    assert seen == [encoded]
    with pytest.raises(DigestCollisionError):
        canonical_digest(
            {"rows": list(range(258))}, namespace="custom.v1", hash_provider=provider, collision_guard=guard
        )


def test_invalid_unicode_and_nonfinite_values_keep_original_failure_types() -> None:
    with pytest.raises(UnicodeEncodeError):
        canonical_bytes(["\ud800"] * 129)
    with pytest.raises(ValueError, match="finite"):
        canonical_bytes([float("nan")] * 129)
