from dataclasses import FrozenInstanceError
import threading
import time
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.existing_term_extractor import (
    EXISTING_NAME_SOURCE,
    EXISTING_TEXT_SOURCE,
    ExistingTermSeeder,
    should_seed_existing_terms,
)
from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.translation.token_batching import ContentTokenCount


def _entry(
    original: str,
    translation: str,
    context: str,
    *,
    stage: int = 1,
    key: str | None = None,
):
    return SimpleNamespace(
        original=original,
        translation=translation,
        context=context,
        stage=stage,
        key=key or original,
    )


class _DynamicDatabase:
    def __init__(self, entries: list[TermEntry] | None = None) -> None:
        self.entries = list(entries or [])
        self.saved_batches: list[list[tuple[str, str, str, str]]] = []

    def as_list(self) -> list[TermEntry]:
        return list(self.entries)

    def add_many_and_save(self, terms: list[tuple[str, str, str, str]]) -> None:
        self.saved_batches.append(list(terms))
        self.entries.extend(
            TermEntry(term=term, translation=translation, source=source, context=context)
            for term, translation, source, context in terms
        )


class _TermManager:
    def __init__(
        self,
        *args,
        existing: list[TermEntry] | None = None,
        dynamic: list[TermEntry] | None = None,
        **kwargs,
    ) -> None:
        self.existing = list(existing or [])
        self.dynamic = _DynamicDatabase(dynamic)

    def get_dynamic_db(self) -> _DynamicDatabase:
        return self.dynamic

    def has_term(self, term: str) -> bool:
        return self.resolve_term(term) is not None

    def resolve_term(self, term: str) -> TermEntry | None:
        key = term.casefold()
        return next(
            (entry for entry in [*self.existing, *self.dynamic.entries] if entry.term.casefold() == key),
            None,
        )

    def load_all(self):
        return {}

    def get_load_log(self):
        return ()

    def match_terms_scoped(self, **kwargs):
        return SimpleNamespace(flat_terms={}, terms_by_entry={})

    def exact_match(self, originals):
        translations = {entry.term: entry.translation for entry in [*self.existing, *self.dynamic.entries]}
        return {original: translations[original] for original in originals if original in translations}


