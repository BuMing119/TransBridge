"""Local executor reuse retains standard jsonschema errors and scope boundaries."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

from jsonschema import Draft202012Validator, FormatChecker, validators
from jsonschema.exceptions import ValidationError
from jsonschema.validators import _RefResolver
import pytest

from transbridge.persistence.v2.ids import EntityKind
from transbridge.persistence.v2.models import SCHEMA_VERSION
from transbridge.persistence.v2.schema import _object_items, _PersistenceValidator, schema_for
from transbridge.persistence.v2.schema_execution import local_schema_validator, schema_execution


def _errors(validator, instance):
    return [
        (tuple(error.absolute_path), tuple(error.absolute_schema_path), error.validator, error.message)
        for error in validator.iter_errors(instance)
    ]


def _document():
    return {
        "schema_version": SCHEMA_VERSION,
        "entity_type": "variant",
        "id": "variant",
        "revision": 1,
        "data": {
            "project_id": "project",
            "translations": {"entry": "Translated"},
            "labels": {"entry": ["reviewed"]},
            "label_library": {"reviewed": {"nested": [True, 1.0]}},
            "entries": [
                {
                    "entry_key": {"namespace": "source:test", "local_key": "entry"},
                    "translation": "Translated",
                    "stage": 3,
                    "labels": ["reviewed"],
                    "provenance": [{"opaque": {"$ref": [True, {"not": "a schema"}]}}],
                    "external_refs": [
                        {
                            "system": "remote",
                            "scope": "project",
                            "opaque_id": 1,
                            "metadata": {"nested": [{"$id": "opaque instance data"}]},
                        }
                    ],
                    "revision": 1,
                    "tombstone": False,
                }
            ],
        },
    }


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "missing_root",
        "missing_entry",
        "extra_root",
        "extra_entry",
        "multiple_errors",
        "metadata_type",
        "opaque_metadata",
        "labels_duplicate",
        "labels_type",
        "boolean_revision",
        "integer_revision",
        "float_integer_revision",
        "enum_boolean",
        "enum_float_integer",
        "entry_key_pattern",
        "long_identity",
        "bad_container",
    ],
)
def test_persistence_schema_errors_match_standard_execution(case):
    document = _document()
    entry = document["data"]["entries"][0]
    if case == "missing_root":
        del document["id"]
    elif case == "missing_entry":
        del entry["translation"]
    elif case == "extra_root":
        document["unexpected"] = None
    elif case == "extra_entry":
        entry["unexpected"] = None
    elif case == "multiple_errors":
        entry.update(translation=False, stage=8, revision=-1, labels=["", ""])
        document["data"]["entries"].append({"entry_key": {"namespace": ""}})
        document["data"]["translations"]["wrong"] = 5
    elif case == "metadata_type":
        entry["external_refs"][0]["metadata"] = []
    elif case == "opaque_metadata":
        entry["provenance"].append({"$schema": "opaque", "$dynamicRef": [1, True, {}]})
    elif case == "labels_duplicate":
        entry["labels"] = ["same", "same"]
    elif case == "labels_type":
        document["data"]["labels"]["entry"] = [True, 1, 1.0]
    elif case == "boolean_revision":
        document["revision"] = True
    elif case == "integer_revision":
        document["revision"] = 1
    elif case == "float_integer_revision":
        document["revision"] = 1.0
    elif case == "enum_boolean":
        entry["stage"] = False
    elif case == "enum_float_integer":
        entry["stage"] = 1.0
    elif case == "entry_key_pattern":
        entry["entry_key"]["namespace"] = ""
    elif case == "long_identity":
        document["id"] = "v" * 65
    elif case == "bad_container":
        document["data"]["entries"] = {"entry": entry}
    schema = schema_for(EntityKind.VARIANT)
    standard = validators.extend(Draft202012Validator, {"items": _object_items})(schema)

    with schema_execution(_PersistenceValidator, schema) as optimized:
        assert _errors(optimized, document) == _errors(standard, document)


def _count_evolves(monkeypatch, validator_type):
    calls = []
    original = validator_type.evolve

    def evolve(self, **changes):
        calls.append(id(changes.get("schema", self.schema)))
        return original(self, **changes)

    monkeypatch.setattr(validator_type, "evolve", evolve)
    return calls


def test_standard_draft_class_is_not_modified():
    original_descend = Draft202012Validator.descend

    validator_type = local_schema_validator({})

    assert validator_type is not Draft202012Validator
    assert Draft202012Validator.descend is original_descend


def test_executor_creation_depends_on_schema_nodes_and_every_input_is_validated(monkeypatch):
    validator_type = local_schema_validator({})
    calls = _count_evolves(monkeypatch, validator_type)
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["text", "number"],
            "properties": {"text": {"type": "string"}, "number": {"type": "integer"}},
            "additionalProperties": False,
        },
    }
    instances = [{"text": "valid", "number": index} for index in range(30)]
    instances[17]["text"] = False
    instances[25]["number"] = True

    with schema_execution(validator_type, schema) as validator:
        errors = _errors(validator, instances)

    assert len(calls) == len(set(calls)) == 3
    assert [error[0] for error in errors] == [(17, "text"), (25, "number")]
    assert errors == _errors(Draft202012Validator(schema), instances)


@pytest.mark.parametrize("keyword", ["enum", "const"])
def test_enum_and_const_objects_are_data_and_not_scanned_as_schemas(monkeypatch, keyword):
    checks = []
    original = Draft202012Validator.VALIDATORS[keyword]

    def check(validator, value, instance, schema):
        checks.append(instance)
        yield from original(validator, value, instance, schema)

    validator_type = local_schema_validator({keyword: check})
    calls = _count_evolves(monkeypatch, validator_type)
    value = {"$ref": "opaque", "properties": {"x": {"unknown": True}}}
    schema = {"type": "array", "items": {keyword: [value] if keyword == "enum" else value}}
    with schema_execution(validator_type, schema) as validator:
        assert not _errors(validator, [deepcopy(value) for _ in range(8)])
    assert len(calls) == 1
    assert len(checks) == 8


@pytest.mark.parametrize(
    "schema",
    [
        True,
        False,
        {"type": "array", "items": False},
        {"type": "array", "items": {"type": "integer", "not": {"const": 1}}},
        {"type": "array", "items": {"$id": "https://example.test/child", "type": "integer"}},
        {"type": "array", "items": {"$anchor": "child", "type": "integer"}},
        {"type": "array", "items": {"$dynamicAnchor": "child", "type": "integer"}},
        {"type": "array", "items": {"$ref": "#/$defs/item"}, "$defs": {"item": {"type": "integer"}}},
        {
            "$dynamicAnchor": "root",
            "type": "array",
            "items": {"$dynamicRef": "#/$defs/item"},
            "$defs": {"item": {"type": "integer"}},
        },
        {"type": "array", "items": {"type": "integer", "description": "future annotation"}},
        {"$schema": "http://json-schema.org/draft-07/schema#", "type": "array", "items": {"type": "integer"}},
    ],
)
def test_unsupported_schema_uses_standard_errors_and_descents(monkeypatch, schema):
    validator_type = local_schema_validator({})
    calls = _count_evolves(monkeypatch, validator_type)
    instance = ["wrong", 1, True, 1.0]
    expected = _errors(validator_type(schema), instance)
    standard_calls = len(calls)
    calls.clear()

    with schema_execution(validator_type, schema) as validator:
        assert _errors(validator, instance) == expected

    assert len(calls) == standard_calls


@pytest.mark.parametrize("configuration", ["_resolver", "resolver"])
def test_explicit_root_resolver_disables_reuse(monkeypatch, configuration):
    schema = {"type": "array", "items": {"type": "integer"}}
    validator_type = local_schema_validator({})
    calls = _count_evolves(monkeypatch, validator_type)
    resolver = (
        Draft202012Validator(schema)._resolver if configuration == "_resolver" else _RefResolver.from_schema(schema)
    )
    options = {configuration: resolver}

    with schema_execution(validator_type, schema, **options) as validator:
        actual = _errors(validator, ["wrong"] * 4)

    assert len(calls) == 4
    assert actual == _errors(Draft202012Validator(schema, **options), ["wrong"] * 4)


def test_explicit_descend_resolver_and_boolean_nodes_use_standard_path(monkeypatch):
    validator_type = local_schema_validator({})
    calls = _count_evolves(monkeypatch, validator_type)
    child = {"type": "integer"}
    schema = {"properties": {"child": child, "closed": False}}
    with schema_execution(validator_type, schema) as validator:
        for _ in range(3):
            actual = list(
                validator.descend("wrong", child, path="child", schema_path="child", resolver=validator._resolver)
            )
            expected = list(Draft202012Validator(schema).descend("wrong", child, path="child", schema_path="child"))
            assert [(error.message, tuple(error.path), tuple(error.schema_path)) for error in actual] == [
                (error.message, tuple(error.path), tuple(error.schema_path)) for error in expected
            ]
        actual = list(validator.descend(1, False, path="closed", schema_path="closed"))
        assert tuple(actual[0].path) == tuple(actual[0].schema_path) == ()
    assert len(calls) == 3


def test_format_checker_is_inherited_by_cached_children():
    checker = FormatChecker()

    @checker.checks("only-ok")
    def only_ok(value):
        return value == "ok"

    schema = {"type": "array", "items": {"type": "string", "format": "only-ok"}}
    validator_type = local_schema_validator({})
    with schema_execution(validator_type, schema, format_checker=checker) as validator:
        actual = _errors(validator, ["ok", "bad", "ok", "bad"])
    assert actual == _errors(Draft202012Validator(schema, format_checker=checker), ["ok", "bad", "ok", "bad"])


def test_changed_handler_is_observed_by_next_validation_call():
    schema = {"type": "array", "items": {"type": "string"}}
    validator_type = local_schema_validator({})
    with schema_execution(validator_type, schema) as validator:
        assert not _errors(validator, ["text"] * 3)

    def changed(validator, value, instance, schema):
        if isinstance(instance, str):
            yield ValidationError("new handler")

    validator_type.VALIDATORS["type"] = changed
    with schema_execution(validator_type, schema) as validator:
        assert [error[3] for error in _errors(validator, ["text"] * 3)] == ["new handler"] * 3


def test_nested_calls_and_exception_restore_the_outer_cache(monkeypatch):
    schema = {"type": "array", "items": {"type": "string"}}
    validator_type = local_schema_validator({})
    calls = _count_evolves(monkeypatch, validator_type)
    with schema_execution(validator_type, schema) as outer:
        assert not _errors(outer, ["text"] * 3)
        with pytest.raises(RuntimeError, match="inner failure"):
            with schema_execution(validator_type, schema) as inner:
                assert not _errors(inner, ["inner"] * 3)
                raise RuntimeError("inner failure")
        assert not _errors(outer, ["text"] * 3)
    assert len(calls) == 2
    # Outside a scope, our class uses normal per-input descend execution.
    assert not _errors(validator_type(schema), ["text"] * 3)
    assert len(calls) == 5


def test_concurrent_validation_calls_do_not_share_executors(monkeypatch):
    schema = {"type": "array", "items": {"type": "integer"}}
    validator_type = local_schema_validator({})
    calls = _count_evolves(monkeypatch, validator_type)
    barrier = Barrier(2)

    def validate(instance):
        with schema_execution(validator_type, schema) as validator:
            barrier.wait(timeout=10)
            return _errors(validator, instance)

    with ThreadPoolExecutor(max_workers=2) as pool:
        valid = pool.submit(validate, list(range(20)))
        invalid = pool.submit(validate, ["bad"] * 20)
        assert not valid.result()
        assert len(invalid.result()) == 20
    assert len(calls) == 2
