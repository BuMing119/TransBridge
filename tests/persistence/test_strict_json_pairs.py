"""Duplicate detection preserves parsed values, order and first-error causes."""

import json

import pytest

from transbridge.persistence.source_artifact_store import _unique_object
from transbridge.persistence.v2.models import SchemaValidationError
from transbridge.persistence.v2.schema import _reject_duplicate_keys, parse_json_bytes


@pytest.mark.parametrize("hook", [_unique_object, _reject_duplicate_keys])
@pytest.mark.parametrize("text", ["{}", '{"z":0,"a":1}', '{"a":{"a":1},"b":[{"a":2}]}'])
def test_unique_objects_preserve_content_and_order(hook, text):
    parsed = json.loads(text, object_pairs_hook=hook)
    assert parsed == json.loads(text)
    assert list(parsed) == list(json.loads(text))


@pytest.mark.parametrize("hook", [_unique_object, _reject_duplicate_keys])
@pytest.mark.parametrize(
    ("text", "key"),
    [
        ('{"x":1,"x":1}', "x"),
        ('{"x":1,"x":2,"a":1,"a":2}', "x"),
        ('{"a":1,"\\u0061":2}', "a"),
        ('{"nested":[{"a/b~":1,"a/b~":2}]}', "a/b~"),
        ('{"":0,"":1}', ""),
    ],
)
def test_duplicate_keys_keep_first_duplicate_diagnostic(hook, text, key):
    prefix = "duplicate source artifact JSON key: " if hook is _unique_object else "duplicate JSON key: "
    with pytest.raises(ValueError) as error:
        json.loads(text, object_pairs_hook=hook)
    assert str(error.value) == prefix + key


def test_persistence_error_wrapper_and_original_cause_remain_intact():
    with pytest.raises(SchemaValidationError) as error:
        parse_json_bytes(b'{"nested":{"a":1,"\\u0061":2}}')
    assert error.value.code == "INVALID_JSON"
    assert str(error.value.__cause__) == "duplicate JSON key: a"


def test_iterable_callers_still_reject_duplicates():
    assert _reject_duplicate_keys(iter([("b", 2), ("a", 1)])) == {"b": 2, "a": 1}
    with pytest.raises(ValueError, match="duplicate JSON key: a"):
        _reject_duplicate_keys(iter([("a", 1), ("a", 2)]))
