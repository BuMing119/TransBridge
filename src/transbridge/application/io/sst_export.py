# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Build SSU9 dictionaries from confirmed Skyrim plugin snapshots.

The binary layout belongs to ``parser.xt.sst_codec``. This module only maps
physical plugin fields to SST identities; it never opens the original path or
publishes a destination. Callers use the normal staged format publication.

Identity rules follow xTranslator commit 9aa38d60860273401f8bb0dd1557c82a295c41b3:
TESVT_espDefinition.pas (EdidName, sanitizeFormID, Compute_FieldIndex),
TESVT_MainLoader.pas (loadEspStrings), and Data/SkyrimSE/_recorddefs.txt.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import astuple, dataclass
from pathlib import Path

from sse_plugin_interface.flags import RecordFlags
from sse_plugin_interface.group import Group
from sse_plugin_interface.plugin import SSEPlugin
from sse_plugin_interface.record import Record
from sse_plugin_interface.subrecord import StringSubrecord

from transbridge.parser.xt.sst_codec import SstDocument, SstRecord, sst_hash
from transbridge.writer.plugin_source import capture_plugin_write_source

from .contracts import FormatId, WriteRequest
from .identity import EntryKey
from .plugin_publication_source import prepared_plugin_source
from .stage_policy import DEFAULT_STAGE_POLICY, StageOperation

PLUGIN_SST_MASTERS = "plugin_sst_masters"
PLUGIN_SST_UNAVAILABLE = "plugin_sst_unavailable"
PLUGIN_SST_LOCATOR = "plugin.sst_locator"
PLUGIN_SST_BLOCKER_CODES = frozenset({
    "SST_EXPORT_SOURCE_INVALID",
    "SST_EXPORT_SOURCE_UNSUPPORTED",
    "SST_EXPORT_MASTER_ENCODING",
    "SST_EXPORT_VMAD_UNSUPPORTED",
    "SOURCE_LOCATOR_NOT_FOUND",
    "SOURCE_LOCATOR_CONFLICT",
})


@dataclass(frozen=True, slots=True)
class PluginSstLocator:
    record_type: str
    field_type: str
    form_id: int
    index: int
    index_max: int
    edid_hash: int
    deleted: bool

    @classmethod
    def from_values(cls, value):
        if not isinstance(value, (list, tuple)) or len(value) != 7:
            raise ValueError("SST physical locator has an invalid shape")
        locator = cls(*value)
        for signature in (locator.record_type, locator.field_type):
            if not isinstance(signature, str) or len(signature) != 4 or len(signature.encode("latin-1")) != 4:
                raise ValueError("SST physical signatures must contain four single-byte characters")
        for number, maximum in (
            (locator.form_id, 0xFFFFFFFF),
            (locator.index, 0xFFFF),
            (locator.index_max, 0xFFFF),
            (locator.edid_hash, 0xFFFFFFFF),
        ):
            if type(number) is not int or not 0 <= number <= maximum:
                raise ValueError("SST physical locator is outside its wire range")
        if type(locator.deleted) is not bool or locator.index > locator.index_max:
            raise ValueError("SST physical locator contains invalid entry state")
        return locator

    def values(self):
        return astuple(self)


class SstExportError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SstExportCancelled(Exception):
    """The caller cancelled before a complete SSU9 document was produced."""


def sanitize_form_id(form_id: int) -> int:
    """Normalize load-order bits exactly as xTranslator's default sanitizeFormID."""
    high = form_id >> 24
    if high == 0:
        return form_id
    if high == 0xFE:
        return form_id if (form_id >> 12) & 0xFF == 0 else (form_id & ~0xFFF000) | 0x1000
    if high == 0xFD:
        return form_id if (form_id >> 16) & 0xFF == 0 else (form_id & ~0xFF0000) | 0x10000
    return (form_id & 0xFFFFFF) | 0x1000000


