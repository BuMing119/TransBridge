from types import SimpleNamespace
from unittest.mock import Mock

from transbridge.smart_assistant.tools import _common
from transbridge.smart_assistant.tools.tool_translator import TranslationController


def test_smart_assistant_accepts_and_persists_csv_term_source(monkeypatch) -> None:
    config = SimpleNamespace(
        term_priority=["dynamic"],
        local_json_path="",
        local_csv_path="",
        local_excel_path="",
        save_to_file=Mock(),
    )
    monkeypatch.setattr(_common, "load_llm_config", lambda: config)

    result = TranslationController().set_term_config(
        {"term_sources": ["csv", "dynamic"], "csv_path": "terms.csv"},
        None,
    )

    assert result.success
    assert config.term_priority == ["csv", "dynamic"]
    assert config.local_csv_path == "terms.csv"
    config.save_to_file.assert_called_once_with()


def test_smart_assistant_rejects_unknown_term_source(monkeypatch) -> None:
    config = SimpleNamespace(term_priority=["dynamic"], save_to_file=Mock())
    monkeypatch.setattr(_common, "load_llm_config", lambda: config)

    result = TranslationController().set_term_config({"term_sources": ["yaml"]}, None)

    assert not result.success
    assert "csv" in result.message
    config.save_to_file.assert_not_called()
