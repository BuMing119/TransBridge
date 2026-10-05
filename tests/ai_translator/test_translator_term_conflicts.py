from __future__ import annotations

from dataclasses import dataclass
import json
import threading
import time
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.batch_planner import Batch
from transbridge.ai_translator.existing_term_extractor import (
    ExistingTermSeedResult,
    TermConflictEvidence,
)
from transbridge.ai_translator.prompt_builder import _extract_partial_translation_results
from transbridge.ai_translator.term_formats import TermEntry
from transbridge.ai_translator.translator import (
    AutoTranslator,
    ProgressCheckpoint,
    TranslationResult,
    TranslatorConfig,
)
from transbridge.application.translation import InMemoryTranslationCheckpointPort
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.infra.limited_llm_client import LimitedLLMClient
from transbridge.infra.llm_structured_outputs import (
    LlmStructuredOutputInvalidResponseError,
    LlmStructuredOutputTruncatedError,
)


def _entry(key: str, *, translation: str = "", stage: int = 0) -> TranslationEntry:
    return TranslationEntry(key, key, f"source:{key}", translation, stage, "BOOK:DESC")


def _config(*, overwrite: bool = False, max_concurrent: int = 2) -> TranslatorConfig:
    llm = SimpleNamespace(
        game_profile="fixture",
        target_lang="zh-CN",
        max_tokens_per_batch=500,
        max_concurrent=max_concurrent,
        max_output_tokens=100,
        config_revision=1,
        provider="fixture",
        base_url="http://fixture.invalid/v1",
        model="fixture-model",
        enable_post_process=False,
        retrieval_enabled=True,
    )
    return TranslatorConfig(llm, "fixture.esp", overwrite=overwrite)


class _PromptBuilder:
    def __init__(self, *_args, **_kwargs) -> None:
        self.calls: list[dict] = []

    def build_translation_prompt(self, entries, terms, batch_type, *, terms_by_entry=None):
        payload = {
            "keys": [entry.key for entry in entries],
            "terms": dict(terms),
            "terms_by_entry": {key: dict(value) for key, value in (terms_by_entry or {}).items()},
            "batch_type": batch_type,
        }
        self.calls.append(payload)
        return [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]

    def extract_partial_pairs(self, _value):
        return {}

    def parse_translation_response(self, response, expected_keys):
        parsed = json.loads(response)
        return {key: value for key, value in parsed.items() if key in expected_keys}


class _TermManager:
    def __init__(self) -> None:
        self.match_snapshots: list[tuple[tuple[str, ...], dict[str, str]]] = []
        self.canonical_translation = "最新权威译法"

    def load_all(self) -> None:
        pass

    def get_load_log(self):
        return ()

    def exact_match(self, _originals):
        return {}

    def match_terms_scoped(self, *, entries, in_flight_terms, **_kwargs):
        snapshot = dict(in_flight_terms)
        self.match_snapshots.append((tuple(entry.key for entry in entries), snapshot))
        return SimpleNamespace(
            flat_terms=snapshot,
            terms_by_entry={entry.key: dict(snapshot) for entry in entries},
        )

    def resolve_term(self, term: str):
        return TermEntry(term, self.canonical_translation, "fixture")


class _Llm:
    def __init__(self, *, repair_translation: str = "采用最新权威译法") -> None:
        self.repair_translation = repair_translation
        self.keys_by_call: list[tuple[str, ...]] = []

    def chat_stream(self, messages, _max_tokens, _callback):
        payload = json.loads(messages[-1]["content"])
        keys = tuple(payload["keys"])
        self.keys_by_call.append(keys)
        response = {key: self.repair_translation if key == "effective" else f"translated:{key}" for key in keys}
        return json.dumps(response, ensure_ascii=False)

    def cancel(self) -> None:
        pass


@dataclass
class _ConflictHarness:
    translator: AutoTranslator
    collection: TranslationEntryCollection
    llm: _Llm
    builder: _PromptBuilder
    term_manager: _TermManager
    repair_options: list[dict]


