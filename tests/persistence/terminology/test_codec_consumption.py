import json

import pytest

from transbridge.application.terminology.models import ScopeKind, TermScope
from transbridge.persistence.terminology import codec


def test_unknown_fields_with_invalid_nested_tags_remain_ignored() -> None:
    raw = {
        "$type": "TermScope",
        "fields": {
            "future": {"$type": "Unknown", "fields": {}},
            "$tuple": {"$enum": "Unknown", "value": "invalid"},
        },
    }
    assert codec.loads(json.dumps(raw), TermScope) == TermScope.project()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"$tuple": ["龙", 1], "$type": "Unknown"}, ("龙", 1)),
        ({"$tuple": "abc"}, ("a", "b", "c")),
        ({"$tuple": {"key": {"$type": "Unknown"}}}, ("key",)),
        ({"$enum": "ScopeKind", "value": "project", "$type": "Unknown"}, ScopeKind.PROJECT),
        (["a", {"b": [1, {"$tuple": [2, 3]}]}], ["a", {"b": [1, (2, 3)]}]),
    ],
)
def test_tag_precedence_and_legacy_non_list_tuples_keep_values(raw, expected) -> None:
    assert codec.loads(json.dumps(raw), object) == expected


@pytest.mark.parametrize(
    ("raw", "cause"),
    [
        ({"$tuple": None}, TypeError),
        ({"$type": "TermScope", "fields": None}, TypeError),
        ({"$type": "TermScope", "fields": "kind"}, TypeError),
        ({"$type": "TermScope", "fields": 1}, TypeError),
        ({"$type": "Unknown", "fields": {}}, KeyError),
        ({"$type": "DraftRef", "fields": {}}, KeyError),
        ({"$type": "TermScope", "fields": {"kind": {"$type": "Unknown"}}}, KeyError),
    ],
)
def test_invalid_payloads_retain_public_error_and_cause(raw, cause) -> None:
    with pytest.raises(ValueError, match="invalid terminology persistence payload") as raised:
        codec.loads(json.dumps(raw), object)
    assert isinstance(raised.value.__cause__, cause)


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_object_boundary_threshold_preserves_payload(monkeypatch, offset: int) -> None:
    calls = []

    def boundary(value):
        calls.append(len(value))
        return value

    monkeypatch.setattr(codec, "_object_boundary", boundary)
    length = codec._OBJECT_BOUNDARY_MIN_CHARS + offset
    value = "x" * (length - len('{"value":""}'))
    payload = '{"value":"' + value + '"}'
    assert len(payload) == length
    assert codec.loads(payload, dict) == {"value": value}
    assert calls == ([] if offset < 0 else [1])
