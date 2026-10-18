"""Mapping table and raw edit state, including incomplete rows that cannot be saved."""

from __future__ import annotations

from PyQt6.QtCore import QModelIndex, QPersistentModelIndex, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QMessageBox, QTableView

from transbridge.application.terminology_profiles import ProfileTermMapping
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from .mapping_delegate import ProfileMappingDelegate
from .mapping_filter import ProfileMappingFilter
from .mapping_model import ProfileMappingModel


class ProfileMappingEditor(QTableView):
    dirty_changed = pyqtSignal(bool)
    search_status = pyqtSignal(bool, str)
    focus_failed = pyqtSignal(str)

    def __init__(self, parent=None, *, thread_pool=None) -> None:
        super().__init__(parent)
        self.mapping_model = ProfileMappingModel(self)
        self.filter_model = ProfileMappingFilter(self.mapping_model, self, thread_pool=thread_pool)
        self.setModel(self.filter_model)
        self._focus_request = None
        self._selected_source = QPersistentModelIndex()
        self.filter_model.rows_changing.connect(self._remember_selection)
        self.filter_model.modelReset.connect(self._restore_selection)
        self.filter_model.status_changed.connect(self._search_status)
        self._delegate = ProfileMappingDelegate(self)
        self.setItemDelegate(self._delegate)
        self._edit_triggers = self.editTriggers()
        self.mapping_model.dirty_changed.connect(self.dirty_changed)
        self.setAccessibleName("译名方案映射")
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for column in range(3):
            self.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        # Scope has a fixed short vocabulary; avoid scanning 1,000 rows after each result swap.
        self.horizontalHeader().setResizeContentsPrecision(100)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.setColumnWidth(4, 150)
        ComponentStyle.apply_static(self, ComponentKind.TABLE)

    @property
    def is_dirty(self) -> bool:
        return self.mapping_model.is_dirty

    def load(self, mappings, *, published=None) -> None:
        self._focus_request = None
        self._selected_source = QPersistentModelIndex()
        self.mapping_model.load(mappings, published=published)

    def search(self, text: str) -> None:
        self._focus_request = None
        self.commit_pending_edits(close=True)
        self.filter_model.set_query(text)

    def show_changed_only(self, enabled: bool) -> None:
        self.commit_pending_edits(close=True)
        self.filter_model.set_changed_only(enabled)

    def focus_original(self, original: str, *, scope=None) -> None:
        self._focus_request = original, scope
        self.clearSelection()
        self.setCurrentIndex(QModelIndex())
        self._selected_source = QPersistentModelIndex()
        self._locate_requested()

    def _locate_requested(self):
        if self._focus_request is None or self.filter_model.search.is_pending:
            return
        original, scope = self._focus_request
        self._focus_request = None
        rows = self.mapping_model.matching_original_rows(original, scope=scope)
        if len(rows) != 1:
            message = (
                "同名术语存在多个使用范围，请在列表中明确选择要编辑的映射。"
                if rows
                else f"此方案没有“{original}”在指定范围内的映射，请添加对应映射。"
            )
            self.focus_failed.emit(message)
            return
        index = self.filter_model.mapFromSource(self.mapping_model.index(rows[0], 2))
        if not index.isValid():
            self.focus_failed.emit("对应映射被筛选条件隐藏，请清除筛选后重试。")
            return
        self.setCurrentIndex(index)
        self.scrollTo(index)
        self.setFocus()

    def _remember_selection(self):
        source = self.filter_model.mapToSource(self.currentIndex())
        if source.isValid():
            self._selected_source = QPersistentModelIndex(source)

    def _restore_selection(self):
        if self._focus_request is None and self._selected_source.isValid():
            self.setCurrentIndex(self.filter_model.mapFromSource(QModelIndex(self._selected_source)))

    def _search_status(self, pending, error):
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers if pending else self._edit_triggers)
        self.search_status.emit(pending, error)
        if not pending and not error:
            self._locate_requested()

    def append_mapping(self, mapping=None) -> None:
        self.commit_pending_edits()
        self.mapping_model.append_mapping(mapping)
        index = self.filter_model.mapFromSource(self.mapping_model.index(self.mapping_model.rowCount() - 1, 0))
        if index.isValid():
            self.setCurrentIndex(index)
            self.scrollTo(index)

    def remove_selected(self) -> None:
        self.commit_pending_edits()
        rows = sorted(
            {self.filter_model.mapToSource(index).row() for index in self.selectionModel().selectedRows()}, reverse=True
        )
        if not rows:
            return
        names = [self.mapping_model.index(row, 0).data() or "未命名术语" for row in reversed(rows)]
        answer = QMessageBox.question(
            self,
            "移除方案映射",
            f"将从此方案草稿移除所选 {len(rows)} 条映射：\n"
            + "、".join(names[:10])
            + ("……" if len(names) > 10 else "")
            + "\n发布后，这些术语将不再由此方案提供译名。项目术语和项目译文会保留。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            for row in rows:
                self.mapping_model.removeRow(row)

    def read_mappings(self) -> tuple[ProfileTermMapping, ...]:
        self.commit_pending_edits()
        return self.mapping_model.read_mappings()

    def commit_pending_edits(self, *, close=False) -> None:
        self._delegate.commit_pending(close=close)

    def close_search(self) -> None:
        self.filter_model.close()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.close_search()
        super().closeEvent(event)
