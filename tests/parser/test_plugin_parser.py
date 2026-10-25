from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from tests.plugin_fixtures import _record, write_plugin
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.plugin_parser import PluginParser


@pytest.mark.parametrize(
    "content",
    [b"", b"TES4\xff\xff\xff\xff", b"WRNG" + bytes(20), b"TES4\x01\x00\x00\x00" + bytes(16)],
)
def test_strict_parse_rejects_incomplete_or_invalid_header_before_loading(tmp_path, content):
    source = tmp_path / "invalid.esp"
    source.write_bytes(content)
    with patch("transbridge.parser.plugin_parser.SSEPluginWithContext.from_file") as load:
        with pytest.raises(ValueError, match="TES4"):
            PluginParser().parse_plugin(source, raise_on_error=True)
        load.assert_not_called()


def test_strict_parse_allows_valid_empty_plugin(tmp_path):
    source = tmp_path / "empty.esp"
    source.write_bytes(_record(b"TES4", 0, b""))
    parser = PluginParser()
    assert parser.parse_plugin(source, raise_on_error=True) == []
    assert parser.get_plugin() is not None


def test_strict_parse_preserves_loader_error_and_cause(tmp_path):
    source = write_plugin(tmp_path / "Test.esp", [(0x800, "Npc", "Name")])
    cause = OSError("original underlying loader error")
    original_error = RuntimeError("loader failure")

    def fail(path):
        raise original_error from cause

    with patch("transbridge.parser.plugin_parser.SSEPluginWithContext.from_file", side_effect=fail) as load:
        with pytest.raises(RuntimeError) as caught:
            PluginParser().parse_plugin(source, raise_on_error=True)
        assert caught.value is original_error
        assert caught.value.__cause__ is cause
        load.assert_called_once_with(source)


# 伪造的 PluginString 对象
class FakeContext:
    def __init__(self, quest=None, dialogue_topic=None):
        self.quest = quest
        self.dialogue_topic = dialogue_topic


class FakePluginString:
    def __init__(self, editor_id, form_id, type_, string, index=1, quest=None, dialogue_topic=None):
        self.editor_id = editor_id
        self.form_id = form_id
        self.type = type_
        self.string = string
        self.index = index
        self.string_id = None
        self.context = FakeContext(quest, dialogue_topic)


def make_fake_strings():
    return [
        FakePluginString("NPC_John", "0001", "INFO NAM1", "Hello"),
        FakePluginString("NPC_Mary", "0002", "INFO NAM1", ""),
        FakePluginString("BOOK_Intro", "0003", "DESC", "Welcome"),
    ]


@patch("transbridge.parser.plugin_parser.PluginStringsLookup.from_plugin", return_value=None)
@patch("transbridge.parser.plugin_parser.SSEPluginWithContext")
def test_parse_plugin_basic(mock_sseplugin, mock_strings_lookup):
    fake_plugin = Mock()
    fake_plugin.extract_strings_with_context.return_value = make_fake_strings()
    mock_sseplugin.from_file.return_value = fake_plugin

    parser = PluginParser()
    results = parser.parse_plugin(Path("dummy.esp"))

    assert len(results) == 2  # 空字符串被跳过
    assert all(isinstance(x, TranslationEntry) for x in results)

    assert results[0].id == "NPC_John:0001|1~INFO:NAM1"
    assert results[0].key == "NPC_John:0001|1~INFO:NAM1"
    assert results[0].original == "Hello"
    assert dict(results[0].metadata) == {"plugin.source_order": 0}
    assert dict(results[1].metadata) == {"plugin.source_order": 2}


@patch("transbridge.parser.plugin_parser.PluginStringsLookup.from_plugin", return_value=None)
@patch("transbridge.parser.plugin_parser.SSEPluginWithContext")
def test_parse_plugin_without_skip_empty(mock_sseplugin, mock_strings_lookup):
    fake_plugin = Mock()
    fake_plugin.extract_strings_with_context.return_value = make_fake_strings()
    mock_sseplugin.from_file.return_value = fake_plugin

    parser = PluginParser()
    results = parser.parse_plugin(Path("dummy.esp"), skip_empty=False)

    assert len(results) == 3


@patch("transbridge.parser.plugin_parser.PluginStringsLookup.from_plugin")
@patch("transbridge.parser.plugin_parser.SSEPluginWithContext")
def test_parse_plugin_can_disable_sibling_strings_discovery(mock_sseplugin, mock_strings_lookup):
    fake_plugin = Mock()
    fake_plugin.extract_strings_with_context.return_value = make_fake_strings()
    mock_sseplugin.from_file.return_value = fake_plugin

    parser = PluginParser()
    results = parser.parse_plugin(Path("dummy.esp"), discover_sibling_strings=False)

    assert len(results) == 2
    mock_strings_lookup.assert_not_called()
    fake_plugin.extract_strings_with_context.assert_called_once_with(strings_lookup=None)


@patch("transbridge.parser.plugin_parser.PluginStringsLookup.from_plugin", return_value=None)
@patch("transbridge.parser.plugin_parser.SSEPluginWithContext")
def test_progress_callback_called(mock_sseplugin, mock_strings_lookup):
    fake_plugin = Mock()
    fake_plugin.extract_strings_with_context.return_value = make_fake_strings()
    mock_sseplugin.from_file.return_value = fake_plugin

    progress = Mock()

    parser = PluginParser()
    parser.parse_plugin(Path("dummy.esp"), progress_callback=progress)

    assert progress.call_count == 3
    progress.assert_any_call(1, 3, "NPC_John_INFO NAM1")
    progress.assert_any_call(2, 3, "NPC_Mary_INFO NAM1")
    progress.assert_any_call(3, 3, "BOOK_Intro_DESC")


@patch("transbridge.parser.plugin_parser.SSEPluginWithContext")
def test_parse_plugin_exception_returns_empty(mock_sseplugin):
    mock_sseplugin.from_file.side_effect = Exception("broken")

    parser = PluginParser()
    result = parser.parse_plugin(Path("bad.esp"))

    assert result == []


def test_create_item():
    parser = PluginParser()
    ps = FakePluginString(
        "NPC_Test",
        "9999",
        "INFO NAM1",
        "Hello world",
        dialogue_topic="0000ABCD|fixture.esm",
    )

    item = parser._create_item(ps, source_order=17)

    assert item.id == "NPC_Test:9999|1~INFO:NAM1"
    assert item.key == "NPC_Test:9999|1~INFO:NAM1"
    assert item.original == "Hello world"
    assert item.translation == ""
    assert item.stage == 0
    assert dict(item.metadata) == {
        "plugin.parent_dial_formid": "0000ABCD|fixture.esm",
        "plugin.source_order": 17,
    }
