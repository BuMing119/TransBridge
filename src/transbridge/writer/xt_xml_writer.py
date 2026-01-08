from pathlib import Path
import xml.etree.ElementTree as ET

from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
from src.transbridge.parser.xt_parser import XT_XmlParser


class XTWriter:
    """
    根据 TranslationEntryCollection 更新 XT XML 内容。
    """

    def __init__(self, parser: XT_XmlParser):
        self.parser = parser
        self.tree: ET.ElementTree = parser._tree
        self.root: ET.Element = self.tree.getroot()

    def apply_collection(self, collection: TranslationEntryCollection) -> int:
        updated = 0

        for string in self.root.findall(".//Content/String"):
            list_id = int(string.attrib.get("List", 0))
            edid = string.findtext("EDID", "").strip()
            rec = string.findtext("REC", "").strip()

            for entry in collection:
                id_left, _, id_right = entry.id.partition(":")

                match1 = (list_id == 0 and edid == id_left)
                match2 = (list_id == 1 and edid == f"[{id_right}]")

                if not (match1 or match2):
                    continue
                if rec != entry.key:
                    continue

                dest_node = string.find("Dest")
                if dest_node is not None:
                    dest_node.text = entry.translation or ""
                    updated += 1

        return updated

    def write(self, path: str | Path):
        self.tree.write(path, encoding="utf-8", xml_declaration=True)
