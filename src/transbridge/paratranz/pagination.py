"""Validate declared pagination totals before trusting a remote snapshot."""

from collections.abc import Mapping


class SnapshotPagination:
    def __init__(self) -> None:
        self.total: int | None = None
        self.pages: int | None = None

    def complete(self, payload, *, page: int, received: int, accumulated: int, requested_size: int) -> bool:
        if not isinstance(payload, Mapping):
            return received < requested_size
        actual_page = payload.get("page", page)
        if isinstance(actual_page, bool) or not isinstance(actual_page, int) or actual_page != page:
            raise ValueError("云端分页页码与请求不一致")
        size = payload.get("pageSize", requested_size)
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise ValueError("云端分页 pageSize 无效")
        for name, attribute in (("rowCount", "total"), ("pageCount", "pages")):
            value = payload.get(name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"云端分页 {name} 无效")
            previous = getattr(self, attribute)
            if previous is not None and previous != value:
                raise ValueError("云端分页总数在读取时变化，本次读取未完成，请重试")
            setattr(self, attribute, value)
        if self.total is not None:
            if accumulated > self.total:
                raise ValueError("云端分页条目数超过声明总数")
            if accumulated == self.total:
                return True
            if not received or (self.pages is not None and page >= self.pages):
                raise ValueError("云端分页尚未返回全部条目，本次读取未完成，请重试")
            return False
        if self.pages is not None:
            if page >= self.pages:
                return True
            if not received:
                raise ValueError("云端分页意外提前结束，本次读取未完成，请重试")
            return False
        return received < size
