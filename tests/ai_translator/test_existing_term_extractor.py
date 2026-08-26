import threading
from types import SimpleNamespace

from transbridge.ai_translator.existing_term_extractor import (
    EXISTING_NAME_SOURCE,
    EXISTING_TEXT_SOURCE,
    ExistingTermSeeder,
    should_seed_existing_terms,
)
from transbridge.ai_translator.term_formats import TermEntry


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
        key = term.casefold()
        return any(entry.term.casefold() == key for entry in [*self.existing, *self.dynamic.entries])

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
    def __init__(self, results: list[TermEntry] | None = None) -> None:
        self.results = list(results or [])
        self.calls: list[list[dict[str, str]]] = []

    def extract(self, pairs: list[dict[str, str]]) -> list[TermEntry]:
        self.calls.append(list(pairs))
        return list(self.results)


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


def test_seed_skips_conflicts_and_preserves_existing_terms() -> None:
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
    assert result.conflicts == 1
    assert result.skipped_existing == 1
    assert manager.dynamic.saved_batches == [
        [
            ("SKYRIM", "SKYRIM", EXISTING_NAME_SOURCE, "BOOK:FULL"),
        ]
    ]


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
