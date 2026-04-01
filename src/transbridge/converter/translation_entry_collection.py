from __future__ import annotations
from collections import defaultdict
from collections.abc import Iterable, Iterator
from typing import Optional,Any
from pathlib import Path
import json

from src.transbridge.converter.translation_entry import TranslationEntry, _normalize_text
from src.transbridge.parser.eet_parser import EET_XmlParser, EET_Entry
from src.transbridge.parser.plugin_parser import PluginParser
from src.transbridge.parser.strings_file import PluginStringsLookup
from src.transbridge.parser.xt_parser import XT_Entry
import re


class TranslationEntryCollection:
    """
    管理多个 TranslationEntry 的集合。
    - 以 TranslationEntry.id 作为唯一索引
    - 不使用 key
    - 适合作为后续 JSON / DB / 导出层的中间结构
    """

    def __init__(self, entries: Iterable[TranslationEntry] | None = None):
        self._entries: dict[str, TranslationEntry] = {}  # 原来的 id 索引
        self._key_index: dict[str, TranslationEntry] = {}  # 新增 key 索引

        if entries:
            for e in entries:
                self.add(e)

    # ---------- 基础容器行为 ----------

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[TranslationEntry]:
        return iter(self._entries.values())

    def __contains__(self, entry_id: str) -> bool:
        return entry_id in self._entries

    # ---------- 基本操作 ----------

    def add(self, entry: TranslationEntry, *, overwrite: bool = True) -> None:
        """
        添加一个 TranslationEntry。

        :param entry: TranslationEntry 实例
        :param overwrite: 若 id 已存在，是否覆盖（默认 True）
        """
        # if not overwrite and entry.id in self._entries:
        #     return
        # self._entries[entry.id] = entry

        # 按 id 添加
        if not overwrite and entry.id in self._entries:
            return
        self._entries[entry.id] = entry
        self._key_index[entry.key] = entry


        # 同步更新 key 索引
        if not overwrite and entry.key in self._key_index:
            return
        self._key_index[entry.key] = entry

    def get(self, entry_id: str) -> Optional[TranslationEntry]:
        """按 id 获取 TranslationEntry；不存在返回 None"""
        return self._entries.get(entry_id)

    def get_by_key(self, key: str) -> Optional[TranslationEntry]:
        """按 key 获取 TranslationEntry；不存在返回 None"""
        return self._key_index.get(key)

    def remove(self, entry_id: str) -> None:
        """按 id 删除一条记录；不存在则忽略"""
        self._entries.pop(entry_id, None)

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
        other: "TranslationEntryCollection",
        *,
        overwrite: bool = True,
    ) -> None:
        """
        合并另一个 TranslationEntryCollection。

        :param other: 另一个集合
        :param overwrite: id 冲突时是否覆盖
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
                updated_entry = TranslationEntry(
                    id=entry.id, key=entry.key, original=entry.original,
                    translation=eet_entry.traduit,
                    stage=1 if eet_entry.status == 99 or eet_entry.traduit else 0,
                    context=entry.context,
                    form_id_with_plugin=entry.form_id_with_plugin,
                )
                self._entries[entry.id] = updated_entry
                self._key_index[entry.key] = updated_entry
                updated_count += 1
                matched = True
                break
            if not matched:
                unmatched.append(entry)

        # --- Phase 2：按 (original, type_field_base) 回退 ---
        if unmatched:
            # key = (form_id, grup:champ, original)，优先有译文的条目
            fallback_index: dict[tuple[str, str, str], EET_Entry] = {}
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
                updated_entry = TranslationEntry(
                    id=entry.id, key=entry.key, original=entry.original,
                    translation=eet_entry.traduit,
                    stage=1 if eet_entry.status == 99 or eet_entry.traduit else 0,
                    context=entry.context,
                    form_id_with_plugin=entry.form_id_with_plugin,
                )
                self._entries[entry.id] = updated_entry
                self._key_index[entry.key] = updated_entry
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
                        self._entries[entry.id] = updated
                        self._key_index[entry.key] = updated
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
                updated_entry = TranslationEntry(
                    id=entry.id, key=entry.key, original=entry.original,
                    translation=xt.dest, stage=1, context=entry.context,
                    form_id_with_plugin=entry.form_id_with_plugin,
                )
                self._entries[entry.id] = updated_entry
                self._key_index[entry.key] = updated_entry
                updated_count += 1

        return updated_count

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

        # Phase 1：按 id 精确查找
        translated_lookup: dict[str, str] = {te.id: te.original for te in all_translated}

        updated_count = 0
        unmatched: list[TranslationEntry] = []

        for entry in list(self._entries.values()):
            translated_text = translated_lookup.get(entry.id)
            if translated_text is None:
                unmatched.append(entry)
                continue
            if translated_text == entry.original:
                continue
            if entry.translation and not overwrite:
                continue
            self.add(
                TranslationEntry(
                    id=entry.id, key=entry.key, original=entry.original,
                    translation=translated_text, stage=1, context=entry.context,
                    form_id_with_plugin=entry.form_id_with_plugin,
                ),
                overwrite=True,
            )
            updated_count += 1

        # Phase 2：按 (original, type_field_base) 回退
        if unmatched:
            fallback_index: dict[tuple[str, str], str] = {}
            for te in all_translated:
                if te.original == "":
                    continue
                _, _, rest = te.id.partition(":")
                _, _, type_part = rest.partition("~")
                fb_key = (te.original, type_part.split("|")[0])
                # 只保留第一个命中（避免重名条目污染）
                if fb_key not in fallback_index:
                    fallback_index[fb_key] = te.original

            for entry in unmatched:
                if entry.translation and not overwrite:
                    continue
                fb_key = (entry.original, self._type_field_base(entry.context))
                translated_text = fallback_index.get(fb_key)
                if translated_text is None or translated_text == entry.original:
                    continue
                self.add(
                    TranslationEntry(
                        id=entry.id, key=entry.key, original=entry.original,
                        translation=translated_text, stage=1, context=entry.context,
                    ),
                    overwrite=True,
                )
                updated_count += 1

        return updated_count

    def update_from_strings_lookup(
            self,
            strings_lookup: "PluginStringsLookup",
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
            # 跳过已有译文（除非覆盖）
            if entry.translation and not overwrite:
                continue
            # 跳过没有 string_id 的条目（非本地化插件）
            if entry.string_id is None:
                continue

            translated_text = strings_lookup.get(entry.string_id)
            if translated_text is None:
                continue
            # 跳过译文与原文相同的条目
            if translated_text == entry.original:
                continue

            self.add(
                TranslationEntry(
                    id=entry.id,
                    key=entry.key,
                    original=entry.original,
                    translation=translated_text,
                    stage=1,
                    context=entry.context,
                    string_id=entry.string_id,
                    form_id_with_plugin=entry.form_id_with_plugin,
                ),
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

    # ---------- 未来扩展（暂不实现） ----------

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
                if dsd_dict:  # to_dsd_dict() 返回空字典表示无译文
                    result.append(dsd_dict)
        return result

    def to_json(
        self,
        *,
        ensure_ascii: bool = False,
        indent: int = 2,
    ) -> str:
        """
        导出为 JSON 字符串。
        """
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
        """
        导出为简化格式的 JSON 字符串，用于外部工具兼容。
        """
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
        """
        保存为 JSON 文件。
        """
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
        """
        保存为简化格式的 JSON 文件，用于外部工具兼容。
        """
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
        
        :param path: JSON 文件路径
        :param overwrite: 若 id 已存在，是否覆盖（默认 True）
        :return: 新的 TranslationEntryCollection 实例
        """
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        
        collection = cls()
        
        # 验证格式 - 应该是一个条目数组
        if not isinstance(data, list):
            raise ValueError("无效的 JSON 格式：应该是一个条目数组")
        
        # 从字典创建 TranslationEntry 对象
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
        """
        导出为 DSD 格式的 JSON 字符串。
        只包含有译文的条目。
        """
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
        """
        导出为 DSD 格式的 JSON 文件，用于 xEdit 脚本等外部工具。

        :param path: 输出文件路径
        :param ensure_ascii: 是否转义非 ASCII 字符
        :param indent: JSON 缩进空格数
        """
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
    ) -> "TranslationEntryCollection":
        """
        从 DSD 格式的 JSON 文件导入翻译条目。

        DSD 格式支持三种变体：
        1. 基础格式：form_id, type, string
        2. QUST CNAM：form_id, type, original, string
        3. 索引格式：form_id, type, index, string

        :param path: DSD 格式 JSON 文件路径
        :param overwrite: 若 id 已存在，是否覆盖（默认 True）
        :return: 新的 TranslationEntryCollection 实例
        """
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))

        collection = cls()

        # 验证格式
        if not isinstance(data, list):
            raise ValueError("无效的 DSD JSON 格式：应该是一个条目数组")

        # 从 DSD 字典创建 TranslationEntry 对象
        for entry_data in data:
            entry = TranslationEntry.from_dsd_dict(entry_data)
            collection.add(entry, overwrite=overwrite)

        return collection


