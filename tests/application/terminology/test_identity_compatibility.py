from dataclasses import dataclass
from enum import Enum, IntEnum, StrEnum
import hashlib

import pytest

from transbridge.application.terminology.identity import canonical_bytes, canonical_digest
from transbridge.application.terminology.models import TermScope


class _Text(StrEnum):
    NAME = "龙"


class _Number(IntEnum):
    ONE = 1


class _Plain(Enum):
    VALUE = "ordinary"


@dataclass(frozen=True)
class _Value:
    text: str
    number: float


def test_canonical_bytes_match_existing_golden_representation() -> None:
    value = {
        "text": _Text.NAME,
        "number": _Number.ONE,
        "plain": _Plain.VALUE,
        "floats": (0.0, -0.0, 1.25, 1e-8),
        "values": [None, True, False, 1, '换行\n引号"反斜线\\'],
        "scope": TermScope.plugin("A.esm"),
        "dto": _Value("剑", 2.5),
        "unordered": frozenset({2, 1}),
    }
    golden = (
        '{"schema":"terminology.canonical-json.v1","value":'
        '{"dto":{"number":2.5,"text":"剑"},"floats":[0.0,-0.0,1.25,1e-08],'
        '"number":1,"plain":"ordinary","scope":{"kind":"plugin","plugin_id":"A.esm"},'
        '"text":"龙","unordered":[1,2],"values":[null,true,false,1,"换行\\n引号\\"反斜线\\\\"]}}'
    ).encode()
    assert canonical_bytes(value) == golden
    assert canonical_digest(value, namespace="terminology.canonical-json.v1") == (
        "terminology.canonical-json.v1:" + hashlib.sha256(golden).hexdigest()
    )


def test_digest_namespaces_remain_distinct() -> None:
    value = (_Value("龙", -0.0),)
    for namespace in ("terminology.manual-decision-state.v1", "terminology.draft-decision-set.v1"):
        golden = ('{"schema":"' + namespace + '","value":[{"number":-0.0,"text":"龙"}]}').encode("utf-8")
        assert canonical_bytes(value, schema=namespace) == golden
        assert canonical_digest(value, namespace=namespace) == namespace + ":" + hashlib.sha256(golden).hexdigest()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_float_is_rejected_before_json_serialization(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        canonical_bytes({"value": value})


def test_scalar_subclasses_keep_canonical_payload_precedence() -> None:
    class Text(str):
        def canonical_payload(self):
            return {"text": str(self)}

    class Number(float):
        def canonical_payload(self):
            return "overridden"

    assert (
        canonical_bytes((Text("龙"), Number(float("nan"))))
        == ('{"schema":"terminology.canonical-json.v1","value":[{"text":"龙"},"overridden"]}').encode()
    )


def test_bytes_and_mapping_validation_are_unchanged() -> None:
    assert canonical_bytes(b"abc") == (
        b'{"schema":"terminology.canonical-json.v1","value":{"bytes_sha256":'
        b'"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad","length":3}}'
    )
    with pytest.raises(TypeError, match="mapping keys must be strings"):
        canonical_bytes({1: "value"})