# Exact Skyrim SE overrides; FULL/DESC are the upstream fallbacks.
_LIST_OVERRIDES = {
    ("MGEF", "DNAM"): 0,
    ("INFO", "NAM1"): 2,
    ("NPC_", "SHRT"): 0,
    ("QUST", "CNAM"): 1,
    ("BOOK", "CNAM"): 1,
    ("WOOP", "TNAM"): 0,
    ("QUST", "NNAM"): 0,
    ("MESG", "ITXT"): 0,
    ("REGN", "RDMP"): 0,
    ("ACTI", "RNAM"): 0,
    ("FLOR", "RNAM"): 0,
    ("INFO", "RNAM"): 0,
    ("BPTD", "BPTN"): 0,
    ("FACT", "MNAM"): 0,
    ("FACT", "FNAM"): 0,
    ("LSCR", "DESC"): 0,
    ("GMST", "DATA"): 0,
    ("PERK", "EPFD"): 0,
    ("PERK", "EPF2"): 0,
}


def _list_index(record_type: str, field_type: str) -> int:
    found = _LIST_OVERRIDES.get((record_type, field_type))
    if found is not None:
        return found
    if field_type in {"FULL", "DESC"}:
        return 0 if field_type == "FULL" else 1
    raise SstExportError(
        "SST_EXPORT_FIELD_UNSUPPORTED",
        f"无法证明 {record_type}:{field_type} 的 xTranslator 文本列表；未生成 SST。",
    )


def _records(groups):
    for group in groups:
        for child in group.children:
            if isinstance(child, Group):
                yield from _records((child,))
            elif isinstance(child, Record):
                yield child


def _positions(record: Record) -> dict[int, tuple[int, int]]:
    """Use physical field order, not the library's quest/response business indices."""
    positions = {id(field): [0, 0] for field in record.subrecords}
    previous = defaultdict(list)
    for field in record.subrecords:
        same = previous[field.type]
        if isinstance(field, StringSubrecord) and same:
            index = positions[id(same[-1])][0] + 1
            positions[id(field)] = [index, index]
            for earlier in same:
                positions[id(earlier)][1] = index
        same.append(field)
    return {key: tuple(value) for key, value in positions.items()}


def _masters(content: bytes, name: str) -> tuple[str, ...]:
    """Read TES4 MAST values without relying on a plugin library's private members."""
    if len(content) < 24 or content[:4] != b"TES4":
        raise SstExportError("SST_EXPORT_SOURCE_INVALID", "插件快照缺少完整 TES4 头。")
    end = 24 + int.from_bytes(content[4:8], "little")
    if end > len(content):
        raise SstExportError("SST_EXPORT_SOURCE_INVALID", "插件 TES4 头被截断。")
    offset = 24
    names = []
    while offset < end:
        if offset + 6 > end:
            raise SstExportError("SST_EXPORT_SOURCE_INVALID", "插件 TES4 子记录被截断。")
        kind = content[offset : offset + 4]
        size = int.from_bytes(content[offset + 4 : offset + 6], "little")
        offset += 6
        if offset + size > end:
            raise SstExportError("SST_EXPORT_SOURCE_INVALID", "插件 TES4 子记录内容被截断。")
        if kind == b"XXXX":
            raise SstExportError("SST_EXPORT_SOURCE_UNSUPPORTED", "尚不支持含扩展 TES4 子记录的插件导出 SST。")
        if kind == b"MAST":
            raw = content[offset : offset + size]
            if not raw.endswith(b"\0"):
                raise SstExportError("SST_EXPORT_SOURCE_INVALID", "插件 master 名称缺少结束符。")
            try:
                # Non-ASCII master decoding depends on xTranslator's code page.
                # Refuse an unproven mapping rather than assigning a different master.
                names.append(raw[:-1].decode("ascii").strip().lower())
            except UnicodeDecodeError as exc:
                raise SstExportError("SST_EXPORT_MASTER_ENCODING", "非 ASCII master 名称尚未验证编码一致性。") from exc
        offset += size
    if not name or not name.isascii():
        raise SstExportError("SST_EXPORT_MASTER_ENCODING", "插件文件名需为已验证的 ASCII 名称。")
    names.append(name.strip().lower())
    return validate_master_names(names)