class _Extractor:
    def __init__(
        self,
        results: list[TermEntry] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.results = list(results or [])
        self.error = error
        self.calls: list[list[dict[str, str]]] = []
        self.raise_on_error_values: list[bool] = []

    def extract(
        self,
        pairs: list[dict[str, str]],
        *,
        raise_on_error: bool = False,
    ) -> list[TermEntry]:
        self.calls.append(list(pairs))
        self.raise_on_error_values.append(raise_on_error)
        if self.error is not None:
            raise self.error
        return list(self.results)


class _CharacterCounter:
    def count(self, text: str) -> ContentTokenCount:
        return ContentTokenCount(len(text), False, "test-chars")


def test_seed_combines_direct_names_and_text_subsegments() -> None:
    manager = _TermManager()
    extractor = _Extractor([TermEntry("Delphine", "戴尔芬", "auto_dialogue")])
    entries = [
        _entry("Whiterun", "白漫城", "LCTN:FULL"),
        _entry("Meet Delphine.", "与戴尔芬会面。", "INFO:NAM1|000001"),
        _entry("Questionable Name", "存疑名称", "NPC_:FULL", stage=2),
        _entry("Hidden Name", "隐藏名称", "NPC_:FULL", stage=-1),
        _entry("Not translated", "", "INFO:NAM1|000002", stage=0),
    ]

    result = ExistingTermSeeder(manager, extractor).seed(entries)

    assert result.direct_added == 1
    assert result.text_added == 1
    assert result.conflicts == 0
    assert extractor.calls == [[{"original": "Meet Delphine.", "translation": "与戴尔芬会面。"}]]
    assert manager.dynamic.saved_batches == [
        [
            ("Whiterun", "白漫城", EXISTING_NAME_SOURCE, "LCTN:FULL"),
            ("Delphine", "戴尔芬", EXISTING_TEXT_SOURCE, ""),
        ]
    ]


def test_seed_records_internal_and_effective_library_conflicts() -> None:
    manager = _TermManager(existing=[TermEntry("Riverwood", "溪木镇", "manual")])
    extractor = _Extractor()
    entries = [
        _entry("Institute", "学院", "LCTN:FULL"),
        _entry(" institute ", "研究所", "LCTN:FULL"),
        _entry("Riverwood", "河木镇", "LCTN:FULL"),
        _entry("SKYRIM", "SKYRIM", "BOOK:FULL"),
    ]

    result = ExistingTermSeeder(manager, extractor).seed(entries)

    assert result.direct_added == 1
    assert result.text_added == 0
    assert result.conflicts == 2
    assert result.skipped_existing == 0
    assert len(result.conflict_records) == 3
    internal = [record for record in result.conflict_records if record.kind == "candidate_internal"]
    library = [record for record in result.conflict_records if record.kind == "effective_library"]
    assert {record.entry_key.local_key for record in internal} == {"Institute", " institute "}
    assert {record.observed_translation for record in internal} == {"学院", "研究所"}
    assert len(library) == 1
    assert library[0].entry_key.local_key == "Riverwood"
    assert library[0].observed_translation == "河木镇"
    assert library[0].canonical_translation == "溪木镇"
    assert library[0].canonical_source == "manual"
    assert manager.dynamic.saved_batches == [
        [
            ("SKYRIM", "SKYRIM", EXISTING_NAME_SOURCE, "BOOK:FULL"),
        ]
    ]


def test_seed_skips_candidate_matching_effective_library_without_conflict() -> None:
    manager = _TermManager(existing=[TermEntry("Riverwood", "溪木镇", "manual", "global")])
    extractor = _Extractor()

    result = ExistingTermSeeder(manager, extractor).seed([
        _entry("riverWOOD", " 溪木镇 ", "LCTN:FULL", key="project-riverwood")
    ])

    assert result.skipped_existing == 1
    assert result.conflicts == 0
    assert result.conflict_records == ()
    assert manager.dynamic.saved_batches == []


def test_llm_candidate_conflict_retains_originating_entry_key_and_canonical_metadata() -> None:
    manager = _TermManager(existing=[TermEntry("Delphine", "德尔芬", "json", "characters")])
    extractor = _Extractor([TermEntry("Delphine", "戴尔芬", "auto_dialogue")])
    entries = [
        _entry(
            "Meet Delphine.",
            "与戴尔芬会面。",
            "INFO:NAM1|000001",
            key="dialogue-entry-1",
        )
    ]

    result = ExistingTermSeeder(manager, extractor).seed(entries)

    assert result.conflicts == 1
    assert result.skipped_existing == 0
    assert len(result.conflict_records) == 1
    evidence = result.conflict_records[0]
    assert evidence.entry_key.local_key == "dialogue-entry-1"
    assert evidence.term == "Delphine"
    assert evidence.observed_translation == "戴尔芬"
    assert evidence.canonical_translation == "德尔芬"
    assert evidence.canonical_source == "json"
    assert evidence.canonical_context == "characters"
    assert evidence.kind == "effective_library"
    with pytest.raises(FrozenInstanceError):
        evidence.canonical_translation = "changed"  # type: ignore[misc]


def test_existing_text_source_avoids_repeating_llm_extraction() -> None:
    manager = _TermManager(dynamic=[TermEntry("Delphine", "戴尔芬", EXISTING_TEXT_SOURCE)])
    extractor = _Extractor([TermEntry("Ignored", "忽略", "auto_dialogue")])
    entries = [
        _entry("Whiterun", "白漫城", "LCTN:FULL"),
        _entry("Meet Delphine.", "与戴尔芬会面。", "INFO:NAM1|000001"),
    ]

    result = ExistingTermSeeder(manager, extractor).seed(entries)

    assert result.direct_added == 1
    assert result.text_added == 0
    assert result.text_extraction_attempted is False
    assert extractor.calls == []


def test_seed_reports_text_batch_progress_and_logs() -> None:
    manager = _TermManager()
    extractor = _Extractor([TermEntry("Delphine", "戴尔芬", "auto_dialogue")])
    entries = [
        _entry("Meet Delphine.", "与戴尔芬会面。", "INFO:NAM1|000001"),
        _entry("Ask Delphine.", "询问戴尔芬。", "INFO:NAM1|000002"),
        _entry("Find Delphine.", "找到戴尔芬。", "INFO:NAM1|000003"),
    ]
    progress: list[tuple[int, int, str]] = []
    logs: list[str] = []

    result = ExistingTermSeeder(manager, extractor, text_batch_size=2).seed(
        entries,
        progress_callback=lambda current, total, message: progress.append((current, total, message)),
        log_callback=logs.append,
    )

    assert result.text_extraction_attempted is True
    assert result.text_batches_completed == 2
    assert result.text_batches_total == 2
    assert result.cancelled is False
    assert [len(batch) for batch in extractor.calls] == [2, 1]
    assert extractor.raise_on_error_values == [True, True]
    assert progress[0] == (0, 2, "准备从 3 条已有译文抽取术语（共 2 批）")
    assert progress[-1] == (2, 2, "已完成术语抽取 2/2 批，本批新增候选 1 个")
    assert logs == [
        "术语初始化：3 条已有译文，2 个 LLM 批次",
        "已完成术语抽取 1/2 批，本批新增候选 1 个",
        "已完成术语抽取 2/2 批，本批新增候选 1 个",
    ]


def test_seed_stops_before_the_next_text_batch() -> None:
    manager = _TermManager()
    extractor = _Extractor([TermEntry("Delphine", "戴尔芬", "auto_dialogue")])
    entries = [
        _entry("Meet Delphine.", "与戴尔芬会面。", "INFO:NAM1|000001"),
        _entry("Ask Delphine.", "询问戴尔芬。", "INFO:NAM1|000002"),
        _entry("Find Delphine.", "找到戴尔芬。", "INFO:NAM1|000003"),
    ]
    stop_event = threading.Event()
    progress: list[tuple[int, int, str]] = []

    def capture_progress(current: int, total: int, message: str) -> None:
        progress.append((current, total, message))
        if current == 1 and message.startswith("已完成术语抽取"):
            stop_event.set()

    result = ExistingTermSeeder(manager, extractor, text_batch_size=1).seed(
        entries,
        progress_callback=capture_progress,
        stop_event=stop_event,
    )

    assert result.cancelled is True
    assert result.text_extraction_attempted is True
    assert result.text_batches_completed == 1
    assert result.text_batches_total == 3
    assert len(extractor.calls) == 1
    assert progress[-1] == (1, 3, "术语抽取已停止（1/3 批）")
    assert manager.dynamic.saved_batches == []


def test_seed_stops_after_the_first_text_batch_failure() -> None:
    manager = _TermManager()
    extractor = _Extractor(error=RuntimeError("unauthorized"))
    entries = [
        _entry("Meet Delphine.", "与戴尔芬会面。", "INFO:NAM1|000001"),
        _entry("Ask Delphine.", "询问戴尔芬。", "INFO:NAM1|000002"),
    ]

    progress: list[tuple[int, int, str]] = []
    result = ExistingTermSeeder(manager, extractor, text_batch_size=1).seed(
        entries,
        progress_callback=lambda current, total, message: progress.append((current, total, message)),
    )

    assert result.error == "unauthorized"
    assert result.text_batches_completed == 0
    assert result.text_batches_total == 2
    assert len(extractor.calls) == 1
    assert extractor.raise_on_error_values == [True]
    assert manager.dynamic.saved_batches == []
    assert progress[-1][0:2] == (0, 2)
    assert "失败" in progress[-1][2]


def test_seed_batches_original_and_translation_by_content_tokens() -> None:
    manager = _TermManager()
    extractor = _Extractor()
    entries = [
        _entry("abc", "译", "INFO:NAM1|1", key="one"),
        _entry("de", "文", "INFO:NAM1|2", key="two"),
        _entry("f", "字", "INFO:NAM1|3", key="three"),
    ]

    result = ExistingTermSeeder(
        manager,
        extractor,
        max_tokens_per_batch=4,
        token_counter=_CharacterCounter(),
    ).seed(entries)

    # Every original+translation pair fits alone, while no adjacent pair fits together.
    assert [len(batch) for batch in extractor.calls] == [1, 1, 1]
    assert result.text_batches_total == 3
    assert result.text_batches_completed == 3


@pytest.mark.parametrize(("max_concurrent", "expected_peak"), [(1, 1), (3, 3)])
def test_seed_honours_configured_term_request_concurrency(max_concurrent: int, expected_peak: int) -> None:
    manager = _TermManager()

    class ConcurrentExtractor:
        def __init__(self) -> None:
            self.lock = threading.Lock()
            self.active = 0
            self.peak = 0

        def extract(self, pairs, *, raise_on_error=False):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            time.sleep(0.04)
            with self.lock:
                self.active -= 1
            return [TermEntry(pairs[0]["original"], pairs[0]["translation"], "auto_dialogue")]

    extractor = ConcurrentExtractor()
    entries = [_entry(f"term-{index}", f"译-{index}", f"INFO:NAM1|{index}") for index in range(6)]

    result = ExistingTermSeeder(
        manager,
        extractor,
        max_concurrent=max_concurrent,
        text_batch_size=1,
    ).seed(entries)

    assert result.error is None
    assert result.text_batches_completed == 6
    assert extractor.peak == expected_peak


def test_out_of_order_term_batches_merge_in_original_batch_order() -> None:
    manager = _TermManager()
    completion_order: list[str] = []

    class OutOfOrderExtractor:
        def extract(self, pairs, *, raise_on_error=False):
            original = pairs[0]["original"]
            delay = {"first": 0.08, "second": 0.04, "third": 0.01}[original]
            time.sleep(delay)
            completion_order.append(original)
            return [TermEntry(original, pairs[0]["translation"], "auto_dialogue")]

    entries = [
        _entry("first", "甲", "INFO:NAM1|1"),
        _entry("second", "乙", "INFO:NAM1|2"),
        _entry("third", "丙", "INFO:NAM1|3"),
    ]

    ExistingTermSeeder(
        manager,
        OutOfOrderExtractor(),
        max_concurrent=3,
        text_batch_size=1,
    ).seed(entries)

    assert completion_order == ["third", "second", "first"]
    assert [term[0] for term in manager.dynamic.saved_batches[0]] == ["first", "second", "third"]


def test_parallel_failure_stops_refill_and_discards_successful_partial_text_terms() -> None:
    manager = _TermManager()

    class FailFastExtractor:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.lock = threading.Lock()

        def extract(self, pairs, *, raise_on_error=False):
            original = pairs[0]["original"]
            with self.lock:
                self.calls.append(original)
            if original == "term-0":
                raise RuntimeError("batch failed")
            time.sleep(0.05)
            return [TermEntry(original, pairs[0]["translation"], "auto_dialogue")]

    extractor = FailFastExtractor()
    entries = [_entry(f"term-{index}", f"译-{index}", f"INFO:NAM1|{index}") for index in range(8)]

    result = ExistingTermSeeder(
        manager,
        extractor,
        max_concurrent=3,
        text_batch_size=1,
    ).seed(entries)

    assert result.error == "batch failed"
    assert set(extractor.calls) <= {"term-0", "term-1", "term-2"}
    assert len(extractor.calls) <= 3
    assert manager.dynamic.saved_batches == []


def test_stop_while_paused_does_not_start_waiting_term_requests() -> None:
    manager = _TermManager()
    extractor = _Extractor()
    pause_event = threading.Event()
    stop_event = threading.Event()
    entries = [_entry(f"term-{index}", f"译-{index}", f"INFO:NAM1|{index}") for index in range(3)]
    holder: list = []

    thread = threading.Thread(
        target=lambda: holder.append(
            ExistingTermSeeder(manager, extractor, max_concurrent=3, text_batch_size=1).seed(
                entries,
                pause_event=pause_event,
                stop_event=stop_event,
            )
        )
    )
    thread.start()
    time.sleep(0.05)
    stop_event.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert holder[0].cancelled is True
    assert extractor.calls == []
    assert manager.dynamic.saved_batches == []


def test_over_budget_term_pair_reports_stable_key_without_llm_call() -> None:
    manager = _TermManager()
    extractor = _Extractor()
    entries = [_entry("1234", "五六", "INFO:NAM1|1", key="stable-key")]

    result = ExistingTermSeeder(
        manager,
        extractor,
        max_tokens_per_batch=5,
        token_counter=_CharacterCounter(),
    ).seed(entries)

    assert extractor.calls == []
    assert result.text_batches_completed == 0
    assert result.error is not None
    assert "stable-key" in result.error
    assert "Token" in result.error


def test_partial_project_detection_requires_existing_and_untranslated_entries() -> None:
    existing = _entry("Whiterun", "白漫城", "LCTN:FULL")
    untranslated = _entry("New line", "", "INFO:NAM1|000002", stage=0)

    assert should_seed_existing_terms([existing, untranslated], [untranslated]) is True
    assert should_seed_existing_terms([untranslated], [untranslated]) is False
    assert should_seed_existing_terms([existing], [existing]) is False


def test_auto_translator_uses_seeded_name_term_in_the_same_run(monkeypatch) -> None:
    from transbridge.ai_translator import noun_extractor, prompt_builder, term_database
    from transbridge.ai_translator.translator import AutoTranslator, ProgressCheckpoint, TranslatorConfig
    from transbridge.application.translation import InMemoryTranslationCheckpointPort
    from transbridge.converter.translation_entry import TranslationEntry
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection
    from transbridge.infra import llm_client

    runtime_managers: list[_TermManager] = []

    class RuntimeTermManager(_TermManager):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__()
            runtime_managers.append(self)

    class RuntimeExtractor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def extract(self, pairs):
            return []

    class ConstructorOnlyPrompt:
        def __init__(self, *args, **kwargs) -> None:
            pass

    monkeypatch.setattr(llm_client, "create_llm_client", lambda config: object())
    monkeypatch.setattr(term_database, "TermDatabaseManager", RuntimeTermManager)
    monkeypatch.setattr(prompt_builder, "PromptBuilder", ConstructorOnlyPrompt)
    monkeypatch.setattr(noun_extractor, "NounExtractor", RuntimeExtractor)
    monkeypatch.setattr(ProgressCheckpoint, "save", lambda self, esp_path: None)
    monkeypatch.setattr(ProgressCheckpoint, "delete", lambda self, esp_path: None)

    config = SimpleNamespace(
        game_profile="fixture",
        target_lang="zh-CN",
        max_tokens_per_batch=100,
        max_concurrent=1,
        max_output_tokens=100,
        config_revision=1,
        provider="fixture",
        base_url="http://127.0.0.1:1/v1",
        model="fixture-model",
        enable_post_process=False,
        retrieval_enabled=True,
    )
    existing = TranslationEntry("existing", "existing", "Whiterun", "白漫城", 1, "LCTN:FULL")
    untranslated = TranslationEntry("new", "new", "Whiterun", "", 0, "LCTN:FULL")
    collection = TranslationEntryCollection([existing, untranslated])
    translator = AutoTranslator(
        TranslatorConfig(config, "fixture.esp"),
        candidate_checkpoint=InMemoryTranslationCheckpointPort(),
        run_id_factory=lambda: "existing-term-seed-run",
    )

    result = translator.translate(
        collection,
        ["new"],
        lambda *args: None,
        threading.Event(),
    )

    assert result.success_count == 1
    assert result.new_dynamic_terms == 1
    assert collection.get("new").translation == "白漫城"
    assert runtime_managers[0].dynamic.entries[0].source == EXISTING_NAME_SOURCE
