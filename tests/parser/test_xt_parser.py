import csv
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
from xml.etree.ElementTree import Element, SubElement

import pytest

from transbridge.parser.xt import XT_Entry, XT_XmlParser


# 创建测试用的 XML 文件
def create_test_xml_file():
    root = Element("SSTXMLRessources")

    # 添加 Params 节点
    params = SubElement(root, "Params")
    addon = SubElement(params, "Addon")
    addon.text = "TestAddon"
    version = SubElement(params, "Version")
    version.text = "1.0"

    # 添加 Content 节点
    content = SubElement(root, "Content")

    # 添加第一条 String 记录
    string1 = SubElement(content, "String", {"List": "0"})
    edid1 = SubElement(string1, "EDID")
    edid1.text = "test_edid_1"
    rec1 = SubElement(string1, "REC")
    rec1.text = "test_rec_1"
    source1 = SubElement(string1, "Source")
    source1.text = "test_source_1"
    dest1 = SubElement(string1, "Dest")
    dest1.text = "test_dest_1"

    # 添加第二条 String 记录
    string2 = SubElement(content, "String", {"List": "1"})
    edid2 = SubElement(string2, "EDID")
    edid2.text = "test_edid_2"
    rec2 = SubElement(string2, "REC")
    rec2.text = "test_rec_2"
    source2 = SubElement(string2, "Source")
    source2.text = "test_source_2"
    dest2 = SubElement(string2, "Dest")
    dest2.text = "test_dest_2"

    # 添加第三条 String 记录（与第一条 EDID 相同）
    string3 = SubElement(content, "String", {"List": ""})  # 空的 List 属性
    edid3 = SubElement(string3, "EDID")
    edid3.text = "test_edid_1"  # 与第一条 EDID 相同
    rec3 = SubElement(string3, "REC")
    rec3.text = "test_rec_3"
    source3 = SubElement(string3, "Source")
    source3.text = "test_source_3"
    dest3 = SubElement(string3, "Dest")
    dest3.text = "test_dest_3"

    # 使用 ElementTree 的 tostring 方法，并添加 XML 声明
    tree = ET.ElementTree(root)
    # 使用二进制模式打开文件
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml", delete=False) as f:
        tree.write(f, encoding="utf-8", xml_declaration=True)
        return f.name


# 测试 XML 文件 fixture
@pytest.fixture
def test_xml_file():
    """创建一个临时 XML 文件用于测试"""
    xml_path = create_test_xml_file()
    yield xml_path
    # 测试结束后删除临时文件
    Path(xml_path).unlink()


# 测试 XT_Entry 数据类
class TestXTEntry:
    def test_to_int_with_valid_value(self):
        assert XT_Entry._to_int("123") == 123
        assert XT_Entry._to_int("0") == 0

    def test_to_int_with_invalid_value(self):
        assert XT_Entry._to_int(None) is None
        assert XT_Entry._to_int("") is None
        assert XT_Entry._to_int("abc") is None
        assert XT_Entry._to_int("  ") is None  # 只有空格

    def test_entry_creation(self):
        entry = XT_Entry(list_id=1, edid="test_edid", rec="test_rec", source="test_source", dest="test_dest")
        assert entry.list_id == 1
        assert entry.edid == "test_edid"
        assert entry.rec == "test_rec"
        assert entry.source == "test_source"
        assert entry.dest == "test_dest"


