"""Format checks must not mistake ordinary commas and spaces for quote pairs."""

import pytest

from transbridge.ai_translator.post_processor.base import PostProcessIssue
from transbridge.ai_translator.post_processor.format_validator import FormatValidator
from transbridge.ai_translator.post_processor.post_processor import PostProcessor, PostProcessorConfig
from transbridge.converter.translation_entry import TranslationEntry


def _entry(text: str) -> TranslationEntry:
    return TranslationEntry("entry", "entry", "Source text", text, 1, "INFO:NAM1")


@pytest.mark.parametrize(
    "text",
    [
        "Hello world.",
        "Hello, world.",
        "One,two,three",
        "他说：“Hello world.”",
        "他说：‘Hello world.’",
        "“未闭合",
        "‘未闭合",
    ],
)
def test_normal_spaces_commas_and_curly_quotes_pass(text):
    assert FormatValidator().check(_entry(text)) == []


@pytest.mark.parametrize("text", ['"unfinished', "'unfinished"])
def test_an_unclosed_ascii_quote_still_has_a_format_diagnostic(text):
    issues = FormatValidator().check(_entry(text))

    assert any(issue.issue_type == PostProcessIssue.QUOTE_MISMATCH for issue in issues)


@pytest.mark.parametrize("text", ["An ordinary translated sentence.", "“未闭合", "‘未闭合"])
def test_plain_text_does_not_trigger_unnecessary_refinement(text):
    calls = []

    class Refiner:
        def refine_batch(self, entries, issues_map):
            calls.extend(entries)
            return {}

    processor = PostProcessor(
        PostProcessorConfig(
            enable_consistency_check=False,
            enable_format_validation=True,
            enable_quality_gate=False,
            enable_refinement=True,
            enable_polish=False,
            enable_llm_arbitration=False,
        )
    )
    processor.register_default_checkers()
    processor._refiner = Refiner()
    entry = _entry(text)

    result = processor.process_entries([entry], max_workers=1, apply_changes=False)

    assert result.issues == []
    assert calls == []
    assert entry.translation == text
