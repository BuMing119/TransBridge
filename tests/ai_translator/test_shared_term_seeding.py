from types import SimpleNamespace

import pytest

from transbridge.ai_translator.existing_term_extractor import ExistingTermSeeder
from transbridge.ai_translator.generated_term_writer import GeneratedTermWriter
from transbridge.ai_translator.shared_term_pool import SharedTermPool
from transbridge.ai_translator.term_database import TermDatabaseManager
from transbridge.ai_translator.term_formats import TermEntry
from transbridge.config.llm import LLMConfig


@pytest.fixture
def seed_project(tmp_path, monkeypatch):
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda _stem: str(tmp_path)))
    config = LLMConfig(term_priority=["dynamic"], enable_semantic_match=False)
    pool = SharedTermPool("project", "variant", "zh_CN", path=tmp_path / "pool.json")

    def manager(plugin):
        result = TermDatabaseManager(config, plugin, shared_term_pool=pool, generated_term_plugin_id=plugin)
        result.load_all()
        return result

    return SimpleNamespace(pool=pool, one=manager("one.esp"), two=manager("two.esp"))


def _entry(original, translation, context):
    return SimpleNamespace(key=original, original=original, translation=translation, context=context, stage=1)


def test_one_plugins_existing_text_does_not_skip_another_plugins_seeding(seed_project):
    GeneratedTermWriter(seed_project.one).save([("Delphine", "戴尔芬", "existing_text", "")])
    calls = []

    def extract(pairs, **_kwargs):
        calls.append(pairs)
        return [TermEntry("Whiterun", "雪漫城", "auto_dialogue")]

    result = ExistingTermSeeder(seed_project.two, SimpleNamespace(extract=extract)).seed([
        _entry("Riverwood", "溪木镇", "LCTN:FULL"),
        _entry("Visit Whiterun.", "前往雪漫城。", "INFO:NAM1"),
    ])
    assert calls
    assert result.text_extraction_attempted
    assert (result.direct_added, result.text_added) == (1, 1)
    assert {entry.term for entry in seed_project.pool.as_list()} == {"Delphine", "Riverwood", "Whiterun"}


@pytest.mark.parametrize("competing_translation", ["溪木镇", "河木镇"])
def test_seeding_reports_only_terms_accepted_after_concurrent_publication(seed_project, competing_translation):
    def save(terms):
        if terms[0][2] == "existing_name":
            GeneratedTermWriter(seed_project.one).save([("Riverwood", competing_translation, "auto_name", "")])
        return GeneratedTermWriter(seed_project.two).save(terms)

    extractor = SimpleNamespace(extract=lambda *_args, **_kwargs: [TermEntry("Whiterun", "雪漫城", "auto_dialogue")])
    result = ExistingTermSeeder(seed_project.two, extractor, save_terms=save).seed([
        _entry("Riverwood", "溪木镇", "LCTN:FULL"),
        _entry("Visit Whiterun.", "前往雪漫城。", "INFO:NAM1"),
    ])
    assert result.direct_added == 0
    assert result.text_added == 1
    assert result.added_count == 1
    assert seed_project.two.resolve_term("Riverwood").translation == competing_translation
