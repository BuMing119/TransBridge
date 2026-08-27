from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from jsonschema import Draft202012Validator
import pytest

from transbridge.ai_translator.structured_schemas import (
    ALL_AI_TRANSLATION_OUTPUT_SCHEMAS,
    ARBITRATION_BATCH_OUTPUT_SCHEMA,
    ARBITRATION_SINGLE_OUTPUT_SCHEMA,
    POLISH_BATCH_OUTPUT_SCHEMA,
    POLISH_SINGLE_OUTPUT_SCHEMA,
    POSTPROCESS_VALUES_OUTPUT_SCHEMA,
    PROOFREAD_OUTPUT_SCHEMA,
    QUALITY_GATE_BATCH_OUTPUT_SCHEMA,
    QUALITY_GATE_SINGLE_OUTPUT_SCHEMA,
    REFINEMENT_BATCH_OUTPUT_SCHEMA,
    REFINEMENT_SINGLE_OUTPUT_SCHEMA,
    TERM_EXTRACTION_OUTPUT_SCHEMA,
    TRANSLATION_OUTPUT_SCHEMA,
)

EXPECTED_SCHEMA_NAMES = (
    "transbridge_translation_v1",
    "transbridge_term_extraction_v1",
    "transbridge_proofread_v1",
    "transbridge_postprocess_values_v1",
    "transbridge_quality_gate_single_v1",
    "transbridge_quality_gate_batch_v1",
    "transbridge_refinement_single_v1",
    "transbridge_refinement_batch_v1",
    "transbridge_polish_single_v1",
    "transbridge_polish_batch_v1",
    "transbridge_arbitration_single_v1",
    "transbridge_arbitration_batch_v1",
)

BATCH_SCHEMAS = (
    TRANSLATION_OUTPUT_SCHEMA,
    TERM_EXTRACTION_OUTPUT_SCHEMA,
    PROOFREAD_OUTPUT_SCHEMA,
    POSTPROCESS_VALUES_OUTPUT_SCHEMA,
    QUALITY_GATE_BATCH_OUTPUT_SCHEMA,
    REFINEMENT_BATCH_OUTPUT_SCHEMA,
    POLISH_BATCH_OUTPUT_SCHEMA,
    ARBITRATION_BATCH_OUTPUT_SCHEMA,
)


def _assert_strict_objects(schema: Mapping[str, Any], path: str = "$") -> None:
    if schema.get("type") == "object":
        properties = schema.get("properties")
        assert isinstance(properties, Mapping), f"{path} must declare properties"
        assert schema.get("additionalProperties") is False, f"{path} must reject undeclared properties"
        assert set(schema.get("required", ())) == set(properties), f"{path} must require every declared field"
        for name, child in properties.items():
            assert isinstance(child, Mapping)
            _assert_strict_objects(child, f"{path}.{name}")

    if schema.get("type") == "array":
        items = schema.get("items")
        assert isinstance(items, Mapping), f"{path} must declare one stable item schema"
        _assert_strict_objects(items, f"{path}[]")


def test_schema_names_are_stable_unique_and_provider_safe() -> None:
    names = tuple(output_schema.name for output_schema in ALL_AI_TRANSLATION_OUTPUT_SCHEMAS)

    assert names == EXPECTED_SCHEMA_NAMES
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) for name in names)


def test_every_schema_has_a_strict_object_root_and_recursive_object_constraints() -> None:
    for output_schema in ALL_AI_TRANSLATION_OUTPUT_SCHEMAS:
        assert output_schema.schema["type"] == "object"
        Draft202012Validator.check_schema(output_schema.schema)
        _assert_strict_objects(output_schema.schema)


def test_every_batch_contract_uses_the_same_results_envelope() -> None:
    for output_schema in BATCH_SCHEMAS:
        assert set(output_schema.schema["properties"]) == {"results"}
        assert output_schema.schema["required"] == ["results"]
        assert output_schema.schema["properties"]["results"]["type"] == "array"


