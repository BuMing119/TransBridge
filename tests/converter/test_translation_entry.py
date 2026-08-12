import pytest
from types import SimpleNamespace

from src.transbridge.converter.translation_entry import TranslationEntry
from src.transbridge.parser.eet_parser import EET_Entry
from src.transbridge.parser.xt import XT_Entry


class TestTranslationEntry:
    """测试 TranslationEntry 类"""

    def test_init(self):
        """测试 TranslationEntry 的初始化"""
        entry = TranslationEntry(
            id="test_id",
            key="test_key",
            original="original_text",
            translation="translated_text",
            stage=1,
            context="test_context"
        )
        assert entry.id == "test_id"
        assert entry.key == "test_key"
        assert entry.original == "original_text"
        assert entry.translation == "translated_text"
        assert entry.stage == 1
        assert entry.context == "test_context"

    def test_creat_from_eet_entry(self):
        """测试从 EET_Entry 创建 TranslationEntry"""
        # 测试无译文的情况 (stage 应为 0)
        eet_entry = EET_Entry(
            grup="INFO",
            id="12345",
            edid="TestEdid",
            champ="NAM1",
            original="Original text",
            traduit="",
            perso="translator",
            index=0,
            status=0,
            idstexte=100,
            commentaire="Test comment",
            icon=None
        )

        entry = TranslationEntry.create_from_eet_entry(eet_entry)

        assert entry.id == "TestEdid:12345|0~INFO:NAM1"
        assert entry.key == "TestEdid:12345|0~INFO:NAM1"
        assert entry.original == "Original text"
        assert entry.translation == ""
        assert entry.stage == 0  # 无译文
        assert entry.context == "INFO:NAM1"

        # 测试 status == 99 的情况 (stage 应为 1)
        eet_entry_status_99 = EET_Entry(
            grup="INFO",
            id="12345",
            edid="TestEdid99",
            champ="NAM1",
            original="Original text 2",
            traduit="Translated text 2",
            perso="translator",
            index=0,
            status=99,  # 设置为 99
            idstexte=100,
            commentaire="Test comment",
            icon=None
        )

        entry_99 = TranslationEntry.create_from_eet_entry(eet_entry_status_99)

        assert entry_99.stage == 1  # status == 99

    def test_create_from_plugin_entry(self):
        """测试从 PluginString 创建 TranslationEntry"""
        # 创建模拟的 PluginString 对象
        plugin_string = SimpleNamespace(
            type="INFO NAM1",
            editor_id="TestEditor",
            form_id="000123",
            string="Original plugin text",
            index=1,
            string_id=None,
            context=SimpleNamespace(quest="0100ABCD"),
        )

        entry = TranslationEntry.create_from_plugin_entry(plugin_string)

        assert entry.id == "TestEditor:000123|1~INFO:NAM1"
        assert entry.key == "TestEditor:000123|1~INFO:NAM1"
        assert entry.original == "Original plugin text"
        assert entry.translation == ""
        assert entry.stage == 0
        assert entry.context == "INFO:NAM1|0100ABCD"

        # 测试没有 type 属性的情况
        plugin_string_no_type = SimpleNamespace(
            editor_id="TestEditor",
            form_id="000456",
            string="Another text",
            index=1,
            string_id=None,
        )

        entry_no_type = TranslationEntry.create_from_plugin_entry(plugin_string_no_type)

        assert entry_no_type.key == "TestEditor:000456|1~UNKNOWN"
        assert entry_no_type.context == "UNKNOWN"

    def test_try_update_from_xt(self):
        """测试使用 XT_Entry 更新 TranslationEntry"""
        # 创建基础 TranslationEntry
        entry = TranslationEntry(
            id="TestEdid:FormID|1~INFO:NAM1",
            key="TestEdid:FormID|1~INFO:NAM1",
            original="Original text",
            translation="",
            stage=0,
            context="INFO:NAM1"
        )

        # 测试 list_id == 0，匹配的情况
        xt_entry_0 = XT_Entry(
            list_id=0,
            edid="TestEdid",
            rec="INFO:NAM1",
            source="Original text",
            dest="Translated text"
        )

        updated_entry = TranslationEntry.try_update_from_xt(entry, xt_entry_0)

        assert updated_entry is not None
        assert updated_entry.translation == "Translated text"
        assert updated_entry.stage == 1

        # 测试 list_id == 1，匹配的情况
        entry2 = TranslationEntry(
            id="AnotherEdid:FormID2|1~DIAL:NAME",
            key="AnotherEdid:FormID2|1~DIAL:NAME",
            original="Another original",
            translation="",
            stage=0,
            context="DIAL:NAME"
        )

        xt_entry_1 = XT_Entry(
            list_id=1,
            edid="[FormID2]",
            rec="DIAL:NAME",
            source="Another original",
            dest="Another translation"
        )

        updated_entry2 = TranslationEntry.try_update_from_xt(entry2, xt_entry_1)

        assert updated_entry2 is not None
        assert updated_entry2.translation == "Another translation"
        assert updated_entry2.stage == 1

        # 测试不匹配的情况 (edid 不匹配)
        xt_entry_mismatch = XT_Entry(
            list_id=0,
            edid="MismatchEdid",
            rec="INFO:NAM1",
            source="Original text",
            dest="Translated text"
        )

        result = TranslationEntry.try_update_from_xt(entry, xt_entry_mismatch)
        assert result is None

        # 测试不匹配的情况 (rec 不匹配)
        xt_entry_mismatch_rec = XT_Entry(
            list_id=0,
            edid="TestEdid",
            rec="MISMATCH:REC",
            source="Original text",
            dest="Translated text"
        )

        result = TranslationEntry.try_update_from_xt(entry, xt_entry_mismatch_rec)
        assert result is None

        # 测试不匹配的情况 (source 不匹配)
        xt_entry_mismatch_source = XT_Entry(
            list_id=0,
            edid="TestEdid",
            rec="INFO:NAM1",
            source="Mismatch source",
            dest="Translated text"
        )

        result = TranslationEntry.try_update_from_xt(entry, xt_entry_mismatch_source)
        assert result is None

        # 测试匹配但不满足更新条件的情况 (stage != 0)
        entry_stage_1 = TranslationEntry(
            id="TestEdid:FormID3|1~INFO:NAM1",
            key="TestEdid:FormID3|1~INFO:NAM1",
            original="Original text",
            translation="",
            stage=1,  # stage 不为 0
            context="INFO:NAM1"
        )

        xt_entry_match = XT_Entry(
            list_id=0,
            edid="TestEdid",
            rec="INFO:NAM1",
            source="Original text",
            dest="Translated text"
        )

        result = TranslationEntry.try_update_from_xt(entry_stage_1, xt_entry_match)
        assert result is entry_stage_1  # 返回原 entry

        # 测试匹配但不满足更新条件的情况 (已有翻译)
        entry_with_translation = TranslationEntry(
            id="TestEdid:FormID4|1~INFO:NAM1",
            key="TestEdid:FormID4|1~INFO:NAM1",
            original="Original text",
            translation="Existing translation",  # 已有翻译
            stage=0,
            context="INFO:NAM1"
        )

        result = TranslationEntry.try_update_from_xt(entry_with_translation, xt_entry_match)
        assert result is entry_with_translation  # 返回原 entry

        # 测试匹配但不满足更新条件的情况 (dest 为空)
        xt_entry_empty_dest = XT_Entry(
            list_id=0,
            edid="TestEdid",
            rec="INFO:NAM1",
            source="Original text",
            dest=""  # dest 为空
        )

        result = TranslationEntry.try_update_from_xt(entry, xt_entry_empty_dest)
        assert result is entry  # 返回原 entry

    def test_to_dict(self):
        """测试将 TranslationEntry 转换为字典"""
        entry = TranslationEntry(
            id="test_id",
            key="test_key",
            original="original_text",
            translation="translated_text",
            stage=1,
            context="test_context"
        )

        result = entry.to_dict()

        assert result == {
            "id": "test_id",
            "key": "test_key",
            "original": "original_text",
            "translation": "translated_text",
            "stage": 1,
            "context": "test_context",
            "string_id": None,
            "full_form_id": None,
            "dsd_type": "",
            "dsd_index": 1,
            "dsd_editor_id": "",
        }

    def test_from_dict(self):
        """测试从字典创建 TranslationEntry"""
        data = {
            "id": "test_id",
            "key": "test_key",
            "original": "original_text",
            "translation": "translated_text",
            "stage": 1,
            "context": "test_context"
        }

        entry = TranslationEntry.from_dict(data)

        assert entry.id == "test_id"
        assert entry.key == "test_key"
        assert entry.original == "original_text"
        assert entry.translation == "translated_text"
        assert entry.stage == 1
        assert entry.context == "test_context"

        # 测试缺少字段的情况
        partial_data = {
            "id": "partial_id"
            # 缺少其他字段
        }

        partial_entry = TranslationEntry.from_dict(partial_data)

        assert partial_entry.id == "partial_id"
        assert partial_entry.key == ""  # 默认值
        assert partial_entry.original == ""  # 默认值
        assert partial_entry.translation == ""  # 默认值
        assert partial_entry.stage == 0  # 默认值
        assert partial_entry.context is None  # 默认值
