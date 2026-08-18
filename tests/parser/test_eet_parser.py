import pytest
from pathlib import Path
from xml.etree.ElementTree import Element
from transbridge.parser.eet_parser import EET_Entry, EET_XmlParser, _text_or_empty, _int_or_none


class TestHelperFunctions:
    """测试辅助函数"""

    def test_text_or_empty_with_none(self):
        assert _text_or_empty(None) == ""

    def test_text_or_empty_with_text(self):
        elem = Element("TAG")
        elem.text = "test content"
        assert _text_or_empty(elem) == "test content"

    def test_text_or_empty_with_empty_element(self):
        elem = Element("TAG")
        assert _text_or_empty(elem) == ""

    def test_int_or_none_with_valid_int(self):
        assert _int_or_none("42") == 42

    def test_int_or_none_with_whitespace(self):
        assert _int_or_none("  42  ") == 42

    def test_int_or_none_with_empty_string(self):
        assert _int_or_none("") is None

    def test_int_or_none_with_invalid_string(self):
        assert _int_or_none("not a number") is None


class TestEETEntry:
    """测试 EET_Entry 数据类"""

    def test_from_xml(self):
        xml_str = """
        <ESP>
            <GRUP>TestGroup</GRUP>
            <ID>TestId</ID>
            <EDID>TestEdid</EDID>
            <CHAMP>TestChamp</CHAMP>
            <ORIGINAL>Original text</ORIGINAL>
            <TRADUIT>Translated text</TRADUIT>
            <PERSO>Personal translation</PERSO>
            <INDEX>1</INDEX>
            <STATUS>2</STATUS>
            <IDSTEXTE>3</IDSTEXTE>
            <COMMENTAIRE>Test comment</COMMENTAIRE>
            <ICON>4</ICON>
        </ESP>
        """
        elem = Element("ESP")
        elem.text = ""
        from xml.etree.ElementTree import fromstring
        elem = fromstring(xml_str)

        entry = EET_Entry.from_xml(elem)

        assert entry.grup == "TestGroup"
        assert entry.id == "TestId"
        assert entry.edid == "TestEdid"
        assert entry.champ == "TestChamp"
        assert entry.original == "Original text"
        assert entry.traduit == "Translated text"
        assert entry.perso == "Personal translation"
        assert entry.index == 1
        assert entry.status == 2
        assert entry.idstexte == 3
        assert entry.commentaire == "Test comment"
        assert entry.icon == 4

    def test_key_property(self):
        entry = EET_Entry(
            grup="Group1",
            id="ID1",
            edid="EDID1",
            champ="Champ1",
            original="Original",
            traduit="Translated",
            perso="Personal",
            index=1,
            status=2,
            idstexte=3,
            commentaire="Comment",
            icon=4
        )
        assert entry.key == ("Group1", "ID1", "EDID1", "Champ1")

@pytest.fixture
def sample_xml():
    return """<?xml version="1.0" encoding="utf-8"?>
    <DocumentElement>
        <ESP>
            <GRUP>Group1</GRUP>
            <ID>ID1</ID>
            <EDID>EDID1</EDID>
            <CHAMP>Champ1</CHAMP>
            <ORIGINAL>Original text 1</ORIGINAL>
            <TRADUIT>Translated text 1</TRADUIT>
            <PERSO>Personal translation 1</PERSO>
            <INDEX>1</INDEX>
            <STATUS>2</STATUS>
            <IDSTEXTE>3</IDSTEXTE>
            <COMMENTAIRE>Comment 1</COMMENTAIRE>
            <ICON>4</ICON>
        </ESP>
        <ESP>
            <GRUP>Group2</GRUP>
            <ID>ID2</ID>
            <EDID>EDID2</EDID>
            <CHAMP>Champ2</CHAMP>
            <ORIGINAL>Original text 2</ORIGINAL>
            <TRADUIT>Translated text 2</TRADUIT>
            <PERSO>Personal translation 2</PERSO>
            <INDEX>5</INDEX>
            <STATUS>6</STATUS>
            <IDSTEXTE>7</IDSTEXTE>
            <COMMENTAIRE>Comment 2</COMMENTAIRE>
            <ICON>8</ICON>
        </ESP>
        <ESP>
            <GRUP>Group1</GRUP>
            <ID>ID1</ID>
            <EDID>EDID1</EDID>
            <CHAMP>Champ3</CHAMP>
            <ORIGINAL>Original text 3</ORIGINAL>
            <TRADUIT>Translated text 3</TRADUIT>
            <PERSO>Personal translation 3</PERSO>
            <INDEX>9</INDEX>
            <STATUS>10</STATUS>
            <IDSTEXTE>11</IDSTEXTE>
            <COMMENTAIRE>Comment 3</COMMENTAIRE>
            <ICON>12</ICON>
        </ESP>
    </DocumentElement>
    """



