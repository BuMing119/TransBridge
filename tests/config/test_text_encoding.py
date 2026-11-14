import pytest

from transbridge.config.repository import ConfigRepository
from transbridge.config.text_encoding import (
    XTRANSLATOR_READ_POLICY,
    TextEncodingDefaults,
    TextEncodings,
    fallback_encoding,
    normalize_text_encoding,
)


def test_text_encoding_defaults_persist_independently_of_other_config(tmp_path):
    path = tmp_path / "settings.ini"
    repository = ConfigRepository(path)
    repository.update_sections({"ui": {"theme": "dark"}})
    defaults = TextEncodingDefaults(repository)
    assert defaults.load() == TextEncodings()

    defaults.save(TextEncodings("Windows-936", "1252"))

    reread = ConfigRepository(path)
    assert TextEncodingDefaults(reread).load() == TextEncodings("cp936", "cp1252")
    assert reread.load().section("ui").get("theme") == "dark"
    assert "default_text_input_encoding = cp936" in path.read_text(encoding="utf-8")
    assert "default_text_output_encoding = cp1252" in path.read_text(encoding="utf-8")
    assert TextEncodings.from_project_data({}) == TextEncodings()


def test_project_and_program_defaults_store_both_language_bindings(tmp_path):
    repository = ConfigRepository(tmp_path / "settings.ini")
    defaults = TextEncodingDefaults(repository)
    encodings = TextEncodings("utf8", "windows-1252", "Russian", "French")

    defaults.save(encodings)

    assert defaults.load() == encodings
    assert TextEncodings.from_project_data(encodings.to_project_data()) == encodings
    assert encodings.read_options() == {
        "input_encoding": "utf-8",
        "source_language": "russian",
        "text_read_policy": XTRANSLATOR_READ_POLICY,
    }
    assert [fallback_encoding(language) for language in ("english", "russian", "polish", "chinese")] == [
        "cp1252",
        "cp1251",
        "cp1250",
        None,
    ]


@pytest.mark.parametrize("unsupported", ["utf-16", "utf-8-sig", "base64", "", "unknown", None])
def test_unsupported_or_invalid_codecs_are_rejected(unsupported):
    with pytest.raises(ValueError, match="encoding"):
        normalize_text_encoding(unsupported)


def test_invalid_persisted_default_is_not_treated_as_missing(tmp_path):
    repository = ConfigRepository(tmp_path / "settings.ini")
    repository.update_sections({"text_encoding": {"default_text_input_encoding": "utf-16"}})
    with pytest.raises(ValueError, match="utf-16"):
        TextEncodingDefaults(repository).load()
