# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Conservative XML proposals independent of the target's physical entry IDs."""

from collections import defaultdict
import json
import re
import xml.etree.ElementTree as ET

from transbridge.converter.eet_matching import known_editor_id
from transbridge.converter.sst_matching import target_locator
from transbridge.parser.eet_parser import EET_Entry

from .contracts import FormatId
from .migration_import import MigrationImportError


def xml_translation_proposals(content, format_id, target):
    """Match field, original and index; never choose among duplicate records."""
    try:
        root = ET.fromstring(content)
        sources = defaultdict(list)
        if format_id is FormatId.XML_EET:
            if root.tag != "DocumentElement":
                raise ValueError("不是 ESP-ESM Translator XML")
            for node in root.findall("ESP"):
                item = EET_Entry.from_xml(node)
                key = (int(item.id, 16), item.grup + item.champ, item.index or 1, item.original)
                sources[key].append((item.traduit, item.edid.casefold()))
        else:
            if root.tag != "SSTXMLRessources":
                raise ValueError("不是 xTranslator XML")
            for node in root.findall(".//Content/String"):
                rec = node.find("REC")
                index = int(rec.get("id", "0")) + 1 if rec is not None else 1
                key = (
                    node.findtext("EDID", "").strip().casefold(),
                    node.findtext("REC", "").strip().replace(":", ""),
                    index,
                    node.findtext("Source", ""),
                )
                sources[key].append((node.findtext("Dest", ""), ""))
    except (ET.ParseError, ValueError, TypeError) as exc:
        raise MigrationImportError("IMPORT_XML_INVALID", f"XML 译文文件无法完整解析：{exc}") from exc

    matches = defaultdict(set)
    targets = {entry.identity: entry for entry in target}
    for entry in target:
        for key in _target_keys(entry, format_id):
            if key in sources:
                matches[key].add(entry.identity)
    proposals = {}
    for key, entries in matches.items():
        records = sources[key]
        if len(entries) != 1 or len(records) != 1:
            continue
        identity = next(iter(entries))
        text, editor_id = records[0]
        known = _target_editor_id(targets[identity])
        if not text or editor_id and known and editor_id != known:
            continue
        if identity in proposals and proposals[identity] != (text, 1):
            raise MigrationImportError("IMPORT_XML_CONFLICT", "多个 XML 记录提供了不同译文，未执行导入。")
        proposals[identity] = (text, 1)
    return proposals


def _target_editor_id(entry):
    known = known_editor_id(entry)
    if known:
        return known
    if "~" in entry.id:
        value = entry.id.partition(":")[0]
        return "" if value in {"None", ""} else value.casefold()
    return ""


def _target_keys(entry, format_id):
    metadata = dict(entry.metadata)
    locator = target_locator(entry)
    signature = locator[1] if locator else (entry.context or "").split("|", 1)[0].replace(":", "")
    index = metadata.get("eet.index")
    if index is None:
        match = re.search(r"\|(\d+)~", entry.id)
        index = int(match[1]) if match else entry.dsd_index
    if format_id is FormatId.XML_EET:
        return {(locator[0], signature, index, entry.original)} if locator else set()
    if metadata.get("io.format") == "xml.xt":
        try:
            marker, _list, edid, rec, index = json.loads(metadata.get("io.locator", entry.id))
        except (ValueError, TypeError):
            return set()
        return {(str(edid).casefold(), rec.replace(":", ""), index, entry.original)} if marker == "xt" else set()
    aliases = {_target_editor_id(entry)}
    if locator:
        aliases.update({f"{locator[0]:08x}", f"[{locator[0]:08x}]"})
    return {(alias, signature, index, entry.original) for alias in aliases}
