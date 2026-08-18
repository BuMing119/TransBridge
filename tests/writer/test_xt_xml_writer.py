
import pytest
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.xt import XT_XmlParser
from transbridge.writer.xt_xml_writer import XTWriter


def create_test_xml(content):
    """创建测试用的 XML 文件并返回路径"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
        f.write(content)
        return f.name


def create_mock_parser(xml_content):
    """创建模拟的 XT_XmlParser 对象"""
    # 解析 XML
    root = ET.fromstring(xml_content)
    tree = ET.ElementTree(root)

    # 创建模拟的解析器
    parser = Mock(spec=XT_XmlParser)
    parser._tree = tree
    parser._root = root

    return parser


def test_apply_collection_updates():
    """测试应用翻译集合更新 XML"""
    # 创建测试 XML
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
<SSTXMLRessources>
    <Params>
        <Addon></Addon>
        <Filename></Filename>
    </Params>
    <Content>
        <String List="0">
            <EDID>NPC_John</EDID>
            <REC>INFO:NAM1</REC>
            <Source>Hello</Source>
            <Dest></Dest>
        </String>
        <String List="1">
            <EDID>[12345]</EDID>
            <REC>INFO:NAM1</REC>
            <Source>Hello</Source>
            <Dest></Dest>
        </String>
        <String List="0">
            <EDID>BOOK_Intro</EDID>
            <REC>DESC</REC>
            <Source>Welcome</Source>
            <Dest></Dest>
        </String>
        <String List="1">
            <EDID>[67890]</EDID>
            <REC>DESC</REC>
            <Source>Welcome</Source>
            <Dest></Dest>
        </String>
    </Content>
</SSTXMLRessources>"""

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
            id="BOOK_Intro:67890|1~DESC",
            key="BOOK_Intro:67890|1~DESC",
            original="Welcome",
            translation="欢迎",
            stage=0,
            context="DESC"
        )
    ]
    collection = TranslationEntryCollection(entries)

    # 创建写入器并应用翻译
    writer = XTWriter(parser)
    updated = writer.apply_collection(collection)

    # 验证更新数量
    assert updated == 4  # 每个条目有两个 String 元素，所以是 4 个更新

    # 验证 XML 内容已更新
    root = parser._root
    string_elements = root.findall(".//Content/String")

    # 验证第一个条目的 List=0 元素
    s0 = string_elements[0]
    assert s0.attrib.get("List") == "0"
    assert s0.findtext("EDID") == "NPC_John"
    assert s0.findtext("REC") == "INFO:NAM1"
    assert s0.findtext("Source") == "Hello"
    assert s0.findtext("Dest") == "你好"

    # 验证第一个条目的 List=1 元素
    s1 = string_elements[1]
    assert s1.attrib.get("List") == "1"
    assert s1.findtext("EDID") == "[12345]"
    assert s1.findtext("REC") == "INFO:NAM1"
    assert s1.findtext("Source") == "Hello"
    assert s1.findtext("Dest") == "你好"

    # 验证第二个条目的 List=0 元素
    s2 = string_elements[2]
    assert s2.attrib.get("List") == "0"
    assert s2.findtext("EDID") == "BOOK_Intro"
    assert s2.findtext("REC") == "DESC"
    assert s2.findtext("Source") == "Welcome"
    assert s2.findtext("Dest") == "欢迎"

    # 验证第二个条目的 List=1 元素
    s3 = string_elements[3]
    assert s3.attrib.get("List") == "1"
    assert s3.findtext("EDID") == "[67890]"
    assert s3.findtext("REC") == "DESC"
    assert s3.findtext("Source") == "Welcome"
    assert s3.findtext("Dest") == "欢迎"


def test_apply_collection_no_match():
    """测试应用翻译集合但无匹配项"""
    # 创建测试 XML
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
<SSTXMLRessources>
    <Params>
        <Addon></Addon>
        <Filename></Filename>
    </Params>
    <Content>
        <String List="0">
            <EDID>NPC_John</EDID>
            <REC>INFO:NAM1</REC>
            <Source>Hello</Source>
            <Dest></Dest>
        </String>
    </Content>
