"""Validated, read-only preparation of JSON and SST translation migrations.

This module deliberately stops before mutating a Project or collection.  It
turns one inspected source into exact ``EntryKey`` state proposals so the UI
boundary can commit the complete draft through the authoritative Variant
command once.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.xt.sst_parser import SST_Parser

from .catalog import FormatCatalog
from .contracts import FormatId, ParseRequest, ProbeRequest, ProbeStatus, SourceDescriptor
from .identity import EntryKey, ExternalEntryRef
from .paratranz import ParatranzJsonAdapter

_JSON_FORMATS = frozenset({
    FormatId.JSON_PARATRANZ,
    FormatId.JSON_DSD,
    FormatId.JSON_TRANSBRIDGE,
})
_SST_FORMATS = frozenset({FormatId.SST_SSU8, FormatId.SST_SSU9})


class MigrationImportError(ValueError):
    """Stable application diagnostic for a rejected migration draft."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class MigrationImportDraft:
    source_path: str
    format_id: FormatId
    states: tuple[tuple[EntryKey, tuple[str, int]], ...]
    skipped_unmatched: int = 0

    def state_mapping(self) -> dict[EntryKey, tuple[str, int]]:
        return dict(self.states)


@dataclass(frozen=True, slots=True)
class _ImportedEntry:
    identity: EntryKey | None
    legacy_id: str
    legacy_key: str
    translation: str
    stage: int
    external_refs: tuple[ExternalEntryRef, ...] = ()
    sst_key: tuple[int, int] | None = None


def prepare_migration_import(
    source_path: str | Path,
    target: TranslationEntryCollection,
    *,
    format_hint: FormatId | str | None = None,
    context: RequestContext | None = None,
) -> MigrationImportDraft:
    """Parse and map one source without changing ``target``.

    Every accepted proposal names an existing target ``EntryKey``.  Any
    ambiguous mapping or conflicting proposal rejects the whole draft.
    """

    path = Path(source_path)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise MigrationImportError(
            "MIGRATION_SOURCE_READ_FAILED",
            f"无法读取迁移源（{type(exc).__name__}）。",
        ) from exc

    hint = _coerce_format_hint(format_hint)
    source = SourceDescriptor(str(path), path.name, len(content))
    probe = FormatCatalog().resolve(ProbeRequest(source, content, hint))
    if probe.status is ProbeStatus.AMBIGUOUS:
        candidates = "、".join(item.value for item in probe.candidates)
        raise MigrationImportError(
            "MIGRATION_FORMAT_AMBIGUOUS",
            f"迁移源同时符合多个格式（{candidates}），请选择明确格式。",
        )
    if probe.status is ProbeStatus.UNSUPPORTED:
        raise MigrationImportError("MIGRATION_FORMAT_UNSUPPORTED", "无法识别迁移源格式或文件内容不完整。")

    format_id = probe.candidates[0]
    if format_id not in _JSON_FORMATS | _SST_FORMATS:
        raise MigrationImportError("MIGRATION_FORMAT_UNSUPPORTED", f"当前迁移入口不支持 {format_id.value}。")

    if format_id in _JSON_FORMATS:
        imported = _parse_json(path, content, format_id, source, context)
    else:
        imported = _parse_sst(path, content, format_id)
    if not imported:
        raise MigrationImportError("MIGRATION_SOURCE_EMPTY", "迁移源中没有可导入的条目。")

    translated = tuple(item for item in imported if item.translation)
    if not translated:
        code = "MIGRATION_SST_NO_TRANSLATIONS" if format_id in _SST_FORMATS else "MIGRATION_SOURCE_NO_TRANSLATIONS"
        raise MigrationImportError(code, "迁移源中没有非空译文，未修改当前版本。")

    states, unmatched = _map_to_target(translated, target)
    if not states:
        raise MigrationImportError("MIGRATION_NO_PROVABLE_MATCHES", "没有译文能唯一映射到当前版本词条。")
    return MigrationImportDraft(str(path), format_id, tuple(states.items()), unmatched)


def _coerce_format_hint(value: FormatId | str | None) -> FormatId | None:
    if value is None or value == "":
        return None
    try:
        return value if isinstance(value, FormatId) else FormatId(value)
    except ValueError as exc:
        raise MigrationImportError("MIGRATION_FORMAT_HINT_INVALID", f"未知的迁移格式：{value}") from exc


