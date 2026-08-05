
import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from src.transbridge.converter.translation_entry import TranslationEntry
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
from src.transbridge.writer.plugin_writer import PluginWriter


# 伪造的 PluginString 对象
class FakePluginString:
    def __init__(self, editor_id, form_id, type_, string, index=0):
        self.editor_id = editor_id
        self.form_id = form_id
        self.type = type_
        self.string = string
        self.index = index  # 添加 index 属性


def make_fake_plugin_strings():
    """创建伪造的插件字符串列表"""
    return [
        FakePluginString("NPC_John", "0001", "INFO NAM1", "Hello"),
        FakePluginString("NPC_Mary", "0002", "INFO NAM1", "World"),
        FakePluginString("BOOK_Intro", "0003", "DESC", "Welcome"),
    ]


def make_fake_plugin():
    """创建伪造的插件对象"""
    plugin = Mock()
    plugin.extract_strings.return_value = make_fake_plugin_strings()
    plugin.replace_strings = Mock()
    plugin.save = Mock()
    return plugin


def test_apply_collection_updates():
    """测试应用翻译集合更新插件"""
    # 创建伪造的插件
    plugin = make_fake_plugin()

    # 创建翻译集合
    entries = [
        TranslationEntry(
            id="NPC_John:0001",
            key="INFO:NAM1",
            original="Hello",
            translation="你好",
            stage=1,
            context=None
        ),
        TranslationEntry(
            id="BOOK_Intro:0003",
            key="DESC",
            original="Welcome",
            translation="欢迎",
            stage=1,
            context=None
        ),
        # 这个条目不会更新，因为没有匹配的翻译
        TranslationEntry(
            id="NPC_Unknown:9999",
            key="INFO:NAM1",
            original="Unknown",
            translation="未知",
            stage=1,
            context=None
        ),
    ]
    collection = TranslationEntryCollection(entries)

    # 创建写入器并应用翻译
    writer = PluginWriter(plugin)
    updated = writer.apply_collection(collection)

    # 验证更新数量
    assert updated == 2

    # 验证 replace_strings 被调用
    plugin.replace_strings.assert_called_once()

    # 获取传递给 replace_strings 的参数
    args, kwargs = plugin.replace_strings.call_args
    modified_strings = args[0]

    # 验证修改的字符串
    assert len(modified_strings) == 2

    # 验证第一个修改的字符串
    assert modified_strings[0].editor_id == "NPC_John"
    assert modified_strings[0].form_id == "0001"
    assert modified_strings[0].type == "INFO NAM1"
    assert modified_strings[0].string == "你好"

    # 验证第二个修改的字符串
    assert modified_strings[1].editor_id == "BOOK_Intro"
    assert modified_strings[1].form_id == "0003"
    assert modified_strings[1].type == "DESC"
    assert modified_strings[1].string == "欢迎"


def test_apply_collection_skip_no_translation():
    """测试跳过没有翻译的条目"""
    # 创建伪造的插件
    plugin = make_fake_plugin()

    # 创建翻译集合，其中一个条目没有翻译
    entries = [
        TranslationEntry(
            id="NPC_John:0001",
            key="INFO:NAM1",
            original="Hello",
            translation="你好",
            stage=1,
            context=None
        ),
        TranslationEntry(
            id="NPC_Mary:0002",
            key="INFO:NAM1",
            original="World",
            translation="",  # 没有翻译
            stage=0,
            context=None
        ),
    ]
    collection = TranslationEntryCollection(entries)

    # 创建写入器并应用翻译
    writer = PluginWriter(plugin)
    updated = writer.apply_collection(collection)

    # 验证更新数量
    assert updated == 1

    # 验证 replace_strings 被调用
    plugin.replace_strings.assert_called_once()

    # 获取传递给 replace_strings 的参数
    args, kwargs = plugin.replace_strings.call_args
    modified_strings = args[0]

    # 验证只有一个修改的字符串
    assert len(modified_strings) == 1
    assert modified_strings[0].editor_id == "NPC_John"


def test_apply_collection_skip_same_translation():
    """测试跳过与原始文本相同的翻译"""
    # 创建伪造的插件
    plugin = make_fake_plugin()

    # 创建翻译集合，其中一个条目的翻译与原始文本相同
    entries = [
        TranslationEntry(
            id="NPC_John:0001",
            key="INFO:NAM1",
            original="Hello",
            translation="Hello",  # 与原始文本相同
            stage=1,
            context=None
        ),
        TranslationEntry(
            id="BOOK_Intro:0003",
            key="DESC",
            original="Welcome",
            translation="欢迎",
            stage=1,
            context=None
        ),
    ]
    collection = TranslationEntryCollection(entries)

    # 创建写入器并应用翻译
    writer = PluginWriter(plugin)
    updated = writer.apply_collection(collection)

    # 验证更新数量
    assert updated == 1

    # 验证 replace_strings 被调用
    plugin.replace_strings.assert_called_once()

    # 获取传递给 replace_strings 的参数
    args, kwargs = plugin.replace_strings.call_args
    modified_strings = args[0]

    # 验证只有一个修改的字符串
    assert len(modified_strings) == 1
    assert modified_strings[0].editor_id == "BOOK_Intro"


def test_apply_collection_no_updates():
    """测试没有更新的情况"""
    # 创建伪造的插件
    plugin = make_fake_plugin()

    # 创建空的翻译集合
    collection = TranslationEntryCollection()

    # 创建写入器并应用翻译
    writer = PluginWriter(plugin)
    updated = writer.apply_collection(collection)

    # 验证没有更新
    assert updated == 0

    # 验证 replace_strings 没有被调用
    plugin.replace_strings.assert_not_called()


def test_write():
    """测试写入插件文件"""
    # 创建伪造的插件
    plugin = make_fake_plugin()

    # 创建写入器
    writer = PluginWriter(plugin)

    # 写入临时文件
    with tempfile.NamedTemporaryFile(suffix=".esp", delete=False) as tmp:
        try:
            writer.write(tmp.name)

            # 验证 save 被调用
            plugin.save.assert_called_once()

            # 获取传递给 save 的参数
            args, kwargs = plugin.save.call_args
            output_path = args[0]

            # 验证路径
            assert output_path == Path(tmp.name)
        finally:
            try:
                Path(tmp.name).unlink()
            except PermissionError:
                # Windows 上临时文件可能无法立即删除，忽略此错误
                pass


def test_write_with_path_object():
    """测试使用 Path 对象作为输出路径"""
    # 创建伪造的插件
    plugin = make_fake_plugin()

    # 创建写入器
    writer = PluginWriter(plugin)

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_output.esp"

        writer.write(output_path)

        # 验证 save 被调用
        plugin.save.assert_called_once()

        # 获取传递给 save 的参数
        args, kwargs = plugin.save.call_args
        save_path = args[0]

        # 验证路径
        assert save_path == output_path
