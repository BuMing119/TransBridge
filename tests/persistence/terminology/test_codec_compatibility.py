from dataclasses import replace
import json

import pytest

from tests.contracts.terminology.test_repository_contract import _build, _version
from transbridge.application.terminology.models import (
    BuildResult,
    DecisionStatus,
    TermDecision,
    TerminologyVersion,
    TermScope,
)
from transbridge.persistence.terminology.codec import dumps, loads

_TERM_JSON = (
    '{"$type":"TermDecision","fields":{"evidence_ids":{"$tuple":["e1"]},'
    '"normalized_original":"dragon","notes":"注释\\n\\"","original":"Dragon",'
    '"project_id":"p","replacement_of":null,"scope":{"$type":"TermScope","fields":'
    '{"kind":{"$enum":"ScopeKind","value":"plugin"},"plugin_id":"A.esm"}},'
    '"status":{"$enum":"DecisionStatus","value":"manual_confirmed"},"suppressed":false,'
    '"term_id":"t","translation":"龙","variant_id":"v","variants":{"$tuple":[]}}}'
)


def test_term_json_matches_existing_tagged_format_exactly() -> None:
    term = TermDecision(
        "t",
        "p",
        "v",
        "Dragon",
        "dragon",
        "龙",
        TermScope.plugin("A.esm"),
        DecisionStatus.MANUAL_CONFIRMED,
        notes='注释\n"',
        evidence_ids=("e1",),
    )
    assert dumps(term) == _TERM_JSON
    assert loads(_TERM_JSON, TermDecision) == term


def test_scalar_fast_path_preserves_enum_tuple_and_float_tags() -> None:
    value = {"items": (DecisionStatus.ADOPTED, None, True, 1, 0.0, -0.0, 1.25, "龙")}
    expected = '{"items":{"$tuple":[{"$enum":"DecisionStatus","value":"adopted"},null,true,1,0.0,-0.0,1.25,"龙"]}}'
    assert dumps(value) == expected
    decoded = loads(expected, dict)
    assert decoded == value
    assert type(decoded["items"][0]) is DecisionStatus
    assert type(decoded["items"][2]) is bool
    assert type(decoded["items"][3]) is int


def test_legacy_default_fields_and_unknown_fields_keep_existing_semantics() -> None:
    build = replace(_build(), input_digest=None)
    payload = json.loads(dumps(build))
    del payload["fields"]["input_digest"]
    payload["fields"]["future_annotation"] = "ignored"
    assert loads(json.dumps(payload), BuildResult) == build


def test_complete_version_roundtrip_preserves_persistence_bytes() -> None:
    version = _version(_build(), "version-1")
    payload = dumps(version)
    decoded = loads(payload, TerminologyVersion)
    assert decoded == version
    assert dumps(decoded) == payload


@pytest.mark.parametrize(
    "payload",
    [
        '{"$type":"Unknown","fields":{}}',
        '{"$type":"DraftRef","fields":{}}',
        '{"$enum":"DecisionStatus","value":"unknown"}',
        '{"$type":"TermScope","fields":{"kind":{"$enum":"ScopeKind","value":"plugin"}}}',
        "{invalid",
    ],
)
def test_invalid_payloads_still_fail_closed(payload: str) -> None:
    with pytest.raises(ValueError, match="invalid terminology persistence payload"):
        loads(payload, object)


def test_expected_type_is_still_checked() -> None:
    with pytest.raises(ValueError, match="expected BuildResult, found TermDecision"):
        loads(_TERM_JSON, BuildResult)