class TestEETXmlParser:
    """测试 EET_XmlParser 类"""

    @pytest.fixture


    def test_from_string(self, sample_xml):
        parser = EET_XmlParser.from_string(sample_xml)
        assert len(parser.entries) == 3

    def test_find_by_grup(self, sample_xml):
        parser = EET_XmlParser.from_string(sample_xml)
        results = parser.find(grup="Group1")
        assert len(results) == 2
        assert all(e.grup == "Group1" for e in results)

    def test_find_by_id(self, sample_xml):
        parser = EET_XmlParser.from_string(sample_xml)
        results = parser.find(id="ID1")
        assert len(results) == 2
        assert all(e.id == "ID1" for e in results)

    def test_find_by_multiple_criteria(self, sample_xml):
        parser = EET_XmlParser.from_string(sample_xml)
        results = parser.find(grup="Group1", id="ID1")
        assert len(results) == 2

    def test_find_by_original_contains(self, sample_xml):
        parser = EET_XmlParser.from_string(sample_xml)
        results = parser.find(original_contains="text 1")
        assert len(results) == 1
        assert "text 1" in results[0].original

    def test_get_by_key(self, sample_xml):
        parser = EET_XmlParser.from_string(sample_xml)
        results = parser.get_by_key("Group1", "ID1", "EDID1", "Champ1")
        assert len(results) == 1
        assert results[0].champ == "Champ1"

    def test_get_by_grup(self, sample_xml):
        parser = EET_XmlParser.from_string(sample_xml)
        results = parser.get_by_grup("Group1")
        assert len(results) == 2
        assert all(e.grup == "Group1" for e in results)

    def test_get_by_id(self, sample_xml):
        parser = EET_XmlParser.from_string(sample_xml)
        results = parser.get_by_id("ID1")
        assert len(results) == 2
        assert all(e.id == "ID1" for e in results)

    def test_get_by_edid(self, sample_xml):
        parser = EET_XmlParser.from_string(sample_xml)
        results = parser.get_by_edid("EDID1")
        assert len(results) == 2
        assert all(e.edid == "EDID1" for e in results)

    def test_to_dicts(self, sample_xml):
        parser = EET_XmlParser.from_string(sample_xml)
        dicts = parser.to_dicts()
        assert len(dicts) == 3
        assert all(isinstance(d, dict) for d in dicts)
        assert "GRUP" in dicts[0]
        assert "ID" in dicts[0]
        assert "ORIGINAL" in dicts[0]

    def test_iteration(self, sample_xml):
        parser = EET_XmlParser.from_string(sample_xml)
        entries = list(parser)
        assert len(entries) == 3
        assert all(isinstance(e, EET_Entry) for e in entries)


class TestEETXmlParserFileOperations:
    """测试 EET_XmlParser 文件操作"""

    @pytest.fixture
    def temp_xml_file(self, tmp_path, sample_xml):
        file_path = tmp_path / "test_eet.xml"
        file_path.write_text(sample_xml, encoding="utf-8")
        return file_path

    def test_from_file(self, temp_xml_file):
        parser = EET_XmlParser.from_file(temp_xml_file)
        assert len(parser.entries) == 3
        assert parser._xml_path == temp_xml_file

    def test_from_file_with_encoding(self, temp_xml_file):
        parser = EET_XmlParser.from_file(temp_xml_file, encoding="utf-8")
        assert len(parser.entries) == 3
        assert parser._xml_path == temp_xml_file


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
