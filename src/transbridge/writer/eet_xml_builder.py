import xml.etree.ElementTree as ET
from pathlib import Path
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection

class EETBuilder:

    @staticmethod
    def build(collection: TranslationEntryCollection, output: str | Path) -> None:
        root = ET.Element("DocumentElement")

        for entry in collection:
            esp = ET.SubElement(root, "ESP")

            # 注意：现在原来的key值存储在context中
            grup, champ = entry.context.split(":")
            editor, formid = entry.id.split(":")

            def add(tag, text):
                ET.SubElement(esp, tag).text = text

            add("GRUP", grup)
            add("ID", formid)              # 可选策略
            add("EDID", editor)
            add("CHAMP", champ)
            add("ORIGINAL", entry.original)
            add("TRADUIT", entry.translation)
            add("STATUS", "99" if entry.stage == 1 else "0")

            # 其他字段给默认值
            add("PERSO", "")
            add("INDEX", "")
            add("IDSTEXTE", "")
            add("COMMENTAIRE", "")
            add("ICON", "")

        tree = ET.ElementTree(root)
        tree.write(output, encoding="utf-8", xml_declaration=True)
