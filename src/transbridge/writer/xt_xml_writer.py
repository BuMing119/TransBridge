from collections import Counter
import logging
from pathlib import Path
import xml.etree.ElementTree as ET

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.xt import XT_XmlParser


class XTWriter:
    """
    根据 TranslationEntryCollection 更新 XT XML 内容。
    """

    def __init__(self, parser: XT_XmlParser):
        self.parser = parser
        self.tree: ET.ElementTree = parser._tree
        self.root: ET.Element = self.tree.getroot()

    def apply_collection(self, collection: TranslationEntryCollection) -> int:
        """
        更新 <Dest> 节点内容。

        Phase 1：edid 匹配（候选：editid / bare formid / [formid]）+ rec/source 校验。
        Phase 2：按 (source, rec) 回退匹配。

        返回成功更新的条数。
        """
        # --- 预构建集合索引，避免 O(n²) ---
        # Phase 1 索引：edid → list[entry]（editid / bare formid / [formid] 三条路）
        by_editid: dict[str, list[TranslationEntry]] = {}
        by_formid: dict[str, list[TranslationEntry]] = {}
        by_bracket_formid: dict[str, list[TranslationEntry]] = {}
        for entry in collection:
            left, _, rest = entry.id.partition(":")
            form_id = rest.partition("|")[0]
            by_editid.setdefault(left, []).append(entry)
            by_formid.setdefault(form_id, []).append(entry)
            by_bracket_formid.setdefault(f"[{form_id}]", []).append(entry)

        # Phase 2 回退索引：(source, rec) → entry（只保留有译文的）
        fallback_index: dict[tuple[str, str], TranslationEntry] = {}
        for entry in collection:
            if not entry.translation or entry.requires_original_match:
                continue
            ctx_base = entry.context.split("|")[0] if entry.context else ""
            fb_key = (entry.original, ctx_base)
            if fb_key not in fallback_index:
                fallback_index[fb_key] = entry

        updated = 0

        nodes = self.root.findall(".//Content/String")
        node_counts = Counter(_node_identity(node) for node in nodes)
        for string in nodes:
            edid = string.findtext("EDID", "").strip()
            rec = string.findtext("REC", "").strip()
            source = string.findtext("Source", "") or ""

            candidates_by_identity = {
                item.identity: item
                for bucket in (by_editid.get(edid, ()), by_formid.get(edid, ()), by_bracket_formid.get(edid, ()))
                for item in bucket
            }
            if any(item.requires_original_match for item in candidates_by_identity.values()):
                locator = _node_identity(string)
                matches = [
                    item
                    for item in candidates_by_identity.values()
                    if item.original == source
                    and (item.context.split("|")[0] if item.context else "") == rec
                    and _entry_index(item) == locator[2]
                    and locator[2] is not None
                ]
                if len(matches) != 1 or node_counts[locator] != 1:
                    logging.getLogger(__name__).warning(
                        "SOURCE_ORIGINAL_MATCH_REQUIRED: XT 词条 %s 无法由索引和原文唯一定位，已跳过。", edid
                    )
                    continue
                entry = matches[0]
                dest_node = string.find("Dest")
                if entry.translation and dest_node is not None:
                    dest_node.text = entry.translation
                    updated += 1
                continue

            # Phase 1：从三种 edid 候选桶中找匹配的 entry
            entry = None
            for candidates in (
                by_editid.get(edid, []),
                by_formid.get(edid, []),
                by_bracket_formid.get(edid, []),
            ):
                for e in candidates:
                    # rec 与 context 基础部分比较（INFO/DIAL context 含 |quest 后缀）
                    ctx_base = e.context.split("|")[0] if e.context else ""
                    if rec == ctx_base and source == e.original:
                        entry = e
                        break
                if entry is not None:
                    break

            # Phase 2：(source, rec) 回退
            if entry is None:
                entry = fallback_index.get((source, rec))

            if entry is None or not entry.translation:
                continue

            dest_node = string.find("Dest")
            if dest_node is not None:
                dest_node.text = entry.translation
                updated += 1

        return updated

    def write(self, path: str | Path):
        self.tree.write(path, encoding="utf-8", xml_declaration=True)


def _entry_index(entry: TranslationEntry) -> int | None:
    index = entry.id.partition("|")[2].partition("~")[0]
    try:
        return int(index)
    except ValueError:
        return None


def _node_identity(node: ET.Element) -> tuple[str, str, int | None, str]:
    rec = node.find("REC")
    try:
        index = int(rec.get("id", "0")) + 1 if rec is not None else 1
    except ValueError:
        index = None
    return (
        (node.findtext("EDID", "") or "").strip(),
        (node.findtext("REC", "") or "").strip(),
        index,
        node.findtext("Source", "") or "",
    )
