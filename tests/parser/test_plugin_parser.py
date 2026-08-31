from pathlib import Path
from unittest.mock import Mock, patch

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.plugin_parser import PluginParser


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