def _harness(
    monkeypatch,
    *,
    conflicts: tuple[TermConflictEvidence, ...],
    overwrite: bool = False,
    repair_translation: str = "采用最新权威译法",
) -> _ConflictHarness:
    from transbridge.ai_translator import existing_term_extractor, noun_extractor, prompt_builder, term_database

    entries = [
        _entry("effective", translation="旧冲突译文", stage=1),
        _entry("internal", translation="内部冲突原译文", stage=1),
        _entry("normal"),
    ]
    collection = TranslationEntryCollection(entries)
    manager = _TermManager()
    builder = _PromptBuilder()
    llm = _Llm(repair_translation=repair_translation)

    class _Seeder:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def seed(self, *_args, **_kwargs):
            return ExistingTermSeedResult(conflicts=len(conflicts), conflict_records=conflicts)

    class _Extractor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    monkeypatch.setattr(term_database, "TermDatabaseManager", lambda **_kwargs: manager)
    monkeypatch.setattr(prompt_builder, "PromptBuilder", lambda **_kwargs: builder)
    monkeypatch.setattr(noun_extractor, "NounExtractor", _Extractor)
    monkeypatch.setattr(existing_term_extractor, "ExistingTermSeeder", _Seeder)
    monkeypatch.setattr(existing_term_extractor, "should_seed_existing_terms", lambda *_args: True)
    monkeypatch.setattr(ProgressCheckpoint, "save", lambda self, esp_path: None)
    monkeypatch.setattr(ProgressCheckpoint, "delete", lambda self, esp_path: None)

    translator = AutoTranslator(
        _config(overwrite=overwrite),
        llm_client=llm,
        candidate_checkpoint=InMemoryTranslationCheckpointPort(),
        run_id_factory=lambda: "term-conflict-run",
    )
    repair_options: list[dict] = []
    original_run_batch = translator._run_batch

    def record_repair_options(*args, **kwargs):
        if kwargs.get("required_terms_by_entry"):
            repair_options.append({
                "required_terms_by_entry": kwargs["required_terms_by_entry"],
                "update_terms": kwargs.get("update_terms"),
            })
        return original_run_batch(*args, **kwargs)

    monkeypatch.setattr(translator, "_run_batch", record_repair_options)
    return _ConflictHarness(translator, collection, llm, builder, manager, repair_options)


def _conflict(entry: TranslationEntry, kind: str) -> TermConflictEvidence:
    return TermConflictEvidence(
        entry.identity,
        "Guild",
        "旧观察译法",
        "冲突记录中的旧权威译法",
        kind,
    )


def _run(harness: _ConflictHarness, checkpoint: ProgressCheckpoint | None = None) -> TranslationResult:
    return harness.translator.translate(
        harness.collection,
        [entry.key for entry in harness.collection],
        lambda *_args: None,
        threading.Event(),
        checkpoint=checkpoint,
    )


def test_unchanged_translation_has_real_success_evidence(monkeypatch) -> None:
    harness = _harness(monkeypatch, conflicts=(), overwrite=True)
    entry = _entry("normal", translation="translated:normal", stage=1)
    harness.collection = TranslationEntryCollection([entry])
    result = _run(harness)

    outcome = result.entry_outcomes[entry.identity]
    assert outcome.status == "succeeded"
    assert outcome.text == "translated:normal"
    assert outcome.stage == 2
    assert harness.llm.keys_by_call == [("normal",)]


@pytest.mark.parametrize("before", ["", "旧译文"])
def test_failed_translation_never_treats_original_value_as_success(monkeypatch, before) -> None:
    harness = _harness(monkeypatch, conflicts=(), overwrite=True)
    entry = _entry("normal", translation=before, stage=1 if before else 0)
    harness.collection = TranslationEntryCollection([entry])
    monkeypatch.setattr(harness.llm, "chat_stream", lambda *_args: "{}")
    result = _run(harness)

    outcome = result.entry_outcomes[entry.identity]
    assert outcome.status == "failed"
    assert outcome.text == before
    assert "模型未返回有效译文" in outcome.reason
    assert not result.post_process_result.candidates


