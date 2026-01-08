from collections import defaultdict
from collections.abc import Iterable, Iterator
from typing import Optional,Any
from pathlib import Path
import json

from src.transbridge.converter.translation_entry import TranslationEntry
from src.transbridge.parser.eet_parser import EET_XmlParser
from src.transbridge.parser.plugin_parser import PluginParser
from src.transbridge.parser.xt_parser import XT_Entry


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
                entry = TranslationEntry.from_eet_entry(eet_entry)
                collection.add(entry, overwrite=overwrite)

            return collection

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
            left, _, right = entry.id.partition(":")

            # list_id = 0 → edid = a
            # list_id = 1 → edid = [b]
            candidate_edids = (left, f"[{right}]")

            # ---------- 3. 只尝试可能匹配的 XT ----------

            for edid in candidate_edids:
                for xt in xt_by_edid.get(edid, []):
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

    def to_dict(self) -> dict[str, Any]:
        """
        将整个集合序列化为 dict（用于 JSON / DB）。
        """
        return {
            "version": 1,
            "count": len(self._entries),
            "entries": [e.to_dict() for e in self._entries.values()],
        }

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


