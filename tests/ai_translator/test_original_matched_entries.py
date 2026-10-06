from __future__ import annotations

from dataclasses import replace
import json
import re
import threading
from types import SimpleNamespace

from jsonschema import Draft202012Validator
import pytest

from transbridge.ai_translator.post_processor.base import PostProcessIssue
from transbridge.ai_translator.post_processor.llm_arbiter import ArbiterDecision
from transbridge.ai_translator.post_processor.polisher import LLMPolisher
from transbridge.ai_translator.post_processor.post_processor import PostProcessor, PostProcessorConfig
from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadPipeline
from transbridge.ai_translator.post_processor.refinement_response import parse_batch_refinement_response
from transbridge.ai_translator.prompt_builder import PromptBuilder
from transbridge.ai_translator.structured_schemas import PROOFREAD_OUTPUT_SCHEMA
from transbridge.ai_translator.translator import AutoTranslator, ProgressCheckpoint, TranslatorConfig
from transbridge.application.translation import InMemoryTranslationCheckpointPort, ProofreadStage
from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
from transbridge.application.translation.entry_alias import ai_entry_id, ai_entry_key
from transbridge.application.translation.postprocess import PostProcessCandidate
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.config_manager import ActionRule, apply_rules


def _entries() -> list[TranslationEntry]:
    return [
        TranslationEntry("same-id", "same-key", original, "", 0, "BOOK:DESC", requires_original_match=True)
        for original in ("Open the gate.", "Close the gate.")
    ]


def _payload(messages) -> dict:
    match = re.search(r"<translation_entries>\s*(.*?)\s*</translation_entries>", messages[-1]["content"], re.S)
    assert match
    return json.loads(match.group(1))


def test_prompt_keeps_each_original_and_its_terms_without_changing_public_keys() -> None:
    entries = _entries()
    terms = {ai_entry_key(entry): {entry.original: str(index)} for index, entry in enumerate(entries)}
    payload = _payload(PromptBuilder().build_translation_prompt(entries, {}, "dialogue", terms_by_entry=terms))

    assert len(payload) == 2
    for entry in entries:
        alias = entry.identity.serialize()
        assert payload[alias] == {"source": entry.original, "terms": terms[alias]}
        assert (entry.id, entry.key) == ("same-id", "same-key")
        assert ai_entry_id(entry) == ai_entry_key(entry) == alias
    ordinary = TranslationEntry("old-id", "old-key", "Text", "", 0, "")
    assert (ai_entry_id(ordinary), ai_entry_key(ordinary)) == ("old-id", "old-key")


@pytest.mark.parametrize("selected", [None, 0, 1])
def test_auto_translator_commits_each_duplicate_independently(monkeypatch, selected) -> None:
    from transbridge.ai_translator import noun_extractor, term_database

    class Terms:
        def load_all(self):
            pass

        def get_load_log(self):
            return ()

        def exact_match(self, _originals):
            return {}

        def match_terms_scoped(self, *, entries, **_kwargs):
            return SimpleNamespace(flat_terms={}, terms_by_entry={ai_entry_key(entry): {} for entry in entries})

    class Llm:
        def chat_stream(self, messages, _max_tokens, callback):
            response = json.dumps({
                "results": [
                    {"entry_id": alias, "translation": "译：" + value["source"]}
                    for alias, value in _payload(messages).items()
                ]
            })
            callback(response)
            return response

        def cancel(self):
            pass

    monkeypatch.setattr(term_database, "TermDatabaseManager", lambda **_kwargs: Terms())
    monkeypatch.setattr(noun_extractor, "NounExtractor", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ProgressCheckpoint, "save", lambda *_args: None)
    monkeypatch.setattr(ProgressCheckpoint, "delete", lambda *_args: None)
    config = LLMConfig()
    config.enable_post_process = False
    config.retrieval_enabled = False
    entries = _entries()
    collection = TranslationEntryCollection(entries)
    translator = AutoTranslator(
        TranslatorConfig(config, "fixture.esp"),
        llm_client=Llm(),
        candidate_checkpoint=InMemoryTranslationCheckpointPort(),
    )
    targets = None if selected is None else [entries[selected].identity.serialize()]

    result = translator.translate(collection, targets, lambda *_args: None, threading.Event())

    expected = entries if selected is None else [entries[selected]]
    assert result.success_count == len(expected), result.errors
    assert result.failed_count == 0
    assert set(result.entry_outcomes) == {entry.identity for entry in expected}
    assert len(collection) == 2
    for index, entry in enumerate(entries):
        actual = collection.get(entry.identity)
        assert actual.translation == ("译：" + entry.original if selected is None or selected == index else "")
        assert (actual.id, actual.key) == ("same-id", "same-key")