</SSTXMLRessources>"""

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
    writer = XTWriter(parser)
    updated = writer.apply_collection(collection)

    # 验证没有更新
    assert updated == 0

    # 验证 XML 内容未更新
    root = parser._root
    string_element = root.find(".//Content/String")
    assert string_element.findtext("Dest") == ""


def test_apply_collection_partial_match():
    """测试部分匹配的情况"""
    # 创建测试 XML
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
<SSTXMLRessources>
    <Params>
        <Addon></Addon>
        <Filename></Filename>
    </Params>
    <Content>
        <String List="0">
            <EDID>NPC_John</EDID>
            <REC>INFO:NAM1</REC>
            <Source>Hello</Source>
            <Dest></Dest>
        </String>
        <String List="1">
            <EDID>[12345]</EDID>
            <REC>INFO:NAM1</REC>
            <Source>Hello</Source>
            <Dest></Dest>
        </String>
        <String List="0">
            <EDID>BOOK_Intro</EDID>
            <REC>DESC</REC>
            <Source>Welcome</Source>
            <Dest></Dest>
        </String>
    </Content>
</SSTXMLRessources>"""

    # 创建模拟解析器
    parser = create_mock_parser(xml_content)

    # 创建部分匹配的翻译集合
    entries = [
        TranslationEntry(
            id="NPC_John:12345|1~INFO:NAM1",
            key="NPC_John:12345|1~INFO:NAM1",
            original="Hello",
            translation="你好",
            stage=1,
            context="INFO:NAM1"
        )
        # 没有提供 BOOK_Intro 的翻译
    ]
    collection = TranslationEntryCollection(entries)

    # 创建写入器并应用翻译
    writer = XTWriter(parser)
    updated = writer.apply_collection(collection)

    # 验证更新数量
    assert updated == 2  # 只有 NPC_John 的两个 String 元素被更新

    # 验证 XML 内容部分更新
    root = parser._root
    string_elements = root.findall(".//Content/String")

    # 验证 NPC_John 的 String 元素已更新
    assert string_elements[0].findtext("Dest") == "你好"
    assert string_elements[1].findtext("Dest") == "你好"

    # 验证 BOOK_Intro 的 String 元素未更新
    assert string_elements[2].findtext("Dest") == ""


def test_write():
    """测试写入 XML 文件"""
    # 创建测试 XML
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
<SSTXMLRessources>
    <Params>
        <Addon></Addon>
        <Filename></Filename>
    </Params>
    <Content>
        <String List="0">
            <EDID>NPC_John</EDID>
            <REC>INFO:NAM1</REC>
            <Source>Hello</Source>
            <Dest></Dest>
        </String>
    </Content>
</SSTXMLRessources>"""

    # 创建模拟解析器
    parser = create_mock_parser(xml_content)

    # 修改内容
    root = parser._root
    string_element = root.find(".//Content/String")
    dest_element = string_element.find("Dest")
    dest_element.text = "你好"

    # 写入临时文件
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        try:
            writer = XTWriter(parser)
            writer.write(tmp.name)

            # 验证文件内容
            tree = ET.parse(tmp.name)
            root = tree.getroot()
            string_element = root.find(".//Content/String")
            assert string_element.findtext("Dest") == "你好"
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
<SSTXMLRessources>
    <Params>
        <Addon></Addon>
        <Filename></Filename>
    </Params>
    <Content>
        <String List="0">
            <EDID>NPC_John</EDID>
            <REC>INFO:NAM1</REC>
            <Source>Hello</Source>
            <Dest></Dest>
        </String>
    </Content>
</SSTXMLRessources>"""

    # 创建模拟解析器
    parser = create_mock_parser(xml_content)

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_output.xml"

        writer = XTWriter(parser)
        writer.write(output_path)

        # 验证文件存在
        assert output_path.exists()

        # 验证内容
        tree = ET.parse(output_path)
        root = tree.getroot()
        assert root.tag == "SSTXMLRessources"


def test_apply_collection_with_none_translation():
    """测试翻译为 None 的情况"""
    # 创建测试 XML
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
<SSTXMLRessources>
    <Params>
        <Addon></Addon>
        <Filename></Filename>
    </Params>
    <Content>
        <String List="0">
            <EDID>NPC_John</EDID>
            <REC>INFO:NAM1</REC>
            <Source>Hello</Source>
            <Dest></Dest>
        </String>
    </Content>
</SSTXMLRessources>"""

    # 创建模拟解析器
    parser = create_mock_parser(xml_content)

    # 创建翻译为 None 的集合
    entries = [
        TranslationEntry(
            id="NPC_John:12345|1~INFO:NAM1",
            key="NPC_John:12345|1~INFO:NAM1",
            original="Hello",
            translation=None,  # 翻译为 None
            stage=0,
            context="INFO:NAM1"
        )
    ]
    collection = TranslationEntryCollection(entries)

    # 创建写入器并应用翻译
    writer = XTWriter(parser)
    updated = writer.apply_collection(collection)

    # 验证更新数量（translation 为 None 的条目被跳过）
    assert updated == 0

    # 验证 XML 内容已更新为空字符串
    root = parser._root
    string_element = root.find(".//Content/String")
    assert string_element.findtext("Dest") == ""
