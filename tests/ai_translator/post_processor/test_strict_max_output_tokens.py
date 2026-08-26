from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from transbridge.ai_translator.post_processor.base import PostProcessIssue
from transbridge.ai_translator.post_processor.llm_arbiter import ArbitrationContext, LLMArbiter
from transbridge.ai_translator.post_processor.post_processor import PostProcessor, PostProcessorConfig
from transbridge.ai_translator.post_processor.quality_gate import QualityGateChecker
from transbridge.config import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _REPO_ROOT / "data" / "prompts"


class _RecordingLLM:
    def __init__(self) -> None:
        self.max_tokens: list[int] = []

    def chat(self, messages, max_tokens=0):
        self.max_tokens.append(max_tokens)
        return "{}"


def _entry() -> TranslationEntry:
    return TranslationEntry(
        id="entry",
        key="entry",
        original="Dragon",
        translation="龙",
        stage=1,
        context="NPC_:FULL",
    )


def _issue() -> PostProcessIssue:
    return PostProcessIssue(
        entry_id="entry",
        issue_type="term_mismatch",
        severity="error",
        message="term mismatch",
        original="Dragon",
        translation="龙",
    )


def _processor(limit: int, llm: _RecordingLLM) -> PostProcessor:
    processor = PostProcessor(
        PostProcessorConfig(
            enable_polish=True,
            max_output_tokens=limit,
        )
    )
    with ExitStack() as stack:
        for module in ("quality_gate", "llm_refiner", "polisher", "llm_arbiter"):
            stack.enter_context(
                patch(
                    f"transbridge.ai_translator.post_processor.{module}._get_prompts_dir",
                    return_value=_PROMPTS_DIR,
                )
            )
        processor.register_default_checkers(llm_client=llm)
    return processor


@pytest.mark.parametrize("limit", [0, 1379])
def test_strict_four_stage_single_and_batch_calls_share_configured_output_limit(limit: int) -> None:
    llm = _RecordingLLM()
    processor = _processor(limit, llm)
    entry = _entry()
    issue = _issue()

    quality = next(checker for checker in processor._checkers if isinstance(checker, QualityGateChecker))
    quality._check_single(entry)
    quality._check_batch_internal([entry])

    processor._refiner.refine(entry, [issue])
    processor._refiner.refine_batch([entry], {entry.id: [issue]})
    processor._polisher.polish(entry)
    processor._polisher.polish_batch([entry])

    arbiter: LLMArbiter = processor._arbiter
    arbiter._quick_decide = lambda _context: None
    context = ArbitrationContext(entry, original_issues=[issue])
    arbiter.arbitrate(context)
    arbiter.arbitrate_batch([context])

    assert len(llm.max_tokens) >= 8
    assert set(llm.max_tokens) == {limit}


@pytest.mark.parametrize("limit", [0, 2468])
def test_postprocessor_config_preserves_llm_output_limit(limit: int) -> None:
    llm_config = LLMConfig(max_output_tokens=limit)

    config = PostProcessorConfig.from_llm_config(llm_config)

    assert config.max_output_tokens == limit
