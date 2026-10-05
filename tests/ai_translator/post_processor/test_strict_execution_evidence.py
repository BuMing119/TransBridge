from __future__ import annotations

from types import SimpleNamespace

import pytest

from transbridge.ai_translator.post_processor.base import PostProcessIssue
from transbridge.ai_translator.post_processor.llm_arbiter import ArbiterDecision
from transbridge.ai_translator.post_processor.llm_refiner import RefineResult
from transbridge.ai_translator.post_processor.polisher import PolishResult
from transbridge.ai_translator.post_processor.post_processor import PostProcessor, PostProcessorConfig
from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadPipeline
from transbridge.ai_translator.post_processor.quality_gate import QualityGateChecker
from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry


def _pipeline(stage, operation):
    config = LLMConfig(
        pp_strategy="strict",
        pp_enable_consistency_check=False,
        pp_enable_format_validation=False,
        pp_enable_quality_gate=stage == "quality_gate",
        pp_enable_refinement=stage == "refine",
        pp_enable_polish=stage == "polish",
        pp_enable_arbitration=stage == "arbitrate",
    )
    processor = PostProcessor(PostProcessorConfig.from_llm_config(config))
    if stage == "quality_gate":
        checker = object.__new__(QualityGateChecker)
        checker.check_batch = operation
        processor.register_checker(checker)
    elif stage == "refine":

        class Checker:
            def check(self, entry):
                return [
                    PostProcessIssue(entry.id, "test", "warning", "Needs refinement", entry.original, entry.translation)
                ]

        processor.register_checker(Checker())
        processor._refiner = SimpleNamespace(refine_batch=lambda entries, issues: operation(entries))
    elif stage == "polish":
        processor._polisher = SimpleNamespace(polish_batch=operation)
    else:
        processor._arbiter = SimpleNamespace(arbitrate_batch=operation)
    return ProofreadPipeline(processor, AiExecutionProfile.from_config("polish", config))


def _entry(key="entry"):
    return TranslationEntry(key, key, "Source", "Existing translation", 1, "BOOK:DESC")


@pytest.mark.parametrize("stage", ["quality_gate", "refine", "polish", "arbitrate"])
def test_caught_llm_stage_exception_remains_failed_and_retryable(stage):
    def fail(entries):
        raise RuntimeError("provider unavailable")

    pipeline = _pipeline(stage, fail)
    entry = _entry()
    result = pipeline.process([entry])[entry.id]
    assert not result.accepted
    assert result.processing_status == "failed"
    assert "provider unavailable" in result.note
    assert entry.translation == "Existing translation"


@pytest.mark.parametrize("stage", ["refine", "polish", "arbitrate"])
def test_missing_stage_response_never_passes_using_the_original_translation(stage):
    pipeline = _pipeline(stage, lambda entries: {})
    entry = _entry()
    result = pipeline.process([entry])[entry.id]
    assert not result.accepted
    assert result.processing_status == "failed"
    assert f"{stage} 未完成" in result.note


def test_partial_polish_response_records_only_real_successes():
    def polish(entries):
        first = entries[0]
        return {first.id: PolishResult(first.id, first.translation, first.translation, confidence=1.0)}

    first, missing = _entry("first"), _entry("missing")
    results = _pipeline("polish", polish).process([first, missing])
    assert results[first.id].accepted
    assert results[first.id].processing_status == "completed"
    assert not results[missing.id].accepted
    assert results[missing.id].processing_status == "failed"


def test_successful_arbitration_cannot_hide_an_upstream_missing_result():
    pipeline = _pipeline("refine", lambda entries: {})
    pipeline._processor._config.enable_llm_arbitration = True
    pipeline._processor._arbiter = SimpleNamespace(
        arbitrate_batch=lambda contexts: {
            context.entry.id: ArbiterDecision(context.entry.id, "pass", "looks fine", 1.0, "accept")
            for context in contexts
        }
    )
    entry = _entry()
    result = pipeline.process([entry])[entry.id]
    assert not result.accepted
    assert result.processing_status == "failed"
    assert "refine 未完成" in result.note


def test_successful_required_refinement_is_recorded_as_completed():
    pipeline = _pipeline(
        "refine",
        lambda entries: {
            entry.id: RefineResult(entry.id, entry.translation, "Refined translation", confidence=1.0)
            for entry in entries
        },
    )
    entry = _entry()
    result = pipeline.process([entry])[entry.id]
    assert result.accepted
    assert result.processing_status == "completed"


@pytest.mark.parametrize(
    "response", [None, '{"results": []}', '{"results": [{"entry_id":"entry","verdict":"unknown"}]}']
)
def test_quality_gate_internal_failure_cannot_be_overruled_as_valid(response):
    class Client:
        def chat(self, **kwargs):
            if response is None:
                raise RuntimeError("provider unavailable")
            return response

    checker = QualityGateChecker(llm_client=Client())
    pipeline = _pipeline("quality_gate", checker.check_batch)
    pipeline._processor._config.enable_llm_arbitration = True
    pipeline._processor._arbiter = SimpleNamespace(
        arbitrate_batch=lambda contexts: {
            context.entry.id: ArbiterDecision(context.entry.id, "pass", "looks fine", 1.0, "accept")
            for context in contexts
        }
    )
    entry = _entry()
    result = pipeline.process([entry])[entry.id]
    assert not result.accepted
    assert result.processing_status == "failed"
    assert any(issue.execution_failed for issue in result.issues)


def test_public_checker_stage_retains_quality_gate_execution_failure():
    from transbridge.application.contracts import ErrorCategory
    from transbridge.application.translation import CheckerStage, PostProcessCandidate

    checker = QualityGateChecker(llm_client=SimpleNamespace(chat=lambda **kwargs: '{"results": []}'))
    entry = _entry()
    candidate = PostProcessCandidate(
        "run", entry.identity, entry.revision, entry.original, entry.translation, entry.translation, 1
    )
    outcome = CheckerStage("quality_gate", checker)((candidate,))
    assert outcome.failed
    assert outcome.diagnostics[0].category is ErrorCategory.EXTERNAL
