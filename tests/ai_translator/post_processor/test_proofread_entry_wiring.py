from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.post_processor.post_processor import PostProcessor, PostProcessorConfig
from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadPipeline
from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory
from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
from transbridge.application.translation.postprocess import PostProcessStageOutcome
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry


def _entry() -> TranslationEntry:
    return TranslationEntry(
        id="entry-id",
        key="entry-key",
        original="Use the plugin sword.",
        translation="使用剑。",
        stage=1,
        context="DIAL:FULL",
    )


def test_pipeline_factory_forwards_refinement_batch_size_and_uses_entry_scoped_terms(monkeypatch) -> None:
    from transbridge.ai_translator.post_processor import proofread_pipeline as module

    captured: dict[str, object] = {}

    class Stage:
        def __init__(self, _client, **kwargs):
            captured.update(kwargs)

    class TermManager:
        def match_terms_for_entry(self, candidate):
            captured["resolved_candidate"] = candidate
            return {"sword": "插件剑"}

        def match_terms(self, _texts):
            raise AssertionError("entry-scoped terminology must take precedence")

    monkeypatch.setattr(module, "ProofreadStage", Stage)
    config = LLMConfig(pp_strategy="proofread", pp_refinement_batch_size=3)
    profile = AiExecutionProfile.from_config("polish", config)

    ProofreadPipeline.create(profile=profile, llm_client=object(), term_manager=TermManager())
    candidate = SimpleNamespace(original="sword")

    assert captured["refinement_batch_size"] == 3
    assert captured["term_resolver"](candidate) == {"sword": "插件剑"}
    assert captured["resolved_candidate"] is candidate


def test_pipeline_does_not_project_rejected_proofread_candidate_as_success() -> None:
    class RejectedStage:
        @staticmethod
        def run(candidates, **_kwargs):
            rejected = candidates[0].with_text("已修改但不可接受", "proofread").with_accepted(False)
            return PostProcessStageOutcome("proofread", (rejected,))

        @staticmethod
        def cancel() -> None:
            return None

    profile = AiExecutionProfile.from_config("polish", LLMConfig(pp_strategy="proofread"))
    pipeline = ProofreadPipeline(PostProcessor(PostProcessorConfig()), profile, proofread_stage=RejectedStage())
    entry = _entry()

    result = pipeline.process([entry])[entry.id]

    assert result.accepted is False
    assert result.verdict == "failed"
    assert result.polished_translation == "使用剑。"


@pytest.mark.parametrize("cancel", [False, True])
def test_pipeline_keeps_diagnostics_in_details_and_reports_actual_outcome(cancel) -> None:
    stop = threading.Event()

    class Stage:
        @staticmethod
        def run(candidates, **_kwargs):
            if cancel:
                stop.set()
            key = candidates[0].entry_key.to_dict()
            diagnostics = (
                Diagnostic("PROOFREAD_RECOVERY_SUCCEEDED", "recovered", severity=DiagnosticSeverity.INFO),
                *(
                    Diagnostic(
                        "PROOFREAD_TERMINOLOGY_REFINEMENT_FAILED",
                        "retained original",
                        details=(("entry_key", key), ("reason", "terminology_still_inconsistent")),
                    )
                    for _ in range(100)
                ),
            )
            return PostProcessStageOutcome("proofread", (candidates[0].with_accepted(False),), diagnostics)

        @staticmethod
        def cancel():
            pass

    profile = AiExecutionProfile.from_config("polish", LLMConfig(pp_strategy="proofread"))
    pipeline = ProofreadPipeline(PostProcessor(PostProcessorConfig()), profile, proofread_stage=Stage())
    entry = _entry()
    progress, logs = [], []
    result = pipeline.process(
        [entry], stop_event=stop, progress_callback=lambda *args: progress.append(args), log_callback=logs.append
    )[entry.id]

    assert len(logs) == 2
    assert logs[0] == "开始校对，共 1 条"
    assert not any("PROOFREAD_" in message or "条记录" in message for message in logs)
    assert len(pipeline.diagnostics) == 101
    assert "terminology_still_inconsistent" in result.note
    assert "recovered" not in result.note
    assert not result.accepted and result.polished_translation == entry.translation
    assert "校对完成" not in progress[-1][3]
    assert ("已取消" if cancel else "未完成 1 条") in progress[-1][3]


def test_cancelled_stage_diagnostic_rejects_even_late_success_without_stop_event() -> None:
    class Stage:
        @staticmethod
        def run(candidates, **_kwargs):
            return PostProcessStageOutcome(
                "proofread",
                (candidates[0].with_text("迟到译文", "proofread"),),
                (Diagnostic("PROOFREAD_REFINEMENT_CANCELLED", "cancelled", category=ErrorCategory.CANCELLED),),
            )

    profile = AiExecutionProfile.from_config("polish", LLMConfig(pp_strategy="proofread"))
    pipeline = ProofreadPipeline(PostProcessor(PostProcessorConfig()), profile, proofread_stage=Stage())
    entry = _entry()
    result = pipeline.process([entry])[entry.id]
    assert not result.accepted and result.polished_translation == entry.translation
    assert "校对已取消" in result.note


def test_smart_assistant_proofread_stage_uses_scoped_terms_and_configured_refinement_size(monkeypatch) -> None:
    import transbridge.application.translation as translation
    from transbridge.smart_assistant.tools._postprocess_tool_runtime import _build_stages

    captured: dict[str, object] = {}

    class Stage:
        def __init__(self, _client, **kwargs):
            captured.update(kwargs)

    class TermManager:
        def match_terms_for_entry(self, candidate):
            captured["resolved_candidate"] = candidate
            return {"sword": "插件剑", "gate": "大门"}

        def match_terms(self, _texts):
            raise AssertionError("Smart Assistant must not perform context-free term matching")

    monkeypatch.setattr(translation, "ProofreadStage", Stage)
    request = SimpleNamespace(
        strategy="proofread",
        effective_config=LLMConfig(pp_strategy="proofread", pp_refinement_batch_size=4),
        limits={
            "max_terms_per_batch": 1,
            "max_tokens_per_batch": 1_000,
            "max_output_tokens": 200,
            "max_concurrent": 2,
        },
    )

    stages = _build_stages(request, object(), TermManager(), object, object)
    candidate = SimpleNamespace(original="sword")

    assert len(stages) == 1
    assert captured["refinement_batch_size"] == 4
    assert captured["max_workers"] == 2
    assert captured["term_resolver"](candidate) == {"sword": "插件剑"}
    assert captured["resolved_candidate"] is candidate