def test_strict_scope_does_not_repair_previously_successful_entries(monkeypatch) -> None:
    conflict = _conflict(_entry("effective"), "effective_library")
    harness = _harness(monkeypatch, conflicts=(conflict,))
    result = harness.translator.translate(
        harness.collection, ["normal"], lambda *_args: None, threading.Event(), strict_target_scope=True
    )

    assert harness.llm.keys_by_call == [("normal",)]
    assert harness.collection.get("effective").translation == "旧冲突译文"
    assert set(result.entry_outcomes) == {harness.collection.get("normal").identity}


def test_term_snapshot_is_observed_before_any_model_call(monkeypatch) -> None:
    harness = _harness(monkeypatch, conflicts=())

    def reject_changed_terms(manager):
        assert manager is harness.term_manager
        assert harness.llm.keys_by_call == []
        raise ValueError("术语已变化")

    harness.translator._term_snapshot_observer = reject_changed_terms
    with pytest.raises(ValueError, match="术语已变化"):
        _run(harness)
    assert harness.llm.keys_by_call == []


@pytest.mark.parametrize("from_dialogue", [False, True])
def test_generated_terms_are_observed_only_after_successful_storage(monkeypatch, from_dialogue):
    harness = _harness(monkeypatch, conflicts=())
    stored, observed = [], []
    database = SimpleNamespace(add_many_and_save=lambda terms: stored.extend(terms))
    monkeypatch.setattr(harness.term_manager, "get_dynamic_db", lambda: database, raising=False)
    monkeypatch.setattr(harness.term_manager, "has_term", lambda _: False, raising=False)
    harness.translator._term_update_observer = lambda manager, terms: observed.append((
        manager,
        list(terms),
        list(stored),
    ))
    entry = _entry("normal")
    result = TranslationResult()
    if from_dialogue:
        harness.translator._extractor = SimpleNamespace(extract=lambda entries: [TermEntry("Noun", "名词", "dialogue")])
        harness.translator._extract_dialogue_terms([entry], {"normal": "译文"}, result, threading.Lock())
    else:
        harness.translator._update_dynamic_terms([entry], {"normal": "译文"}, result, threading.Lock())
    assert len(observed) == 1
    manager, terms, already_stored = observed[0]
    assert manager is harness.term_manager
    assert terms and terms == already_stored == stored


@pytest.mark.parametrize("original,translation", [("...", "..."), ("Name", "{name}")])
def test_invalid_name_candidate_is_not_saved_or_counted_as_new_term(monkeypatch, original, translation):
    harness = _harness(monkeypatch, conflicts=())
    stored, observed = [], []
    harness.term_manager.get_dynamic_db = lambda: SimpleNamespace(add_many_and_save=stored.extend)
    harness.translator._term_update_observer = lambda *args: observed.append(args)
    entry = SimpleNamespace(id="normal", original=original, context="NPC_:FULL")
    result = TranslationResult()
    harness.translator._update_dynamic_terms([entry], {"normal": translation}, result, threading.Lock())
    assert stored == [] and observed == []
    assert result.new_dynamic_terms == 0


def test_failed_postprocess_stage_does_not_publish_initial_translation(monkeypatch, tmp_path) -> None:
    from transbridge.application.translation import ProofreadStage

    harness = _harness(monkeypatch, conflicts=())
    harness.translator._cfg.llm_config.enable_post_process = True
    harness.translator._cfg.llm_config.pp_polish_level = "moderate"
    harness.translator._cfg.llm_config.target_lang = "zh_CN"
    harness.translator._cfg.llm_config.game_profile = "skyrim_se"
    harness.translator._cfg.esp_path = str(tmp_path / "fixture.esp")

    def fail_stage(self, candidates):
        raise RuntimeError("proofread unavailable")

    monkeypatch.setattr(ProofreadStage, "__call__", fail_stage)
    result = _run(harness)
    entry = harness.collection.get("normal")

    assert result.success_count == 1  # First-pass statistics remain compatible.
    assert entry.translation == "translated:normal"  # Isolated draft only.
    assert result.entry_outcomes[entry.identity].status == "failed"
    assert result.entry_outcomes[entry.identity].text == ""
    assert result.entry_outcomes[entry.identity].reason == "后处理未完成"