# 测试 XT_XmlParser 类
class TestXTXmlParser:
    def test_from_file(self, test_xml_file):
        """测试从文件创建 XT_XmlParser 实例"""
        parser = XT_XmlParser.from_file(test_xml_file)

        # 检查参数
        assert parser.params == {"Addon": "TestAddon", "Version": "1.0"}

        # 检查条目数量
        assert len(parser.entries) == 3

        # 检查第一条条目
        assert parser.entries[0].list_id == 0
        assert parser.entries[0].edid == "test_edid_1"
        assert parser.entries[0].rec == "test_rec_1"
        assert parser.entries[0].source == "test_source_1"
        assert parser.entries[0].dest == "test_dest_1"

        # 检查第二条条目
        assert parser.entries[1].list_id == 1
        assert parser.entries[1].edid == "test_edid_2"

        # 检查第三条条目（List 属性为空）
        assert parser.entries[2].list_id is None

    def test_parse_params(self, test_xml_file):
        """测试解析 XML 参数"""
        params = XT_XmlParser._parse_params(test_xml_file)
        assert params == {"Addon": "TestAddon", "Version": "1.0"}

    def test_iter_entries(self, test_xml_file):
        """测试流式解析条目"""
        entries = list(XT_XmlParser._iter_entries(test_xml_file))
        assert len(entries) == 3

        # 检查第一条条目
        assert entries[0].list_id == 0
        assert entries[0].edid == "test_edid_1"

        # 检查第三条条目（List 属性为空）
        assert entries[2].list_id is None

    def test_get_by_edid(self, test_xml_file):
        """测试按 EDID 查询条目"""
        parser = XT_XmlParser.from_file(test_xml_file)

        # 查询存在的 EDID（有多个匹配）
        entries = parser.get_by_edid("test_edid_1")
        assert len(entries) == 2
        assert entries[0].edid == "test_edid_1"
        assert entries[1].edid == "test_edid_1"

        # 查询存在的 EDID（只有一个匹配）
        entries = parser.get_by_edid("test_edid_2")
        assert len(entries) == 1
        assert entries[0].edid == "test_edid_2"

        # 查询不存在的 EDID
        entries = parser.get_by_edid("nonexistent_edid")
        assert len(entries) == 0

    def test_find(self, test_xml_file):
        """测试按自定义条件过滤条目"""
        parser = XT_XmlParser.from_file(test_xml_file)

        # 查找 list_id 为 0 的条目
        entries = parser.find(lambda e: e.list_id == 0)
        assert len(entries) == 1
        assert entries[0].list_id == 0

        # 查找 edid 以 "1" 结尾的条目
        entries = parser.find(lambda e: e.edid.endswith("1"))
        assert len(entries) == 2

        # 查找不满足条件的条目
        entries = parser.find(lambda e: e.list_id == 99)
        assert len(entries) == 0

    def test_iter(self, test_xml_file):
        """测试迭代所有条目"""
        parser = XT_XmlParser.from_file(test_xml_file)

        entries = list(parser.iter())
        assert len(entries) == 3
        assert entries[0].edid == "test_edid_1"
        assert entries[1].edid == "test_edid_2"
        assert entries[2].edid == "test_edid_1"

    def test_to_json(self, test_xml_file):
        """测试导出为 JSON 字符串"""
        parser = XT_XmlParser.from_file(test_xml_file)

        json_str = parser.to_json()
        data = json.loads(json_str)

        # 检查参数
        assert data["params"] == {"Addon": "TestAddon", "Version": "1.0"}

        # 检查条目数量
        assert len(data["entries"]) == 3

        # 检查第一条条目
        assert data["entries"][0]["list_id"] == 0
        assert data["entries"][0]["edid"] == "test_edid_1"

    def test_to_json_file(self, test_xml_file):
        """测试导出为 JSON 文件"""
        parser = XT_XmlParser.from_file(test_xml_file)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json_path = f.name

        try:
            parser.to_json_file(json_path)

            # 读取导出的 JSON 文件并验证
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)

            # 检查参数
            assert data["params"] == {"Addon": "TestAddon", "Version": "1.0"}

            # 检查条目数量
            assert len(data["entries"]) == 3

            # 检查第一条条目
            assert data["entries"][0]["list_id"] == 0
            assert data["entries"][0]["edid"] == "test_edid_1"
        finally:
            Path(json_path).unlink()

    def test_to_csv_file(self, test_xml_file):
        """测试导出为 CSV 文件"""
        parser = XT_XmlParser.from_file(test_xml_file)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            csv_path = f.name

        try:
            parser.to_csv_file(csv_path)

            # 读取导出的 CSV 文件并验证
            with open(csv_path, encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            # 检查行数
            assert len(rows) == 3

            # 检查列名
            assert set(rows[0].keys()) == {"list_id", "edid", "rec", "source", "dest", "index"}

            # 检查第一行数据
            assert rows[0]["list_id"] == "0"
            assert rows[0]["edid"] == "test_edid_1"
            assert rows[0]["rec"] == "test_rec_1"
            assert rows[0]["source"] == "test_source_1"
            assert rows[0]["dest"] == "test_dest_1"

            # 检查第三行数据（List 属性为空）
            assert rows[2]["list_id"] == ""
        finally:
            Path(csv_path).unlink()
