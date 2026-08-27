"""Stable Provider-neutral schemas for structured AI-translation responses.

The schemas deliberately use only the JSON Schema subset shared by the
OpenAI-compatible and Anthropic protocols.  They describe response shape and
primitive types; entry ownership, completeness, non-empty translations, and
other domain rules remain the responsibility of the existing parsers.
"""

from __future__ import annotations

from typing import Any

from transbridge.infra.llm_structured_outputs import LlmOutputSchema

JsonSchema = dict[str, Any]


def _object(properties: dict[str, JsonSchema]) -> JsonSchema:
    """Build a strict object whose declared fields are all required."""

    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _array(item_schema: JsonSchema) -> JsonSchema:
    return {"type": "array", "items": item_schema}


def _string() -> JsonSchema:
    return {"type": "string"}


def _string_array() -> JsonSchema:
    return _array(_string())


def _confidence() -> JsonSchema:
    return {"type": "number", "minimum": 0, "maximum": 1}


def _entry_key() -> JsonSchema:
    return _object({
        "namespace": _string(),
        "local_key": _string(),
    })


def _results_envelope(item_schema: JsonSchema) -> JsonSchema:
    return _object({"results": _array(item_schema)})


def _quality_gate_fields() -> dict[str, JsonSchema]:
    return {
        "verdict": {"type": "string", "enum": ["pass", "fail", "uncertain"]},
        "reason": _string(),
        "issues": _string_array(),
    }


def _fixes_applied() -> JsonSchema:
    return _array(
        _object({
            "issue_type": _string(),
            "original_problem": _string(),
            "fix_description": _string(),
        })
    )


def _refinement_fields() -> dict[str, JsonSchema]:
    return {
        "refined_translation": _string(),
        "fixes_applied": _fixes_applied(),
        "confidence": _confidence(),
        "needs_arbitration": {"type": "boolean"},
        "note": _string(),
    }


def _polish_changes() -> JsonSchema:
    return _array(
        _object({
            "aspect": _string(),
            "before": _string(),
            "after": _string(),
            "reason": _string(),
        })
    )


def _polish_fields() -> dict[str, JsonSchema]:
    return {
        "polished_translation": _string(),
        "changes": _polish_changes(),
        "confidence": _confidence(),
        "needs_arbitration": {"type": "boolean"},
        "note": _string(),
    }


def _arbitration_fields() -> dict[str, JsonSchema]:
    return {
        "verdict": {"type": "string", "enum": ["pass", "reject", "pending"]},
        "reason": _string(),
        "confidence": _confidence(),
        "suggested_action": _string(),
        "alternatives": _string_array(),
    }


TRANSLATION_OUTPUT_SCHEMA = LlmOutputSchema(
    name="transbridge_translation_v1",
    schema=_results_envelope(
        _object({
            "entry_id": _string(),
            "translation": _string(),
        })
    ),
)

TERM_EXTRACTION_OUTPUT_SCHEMA = LlmOutputSchema(
    name="transbridge_term_extraction_v1",
    schema=_results_envelope(
        _object({
            "term": _string(),
            "translation": _string(),
        })
    ),
)

PROOFREAD_OUTPUT_SCHEMA = LlmOutputSchema(
    name="transbridge_proofread_v1",
    schema=_results_envelope(
        _object({
            "entry_key": _entry_key(),
            "final_translation": _string(),
        })
    ),
)

POSTPROCESS_VALUES_OUTPUT_SCHEMA = LlmOutputSchema(
    name="transbridge_postprocess_values_v1",
    schema=_results_envelope(
        _object({
            "entry_key": _entry_key(),
            "value": _string(),
        })
    ),
)

QUALITY_GATE_SINGLE_OUTPUT_SCHEMA = LlmOutputSchema(
    name="transbridge_quality_gate_single_v1",
    schema=_object(_quality_gate_fields()),
)

QUALITY_GATE_BATCH_OUTPUT_SCHEMA = LlmOutputSchema(
    name="transbridge_quality_gate_batch_v1",
    schema=_results_envelope(_object({"entry_id": _string(), **_quality_gate_fields()})),
)

REFINEMENT_SINGLE_OUTPUT_SCHEMA = LlmOutputSchema(
    name="transbridge_refinement_single_v1",
    schema=_object(_refinement_fields()),
)

REFINEMENT_BATCH_OUTPUT_SCHEMA = LlmOutputSchema(
    name="transbridge_refinement_batch_v1",
    schema=_results_envelope(_object({"entry_id": _string(), **_refinement_fields()})),
)

POLISH_SINGLE_OUTPUT_SCHEMA = LlmOutputSchema(
    name="transbridge_polish_single_v1",
    schema=_object(_polish_fields()),
)

POLISH_BATCH_OUTPUT_SCHEMA = LlmOutputSchema(
    name="transbridge_polish_batch_v1",
    schema=_results_envelope(_object({"entry_id": _string(), **_polish_fields()})),
)

ARBITRATION_SINGLE_OUTPUT_SCHEMA = LlmOutputSchema(
    name="transbridge_arbitration_single_v1",
    schema=_object(_arbitration_fields()),
)

ARBITRATION_BATCH_OUTPUT_SCHEMA = LlmOutputSchema(
    name="transbridge_arbitration_batch_v1",
    schema=_results_envelope(_object({"entry_id": _string(), **_arbitration_fields()})),
)


ALL_AI_TRANSLATION_OUTPUT_SCHEMAS = (
    TRANSLATION_OUTPUT_SCHEMA,
    TERM_EXTRACTION_OUTPUT_SCHEMA,
    PROOFREAD_OUTPUT_SCHEMA,
    POSTPROCESS_VALUES_OUTPUT_SCHEMA,
    QUALITY_GATE_SINGLE_OUTPUT_SCHEMA,
    QUALITY_GATE_BATCH_OUTPUT_SCHEMA,
    REFINEMENT_SINGLE_OUTPUT_SCHEMA,
    REFINEMENT_BATCH_OUTPUT_SCHEMA,
    POLISH_SINGLE_OUTPUT_SCHEMA,
    POLISH_BATCH_OUTPUT_SCHEMA,
    ARBITRATION_SINGLE_OUTPUT_SCHEMA,
    ARBITRATION_BATCH_OUTPUT_SCHEMA,
)


__all__ = [
    "ALL_AI_TRANSLATION_OUTPUT_SCHEMAS",
    "ARBITRATION_BATCH_OUTPUT_SCHEMA",
    "ARBITRATION_SINGLE_OUTPUT_SCHEMA",
    "POLISH_BATCH_OUTPUT_SCHEMA",
    "POLISH_SINGLE_OUTPUT_SCHEMA",
    "POSTPROCESS_VALUES_OUTPUT_SCHEMA",
    "PROOFREAD_OUTPUT_SCHEMA",
    "QUALITY_GATE_BATCH_OUTPUT_SCHEMA",
    "QUALITY_GATE_SINGLE_OUTPUT_SCHEMA",
    "REFINEMENT_BATCH_OUTPUT_SCHEMA",
    "REFINEMENT_SINGLE_OUTPUT_SCHEMA",
    "TERM_EXTRACTION_OUTPUT_SCHEMA",
    "TRANSLATION_OUTPUT_SCHEMA",
]
