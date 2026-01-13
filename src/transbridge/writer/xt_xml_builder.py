import xml.etree.ElementTree as ET
from pathlib import Path
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection


class XTBuilder:
    """
    从 TranslationEntryCollection 生成新的 XT XML 文件
    """

    @staticmethod
    def build(collection: TranslationEntryCollection, output: str | Path) -> None:
        root = ET.Element("SSTXMLRessources")

        # 默认 params，可扩展
        params = ET.SubElement(root, "Params")
        ET.SubElement(params, "Addon").text = ""
        ET.SubElement(params, "Filename").text = ""

        content = ET.SubElement(root, "Content")

        for entry in collection:
            id_left, _, id_right = entry.id.partition(":")
            # 注意：现在原来的key值存储在context中
            rec = entry.context
            src = entry.original
            dest = entry.translation

            # list_id = 0 (“editorID 匹配左侧”)
            s0 = ET.SubElement(content, "String", {"List": "0"})
            XTBuilder._fill_string_node(s0, id_left, rec, src, dest)

            # list_id = 1 (“formID 匹配右侧 + []”)
            s1 = ET.SubElement(content, "String", {"List": "1"})
            XTBuilder._fill_string_node(s1, f"[{id_right}]", rec, src, dest)

        tree = ET.ElementTree(root)
        tree.write(output, encoding="utf-8", xml_declaration=True)

    @staticmethod
    def _fill_string_node(node, edid, rec, src, dest):
        ET.SubElement(node, "EDID").text = edid
        ET.SubElement(node, "REC").text = rec
        ET.SubElement(node, "Source").text = src
        ET.SubElement(node, "Dest").text = dest or ""
