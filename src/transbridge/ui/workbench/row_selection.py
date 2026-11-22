# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Address the workbench's Qt row selection through stable entry identities."""

from transbridge.application.io.identity import EntryKey


class WorkbenchRowSelection:
    def __init__(self, preview):
        self.preview = preview
        preview._ctx.workbench_row_selection = self
        preview.destroyed.connect(self.close)

    def close(self):
        if self.preview._ctx.workbench_row_selection is self:
            self.preview._ctx.workbench_row_selection = None

    def entries(self):
        paging, table = self.preview._paging, self.preview._table
        if paging.query_ready:
            return tuple(
                entry for first, last in table.selected_row_spans() for entry in paging.result.entries[first : last + 1]
            )
        return tuple(
            entry
            for first, last in table.selected_row_spans()
            for row in range(first, last + 1)
            if (entry := table.entry_at_row(row)) is not None
        )

    def is_complete(self):
        paging = self.preview._paging
        return paging.collection is None or paging.query_ready or not self.preview._table.selected_row_spans()

    def apply(self, entry_ids, action):
        preview, table = self.preview, self.preview._table
        collection = preview._ctx.collection
        if collection is None or preview._filter_scope != preview._current_filter_scope(collection):
            raise ValueError("活动来源已变化，请等待工作台载入后重新选择。")
        if action == "clear":
            table.clearSelection()
            return
        if action not in {"select", "deselect"}:
            raise ValueError("请选择选中、取消选中或清空表格选区。")
        paging = preview._paging
        if paging.collection is not None:
            # Require the projected filter and sort, even for a loaded first page.
            paging.filtered_entries()
        rows = []
        for key in entry_ids:
            if not isinstance(key, str) or not key:
                raise ValueError("词条标识必须是非空字符串。")
            if paging.collection is not None:
                alias_row = table.find_entry_row(-1, key)
                try:
                    identity = EntryKey.deserialize(key)
                except ValueError:
                    if paging.ambiguous_row_id(key):
                        raise ValueError("词条标识有歧义，请使用完整词条身份。") from None
                    identity = EntryKey(paging.collection.source.info.namespace, key)
                row = table.row_for_identity(identity)
                if row < 0:
                    row = alias_row
                if alias_row >= 0 and alias_row != row:
                    raise ValueError("词条标识有歧义，请使用完整词条身份。")
            else:
                entry = collection.get(key)
                row = -1 if entry is None else table.row_for_identity(entry.identity)
            if row >= 0:
                entry = table.entry_at_row(row)
                if entry.requires_original_match and key != entry.identity.serialize():
                    raise ValueError("请使用查询结果中包含原文的完整词条身份。")
            if row < 0:
                raise ValueError("词条不在当前工作台筛选结果中或标识有歧义，请使用查询结果中的完整 key。")
            rows.append(row)
        table.select_rows(rows, select=action == "select")