def test_cancelled_translation_has_no_successful_commit_evidence(monkeypatch) -> None:
    harness = _harness(monkeypatch, conflicts=())
    stop = threading.Event()

    def cancel_after_response(*_args):
        stop.set()
        return '{"normal":"generated"}'

    monkeypatch.setattr(harness.llm, "chat_stream", cancel_after_response)
    result = harness.translator.translate(harness.collection, ["normal"], lambda *_args: None, stop)
    entry = harness.collection.get("normal")
    assert result.entry_outcomes[entry.identity].status == "cancelled"
    assert entry.translation == ""


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("only_blank", [False, True])
def test_blank_sources_never_reach_llm_or_progress_and_preserve_values(monkeypatch, overwrite, only_blank) -> None:
    harness = _harness(monkeypatch, conflicts=(), overwrite=overwrite)
    blanks = [
        TranslationEntry("empty", "empty", "", "", 0, "ARMO:DESC"),
        TranslationEntry("spaces", "spaces", " \t\n\u3000", "preserve", 1, "ARMO:DESC"),
    ]
    normal = _entry("normal")
    entries = blanks if only_blank else [*blanks, normal]
    harness.collection = TranslationEntryCollection(entries)
    before = [(e.original, e.translation, e.stage, e.revision) for e in blanks]
    updates = []

    result = harness.translator.translate(
        harness.collection,
        [e.key for e in entries],
        lambda *args: updates.append(args),
        threading.Event(),
    )

    assert result.failed_count == 0
    assert result.success_count == (0 if only_blank else 1)
    assert harness.llm.keys_by_call == ([] if only_blank else [("normal",)])
    stored_blanks = [harness.collection.get(e.key) for e in blanks]
    assert [(e.original, e.translation, e.stage, e.revision) for e in stored_blanks] == before
    if only_blank:
        assert not updates
    else:
        assert updates and all(update[1] == 1 for update in updates)
        assert harness.collection.get(normal.key).translation == "translated:normal"


def test_only_effective_library_conflicts_enter_single_entry_repair_and_use_latest_authority(monkeypatch) -> None:
    effective = _entry("effective", translation="旧冲突译文", stage=1)
    internal = _entry("internal", translation="内部冲突原译文", stage=1)
    harness = _harness(
        monkeypatch,
        conflicts=(_conflict(internal, "candidate_internal"), _conflict(effective, "effective_library")),
    )

    result = _run(harness)

    assert result.failed_count == 0
    assert harness.llm.keys_by_call.count(("effective",)) == 1
    assert all("internal" not in keys for keys in harness.llm.keys_by_call)
    assert harness.collection.get("effective").translation == "采用最新权威译法"
    assert harness.collection.get("internal").translation == "内部冲突原译文"
    assert harness.repair_options == [
        {
            "required_terms_by_entry": {"effective": {"Guild": "冲突记录中的旧权威译法"}},
            "update_terms": False,
        }
    ]
    repair_prompt = next(call for call in harness.builder.calls if call["keys"] == ["effective"])
    assert repair_prompt["terms_by_entry"]["effective"]["Guild"] == "最新权威译法"


def test_overwrite_mode_replaces_normal_occurrence_with_one_conflict_repair(monkeypatch) -> None:
    effective = _entry("effective", translation="旧冲突译文", stage=1)
    harness = _harness(
        monkeypatch,
        conflicts=(_conflict(effective, "effective_library"),),
        overwrite=True,
    )

    result = _run(harness)

    flattened = [key for call in harness.llm.keys_by_call for key in call]
    assert result.failed_count == 0
    assert flattened.count("effective") == 1
    assert len(harness.repair_options) == 1


def test_failed_conflict_constraint_preserves_original_translation(monkeypatch) -> None:
    effective = _entry("effective", translation="旧冲突译文", stage=1)
    harness = _harness(
        monkeypatch,
        conflicts=(_conflict(effective, "effective_library"),),
        repair_translation="仍然使用错误译法",
    )

    result = _run(harness)

    assert result.failed_count == 1
    assert any("未采用权威术语" in failure for failure in result.failed_entries)
    assert harness.collection.get("effective").translation == "旧冲突译文"
    assert harness.collection.get("normal").translation == "translated:normal"


