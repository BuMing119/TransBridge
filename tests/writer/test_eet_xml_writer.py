
import pytest
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock

from src.transbridge.converter.translation_entry import TranslationEntry
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
from src.transbridge.parser.eet_parser import EET_XmlParser, EET_Entry
from src.transbridge.writer.eet_xml_writer import EETWriter


def create_test_xml(content):
    """创建测试用的 XML 文件并返回路径"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
        f.write(content)
        return f.name


def create_mock_parser(xml_content):
    """创建模拟的 EET_XmlParser 对象"""
    # 解析 XML
    root = ET.fromstring(xml_content)
    tree = ET.ElementTree(root)

    # 创建模拟的解析器
    parser = Mock(spec=EET_XmlParser)
    parser._tree = tree
    parser._root = root

    return parser


def test_apply_collection_updates():
    """测试应用翻译集合更新 XML"""
    # 创建测试 XML
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
<DocumentElement>
  <ESP>
    <GRUP>INFO</GRUP>
    <ID>12345</ID>
    <EDID>NPC_John</EDID>
    <CHAMP>NAM1</CHAMP>
    <ORIGINAL>Hello</ORIGINAL>
    <TRADUIT></TRADUIT>
    <STATUS>0</STATUS>
    <PERSO></PERSO>
    <INDEX></INDEX>
    <IDSTEXTE></IDSTEXTE>
    <COMMENTAIRE></COMMENTAIRE>
    <ICON></ICON>
  </ESP>
  <ESP>
    <GRUP>BOOK</GRUP>
    <ID>67890</ID>
    <EDID>BOOK_Intro</EDID>
    <CHAMP>DESC</CHAMP>
    <ORIGINAL>Welcome</ORIGINAL>
    <TRADUIT></TRADUIT>
    <STATUS>0</STATUS>
    <PERSO></PERSO>
    <INDEX></INDEX>
    <IDSTEXTE></IDSTEXTE>
    <COMMENTAIRE></COMMENTAIRE>
    <ICON></ICON>
  </ESP>
</DocumentElement>"""

    # 创建模拟解析器
    parser = create_mock_parser(xml_content)

    # 创建翻译集合
    entries = [
        TranslationEntry(
            id="NPC_John:12345|1~INFO:NAM1",
            key="NPC_John:12345|1~INFO:NAM1",
            original="Hello",
            translation="你好",
            stage=1,
            context="INFO:NAM1"
        ),
        TranslationEntry(
            id="BOOK_Intro:67890|1~BOOK:DESC",
            key="BOOK_Intro:67890|1~BOOK:DESC",
            original="Welcome",
            translation="欢迎",
            stage=1,
            context="BOOK:DESC"
        )
    ]
    collection = TranslationEntryCollection(entries)

    # 创建写入器并应用翻译
    writer = EETWriter(parser)
    updated = writer.apply_collection(collection)

    # 验证更新数量
    assert updated == 2

    # 验证 XML 内容已更新
    root = parser._root
    esp_elements = root.findall("ESP")

    # 验证第一个条目
    esp1 = esp_elements[0]
    assert esp1.findtext("TRADUIT") == "你好"
    assert esp1.findtext("STATUS") == "99"  # stage=1

    # 验证第二个条目
    esp2 = esp_elements[1]
    assert esp2.findtext("TRADUIT") == "欢迎"
    assert esp2.findtext("STATUS") == "99"  # stage=1


def test_apply_collection_no_match():
    """测试应用翻译集合但无匹配项"""
    # 创建测试 XML
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
<DocumentElement>
  <ESP>
    <GRUP>INFO</GRUP>
    <ID>12345</ID>
    <EDID>NPC_John</EDID>
    <CHAMP>NAM1</CHAMP>
    <ORIGINAL>Hello</ORIGINAL>
    <TRADUIT></TRADUIT>
    <STATUS>0</STATUS>
    <PERSO></PERSO>
    <INDEX></INDEX>
    <IDSTEXTE></IDSTEXTE>
    <COMMENTAIRE></COMMENTAIRE>
    <ICON></ICON>
  </ESP>
</DocumentElement>"""

    # 创建模拟解析器
    parser = create_mock_parser(xml_content)

    # 创建不匹配的翻译集合
    entries = [
        TranslationEntry(
            id="NPC_Mary:54321",  # 不同的 ID
            key="INFO:NAM1",
            original="Hello",
            translation="你好",
            stage=1,
            context=None
        )
    ]
    collection = TranslationEntryCollection(entries)

    # 创建写入器并应用翻译
    writer = EETWriter(parser)
    updated = writer.apply_collection(collection)

    # 验证没有更新
    assert updated == 0

    # 验证 XML 内容未更新
    root = parser._root
    esp_element = root.find("ESP")
    assert esp_element.findtext("TRADUIT") == ""


def test_write():
    """测试写入 XML 文件"""
    # 创建测试 XML
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
<DocumentElement>
  <ESP>
    <GRUP>INFO</GRUP>
    <ID>12345</ID>
    <EDID>NPC_John</EDID>
    <CHAMP>NAM1</CHAMP>
    <ORIGINAL>Hello</ORIGINAL>
    <TRADUIT></TRADUIT>
    <STATUS>0</STATUS>
    <PERSO></PERSO>
    <INDEX></INDEX>
    <IDSTEXTE></IDSTEXTE>
    <COMMENTAIRE></COMMENTAIRE>
    <ICON></ICON>
  </ESP>
</DocumentElement>"""

    # 创建模拟解析器
    parser = create_mock_parser(xml_content)

    # 修改内容
    root = parser._root
    esp_element = root.find("ESP")
    traduit_element = esp_element.find("TRADUIT")
    traduit_element.text = "你好"

    # 写入临时文件
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        try:
            writer = EETWriter(parser)
            writer.write(tmp.name)

            # 验证文件内容
            tree = ET.parse(tmp.name)
            root = tree.getroot()
            esp_element = root.find("ESP")
            assert esp_element.findtext("TRADUIT") == "你好"
        finally:
            try:
                Path(tmp.name).unlink()
            except PermissionError:
                # Windows 上临时文件可能无法立即删除，忽略此错误
                pass


def test_write_with_path_object():
    """测试使用 Path 对象作为输出路径"""
    # 创建测试 XML
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
<DocumentElement>
  <ESP>
    <GRUP>INFO</GRUP>
    <ID>12345</ID>
    <EDID>NPC_John</EDID>
    <CHAMP>NAM1</CHAMP>
    <ORIGINAL>Hello</ORIGINAL>
    <TRADUIT></TRADUIT>
    <STATUS>0</STATUS>
    <PERSO></PERSO>
    <INDEX></INDEX>
    <IDSTEXTE></IDSTEXTE>
    <COMMENTAIRE></COMMENTAIRE>
    <ICON></ICON>
  </ESP>
</DocumentElement>"""

    # 创建模拟解析器
    parser = create_mock_parser(xml_content)

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_output.xml"

        writer = EETWriter(parser)
        writer.write(output_path)

        # 验证文件存在
        assert output_path.exists()

        # 验证内容
        tree = ET.parse(output_path)
        root = tree.getroot()
        assert root.tag == "DocumentElement"
