from collections.abc import Iterable, Iterator
from typing import Optional

from src.transbridge.converter.translation_entry import TranslationEntry


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

    # ---------- 查询 / 过滤（可扩展） ----------

    def filter(self, predicate) -> list[TranslationEntry]:
        """
        按自定义条件过滤。
        示例：
            coll.filter(lambda e: e.stage == 1)
        """
        return [e for e in self._entries.values() if predicate(e)]

    # ---------- 未来扩展（暂不实现） ----------

    def to_dicts(self) -> list[dict]:
        """
        预留：导出为 dict 列表（json / pandas / db）
        """
        raise NotImplementedError

    def to_json(self) -> str:
        """
        预留：导出为 JSON
        """
        raise NotImplementedError