def validate_master_names(names):
    if not isinstance(names, (list, tuple)) or not names:
        raise ValueError("SST master names must be a non-empty sequence")
    if any(
        not isinstance(name, str) or not name.isascii() or not name.strip() or any(char in name for char in "/\\:\x00")
        for name in names
    ):
        raise ValueError("SST master names must identify plugins without source paths")
    return tuple(names)


def _cancel(request: WriteRequest) -> None:
    if request.cancellation is not None and request.cancellation.is_cancelled:
        raise SstExportCancelled()


def build_sst_from_plugin(request: WriteRequest) -> SstDocument:
    """Create SSU9 for the requested entries using only their confirmed plugin source.

    Missing, conflicting, or foreign locators fail the whole export. This is a
    Skyrim plugin export, not a generic text-to-SST generator. VMAD property
    entries and field definitions outside the verified Skyrim mapping are rejected.
    """
    from .legacy_adapters import _snapshot_namespace

    _cancel(request)
    snapshot = request.source_snapshot
    if request.format_id is not FormatId.SST_SSU9 or snapshot is None or snapshot.format_id is not FormatId.PLUGIN_SSE:
        raise SstExportError("SST_EXPORT_SOURCE_REQUIRED", "新建 SSU9 需要对应插件的已保存来源快照。")
    if snapshot.content is None:
        return _build_from_saved_entries(request, _snapshot_namespace(snapshot))
    masters = _masters(snapshot.content, Path(snapshot.source.display_name or snapshot.source.uri).name)
    # Publication retires parsed plugins between render and fidelity validation.
    # Request a fresh source when this is a repeated build in the same scope.
    prepared = prepared_plugin_source(snapshot, for_write=True)
    if prepared.parser is None or prepared.parser.get_plugin() is None:
        raise SstExportError("SST_EXPORT_SOURCE_REQUIRED", "插件快照无法提供完整解析结果。")
    plugin = prepared.parser.get_plugin()
    namespace = _snapshot_namespace(snapshot)
    known = {(entry.key, entry.identity.original): entry for entry in prepared.entries}
    physical = defaultdict(list)
    for source, field in capture_plugin_write_source(plugin, prepared.parser.get_strings_lookup()):
        physical[(source.key, source.original)].append((source, field))
    owners = {}
    for record in _records(plugin.groups):
        _cancel(request)
        if any(field.type == "VMAD" for field in record.subrecords):
            # The plugin parser does not expose VMAD strings as entries. Detect
            # the physical field so a dictionary cannot silently omit them.
            raise SstExportError("SST_EXPORT_VMAD_UNSUPPORTED", "插件含尚未接入的 VMAD 属性，未生成 SST。")
        positions = _positions(record)
        for field in record.subrecords:
            if isinstance(field, StringSubrecord):
                owners[id(field)] = (record, *positions[id(field)])
    policy = request.stage_policy or DEFAULT_STAGE_POLICY
    records = []
    seen = set()
    for entry in request.entries:
        _cancel(request)
        identity = getattr(entry, "entry_key", None)
        if not isinstance(identity, EntryKey) or identity.namespace != namespace or identity in seen:
            raise SstExportError("SOURCE_IDENTITY_CONFLICT", "SST 导出包含重复或其他来源的词条身份。")
        seen.add(identity)
        source = known.get((identity.local_key, identity.original))
        if source is None or source.original != entry.original or source.string_id != entry.string_id:
            raise SstExportError("SOURCE_LOCATOR_NOT_FOUND", "SST 导出词条与已保存插件的身份、原文或 StringID 不一致。")
        if source.dsd_type.endswith(" VMAD") or "VMAD" in (entry.context or ""):
            raise SstExportError("SST_EXPORT_VMAD_UNSUPPORTED", "VMAD 属性身份尚未接入，未生成 SST。")
        matches = physical[(source.key, source.original)]
        if len(matches) != 1:
            raise SstExportError("SOURCE_LOCATOR_CONFLICT", "SST 导出词条不能唯一定位到插件物理字段。")
        _physical_source, field = matches[0]
        record, index, index_max = owners[id(field)]
        locator = physical_sst_locator(record, field, index, index_max)
        records.append(_render_record(entry, locator, policy))
    document = SstDocument(b"SSU9", 0, masters, (), tuple(records))
    document.to_bytes()  # Validate all representable ranges before a caller creates an artifact.
    _cancel(request)
    return document


