from __future__ import annotations
from collections import defaultdict
from collections.abc import Iterable, Iterator
from typing import Optional,Any
from pathlib import Path
import json

from src.transbridge.converter.translation_entry import TranslationEntry
from src.transbridge.parser.eet_parser import EET_XmlParser, EET_Entry
from src.transbridge.parser.plugin_parser import PluginParser
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
        self._entries: dict[str, TranslationEntry] = {}

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
        if not overwrite and entry.id in self._entries:
            return
        self._entries[entry.id] = entry

    def get(self, entry_id: str) -> Optional[TranslationEntry]:
        """按 id 获取 TranslationEntry；不存在返回 None"""
        return self._entries.get(entry_id)

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

    def update_from_eet_xml(
            self,
            path: str | Path,
    ) -> int:
        """
        从 EET XML 文件中更新已存在的翻译项。

        规则：
        - 只更新已存在的 TranslationEntry
        - 匹配条件：id 和 original 必须一致
        - 只更新 translation 和 stage 字段，其他字段保持不变
        - 如果 EET 中 traduit 为空，则不更新

        :param path: EET XML 文件路径
        :return: 实际发生更新的条目数量
        """
        parser = EET_XmlParser.from_file(path)
        updated_count = 0

        # 预先按 id 分组 EET 条目（加速匹配）
        eet_by_id: dict[str, list[EET_Entry]] = defaultdict(list)
        for eet_entry in parser:
            # 构建 EET 条目的 id，与 TranslationEntry.id 格式一致
            if eet_entry.edid:
                eet_id = f"{eet_entry.edid}:{eet_entry.id}|{eet_entry.index}"
            else:
                eet_id = f"None:{eet_entry.id}|{eet_entry.index}"
            eet_by_id[eet_id].append(eet_entry)

        # 遍历已存在的 TranslationEntry
        for entry in list(self._entries.values()):
            # 查找匹配的 EET 条目
            matching_eet_entries = eet_by_id.get(entry.id, [])

            for eet_entry in matching_eet_entries:
                # 检查 original 是否匹配
                if eet_entry.original != entry.original:
                    continue

                # 检查 traduit 是否为空
                if not eet_entry.traduit:
                    continue

                # 更新 translation 和 stage
                updated_entry = TranslationEntry(
                    id=entry.id,
                    key=entry.key,
                    original=entry.original,
                    translation=eet_entry.traduit,
                    stage=1 if eet_entry.status == 99 or eet_entry.traduit else 0,
                    context=entry.context
                )

                self._entries[entry.id] = updated_entry
                updated_count += 1
                # 每个条目只更新一次
                break

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

        规则：
        - XT 不能创建新 TranslationEntry
        - 只能更新已存在且匹配的 entry
        - 是否更新由 TranslationEntry.try_update_from_xt 决定

        :return: 实际发生更新的条目数量
        """

        # ---------- 1. 预先按 edid 分组 XT（加速匹配） ----------

        xt_by_edid: dict[str, list[XT_Entry]] = defaultdict(list)
        for xt in xt_entries:
            xt_by_edid[xt.edid].append(xt)

        updated_count = 0

        # ---------- 2. 遍历已有 TranslationEntry ----------

        for entry in list(self._entries.values()):
            # TranslationEntry.id = "a:b"
            left, _, right_with_other = entry.id.partition(":")
            right = right_with_other.split("|")[0]

            # list_id = 0 → edid = a
            # list_id = 1 → edid = [b]
            candidate_edids = (left, f"[{right}]")

            # ---------- 3. 只尝试可能匹配的 XT ----------

            for edid in candidate_edids:
                for xt in xt_by_edid.get(edid, []):
                    # 注意：现在原来的key值存储在context中，所以try_update_from_xt会使用entry.context进行匹配
                    updated = TranslationEntry.try_update_from_xt(entry, xt)

                    if updated is None:
                        continue

                    if updated is not entry:
                        self._entries[entry.id] = updated
                        entry = updated
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


