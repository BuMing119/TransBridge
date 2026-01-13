from pathlib import Path
import xml.etree.ElementTree as ET

from src.transbridge.converter.translation_entry import TranslationEntry
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
from src.transbridge.parser.eet_parser import EET_XmlParser


class EETWriter:
    """
    根据 TranslationEntryCollection 更新 EET XML 内容。
    """

    def __init__(self, parser: EET_XmlParser):
        self.parser = parser
        self.tree: ET.ElementTree = parser._tree
        self.root: ET.Element = self.tree.getroot()

    def apply_collection(self, collection: TranslationEntryCollection) -> int:
        """
        更新翻译文本（<TRADUIT>）与状态（<STATUS>）。
        返回成功更新的条数。
        """
        updated = 0

        for esp in self.root.findall(".//ESP"):
            edid = esp.findtext("EDID", "").strip()
            grup = esp.findtext("GRUP", "").strip()
            champ = esp.findtext("CHAMP", "").strip()

            entry_id = edid
            entry_key = f"{grup}:{champ}"

            entry = collection.get(entry_id)
            if not entry:
                continue
            # 注意：现在原来的key值存储在context中
            if entry.context != entry_key:
                continue

            # 更新 TRADUIT
            trad_node = esp.find("TRADUIT")
            if trad_node is not None:
                trad_node.text = entry.translation or ""

            # 更新 STATUS
            status_node = esp.find("STATUS")
            if status_node is not None:
                status_node.text = "99" if entry.stage == 1 else "0"

            updated += 1

        return updated

    def write(self, path: str | Path):
        """
        保存更新后的 XML。
        """
        self.tree.write(path, encoding="utf-8", xml_declaration=True)