def test_pending_conflict_repair_is_restored_when_extraction_marker_skips_new_evidence(monkeypatch) -> None:
    effective = _entry("effective", translation="旧冲突译文", stage=1)
    harness = _harness(monkeypatch, conflicts=())
    checkpoint = ProgressCheckpoint(
        esp_stem="fixture",
        target_entry_ids=["effective", "internal", "normal"],
        overwrite=False,
        completed_fingerprints=[],
        result_so_far={},
        run_id="term-conflict-run",
        term_repairs=[
            {
                "entry_key": effective.identity.to_dict(),
                "required_terms": {"Guild": "冲突记录中的旧权威译法"},
            }
        ],
    )

    result = _run(harness, checkpoint)

    assert result.failed_count == 0
    assert harness.llm.keys_by_call.count(("effective",)) == 1
    assert harness.collection.get("effective").translation == "采用最新权威译法"


def test_queued_batch_builds_prompt_after_admission_with_latest_in_flight_terms(monkeypatch) -> None:
    from transbridge.ai_translator import noun_extractor, prompt_builder, term_database

    manager = _TermManager()
    builder = _PromptBuilder()
    budget = AiRequestBudget(1)
    first_started = threading.Event()
    release_first = threading.Event()
    call_lock = threading.Lock()
    calls = 0

    class _BlockingLlm:
        def chat_stream(self, messages, _max_tokens, _callback):
            nonlocal calls
            payload = json.loads(messages[-1]["content"])
            with call_lock:
                calls += 1
                call_number = calls
            if call_number == 1:
                first_started.set()
                assert release_first.wait(2)
            return json.dumps({key: f"translated:{key}" for key in payload["keys"]})

        def cancel(self) -> None:
            pass

    class _Extractor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    monkeypatch.setattr(term_database, "TermDatabaseManager", lambda **_kwargs: manager)
    monkeypatch.setattr(prompt_builder, "PromptBuilder", lambda **_kwargs: builder)
    monkeypatch.setattr(noun_extractor, "NounExtractor", _Extractor)
    raw_llm = _BlockingLlm()
    translator = AutoTranslator(
        _config(max_concurrent=1),
        llm_client=raw_llm,
        request_budget=budget,
        candidate_checkpoint=InMemoryTranslationCheckpointPort(),
    )
    translator._llm = LimitedLLMClient(raw_llm, budget)
    translator._candidate_session = SimpleNamespace(
        accept=lambda translations, _collection: SimpleNamespace(accepted=len(translations))
    )
    first = _entry("first")
    second = _entry("second")
    collection = TranslationEntryCollection([first, second])
    result = TranslationResult()
    lock = threading.Lock()
    errors: list[BaseException] = []

    def run(entry: TranslationEntry) -> None:
        try:
            translator._run_batch(Batch([entry], "其他"), collection, result, lock)
        except BaseException as exc:
            errors.append(exc)

    first_thread = threading.Thread(target=run, args=(first,))
    second_thread = threading.Thread(target=run, args=(second,))
    first_thread.start()
    assert first_started.wait(1)
    second_thread.start()
    _wait_until(lambda: budget.snapshot().waiting == 1)
    assert [call["keys"] for call in builder.calls] == [["first"]]

    with translator._in_flight_lock:
        translator._in_flight_terms["NewestTerm"] = "最新译法"
    release_first.set()
    first_thread.join(2)
    second_thread.join(2)

    assert errors == []
    second_snapshot = next(snapshot for keys, snapshot in manager.match_snapshots if keys == ("second",))
    assert second_snapshot["NewestTerm"] == "最新译法"
    second_prompt = next(call for call in builder.calls if call["keys"] == ["second"])
    assert second_prompt["terms"]["NewestTerm"] == "最新译法"


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


class _ResultsEnvelopeBuilder(_PromptBuilder):
    def extract_partial_pairs(self, value):
        return _extract_partial_translation_results(value)

    def parse_translation_response(self, response, expected_keys):
        payload = json.loads(response)
        results: dict[str, str] = {}
        duplicates: set[str] = set()
        for item in payload["results"]:
            entry_id = item["entry_id"]
            if entry_id not in expected_keys:
                continue
            if entry_id in results:
                duplicates.add(entry_id)
            else:
                results[entry_id] = item["translation"]
        for entry_id in duplicates:
            results.pop(entry_id, None)
        return results


