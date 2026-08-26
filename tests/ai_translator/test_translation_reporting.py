from __future__ import annotations

from transbridge.ai_translator.translator import TranslationResult
from transbridge.application.contracts import OperationOutcome
from transbridge.application.translation import build_translation_report_snapshot
from transbridge.converter.translation_entry import TranslationEntry


def test_translation_completion_builds_report_snapshot_without_postprocess() -> None:
    entry = TranslationEntry(
        id="entry-1",
        key="ENTRY:1",
        original="Source",
        translation="译文",
        stage=2,
        context="INFO:FULL",
    )
    result = TranslationResult(success_count=1, new_dynamic_terms=2)

    snapshot = build_translation_report_snapshot(
        result,
        [entry],
        run_id="ai-run",
        cancelled=False,
        before_text_by_key={entry.identity: ""},
    )

    assert snapshot.run_id == "ai-run"
    assert snapshot.outcome is OperationOutcome.COMPLETED
    assert snapshot.input_count == 1
    assert snapshot.candidates[0].original == "Source"
    assert snapshot.candidates[0].before_text == ""
    assert snapshot.candidates[0].text == "译文"
    assert snapshot.run_spec_summary["translation_counts"] == {
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
        "new_dynamic_terms": 2,
    }
    assert snapshot.run_spec_summary["post_process_enabled"] is False


def test_translation_failures_are_visible_without_copying_failure_messages() -> None:
    result = TranslationResult(success_count=2, failed_count=1, failed_entries=["secret upstream detail"])

    snapshot = build_translation_report_snapshot(result, [], run_id="partial-run", cancelled=False)

    assert snapshot.outcome is OperationOutcome.PARTIAL
    assert snapshot.failure_count == 1
    assert [diagnostic.code for diagnostic in snapshot.diagnostics] == ["AI_TRANSLATION_ITEMS_FAILED"]
    assert "secret upstream detail" not in str(snapshot.to_dict())
