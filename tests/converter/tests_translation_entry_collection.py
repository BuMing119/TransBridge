
import json
from unittest.mock import MagicMock, mock_open, patch

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.xt import XT_Entry


class TestTranslationEntryCollection:
    """测试 TranslationEntryCollection 类"""

    def test_init_empty(self):
        """测试空集合初始化"""
        collection = TranslationEntryCollection()
        assert len(collection) == 0
        assert list(collection) == []

    def test_init_with_entries(self):
        """测试使用条目初始化集合"""
        entry1 = TranslationEntry("id1", "key1", "original1", "translation1", 0, None)
        entry2 = TranslationEntry("id2", "key2", "original2", "translation2", 1, None)

        collection = TranslationEntryCollection([entry1, entry2])
        assert len(collection) == 2
        assert "id1" in collection
        assert "id2" in collection
        assert collection.get("id1") == entry1
        assert collection.get("id2") == entry2

    def test_init_builds_external_reference_index_once(self, monkeypatch):
        original = TranslationEntryCollection._build_external_index
        calls = 0

        def counted(entries):
            nonlocal calls
            calls += 1
            return original(entries)

        monkeypatch.setattr(
            TranslationEntryCollection,
            "_build_external_index",
            staticmethod(counted),
        )
        entries = [
            TranslationEntry(str(index), f"key-{index}", "source", "", 0, None)
            for index in range(500)
        ]

        collection = TranslationEntryCollection(entries)

        assert len(collection) == len(entries)
        assert calls == 1

    def test_add(self):
        """测试添加条目"""
        collection = TranslationEntryCollection()
        entry = TranslationEntry("id1", "key1", "original1", "translation1", 0, None)

        collection.add(entry)
        assert len(collection) == 1
        assert collection.get("id1") == entry
        assert "id1" in collection

    def test_add_no_overwrite(self):
        """测试添加条目但不覆盖"""
        collection = TranslationEntryCollection()
        entry1 = TranslationEntry("id1", "key1", "original1", "translation1", 0, None)
        entry2 = TranslationEntry("id1", "key1", "original1", "translation2", 1, None)

        collection.add(entry1)
        collection.add(entry2, overwrite=False)

        assert len(collection) == 1
        assert collection.get("id1") == entry1

    def test_add_overwrite(self):
        """测试添加条目并覆盖"""
        collection = TranslationEntryCollection()
        entry1 = TranslationEntry("id1", "key1", "original1", "translation1", 0, None)
        entry2 = TranslationEntry("id1", "key1", "original1", "translation2", 1, None)

        collection.add(entry1)
        original_snapshot = entry1.snapshot()
        collection.add(entry2, overwrite=True)

        assert len(collection) == 1
        updated = collection.get("id1")
        assert updated.id == entry2.id
        assert updated.key == entry2.key
        assert updated.translation == entry2.translation
        assert updated.stage == entry2.stage
        assert updated.revision.value == 1
        assert entry1.snapshot() == original_snapshot

    def test_remove(self):
        """测试删除条目"""
        collection = TranslationEntryCollection()
        entry = TranslationEntry("id1", "key1", "original1", "translation1", 0, None)

        collection.add(entry)
        assert len(collection) == 1

        collection.remove("id1")
        assert len(collection) == 0
        assert "id1" not in collection

    def test_remove_nonexistent(self):
        """测试删除不存在的条目"""
        collection = TranslationEntryCollection()
        collection.remove("nonexistent")  # 不应该抛出异常
        assert len(collection) == 0

    def test_add_many(self):
        """测试批量添加条目"""
        collection = TranslationEntryCollection()
        entries = [
            TranslationEntry("id1", "key1", "original1", "translation1", 0, None),
            TranslationEntry("id2", "key2", "original2", "translation2", 1, None),
            TranslationEntry("id3", "key3", "original3", "translation3", 0, None),
        ]

        collection.add_many(entries)
        assert len(collection) == 3
        for entry in entries:
            assert entry.id in collection

    def test_merge(self):
        """测试合并集合"""
        collection1 = TranslationEntryCollection([
            TranslationEntry("id1", "key1", "original1", "translation1", 0, None),
            TranslationEntry("id2", "key2", "original2", "translation2", 1, None),
        ])

        collection2 = TranslationEntryCollection([
            TranslationEntry("id2", "key2", "original2", "translation2_new", 0, None),
            TranslationEntry("id3", "key3", "original3", "translation3", 0, None),
        ])

        collection1.merge(collection2)
        assert len(collection1) == 3
        assert collection1.get("id1").translation == "translation1"
        assert collection1.get("id2").translation == "translation2_new"  # 覆盖
        assert collection1.get("id3").translation == "translation3"

    def test_merge_no_overwrite(self):
        """测试合并集合但不覆盖"""
        collection1 = TranslationEntryCollection([
            TranslationEntry("id1", "key1", "original1", "translation1", 0, None),
            TranslationEntry("id2", "key2", "original2", "translation2", 1, None),
        ])

        collection2 = TranslationEntryCollection([
            TranslationEntry("id2", "key2", "original2", "translation2_new", 0, None),
            TranslationEntry("id3", "key3", "original3", "translation3", 0, None),
        ])

        collection1.merge(collection2, overwrite=False)
        assert len(collection1) == 3
        assert collection1.get("id1").translation == "translation1"
        assert collection1.get("id2").translation == "translation2"  # 不覆盖
        assert collection1.get("id3").translation == "translation3"

    def test_filter(self):
        """测试过滤条目"""
        collection = TranslationEntryCollection([
            TranslationEntry("id1", "key1", "original1", "translation1", 0, None),
            TranslationEntry("id2", "key2", "original2", "translation2", 1, None),
            TranslationEntry("id3", "key3", "original3", "translation3", 0, None),
        ])

        stage0_entries = collection.filter(lambda e: e.stage == 0)
        assert len(stage0_entries) == 2
        assert all(e.stage == 0 for e in stage0_entries)

    def test_to_dict(self):
        """测试转换为字典"""
        collection = TranslationEntryCollection([
            TranslationEntry("id1", "key1", "original1", "translation1", 0, None),
            TranslationEntry("id2", "key2", "original2", "translation2", 1, None),
        ])

        result = collection.to_dict()
        assert len(result) == 2
        assert result[0]["schema_version"] == 2
        assert result[0]["id"] == "id1"
        assert result[1]["id"] == "id2"

    def test_to_json(self):
        """测试转换为JSON"""
        collection = TranslationEntryCollection([
            TranslationEntry("id1", "key1", "original1", "translation1", 0, None),
        ])

        json_str = collection.to_json()
        data = json.loads(json_str)
        assert len(data) == 1
        assert data[0]["schema_version"] == 2
        assert data[0]["id"] == "id1"

    @patch('builtins.open', new_callable=mock_open)
    @patch('pathlib.Path.write_text')
    def test_to_json_file(self, mock_write_text, mock_file):
        """测试保存为JSON文件"""
        collection = TranslationEntryCollection([
            TranslationEntry("id1", "key1", "original1", "translation1", 0, None),
        ])

        collection.to_json_file("test.json")
        mock_write_text.assert_called_once()

    @patch.object(TranslationEntry, 'try_update_from_xt')
    def test_apply_xt_entries(self, mock_try_update):
        """测试应用XT条目"""
        # 设置mock返回值
        def side_effect(entry, xt):
            if xt.edid == "edid1" and entry.id == "edid1:form1":
                # 更新第一个条目
                return TranslationEntry(entry.id, entry.key, entry.original, "Translated text 1", 1, entry.context)
            elif xt.edid == "form2" and entry.id == "edid2:form2":
                # 不更新第二个条目（已有翻译）
                return entry
            elif xt.edid == "edid3" and entry.id == "edid3:form3":
                # 更新第三个条目
                return TranslationEntry(entry.id, entry.key, entry.original, "Translated text 3", 1, entry.context)
            else:
                # 不匹配
                return None

        mock_try_update.side_effect = side_effect

        # 创建初始集合
        collection = TranslationEntryCollection([
            TranslationEntry("edid1:form1", "INFO:DESC", "Original text 1", "", 0, None),
            TranslationEntry("edid2:form2", "INFO:NAM1", "Original text 2", "Existing translation", 1, None),
            TranslationEntry("edid3:form3", "MISC:FULL", "Original text 3", "", 0, None),
        ])

        # 创建XT条目
        xt_entries = [
            XT_Entry(0, "edid1", "INFO:DESC", "Original text 1", "Translated text 1"),
            XT_Entry(1, "form2", "INFO:NAM1", "Original text 2", "Translated text 2"),  # 不会更新，因为已有翻译
            XT_Entry(0, "edid3", "MISC:FULL", "Original text 3", "Translated text 3"),
            XT_Entry(0, "nonexistent", "INFO:DESC", "Nonexistent text", "Translation"),  # 不会更新，因为没有匹配
        ]

        # 应用XT条目
        updated_count = collection.apply_xt_entries(xt_entries)

        # 验证结果
        assert updated_count == 2  # 只有两个条目被更新
        assert collection.get("edid1:form1").translation == "Translated text 1"
        assert collection.get("edid1:form1").stage == 1
        assert collection.get("edid2:form2").translation == "Existing translation"  # 未更改
        assert collection.get("edid2:form2").stage == 1
        assert collection.get("edid3:form3").translation == "Translated text 3"
        assert collection.get("edid3:form3").stage == 1

    @patch('transbridge.converter.translation_entry_collection.EET_XmlParser')
    def test_from_eet_xml(self, mock_parser_class):
        """测试从EET XML文件创建集合"""
        # 创建模拟的EET条目
        mock_eet_entry1 = MagicMock()
        mock_eet_entry1.edid = "edid1"
        mock_eet_entry1.id = "00000001"
        mock_eet_entry1.index = 1
        mock_eet_entry1.grup = "INFO"
        mock_eet_entry1.champ = "DESC"
        mock_eet_entry1.original = "Original text 1"
        mock_eet_entry1.traduit = "Translated text 1"
        mock_eet_entry1.status = 99  # 已翻译

        mock_eet_entry2 = MagicMock()
        mock_eet_entry2.edid = "edid2"
        mock_eet_entry2.id = "00000002"
        mock_eet_entry2.index = 1
        mock_eet_entry2.grup = "INFO"
        mock_eet_entry2.champ = "NAM1"
        mock_eet_entry2.original = "Original text 2"
        mock_eet_entry2.traduit = ""
        mock_eet_entry2.status = 0  # 未翻译

        # 创建模拟解析器
        mock_parser = MagicMock()
        mock_parser.__iter__ = MagicMock(return_value=iter([mock_eet_entry1, mock_eet_entry2]))
        mock_parser_class.from_file.return_value = mock_parser

        # 调用方法
        collection = TranslationEntryCollection.from_eet_xml("test.xml")

        # 验证结果
        assert len(collection) == 2
        entry1 = collection.get("edid1:00000001|1~INFO:DESC")
        entry2 = collection.get("edid2:00000002|1~INFO:NAM1")
        assert entry1.identity.local_key == "edid1:00000001|1~INFO:DESC"
        assert entry1.stage == 1  # status=99 => stage=1
        assert entry1.translation == "Translated text 1"
        assert entry2.identity.local_key == "edid2:00000002|1~INFO:NAM1"
        assert entry2.stage == 0  # status=0 => stage=0
        assert entry2.translation == ""

        # 确保解析器被正确调用
        mock_parser_class.from_file.assert_called_once_with("test.xml")

    @patch('transbridge.converter.translation_entry_collection.PluginParser')
    def test_from_plugin(self, mock_parser_class):
        """测试从Plugin文件创建集合"""
        # 创建模拟的TranslationEntry条目
        mock_entry1 = MagicMock()
        mock_entry1.id = "edid1:form1"
        mock_entry2 = MagicMock()
        mock_entry2.id = "edid2:form2"

        # 创建模拟解析器
        mock_parser = MagicMock()
        mock_parser.parse_plugin.return_value = [mock_entry1, mock_entry2]
        mock_parser_class.return_value = mock_parser

        # 调用方法
        collection = TranslationEntryCollection.from_plugin("test.esp")

        # 验证结果
        assert len(collection) == 2
        assert collection.get("edid1:form1") == mock_entry1
        assert collection.get("edid2:form2") == mock_entry2

        # 确保解析器被正确调用
        mock_parser.parse_plugin.assert_called_once()

    def test_from_entries(self):
        """测试从已有条目创建集合"""
        entry1 = MagicMock()
        entry2 = MagicMock()

        collection = TranslationEntryCollection.from_entries([entry1, entry2])

        assert len(collection) == 2
        # 注意：这里不能直接检查集合内容，因为TranslationEntryCollection的add方法需要TranslationEntry对象
        # 在实际使用中，这个方法应该接收TranslationEntry对象的列表