def _parse_json(
    path: Path,
    content: bytes,
    format_id: FormatId,
    source: SourceDescriptor,
    context: RequestContext | None,
) -> tuple[_ImportedEntry, ...]:
    if format_id is FormatId.JSON_PARATRANZ:
        request_context = context or RequestContext("ui.migration-import")
        result = ParatranzJsonAdapter().parse(ParseRequest(source, request_context, format_hint=format_id))
        if result.outcome is not OperationOutcome.COMPLETED:
            diagnostic = result.diagnostics[0] if result.diagnostics else None
            code = "MIGRATION_JSON_INVALID" if diagnostic is None else diagnostic.code
            message = "ParaTranz JSON 无法完整解析。" if diagnostic is None else diagnostic.message
            raise MigrationImportError(code, message)
        return tuple(
            _ImportedEntry(
                item.entry_key,
                item.key,
                item.key,
                item.translation,
                item.stage,
                item.external_refs,
            )
            for item in result.entries
        )

    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise MigrationImportError("MIGRATION_JSON_ENCODING_INVALID", "JSON 必须使用 UTF-8 编码。") from exc
    except json.JSONDecodeError as exc:
        raise MigrationImportError(
            "MIGRATION_JSON_INVALID",
            f"JSON 语法无效（第 {exc.lineno} 行，第 {exc.colno} 列）。",
        ) from exc

    records = payload.get("entries") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise MigrationImportError("MIGRATION_JSON_ROOT_INVALID", "JSON 根节点必须是条目数组或 entries 数组。")

    entries: list[_ImportedEntry] = []
    seen: set[tuple[EntryKey | None, str, str]] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise MigrationImportError("MIGRATION_JSON_RECORD_INVALID", f"第 {index + 1} 条记录不是对象。")
        try:
            entry = (
                TranslationEntry.from_dsd_dict(record)
                if format_id is FormatId.JSON_DSD
                else TranslationEntry.from_dict(record)
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MigrationImportError(
                "MIGRATION_JSON_RECORD_INVALID",
                f"第 {index + 1} 条记录不符合 {format_id.value} 格式。",
            ) from exc
        imported = _ImportedEntry(
            entry.identity,
            entry.id,
            entry.key,
            entry.translation,
            entry.stage,
            entry.external_refs,
        )
        identity = (imported.identity, imported.legacy_id, imported.legacy_key)
        if identity in seen:
            raise MigrationImportError("MIGRATION_SOURCE_KEY_DUPLICATE", f"第 {index + 1} 条记录重复声明同一词条。")
        seen.add(identity)
        entries.append(imported)
    return tuple(entries)


def _parse_sst(path: Path, content: bytes, format_id: FormatId) -> tuple[_ImportedEntry, ...]:
    try:
        parsed = SST_Parser.from_file(str(path))
    except Exception as exc:  # noqa: BLE001 - normalize all legacy parser failures at this boundary
        raise MigrationImportError("MIGRATION_SST_INVALID", "SST 文件无法完整解析。") from exc
    actual = FormatId.SST_SSU8 if content.startswith(b"SSU8") else FormatId.SST_SSU9
    if actual is not format_id:
        raise MigrationImportError("MIGRATION_FORMAT_HINT_CONFLICT", "所选 SST 格式与文件签名不一致。")

    imported: list[_ImportedEntry] = []
    for entry in parsed.entries:
        key = (entry.form_id, entry.index)
        imported.append(
            _ImportedEntry(
                None,
                f"{entry.form_id:08X}|{entry.index}",
                f"{entry.form_id:08X}|{entry.index}",
                entry.translated_text,
                1 if entry.translated_text else 0,
                sst_key=key,
            )
        )
    return tuple(imported)


def _map_to_target(
    imported: tuple[_ImportedEntry, ...],
    target: TranslationEntryCollection,
) -> tuple[dict[EntryKey, tuple[str, int]], int]:
    target_entries = tuple(target)
    by_identity = {entry.identity: entry.identity for entry in target_entries}
    by_legacy: dict[str, set[EntryKey]] = {}
    by_external: dict[tuple[str, str, str, object], set[EntryKey]] = {}
    by_sst: dict[tuple[int, int], set[EntryKey]] = {}
    for entry in target_entries:
        for legacy in {entry.id, entry.key, entry.identity.local_key}:
            by_legacy.setdefault(legacy, set()).add(entry.identity)
        for reference in entry.external_refs:
            by_external.setdefault(reference.index_key, set()).add(entry.identity)
        sst_key = _target_sst_key(entry.id)
        if sst_key is not None:
            by_sst.setdefault(sst_key, set()).add(entry.identity)

    proposals: dict[EntryKey, tuple[str, int]] = {}
    unmatched = 0
    for source in imported:
        candidates: set[EntryKey] = set()
        if source.identity in by_identity:
            candidates.add(source.identity)
        for legacy in {source.legacy_id, source.legacy_key, source.identity.local_key if source.identity else ""}:
            if legacy:
                candidates.update(by_legacy.get(legacy, ()))
        for reference in source.external_refs:
            candidates.update(by_external.get(reference.index_key, ()))
        if source.sst_key is not None:
            candidates.update(by_sst.get(source.sst_key, ()))

        if len(candidates) > 1:
            raise MigrationImportError(
                "MIGRATION_MAPPING_AMBIGUOUS",
                f"来源词条 {source.legacy_key!r} 可映射到多个当前词条，未执行导入。",
            )
        if not candidates:
            unmatched += 1
            continue
        target_key = next(iter(candidates))
        state = (source.translation, source.stage if source.stage != 0 else 1)
        previous = proposals.get(target_key)
        if previous is not None and previous != state:
            raise MigrationImportError(
                "MIGRATION_ENTRY_KEY_CONFLICT",
                f"多个来源记录为同一 EntryKey 提供了不同译文：{target_key.local_key}",
            )
        proposals[target_key] = state
    return proposals, unmatched


def _target_sst_key(legacy_id: str) -> tuple[int, int] | None:
    after_colon = legacy_id.split(":", 1)[1] if ":" in legacy_id else legacy_id
    form_id, separator, remainder = after_colon.partition("|")
    if not separator:
        return None
    index = remainder.split("~", 1)[0]
    try:
        return int(form_id, 16), int(index)
    except (TypeError, ValueError):
        return None


__all__ = ["MigrationImportDraft", "MigrationImportError", "prepare_migration_import"]