def _candidate(entry: TranslationEntry) -> PostProcessCandidate:
    return PostProcessCandidate(
        run_id="qualified-test",
        entry_key=entry.identity,
        before_revision=entry.revision,
        original=entry.original,
        before_text="旧译文",
        text="旧译文",
        stage=1,
        context=entry.context or "",
    )


class _ProofreadClient:
    def __init__(self, *, omit_original=False):
        self.omit_original = omit_original
        self.inputs = []

    def chat_prepared(self, prepare, _max_tokens=0):
        messages = prepare()
        payload = json.loads(messages[-1]["content"])
        self.inputs.extend(payload["entries"])
        assert "including original when present" in messages[0]["content"]
        results = []
        for entry in payload["entries"]:
            key = dict(entry["entry_key"])
            if self.omit_original:
                key.pop("original", None)
            results.append({"entry_key": key, "final_translation": "校对：" + entry["original"]})
        response = {"results": results}
        Draft202012Validator(PROOFREAD_OUTPUT_SCHEMA.schema).validate(response)
        return json.dumps(response)


def test_native_proofread_schema_and_stage_preserve_complete_identity() -> None:
    entries = _entries()
    client = _ProofreadClient()
    outcome = ProofreadStage(client, max_tokens_per_batch=10_000)(tuple(_candidate(entry) for entry in entries))

    assert outcome.diagnostics == ()
    assert len(outcome.candidates) == 2
    for entry, candidate in zip(entries, outcome.candidates, strict=True):
        assert candidate.entry_key == entry.identity
        assert candidate.accepted
        assert candidate.text == "校对：" + entry.original
    assert {entry["entry_key"]["original"] for entry in client.inputs} == {entry.original for entry in entries}


def test_native_proofread_rejects_bare_key_for_qualified_entries() -> None:
    entries = _entries()
    outcome = ProofreadStage(_ProofreadClient(omit_original=True), max_tokens_per_batch=10_000)(
        tuple(_candidate(entry) for entry in entries)
    )

    assert all(not candidate.accepted for candidate in outcome.candidates)
    assert all(candidate.text == "旧译文" for candidate in outcome.candidates)


def test_proofread_pipeline_projects_distinct_results_with_canonical_identity() -> None:
    entries = [replace(entry, translation="旧译文", stage=1) for entry in _entries()]
    config = LLMConfig()
    config.pp_strategy = "proofread"
    pipeline = ProofreadPipeline(
        PostProcessor(PostProcessorConfig()),
        AiExecutionProfile.from_config("polish", config),
        proofread_stage=ProofreadStage(_ProofreadClient(), max_tokens_per_batch=10_000),
    )

    results = pipeline.process(entries)

    assert len(results) == 2
    for entry in entries:
        result = results[entry.identity.serialize()]
        assert result.identity == entry.identity
        assert result.accepted
        assert result.polished_translation == "校对：" + entry.original
        assert entry.translation == "旧译文"


def test_legacy_polisher_and_mixed_rules_do_not_merge_shared_ids() -> None:
    entries = _entries()
    entries[1] = replace(entries[1], translation="旧译文", stage=1)
    response = json.dumps({
        "results": [
            {"entry_id": ai_entry_id(entry), "polished_translation": "新：" + entry.original, "confidence": 0.9}
            for entry in entries
        ]
    })
    results = object.__new__(LLMPolisher)._parse_batch_polish_response(entries, response)
    actions = apply_rules(
        [ActionRule(status_filter={0}, action="translate"), ActionRule(status_filter={1}, action="polish")], entries
    )

    assert len(results) == len(actions) == 2
    assert actions == {ai_entry_id(entries[0]): "translate", ai_entry_id(entries[1]): "polish"}
    for entry in entries:
        assert results[ai_entry_id(entry)].polished_translation == "新：" + entry.original


