from __future__ import annotations

import pytest

from transbridge.ai_translator.post_processor.prompt_contract import build_postprocess_messages
from transbridge.ai_translator.structured_schemas import (
    ARBITRATION_BATCH_OUTPUT_SCHEMA,
    ARBITRATION_SINGLE_OUTPUT_SCHEMA,
    POLISH_BATCH_OUTPUT_SCHEMA,
    POLISH_SINGLE_OUTPUT_SCHEMA,
    QUALITY_GATE_BATCH_OUTPUT_SCHEMA,
    QUALITY_GATE_SINGLE_OUTPUT_SCHEMA,
    REFINEMENT_BATCH_OUTPUT_SCHEMA,
    REFINEMENT_SINGLE_OUTPUT_SCHEMA,
)
from transbridge.infra.llm_structured_outputs import extract_structured_output_directive


@pytest.mark.parametrize(
    ("stage", "shape", "expected_schema"),
    [
        ("quality_gate", "single", QUALITY_GATE_SINGLE_OUTPUT_SCHEMA),
        ("quality_gate", "batch", QUALITY_GATE_BATCH_OUTPUT_SCHEMA),
        ("refinement", "single", REFINEMENT_SINGLE_OUTPUT_SCHEMA),
        ("refinement", "batch", REFINEMENT_BATCH_OUTPUT_SCHEMA),
        ("polish", "single", POLISH_SINGLE_OUTPUT_SCHEMA),
        ("polish", "batch", POLISH_BATCH_OUTPUT_SCHEMA),
        ("arbitration", "single", ARBITRATION_SINGLE_OUTPUT_SCHEMA),
        ("arbitration", "batch", ARBITRATION_BATCH_OUTPUT_SCHEMA),
    ],
)
def test_postprocess_messages_carry_the_stage_schema(stage, shape, expected_schema) -> None:
    messages = build_postprocess_messages(
        stage=stage,
        shape=shape,
        rendered_system="stable system",
        user_content="dynamic user",
    )

    clean_messages, output_schema = extract_structured_output_directive(messages)

    assert output_schema == expected_schema
    assert [message["role"] for message in clean_messages] == ["system", "user"]
    assert all("_transbridge_structured_output" not in message for message in clean_messages)
