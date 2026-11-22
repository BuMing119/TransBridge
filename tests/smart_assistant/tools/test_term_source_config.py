import pytest

from transbridge.config.llm import LLMConfig
from transbridge.smart_assistant.tools import _common
from transbridge.smart_assistant.tools.tool_translator import TranslationController


def test_smart_assistant_accepts_and_persists_csv_term_source(tmp_path, monkeypatch) -> None:
    from tests.contracts.config.test_unified_repository import _repository

    repository = _repository(tmp_path)
    config = LLMConfig.load_from_file(repository=repository)
    monkeypatch.setattr(_common, "load_llm_config", lambda: config)

    sources = ["csv", "dynamic", "paratranz", "json", "excel"]
    result = TranslationController().set_term_config(
        {"term_sources": sources, "csv_path": "  terms.csv  "},
        None,
    )

    assert result.success
    assert config.term_priority == sources
    assert config.local_csv_path == "terms.csv"
    assert repository.load().value("llm", "term_priority") == ",".join(sources)


@pytest.mark.parametrize("order", [[], ["yaml"], ["json", "csv"], ["dynamic", "dynamic", "csv", "excel", "json"]])
def test_smart_assistant_rejects_unavailable_term_source_order(monkeypatch, order) -> None:
    config = LLMConfig()
    monkeypatch.setattr(LLMConfig, "save_to_file", lambda self: pytest.fail("invalid order was saved"))
    monkeypatch.setattr(_common, "load_llm_config", lambda: config)

    result = TranslationController().set_term_config({"term_sources": order}, None)

    assert not result.success
    assert "csv" in result.message