def physical_sst_locator(record, field, index, index_max):
    editor_id = SSEPlugin.get_record_edid(record)
    form_id = int(record.formid, 16)
    name = str(editor_id) if editor_id else f"[{sanitize_form_id(form_id):08X}]"
    return PluginSstLocator(
        record.type, field.type, form_id, index, index_max, sst_hash(name), RecordFlags.Deleted in record.flags
    )


def _build_from_saved_entries(request, namespace):
    snapshot = request.source_snapshot
    fields = dict(snapshot.metadata)
    if snapshot.lease_id != "project-translation-content" or PLUGIN_SST_MASTERS not in fields:
        raise SstExportError(
            fields.get(PLUGIN_SST_UNAVAILABLE, "SST_EXPORT_SOURCE_REQUIRED"),
            "工程没有可用的 SST 定位信息，无法导出此来源。",
        )
    masters = validate_master_names(fields[PLUGIN_SST_MASTERS])
    policy = request.stage_policy or DEFAULT_STAGE_POLICY
    records, seen = [], set()
    for entry in request.entries:
        _cancel(request)
        identity = entry.entry_key
        if not isinstance(identity, EntryKey) or identity.namespace != namespace or identity in seen:
            raise SstExportError("SOURCE_IDENTITY_CONFLICT", "SST 导出包含重复或其他来源的词条身份。")
        seen.add(identity)
        try:
            locator = PluginSstLocator.from_values(dict(entry.metadata).get(PLUGIN_SST_LOCATOR))
        except ValueError as exc:
            raise SstExportError("SOURCE_LOCATOR_NOT_FOUND", "SST 导出词条缺少已保存的物理定位信息。") from exc
        records.append(_render_record(entry, locator, policy))
    document = SstDocument(b"SSU9", 0, masters, (), tuple(records))
    document.to_bytes()
    _cancel(request)
    return document


def _render_record(entry, locator, policy):
    if locator.deleted:
        raise SstExportError("SST_EXPORT_DELETED_RECORD", "SST 导出包含已删除插件记录。")
    decision = policy.evaluate(entry.stage, entry.translation, StageOperation.PUBLISH, original=entry.original)
    if decision.blocks_publish or decision.publish_text is None:
        diagnostic = decision.diagnostic
        raise SstExportError(
            diagnostic.code if diagnostic else "STAGE_PUBLISH_BLOCKED",
            diagnostic.message if diagnostic else "词条状态不允许发布。",
        )
    params = 0 if decision.preserve_source else {1: 1, 2: 4, 3: 1, 5: 1, 9: 2}.get(entry.stage, 0)
    string_id = 0 if entry.string_id is None else entry.string_id
    if not 0 <= string_id <= 0xFFFFFFFF:
        raise SstExportError("SST_EXPORT_STRING_ID_INVALID", "插件 StringID 不在 uint32 范围内。")
    return SstRecord(
        list_index=_list_index(locator.record_type, locator.field_type),
        string_id=string_id if string_id < 0x80000000 else string_id - 0x100000000,
        form_id=locator.form_id,
        record_type=locator.record_type,
        field_type=locator.field_type,
        index=locator.index,
        index_max=locator.index_max,
        edid_hash=locator.edid_hash,
        collab_id=0,
        params=params,
        original=entry.original,
        translation=decision.publish_text,
    )
