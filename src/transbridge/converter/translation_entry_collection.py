from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import replace
import json
from pathlib import Path
from threading import RLock
from typing import Any
import warnings

from transbridge.application.contracts import Diagnostic, RequestContext
from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef
from transbridge.application.io.mutation import (
    ChangeSet,
    EntrySnapshot,
    LegacyEntryMapping,
    LegacyMappingReport,
    MutationResult,
    MutationStatus,
)
from transbridge.converter.translation_entry import (
    STAGE_TRANSLATED,
    STAGE_UNTRANSLATED,
    TranslationEntry,
    _normalize_text,
)
from transbridge.parser.eet_parser import EET_Entry, EET_XmlParser
from transbridge.parser.plugin_parser import PluginParser
from transbridge.parser.strings_file import PluginStringsLookup
from transbridge.parser.xt import SST_Entry, XT_Entry


class TranslationEntryCollection:
    """
    管理多个 TranslationEntry 的集合。
    - 以序列化 EntryKey(namespace, local_key) 作为唯一主索引
    - legacy id/key 查找为只读扫描 facade，不维护第二套可写索引
    - ExternalEntryRef 索引不参与主身份，且拒绝跨条目冲突
    - 适合作为后续 JSON / DB / 导出层的中间结构
    """

    def __init__(self, entries: Iterable[TranslationEntry] | None = None):
        self._entries: dict[str, TranslationEntry] = {}
        self._external_ref_index: dict[
            tuple[str, str, str, str | int | float | bool | None],
            frozenset[str],
        ] = {}
        self._collection_revision = EntryRevision()
        self._lock = RLock()

        if entries:
            self._bulk_load(entries)

    # ---------- 基础容器行为 ----------

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[TranslationEntry]:
        return iter(self._entries.values())

    def __contains__(self, key: str | EntryKey) -> bool:
        return self.get(key) is not None

    @property
    def collection_revision(self) -> EntryRevision:
        return self._collection_revision

    # ---------- 基本操作 ----------

    def _bulk_load(self, entries: Iterable[TranslationEntry]) -> None:
        """Build an initial collection in linear time before it becomes observable."""

        with self._lock:
            projected: dict[str, TranslationEntry] = {}
            revision = self._collection_revision
            for entry in entries:
                storage_key = entry.identity.serialize()
                existing = projected.get(storage_key)
                if existing is not None:
                    warnings.warn(
                        "TranslationEntryCollection constructor received a duplicate EntryKey; "
                        "the last value wins",
                        DeprecationWarning,
                        stacklevel=3,
                    )
                    entry = replace(
                        entry,
                        revision=EntryRevision(max(existing.revision.value + 1, entry.revision.value)),
                        external_refs=entry.external_refs or existing.external_refs,
                        provenance=entry.provenance or existing.provenance,
                        metadata=entry.metadata or existing.metadata,
                    )
                projected[storage_key] = entry
                revision = revision.next()
            external_index, conflicts = self._build_external_index(projected)
            if conflicts:
                raise ValueError(f"external reference conflict: {conflicts[0][0]}")
            self._entries = projected
            self._external_ref_index = external_index
            self._collection_revision = revision

    def add(self, entry: TranslationEntry, *, overwrite: bool = True) -> None:
        """
        添加一个 TranslationEntry。

        :param entry: TranslationEntry 实例
        :param overwrite: 若 key 已存在，是否覆盖（默认 True）
        """
        with self._lock:
            entry = self._normalize_legacy_upsert(entry)
            storage_key = entry.identity.serialize()
            existing = self._entries.get(storage_key)
            if existing is not None and not overwrite:
                return
            if existing is not None:
                warnings.warn(
                    "TranslationEntryCollection.add(overwrite=True) is a compatibility facade; "
                    "use apply(ChangeSet, context)",
                    DeprecationWarning,
                    stacklevel=2,
                )
                next_revision = max(existing.revision.value + 1, entry.revision.value)
                entry = replace(
                    entry,
                    revision=EntryRevision(next_revision),
                    external_refs=entry.external_refs or existing.external_refs,
                    provenance=entry.provenance or existing.provenance,
                    metadata=entry.metadata or existing.metadata,
                )
            projected = dict(self._entries)
            projected[storage_key] = entry
            external_index, conflicts = self._build_external_index(projected)
            if conflicts:
                raise ValueError(f"external reference conflict: {conflicts[0][0]}")
            self._entries = projected
            self._external_ref_index = external_index
            self._collection_revision = self._collection_revision.next()

    def _normalize_legacy_upsert(self, entry: TranslationEntry) -> TranslationEntry:
        if entry.identity.namespace.value != "legacy:v1":
            return entry
        matches = self._legacy_matches(entry.key, include_id=True)
        if len(matches) != 1:
            return entry
        existing = matches[0]
        if existing.identity == entry.identity:
            return entry
        warnings.warn(
            "Legacy collection.add() resolved a unique local key; use CollectionMutationPort.apply()",
            DeprecationWarning,
            stacklevel=3,
        )
        return replace(
            entry,
            entry_key=existing.identity,
            external_refs=entry.external_refs or existing.external_refs,
            revision=existing.revision,
            provenance=entry.provenance or existing.provenance,
            metadata=entry.metadata or existing.metadata,
        )

    def get(self, key: str | EntryKey) -> TranslationEntry | None:
        """Read by exact EntryKey, or by an unambiguous legacy key/id facade."""
        if isinstance(key, EntryKey):
            return self._entries.get(key.serialize())
        exact = self._entries.get(key)
        if exact is not None:
            return exact
        matches = self._legacy_matches(key, include_id=True)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            warnings.warn(
                f"Legacy key {key!r} is ambiguous across source namespaces",
                RuntimeWarning,
                stacklevel=2,
            )
        return None

    def _legacy_matches(self, value: str, *, include_id: bool) -> list[TranslationEntry]:
        local_matches = [entry for entry in self._entries.values() if entry.identity.local_key == value]
        if local_matches or not include_id:
            return local_matches
        return [entry for entry in self._entries.values() if entry.id == value]

    def get_by_id(self, entry_id: str) -> TranslationEntry | None:
        """Read-only V1 id facade; ambiguous ids never resolve by traversal order."""
        matches = [entry for entry in self._entries.values() if entry.id == entry_id]
        return matches[0] if len(matches) == 1 else None

    def get_by_key(self, key: str) -> TranslationEntry | None:
        """[已废弃] 使用 get(key) 替代。"""
        warnings.warn("get_by_key is deprecated, use get(key) instead", DeprecationWarning, stacklevel=2)
        return self.get(key)

    def remove(self, key: str | EntryKey) -> None:
        """按 key 删除一条记录；不存在则忽略"""
        with self._lock:
            entry = self.get(key)
            if entry is None:
                return
            projected = dict(self._entries)
            projected.pop(entry.identity.serialize(), None)
            external_index, _ = self._build_external_index(projected)
            self._entries = projected
            self._external_ref_index = external_index
            self._collection_revision = self._collection_revision.next()

    def get_by_external_ref(self, reference: ExternalEntryRef) -> tuple[TranslationEntry, ...]:
        storage_keys = self._external_ref_index.get(reference.index_key, frozenset())
        return tuple(self._entries[key] for key in sorted(storage_keys))

    def snapshot(self, entry_key: EntryKey) -> EntrySnapshot | None:
        entry = self.get(entry_key)
        return None if entry is None else entry.snapshot()

    @staticmethod
    def _build_external_index(
        entries: dict[str, TranslationEntry],
    ) -> tuple[
        dict[tuple[str, str, str, str | int | float | bool | None], frozenset[str]],
        tuple[tuple[str, tuple[str, ...]], ...],
    ]:
        mutable: dict[tuple[str, str, str, str | int | float | bool | None], set[str]] = defaultdict(set)
        for storage_key, entry in entries.items():
            for reference in entry.external_refs:
                mutable[reference.index_key].add(storage_key)
        conflicts = tuple(
            (repr(index_key), tuple(sorted(storage_keys)))
            for index_key, storage_keys in mutable.items()
            if len(storage_keys) > 1
        )
        return {key: frozenset(value) for key, value in mutable.items()}, conflicts

    def apply(self, change_set: ChangeSet, context: RequestContext) -> MutationResult:
        """Atomically apply a trusted-context-authorized V2 ChangeSet."""

        with self._lock:
            previous_revision = self._collection_revision
            context_error = self._validate_mutation_context(change_set, context)
            if context_error is not None:
                return MutationResult(
                    MutationStatus.REJECTED,
                    change_set.run_id,
                    previous_revision,
                    previous_revision,
                    diagnostics=(context_error,),
                )

            projected = dict(self._entries)
            changed: list[TranslationEntry] = []
            for patch in change_set.patches:
                storage_key = patch.entry_key.serialize()
                entry = projected.get(storage_key)
                if entry is None:
                    return self._mutation_conflict(
                        change_set,
                        previous_revision,
                        "ENTRY_NOT_FOUND",
                        "The entry does not exist in this collection.",
                        patch.entry_key,
                    )
                expected = change_set.expected_revision(patch.entry_key)
                if entry.revision != expected:
                    return self._mutation_conflict(
                        change_set,
                        previous_revision,
                        "ENTRY_REVISION_CONFLICT",
                        "The entry revision changed before the ChangeSet was applied.",
                        patch.entry_key,
                    )
                missing_permissions = patch.missing_permissions(context.permissions)
                if missing_permissions:
                    return MutationResult(
                        MutationStatus.REJECTED,
                        change_set.run_id,
                        previous_revision,
                        previous_revision,
                        diagnostics=(
                            Diagnostic(
                                "ENTRY_FIELD_PERMISSION_DENIED",
                                "The trusted request context does not grant all requested entry fields.",
                                details=(
                                    ("entry_key", patch.entry_key.serialize()),
                                    ("missing_permissions", missing_permissions),
                                ),
                            ),
                        ),
                    )
                updated = replace(
                    entry,
                    **patch.as_dict(),
                    revision=entry.revision.next(),
                    provenance=(*entry.provenance, change_set.provenance),
                )
                projected[storage_key] = updated
                changed.append(updated)

            external_index, conflicts = self._build_external_index(projected)
            if conflicts:
                return MutationResult(
                    MutationStatus.CONFLICT,
                    change_set.run_id,
                    previous_revision,
                    previous_revision,
                    diagnostics=(
                        Diagnostic(
                            "EXTERNAL_REF_CONFLICT",
                            "An external reference would identify more than one EntryKey.",
                            details=(("external_ref", conflicts[0][0]), ("entry_keys", conflicts[0][1])),
                        ),
                    ),
                )

            self._entries = projected
            self._external_ref_index = external_index
            self._collection_revision = previous_revision.next()
            return MutationResult(
                MutationStatus.APPLIED,
                change_set.run_id,
                previous_revision,
                self._collection_revision,
                changed_keys=tuple(entry.identity for entry in changed),
                snapshots=tuple(entry.snapshot() for entry in changed),
            )

    @staticmethod
    def _validate_mutation_context(change_set: ChangeSet, context: RequestContext) -> Diagnostic | None:
        if context.run_id != change_set.run_id:
            return Diagnostic(
                "CHANGESET_RUN_CONTEXT_MISMATCH",
                "The ChangeSet run_id must match the trusted request context.",
            )
        if context.owner_id != change_set.provenance.actor:
            return Diagnostic(
                "CHANGESET_ACTOR_CONTEXT_MISMATCH",
                "The provenance actor must match the trusted request owner.",
            )
        return None

    @staticmethod
    def _mutation_conflict(
        change_set: ChangeSet,
        revision: EntryRevision,
        code: str,
        message: str,
        entry_key: EntryKey,
    ) -> MutationResult:
        return MutationResult(
            MutationStatus.CONFLICT,
            change_set.run_id,
            revision,
            revision,
            diagnostics=(Diagnostic(code, message, details=(("entry_key", entry_key.serialize()),)),),
        )

    def legacy_mapping_report(self) -> LegacyMappingReport:
        """Return a read-only V1 id/key to EntryKey mapping and all ambiguity."""

        mappings = tuple(
            LegacyEntryMapping(entry.id, entry.key, entry.identity)
            for entry in self._entries.values()
        )
        by_local_key: dict[str, list[EntryKey]] = defaultdict(list)
        for mapping in mappings:
            by_local_key[mapping.legacy_key].append(mapping.entry_key)
        ambiguous = tuple(sorted(key for key, values in by_local_key.items() if len(values) > 1))
        _, conflicts = self._build_external_index(self._entries)
        external_conflicts = tuple(
            (
                reference,
                tuple(
                    self._entries[storage_key].identity
                    for storage_key in storage_keys
                    if storage_key in self._entries
                ),
            )
            for reference, storage_keys in conflicts
        )
        return LegacyMappingReport(mappings, ambiguous, external_conflicts)

    # ---------- 批量操作 ----------

    def add_many(
        self,
        entries: Iterable[TranslationEntry],
        *,
        overwrite: bool = True,
    ) -> None:
        """批量添加 TranslationEntry"""
        for e in entries:
            self.add(e, overwrite=overwrite)

    def merge(
        self,
        other: TranslationEntryCollection,
        *,
        overwrite: bool = True,
    ) -> None:
        """
        合并另一个 TranslationEntryCollection。

        :param other: 另一个集合
        :param overwrite: key 冲突时是否覆盖
        """
        for e in other:
            self.add(e, overwrite=overwrite)

    @classmethod
    def from_eet_xml(
            cls,
            path: str | Path,
            *,
            overwrite: bool = True,
    ) -> TranslationEntryCollection:
        """
        从 EET XML 文件一次性导入。
        """
        parser = EET_XmlParser.from_file(path)

        collection = TranslationEntryCollection()

        for eet_entry in parser:
            entry = TranslationEntry.create_from_eet_entry(eet_entry)
            collection.add(entry, overwrite=overwrite)

        return collection

    @staticmethod
    def _type_field_base(context: str) -> str:
        """从 context 提取基础 TYPE:FIELD（去掉 INFO/DIAL 的 |quest_formid 后缀）。"""
        return context.split("|")[0] if context else ""

    @staticmethod
    def _form_id_from_entry_id(entry_id: str) -> str:
        """从 entry.id 提取 form_id（冒号与竖线之间的部分）。"""
        _, _, rest = entry_id.partition(":")
        return rest.partition("|")[0]

    def update_from_eet_xml(
            self,
            path: str | Path,
    ) -> int:
        """
        从 EET XML 文件中更新已存在的翻译项。

        Phase 1：按完整 entry.id 精确匹配 + original 校验。
        Phase 2：对未命中的条目，按 (original, type_field_base) 回退匹配。

        :param path: EET XML 文件路径
        :return: 实际发生更新的条目数量
        """
        parser = EET_XmlParser.from_file(path)
        all_eet: list[EET_Entry] = list(parser)
        updated_count = 0

        # --- Phase 1：按完整 id 精确匹配 ---
        eet_by_id: dict[str, list[EET_Entry]] = defaultdict(list)
        for eet_entry in all_eet:
            eet_id = TranslationEntry._build_eet_id(
                eet_entry.edid, eet_entry.id, eet_entry.index, eet_entry.grup, eet_entry.champ
            )
            eet_by_id[eet_id].append(eet_entry)

        unmatched: list[TranslationEntry] = []

        for entry in list(self._entries.values()):
            matched = False
            for eet_entry in eet_by_id.get(entry.id, []):
                if eet_entry.original != entry.original or not eet_entry.traduit:
                    continue
                updated_entry = replace(
                    entry,
                    translation=eet_entry.traduit,
                    stage=STAGE_TRANSLATED if eet_entry.status == 99 or eet_entry.traduit else STAGE_UNTRANSLATED,
                )
                self.add(updated_entry, overwrite=True)
                updated_count += 1
                matched = True
                break
            if not matched:
                unmatched.append(entry)

        # --- Phase 2：按 (original, type_field_base) 回退 ---
        if unmatched:
            # key = (form_id, grup:champ, original)，优先有译文的条目
            fallback_index: dict[tuple[str, str], EET_Entry] = {}
            for eet_entry in all_eet:
                if not eet_entry.traduit:
                    continue
                fb_key = (eet_entry.original, f"{eet_entry.grup}:{eet_entry.champ}")
                if fb_key not in fallback_index:
                    fallback_index[fb_key] = eet_entry

            for entry in unmatched:
                fb_key = (entry.original, self._type_field_base(entry.context))
                eet_entry = fallback_index.get(fb_key)
                if eet_entry is None:
                    continue
                updated_entry = replace(
                    entry,
                    translation=eet_entry.traduit,
                    stage=STAGE_TRANSLATED if eet_entry.status == 99 or eet_entry.traduit else STAGE_UNTRANSLATED,
                )
                self.add(updated_entry, overwrite=True)
                updated_count += 1

        return updated_count

    # ---------- Plugin ----------

    @classmethod
    def from_plugin(
            cls,
            path: str | Path,
            *,
            skip_empty: bool = True,
            overwrite: bool = True,
    ) -> TranslationEntryCollection:
        """
        从 Plugin 文件一次性导入。
        """
        parser = PluginParser()
        entries = parser.parse_plugin(
            Path(path),
            skip_empty=skip_empty,
        )

        return TranslationEntryCollection(entries=entries)

    # ---------- 通用入口（可选） ----------

    @classmethod
    def from_entries(
            cls,
            entries: Iterable[TranslationEntry],
    ) -> TranslationEntryCollection:
        """
        从已有 TranslationEntry 集合构建（通用入口）。
        """
        return TranslationEntryCollection(entries)

    def apply_xt_entries(
            self,
            xt_entries: Iterable[XT_Entry],
    ) -> int:
        """
        将 XT_Entry 批量应用到已有的 TranslationEntry 上。

        Phase 1：按 edid 桶查找（候选：editid / bare formid / [formid]）+ rec/source/index 校验。
        Phase 2：对未命中的条目，按 (original, type_field_base) 回退匹配。

        :return: 实际发生更新的条目数量
        """
        all_xt: list[XT_Entry] = list(xt_entries)

        # --- Phase 1：按 edid 分组 ---
        xt_by_edid: dict[str, list[XT_Entry]] = defaultdict(list)
        for xt in all_xt:
            xt_by_edid[xt.edid].append(xt)

        updated_count = 0
        unmatched: list[TranslationEntry] = []

        for entry in list(self._entries.values()):
            left, _, right_with_other = entry.id.partition(":")
            right = right_with_other.split("|")[0]

            # 扩展候选：editid / bare formid / [formid]
            candidate_edids = (left, right, f"[{right}]")

            matched = False
            for edid in candidate_edids:
                for xt in xt_by_edid.get(edid, []):
                    updated = TranslationEntry.try_update_from_xt(entry, xt)
                    if updated is None:
                        continue
                    if updated is not entry:
                        self.add(updated, overwrite=True)
                        entry = updated
                        updated_count += 1
                    matched = True
                    break
                if matched:
                    break

            if not matched:
                unmatched.append(entry)

        # --- Phase 2：按 (original, type_field_base) 回退 ---
        if unmatched:
            fallback_index: dict[tuple[str, str], XT_Entry] = {}
            for xt in all_xt:
                if not xt.dest:
                    continue
                fb_key = (_normalize_text(xt.source), xt.rec)
                if fb_key not in fallback_index:
                    fallback_index[fb_key] = xt

            for entry in unmatched:
                fb_key = (_normalize_text(entry.original), self._type_field_base(entry.context))
                xt = fallback_index.get(fb_key)
                if xt is None or not xt.dest:
                    continue
                updated_entry = replace(entry, translation=xt.dest, stage=STAGE_TRANSLATED)
                self.add(updated_entry, overwrite=True)
                updated_count += 1

        return updated_count

    def apply_sst_entries(
            self,
            sst_entries: Iterable[SST_Entry],
    ) -> dict:
        """将 SST_Entry 批量应用到已有的 TranslationEntry 上。

        按 form_id + index 匹配。返回统计 dict: {matched, updated, skipped}。
        """
        all_sst: list[SST_Entry] = list(sst_entries)
        # 按 (form_id, index) 构建查找表
        sst_by_key: dict[tuple[int, int], SST_Entry] = {}
        for sst in all_sst:
            key = (sst.form_id, sst.index)
            if key not in sst_by_key:
                sst_by_key[key] = sst

        matched = 0
        updated = 0

        for entry in list(self._entries.values()):
            # 从 entry.id 提取 form_id + index
            after_colon = entry.id.split(":", 1)[1] if ":" in entry.id else entry.id
            form_id_hex, _, rest = after_colon.partition("|")
            index_str = rest.split("~")[0] if "~" in rest else rest
            try:
                entry_form_id = int(form_id_hex, 16)
                entry_index = int(index_str) if index_str else 0
            except (ValueError, TypeError):
                continue

            sst = sst_by_key.get((entry_form_id, entry_index))
            if sst is None:
                continue
            matched += 1
            result = TranslationEntry.try_update_from_sst(entry, sst)
            if result is not None and result is not entry:
                self.add(result, overwrite=True)
                updated += 1

        skipped = matched - updated
        return {"matched": matched, "updated": updated, "skipped": skipped}

    def update_from_translated_plugin(
            self,
            path: str | Path,
            *,
            overwrite: bool = False,
    ) -> int:
        """
        从已翻译的 ESP/ESM 中提取译文并更新集合。

        Phase 1：按 entry.id 精确匹配。
        Phase 2：对未命中的条目，按 (original, type_field_base) 回退匹配。

        :param path: 已翻译插件文件路径
        :param overwrite: 是否覆盖已有译文，默认 False
        :return: 实际发生更新的条目数量
        """
        translated_entries = PluginParser().parse_plugin(Path(path), skip_empty=True)
        all_translated = list(translated_entries)

        # Phase 1：按 id 精确查找（辅助索引）
        translated_lookup: dict[str, str] = {te.id: te.translation for te in all_translated}

        updated_count = 0
        unmatched: list[TranslationEntry] = []

        for entry in list(self._entries.values()):
            translated_text = translated_lookup.get(entry.id)
            if translated_text is None or not translated_text:
                unmatched.append(entry)
                continue
            if translated_text == entry.original:
                continue
            if entry.translation and not overwrite:
                continue
            self.add(
                replace(entry, translation=translated_text, stage=STAGE_TRANSLATED),
                overwrite=True,
            )
            updated_count += 1

        # Phase 2：按 (original, type_field_base) 回退
        if unmatched:
            fallback_index: dict[tuple[str, str], str] = {}
            for te in all_translated:
                if not te.translation:
                    continue
                _, _, rest = te.id.partition(":")
                _, _, type_part = rest.partition("~")
                fb_key = (te.original, type_part.split("|")[0])
                if fb_key not in fallback_index:
                    fallback_index[fb_key] = te.translation

            for entry in unmatched:
                if entry.translation and not overwrite:
                    continue
                fb_key = (entry.original, self._type_field_base(entry.context))
                translated_text = fallback_index.get(fb_key)
                if translated_text is None or translated_text == entry.original:
                    continue
                self.add(
                    replace(entry, translation=translated_text, stage=STAGE_TRANSLATED),
                    overwrite=True,
                )
                updated_count += 1

        return updated_count

    def update_from_strings_lookup(
            self,
            strings_lookup: PluginStringsLookup,
            *,
            overwrite: bool = False,
    ) -> int:
        """
        从 PluginStringsLookup 批量更新译文（用于导入已翻译的 strings 文件）。

        通过 entry.string_id 精确匹配 strings 文件中的翻译。

        注意：此方法仅适用于本地化插件（有 string_id 的条目）。
        strings 文件存储的是翻译文本，无法通过文本内容回退匹配。

        :param strings_lookup: PluginStringsLookup 实例（加载了目标语言的 strings 文件）
        :param overwrite: 是否覆盖已有译文，默认 False
        :return: 实际发生更新的条目数量
        """
        updated_count = 0

        for entry in list(self._entries.values()):
            if entry.translation and not overwrite:
                continue
            if entry.string_id is None:
                continue

            translated_text = strings_lookup.get(entry.string_id)
            if translated_text is None:
                continue
            if translated_text == entry.original:
                continue

            self.add(
                replace(entry, translation=translated_text, stage=STAGE_TRANSLATED),
                overwrite=True,
            )
            updated_count += 1

        return updated_count

    # ---------- 查询 / 过滤（可扩展） ----------

    def filter(self, predicate) -> list[TranslationEntry]:
        """
        按自定义条件过滤。
        示例：
            coll.filter(lambda e: e.stage == 1)
        """
        return [e for e in self._entries.values() if predicate(e)]

    # ---------- 序列化 ----------

    def to_dict(self) -> list[dict[str, Any]]:
        """
        将整个集合序列化为条目列表（用于 JSON）。
        返回一个简单的条目数组，不包含任何嵌套结构。
        """
        return [e.to_dict() for e in self._entries.values()]

    def to_export_dict(self) -> list[dict[str, Any]]:
        """
        导出为 DSD 格式的条目列表，用于外部工具兼容。
        格式：[{"form_id": "...", "type": "...", "string": "..."}, ...]
        只包含有译文的条目。
        """
        result = []
        for e in self._entries.values():
            if e.translation:
                dsd_dict = e.to_dsd_dict()
                if dsd_dict:
                    result.append(dsd_dict)
        return result

    def to_json(
        self,
        *,
        ensure_ascii: bool = False,
        indent: int = 2,
    ) -> str:
        """导出为 JSON 字符串。"""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=ensure_ascii,
            indent=indent,
        )

    def to_export_json(
        self,
        *,
        ensure_ascii: bool = False,
        indent: int = 2,
    ) -> str:
        """导出为简化格式的 JSON 字符串，用于外部工具兼容。"""
        return json.dumps(
            self.to_export_dict(),
            ensure_ascii=ensure_ascii,
            indent=indent,
        )

    def to_json_file(
        self,
        path: str | Path,
        *,
        ensure_ascii: bool = False,
        indent: int = 2,
    ) -> None:
        """保存为 JSON 文件。"""
        path = Path(path)
        path.write_text(
            self.to_json(ensure_ascii=ensure_ascii, indent=indent),
            encoding="utf-8",
        )

    def to_export_json_file(
        self,
        path: str | Path,
        *,
        ensure_ascii: bool = False,
        indent: int = 2,
    ) -> None:
        """保存为简化格式的 JSON 文件，用于外部工具兼容。"""
        path = Path(path)
        path.write_text(
            self.to_export_json(ensure_ascii=ensure_ascii, indent=indent),
            encoding="utf-8",
        )

    @classmethod
    def from_json_file(
        cls,
        path: str | Path,
        *,
        overwrite: bool = True,
    ) -> TranslationEntryCollection:
        """
        从 JSON 文件加载数据并创建 TranslationEntryCollection 实例。
        JSON 格式应为简单的条目数组，不包含嵌套结构。
        """
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))

        collection = cls()

        if not isinstance(data, list):
            raise ValueError("无效的 JSON 格式：应该是一个条目数组")

        for entry_data in data:
            entry = TranslationEntry.from_dict(entry_data)
            collection.add(entry, overwrite=overwrite)

        return collection

    # ==================== DSD 格式导入/导出 ====================

    def to_dsd_json(
        self,
        *,
        ensure_ascii: bool = False,
        indent: int = 2,
    ) -> str:
        """导出为 DSD 格式的 JSON 字符串。只包含有译文的条目。"""
        return json.dumps(
            self.to_export_dict(),
            ensure_ascii=ensure_ascii,
            indent=indent,
        )

    def to_dsd_json_file(
        self,
        path: str | Path,
        *,
        ensure_ascii: bool = False,
        indent: int = 2,
    ) -> None:
        """导出为 DSD 格式的 JSON 文件，用于 xEdit 脚本等外部工具。"""
        path = Path(path)
        path.write_text(
            self.to_dsd_json(ensure_ascii=ensure_ascii, indent=indent),
            encoding="utf-8",
        )

    @classmethod
    def from_dsd_json_file(
        cls,
        path: str | Path,
        *,
        overwrite: bool = True,
    ) -> TranslationEntryCollection:
        """
        从 DSD 格式的 JSON 文件导入翻译条目。
        """
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))

        collection = cls()

        if not isinstance(data, list):
            raise ValueError("无效的 DSD JSON 格式：应该是一个条目数组")

        for entry_data in data:
            entry = TranslationEntry.from_dsd_dict(entry_data)
            collection.add(entry, overwrite=overwrite)

        return collection