@pytest.mark.parametrize(
    ("output_schema", "payload"),
    [
        (
            TRANSLATION_OUTPUT_SCHEMA,
            {"results": [{"entry_id": "entry-42", "translation": "龙裔"}]},
        ),
        (
            TERM_EXTRACTION_OUTPUT_SCHEMA,
            {"results": [{"term": "Dragonborn", "translation": "龙裔"}]},
        ),
        (
            PROOFREAD_OUTPUT_SCHEMA,
            {
                "results": [
                    {
                        "entry_key": {"namespace": "legacy:v1", "local_key": "entry-42"},
                        "final_translation": "龙裔回来了。",
                    }
                ]
            },
        ),
        (
            POSTPROCESS_VALUES_OUTPUT_SCHEMA,
            {
                "results": [
                    {
                        "entry_key": {"namespace": "legacy:v1", "local_key": "entry-42"},
                        "value": "pass",
                    }
                ]
            },
        ),
        (
            QUALITY_GATE_SINGLE_OUTPUT_SCHEMA,
            {"verdict": "pass", "reason": "Accurate and fluent.", "issues": []},
        ),
        (
            QUALITY_GATE_BATCH_OUTPUT_SCHEMA,
            {
                "results": [
                    {
                        "entry_id": "entry-42",
                        "verdict": "uncertain",
                        "reason": "Context is ambiguous.",
                        "issues": ["Speaker is unknown."],
                    }
                ]
            },
        ),
        (
            REFINEMENT_SINGLE_OUTPUT_SCHEMA,
            {
                "refined_translation": "欢迎，龙裔。",
                "fixes_applied": [
                    {
                        "issue_type": "terminology",
                        "original_problem": "Inconsistent proper noun.",
                        "fix_description": "Applied the approved term.",
                    }
                ],
                "confidence": 0.95,
                "needs_arbitration": False,
                "note": "",
            },
        ),
        (
            REFINEMENT_BATCH_OUTPUT_SCHEMA,
            {
                "results": [
                    {
                        "entry_id": "entry-42",
                        "refined_translation": "欢迎，龙裔。",
                        "fixes_applied": [],
                        "confidence": 1.0,
                        "needs_arbitration": False,
                        "note": "No correction was necessary.",
                    }
                ]
            },
        ),
        (
            POLISH_SINGLE_OUTPUT_SCHEMA,
            {
                "polished_translation": "龙裔归来了。",
                "changes": [
                    {
                        "aspect": "fluency",
                        "before": "龙裔回来了。",
                        "after": "龙裔归来了。",
                        "reason": "Better fits the narrative tone.",
                    }
                ],
                "confidence": 0.9,
                "needs_arbitration": False,
                "note": "",
            },
        ),
        (
            POLISH_BATCH_OUTPUT_SCHEMA,
            {
                "results": [
                    {
                        "entry_id": "entry-42",
                        "polished_translation": "龙裔归来了。",
                        "changes": [],
                        "confidence": 0.9,
                        "needs_arbitration": False,
                        "note": "Already fluent.",
                    }
                ]
            },
        ),
        (
            ARBITRATION_SINGLE_OUTPUT_SCHEMA,
            {
                "verdict": "pending",
                "reason": "Creative intent is unclear.",
                "confidence": 0.6,
                "suggested_action": "Request human review.",
                "alternatives": ["Keep the current translation."],
            },
        ),
        (
            ARBITRATION_BATCH_OUTPUT_SCHEMA,
            {
                "results": [
                    {
                        "entry_id": "entry-42",
                        "verdict": "pass",
                        "reason": "All detected issues were corrected.",
                        "confidence": 0.98,
                        "suggested_action": "Accept the candidate.",
                        "alternatives": [],
                    }
                ]
            },
        ),
    ],
)
def test_typical_payloads_match_business_parser_contracts(output_schema, payload: dict[str, Any]) -> None:
    Draft202012Validator(output_schema.schema).validate(payload)


@pytest.mark.parametrize(
    ("output_schema", "payload"),
    [
        (
            QUALITY_GATE_SINGLE_OUTPUT_SCHEMA,
            {"verdict": "reject", "reason": "Wrong enum for this parser.", "issues": []},
        ),
        (
            REFINEMENT_SINGLE_OUTPUT_SCHEMA,
            {
                "refined_translation": "text",
                "fixes_applied": [],
                "confidence": 1.1,
                "needs_arbitration": False,
                "note": "",
            },
        ),
        (
            POLISH_SINGLE_OUTPUT_SCHEMA,
            {
                "polished_translation": "text",
                "changes": [{"aspect": "style", "before": "a", "after": "b", "reason": "c", "extra": 1}],
                "confidence": 0.8,
                "needs_arbitration": False,
                "note": "",
            },
        ),
        (
            ARBITRATION_SINGLE_OUTPUT_SCHEMA,
            {
                "verdict": "pass",
                "reason": "Valid fields except for the missing action.",
                "confidence": 0.9,
                "alternatives": [],
            },
        ),
    ],
)
def test_schemas_reject_parser_incompatible_enums_types_and_objects(output_schema, payload: dict[str, Any]) -> None:
    assert list(Draft202012Validator(output_schema.schema).iter_errors(payload))
