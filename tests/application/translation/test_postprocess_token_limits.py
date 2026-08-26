from __future__ import annotations

import hashlib
import threading
from types import SimpleNamespace

from transbridge.ai_translator.translator import AutoTranslator
from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.translation.postprocess import PostProcessCandidate
from transbridge.application.translation.postprocess_stages import (
    CheckerStage,
    LlmPostProcessStage,
    PostProcessLlmPhase,
    PostProcessLlmResponse,
)


def _candidate(local_key: str, *, original: str = "source", text: str = "draft") -> PostProcessCandidate:
    return PostProcessCandidate(
        run_id="run",
        entry_key=EntryKey(SourceNamespace.legacy(), local_key),
        before_revision=EntryRevision(),
        original=original,
        before_text=text,
        text=text,
        stage=2,
        context="context",
    )


def test_llm_checker_skips_oversized_business_content_before_call() -> None:
    class Checker:
        def __init__(self) -> None:
            self.calls = 0

        def check(self, _entry):
            self.calls += 1
            return []

    checker = Checker()
    outcome = CheckerStage(
        "quality_gate",
        checker,
        model="unknown-compatible-model",
        max_tokens_per_batch=1,
    )((_candidate("oversized"),))

    assert checker.calls == 0
    assert [item.code for item in outcome.diagnostics] == ["POSTPROCESS_CONTENT_TOKEN_LIMIT"]


def test_llm_postprocess_stage_keeps_item_limit_as_secondary_boundary() -> None:
    class Port:
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []

        def apply(self, _phase, request):
            self.batch_sizes.append(len(request.candidates))
            values = tuple(
                (candidate.entry_key, f"updated-{candidate.entry_key.local_key}") for candidate in request.candidates
            )
            return PostProcessLlmResponse(values, hashlib.sha256(repr(values).encode()).hexdigest())

    port = Port()
    outcome = LlmPostProcessStage(
        PostProcessLlmPhase.REFINE,
        port,
        model="unknown-compatible-model",
        max_tokens_per_batch=10_000,
        max_items=2,
    )(tuple(_candidate(str(index)) for index in range(5)))

    assert port.batch_sizes == [2, 2, 1]
    assert [candidate.text for candidate in outcome.candidates] == [f"updated-{index}" for index in range(5)]


def test_dialogue_term_extraction_rebatches_original_and_translation_and_skips_oversized() -> None:
    class Extractor:
        def __init__(self) -> None:
            self.calls: list[list[dict[str, str]]] = []

        def extract(self, pairs):
            self.calls.append(pairs)
            return []

    translator = object.__new__(AutoTranslator)
    translator._cfg = SimpleNamespace(
        llm_config=SimpleNamespace(model="unknown-compatible-model", max_tokens_per_batch=6)
    )
    translator._extractor = Extractor()
    entries = [
        SimpleNamespace(id="one", identity=EntryKey(SourceNamespace.legacy(), "one"), original="a"),
        SimpleNamespace(id="two", identity=EntryKey(SourceNamespace.legacy(), "two"), original="b"),
        SimpleNamespace(id="long", identity=EntryKey(SourceNamespace.legacy(), "long"), original="too-long"),
    ]
    logs: list[str] = []

    translator._extract_dialogue_terms(
        entries,
        {"one": "甲", "two": "乙", "long": "很长"},
        SimpleNamespace(new_dynamic_terms=0),
        threading.Lock(),
        logs.append,
    )

    assert translator._extractor.calls == [
        [{"original": "a", "translation": "甲"}],
        [{"original": "b", "translation": "乙"}],
    ]
    assert len(logs) == 1
    assert "对话术语抽取已跳过" in logs[0]