def test_strict_pipeline_checks_refines_polishes_and_accepts_each_original() -> None:
    entries = [replace(entry, translation="旧译文", stage=1) for entry in _entries()]

    class Checker:
        def check(self, entry):
            return [
                PostProcessIssue(ai_entry_key(entry), "meaning", "error", "修复", entry.original, entry.translation)
            ]

    class Refiner:
        def refine_batch(self, batch, issues_map):
            assert set(issues_map) == {ai_entry_id(entry) for entry in batch}
            return parse_batch_refinement_response(
                batch,
                json.dumps({
                    "results": [
                        {
                            "entry_id": ai_entry_id(entry),
                            "refined_translation": "修：" + entry.original,
                            "confidence": 0.9,
                        }
                        for entry in batch
                    ]
                }),
            )

    class Polisher:
        def polish_batch(self, batch):
            assert all(entry.translation == "修：" + entry.original for entry in batch)
            return object.__new__(LLMPolisher)._parse_batch_polish_response(
                batch,
                json.dumps({
                    "results": [
                        {
                            "entry_id": ai_entry_id(entry),
                            "polished_translation": "润：" + entry.original,
                            "confidence": 0.9,
                        }
                        for entry in batch
                    ]
                }),
            )

    class Arbiter:
        def arbitrate_batch(self, contexts):
            return {
                ai_entry_key(context.entry): ArbiterDecision(ai_entry_key(context.entry), "pass", "已修复", 0.9, "接受")
                for context in contexts
            }

    processor = PostProcessor(
        PostProcessorConfig(
            enable_consistency_check=False,
            enable_format_validation=False,
            enable_quality_gate=False,
            enable_refinement=True,
            enable_polish=True,
            enable_llm_arbitration=True,
        )
    )
    processor.register_checker(Checker())
    processor._refiner = Refiner()
    processor._polisher = Polisher()
    processor._arbiter = Arbiter()
    config = LLMConfig()
    config.pp_strategy = "strict"

    results = ProofreadPipeline(processor, AiExecutionProfile.from_config("polish", config)).process(entries)

    assert len(results) == 2
    for entry in entries:
        result = results[ai_entry_id(entry)]
        assert result.accepted
        assert result.identity == entry.identity
        assert result.polished_translation == "润：" + entry.original


def test_polish_apply_and_report_keep_individual_preview_decisions() -> None:
    from transbridge.ui.tools.ai_translator.result_presenter import ResultPresenter

    entries = [replace(entry, translation="旧译文", stage=1) for entry in _entries()]
    collection = TranslationEntryCollection(entries)
    results = {
        ai_entry_id(entry): SimpleNamespace(
            original_translation="旧译文", polished_translation="新译文", confidence=0.9
        )
        for entry in entries
    }
    presenter = ResultPresenter()
    summary = presenter.apply_decisions(
        collection, entries, {ai_entry_id(entries[0]): "新译文", ai_entry_id(entries[1]): None}, results=results
    )
    report = presenter.build_polish_report(results, entries, summary, polish_level="standard", esp_path=None)

    assert (summary.accepted, summary.rejected) == (1, 1)
    assert collection.get(entries[0].identity).translation == "新译文"
    assert collection.get(entries[1].identity).translation == "旧译文"
    assert {candidate.entry_key: candidate.accepted for candidate in report.snapshot.candidates} == {
        entries[0].identity: True,
        entries[1].identity: False,
    }


def test_legacy_task_session_and_run_identity_accept_distinct_originals() -> None:
    from transbridge.ui.tools.ai_translator.run_spec import _entry_key
    from transbridge.ui.tools.ai_translator.task_session import TaskSession

    entries = _entries()
    session = object.__new__(TaskSession)
    session._ctx = SimpleNamespace(
        slots={"source": SimpleNamespace(collection=TranslationEntryCollection(entries))},
        uses_authoritative_projection=False,
    )

    states = session._read_states()

    assert len(states["source"].entries) == 2
    assert tuple(_entry_key(entry) for entry in entries) == tuple(entry.identity.serialize() for entry in entries)
