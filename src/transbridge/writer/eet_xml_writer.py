from pathlib import Path
import xml.etree.ElementTree as ET

from src.transbridge.converter.translation_entry import (
    TranslationEntry,
    STAGE_TRANSLATED, STAGE_LOCKED, STAGE_HIDDEN,
)
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

        Phase 1：按完整 entry.id 精确查找（需要 EDID/ID/INDEX/GRUP/CHAMP）。
        Phase 2：按 (original, grup:champ) 回退匹配。

        返回成功更新的条数。
        """
        # --- 预构建 Phase 2 回退索引：(original, type_field_base) → entry ---
        fallback_index: dict[tuple[str, str], TranslationEntry] = {}
        for entry in collection:
            if not entry.translation:
                continue
            ctx_base = entry.context.split("|")[0] if entry.context else ""
            fb_key = (entry.original, ctx_base)
            if fb_key not in fallback_index:
                fallback_index[fb_key] = entry

        updated = 0

        for esp in self.root.findall(".//ESP"):
            edid = esp.findtext("EDID", "").strip()
            grup = esp.findtext("GRUP", "").strip()
            champ = esp.findtext("CHAMP", "").strip()
            form_id = esp.findtext("ID", "").strip()
            original = esp.findtext("ORIGINAL", "") or ""

            index_text = esp.findtext("INDEX", "").strip()
            try:
                index = int(index_text) if index_text else None
            except ValueError:
                index = None

            type_field = f"{grup}:{champ}"

            # Phase 1：精确 id 匹配
            full_id = TranslationEntry._build_eet_id(edid, form_id, index, grup, champ)
            entry = collection.get(full_id)
            if entry is None:
                # Phase 2：(original, type_field) 回退
                entry = fallback_index.get((original, type_field))

            if entry is None or not entry.translation:
                continue

            # 确定写回策略
            if entry.stage == STAGE_HIDDEN:
                # 已隐藏：强制原文，不写译文
                should_translate = False
                status = "0"
            elif entry.stage == STAGE_LOCKED:
                # 已锁定：强制译文
                should_translate = True
                status = "99"
            elif entry.stage >= STAGE_TRANSLATED and entry.translation:
                # 正常有译文
                should_translate = True
                status = "99"
            else:
                # 未翻译或无译文
                should_translate = False
                status = "0"

            if should_translate:
                trad_node = esp.find("TRADUIT")
                if trad_node is not None:
                    trad_node.text = entry.translation

            status_node = esp.find("STATUS")
            if status_node is not None:
                status_node.text = status

            updated += 1

        return updated

    def write(self, path: str | Path):
        """
        保存更新后的 XML。
        """
        self.tree.write(path, encoding="utf-8", xml_declaration=True)
