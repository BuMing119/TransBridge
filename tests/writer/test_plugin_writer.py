
import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.writer.plugin_writer import PluginWriter


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
    plugin.extract_strings_with_context.return_value = make_fake_plugin_strings()
    plugin.find_string_subrecord.return_value = Mock()
    plugin.save = Mock()
    return plugin


def test_apply_collection_updates():
    """测试应用翻译集合更新插件"""
    # 创建伪造的插件
    plugin = make_fake_plugin()

    # 创建翻译集合
    entries = [
        TranslationEntry(
            id="NPC_John:0001|0~INFO:NAM1",
            key="NPC_John:0001|0~INFO:NAM1",
            original="Hello",
            translation="你好",
            stage=1,
            context="INFO:NAM1"
        ),
        TranslationEntry(
            id="BOOK_Intro:0003|0~DESC",
            key="BOOK_Intro:0003|0~DESC",
            original="Welcome",
            translation="欢迎",
            stage=1,
            context="DESC"
        ),
        # 这个条目不会更新，因为没有匹配的翻译
        TranslationEntry(
            id="NPC_Unknown:9999|0~INFO:NAM1",
            key="NPC_Unknown:9999|0~INFO:NAM1",
            original="Unknown",
            translation="未知",
            stage=1,
            context="INFO:NAM1"
        ),
    ]
    collection = TranslationEntryCollection(entries)

    # 创建写入器并应用翻译
    writer = PluginWriter(plugin)
    updated = writer.apply_collection(collection)

    # 验证更新数量
    assert updated == 2

    # 验证 find_string_subrecord 被调用两次（两个匹配项）
    assert plugin.find_string_subrecord.call_count == 2


def test_apply_collection_skip_no_translation():
    """测试跳过没有翻译的条目"""
    # 创建伪造的插件
    plugin = make_fake_plugin()

    # 创建翻译集合，其中一个条目没有翻译
    entries = [
        TranslationEntry(
            id="NPC_John:0001|0~INFO:NAM1",
            key="NPC_John:0001|0~INFO:NAM1",
            original="Hello",
            translation="你好",
            stage=1,
            context="INFO:NAM1"
        ),
        TranslationEntry(
            id="NPC_Mary:0002|0~INFO:NAM1",
            key="NPC_Mary:0002|0~INFO:NAM1",
            original="World",
            translation="",  # 没有翻译
            stage=0,
            context="INFO:NAM1"
        ),
    ]
    collection = TranslationEntryCollection(entries)

    # 创建写入器并应用翻译
    writer = PluginWriter(plugin)
    updated = writer.apply_collection(collection)

    # 验证更新数量
    assert updated == 1

    # 验证 find_string_subrecord 只被调用一次
    assert plugin.find_string_subrecord.call_count == 1


def test_apply_collection_skip_same_translation():
    """测试跳过与原始文本相同的翻译"""
    # 创建伪造的插件
    plugin = make_fake_plugin()

    # 创建翻译集合，其中一个条目的翻译与原始文本相同
    entries = [
        TranslationEntry(
            id="NPC_John:0001|0~INFO:NAM1",
            key="NPC_John:0001|0~INFO:NAM1",
            original="Hello",
            translation="Hello",  # 与原始文本相同
            stage=1,
            context="INFO:NAM1"
        ),
        TranslationEntry(
            id="BOOK_Intro:0003|0~DESC",
            key="BOOK_Intro:0003|0~DESC",
            original="Welcome",
            translation="欢迎",
            stage=1,
            context="DESC"
        ),
    ]
    collection = TranslationEntryCollection(entries)

    # 创建写入器并应用翻译
    writer = PluginWriter(plugin)
    updated = writer.apply_collection(collection)

    # 验证更新数量
    assert updated == 1

    # 验证 find_string_subrecord 只被调用一次
    assert plugin.find_string_subrecord.call_count == 1


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

    # 验证 find_string_subrecord 没有被调用
    plugin.find_string_subrecord.assert_not_called()


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
