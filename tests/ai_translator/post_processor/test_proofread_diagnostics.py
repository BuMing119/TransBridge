from types import SimpleNamespace

from transbridge.ai_translator.post_processor.proofread_diagnostics import diagnostic_note
from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadPipeline
from transbridge.application.contracts import Diagnostic, ErrorCategory
from transbridge.application.translation.postprocess import PostProcessStageOutcome
from transbridge.converter.translation_entry import TranslationEntry


def test_schema_note_explains_missing_translation_before_surplus_fields():
    diagnostic = Diagnostic(
        "PROOFREAD_RESPONSE_SCHEMA_INVALID",
        "Invalid schema",
        details=(
            (
                "validation_details",
                {
                    "errors": [
                        {"validator": "required", "missing_fields": ["final_translation"]},
                        {"validator": "additionalProperties", "unexpected_fields": ["original", "current_translation"]},
                    ]
                },
            ),
        ),
    )
    assert diagnostic_note([diagnostic]) == (
        "[PROOFREAD_RESPONSE_SCHEMA_INVALID] 未返回译文；多返回字段：original、current_translation"
    )


def test_cancelled_run_diagnostics_are_not_copied_to_every_entry():
    entries = [TranslationEntry(str(i), str(i), "Hello", "你好", 1, "") for i in range(100)]
    diagnostics = tuple(
        Diagnostic("CANCELLED", f"batch {i} cancelled", category=ErrorCategory.CANCELLED) for i in range(50)
    )
    batch_diagnostic = Diagnostic(
        "BATCH_CANCELLED",
        "Only the first entry was running",
        category=ErrorCategory.CANCELLED,
        details=(("entry_keys", (entries[0].identity.to_dict(),)),),
    )

    def run(candidates, **kwargs):
        return PostProcessStageOutcome("proofread", candidates, (*diagnostics, batch_diagnostic))

    pipeline = ProofreadPipeline(None, SimpleNamespace(enable_proofread=True), proofread_stage=SimpleNamespace(run=run))
    results = pipeline.process(entries)
    assert len(pipeline.diagnostics) == 51
    assert "Only the first entry" in results["0"].note
    assert results["0"].processing_status == "cancelled"
    assert results["1"].processing_status == "not_started"
    assert all("batch " not in result.note for result in results.values())
    assert sum(len(result.note) for result in results.values()) < 3000


def test_cancelled_run_preserves_completed_candidate_without_accepting_it():
    entry = TranslationEntry("id", "key", "Hello", "你好", 1, "")

    def run(candidates, **kwargs):
        return PostProcessStageOutcome(
            "proofread",
            (candidates[0].with_text("您好", "proofread"),),
            (Diagnostic("CANCELLED", "Run cancelled", category=ErrorCategory.CANCELLED),),
        )

    pipeline = ProofreadPipeline(None, SimpleNamespace(enable_proofread=True), proofread_stage=SimpleNamespace(run=run))
    result = pipeline.process([entry])[entry.id]
    assert result.candidate_translation == "您好"
    assert result.polished_translation == "你好"
    assert result.processing_status == "completed"
    assert not result.accepted