def _stream_recovery_translator(monkeypatch, llm):
    from transbridge.ai_translator import prompt_builder, term_database

    manager = _TermManager()
    builder = _ResultsEnvelopeBuilder()
    monkeypatch.setattr(term_database, "TermDatabaseManager", lambda **_kwargs: manager)
    monkeypatch.setattr(prompt_builder, "PromptBuilder", lambda **_kwargs: builder)
    translator = AutoTranslator(
        _config(),
        llm_client=llm,
        candidate_checkpoint=InMemoryTranslationCheckpointPort(),
    )
    translator._llm = llm
    accepted: list[dict[str, str]] = []

    def accept(translations, _collection):
        accepted.append(dict(translations))
        return SimpleNamespace(accepted=len(translations))

    translator._candidate_session = SimpleNamespace(accept=accept)
    return translator, accepted


def test_truncated_structured_stream_salvages_complete_items_and_retries_missing(monkeypatch) -> None:
    entries = [_entry("first"), _entry("second")]

    class TruncatingLlm:
        def __init__(self) -> None:
            self.calls = 0

        def chat_stream(self, messages, _max_tokens, callback):
            self.calls += 1
            keys = json.loads(messages[-1]["content"])["keys"]
            if self.calls == 1:
                partial = json.dumps({"results": [{"entry_id": keys[0], "translation": "translated:first"}]})
                callback(partial)
                raise LlmStructuredOutputTruncatedError("truncated")
            response = json.dumps({"results": [{"entry_id": key, "translation": f"translated:{key}"} for key in keys]})
            callback(response)
            return response

        def cancel(self):
            pass

    llm = TruncatingLlm()
    translator, accepted = _stream_recovery_translator(monkeypatch, llm)
    result = TranslationResult()

    translator._run_batch(Batch(entries, "其他"), TranslationEntryCollection(entries), result, threading.Lock())

    assert llm.calls == 2
    assert accepted == [{"first": "translated:first"}, {"second": "translated:second"}]
    assert result.success_count == 2
    assert result.failed_count == 0


def test_duplicate_stream_item_is_not_accepted_before_single_entry_retry(monkeypatch) -> None:
    entries = [_entry("first"), _entry("second")]

    class DuplicateLlm:
        def __init__(self) -> None:
            self.calls = 0

        def chat_stream(self, messages, _max_tokens, callback):
            self.calls += 1
            keys = json.loads(messages[-1]["content"])["keys"]
            if self.calls == 1:
                response = json.dumps({
                    "results": [
                        {"entry_id": "first", "translation": "unsafe-first"},
                        {"entry_id": "first", "translation": "duplicate-first"},
                        {"entry_id": "second", "translation": "translated:second"},
                    ]
                })
            else:
                response = json.dumps({
                    "results": [{"entry_id": key, "translation": f"translated:{key}"} for key in keys]
                })
            callback(response)
            return response

        def cancel(self):
            pass

    llm = DuplicateLlm()
    translator, accepted = _stream_recovery_translator(monkeypatch, llm)
    result = TranslationResult()

    translator._run_batch(Batch(entries, "其他"), TranslationEntryCollection(entries), result, threading.Lock())

    assert llm.calls == 2
    assert accepted == [{"second": "translated:second"}, {"first": "translated:first"}]
    assert result.success_count == 2
    assert result.failed_count == 0


def test_invalid_structured_stream_does_not_accept_prevalidated_items(monkeypatch) -> None:
    entries = [_entry("first"), _entry("second")]

    class InvalidLlm:
        def chat_stream(self, messages, _max_tokens, callback):
            del messages
            callback(json.dumps({"results": [{"entry_id": "first", "translation": "unsafe-first"}]}))
            raise LlmStructuredOutputInvalidResponseError("invalid")

        def cancel(self):
            pass

    translator, accepted = _stream_recovery_translator(monkeypatch, InvalidLlm())
    result = TranslationResult()

    translator._run_batch(Batch(entries, "其他"), TranslationEntryCollection(entries), result, threading.Lock())

    assert accepted == []
    assert result.success_count == 0
    assert result.failed_count == 2
