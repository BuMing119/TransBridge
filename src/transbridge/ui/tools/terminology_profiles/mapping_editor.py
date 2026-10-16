"""Mapping table and raw edit state, including incomplete rows that cannot be saved."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from transbridge.application.terminology_profiles import ProfileTermMapping
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from .mapping_delegate import ProfileMappingDelegate
from .mapping_model import ProfileMappingModel


class ProfileMappingEditor(QTableView):
    dirty_changed = pyqtSignal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.mapping_model = ProfileMappingModel(self)
        self.setModel(self.mapping_model)
        self._delegate = ProfileMappingDelegate(self)
        self.setItemDelegate(self._delegate)
        self.mapping_model.dirty_changed.connect(self.dirty_changed)
        self.setAccessibleName("译名方案映射")
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for column in range(3):
            self.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.setColumnWidth(4, 150)
        ComponentStyle.apply_static(self, ComponentKind.TABLE)

    @property
    def is_dirty(self) -> bool:
        return self.mapping_model.is_dirty

    def load(self, mappings) -> None:
        self.mapping_model.load(mappings)

    def append_mapping(self, mapping=None) -> None:
        self.commit_pending_edits()
        self.mapping_model.append_mapping(mapping)

    def remove_selected(self) -> None:
        current = self.currentIndex()
        if current.isValid():
            self.mapping_model.removeRow(current.row())

    def read_mappings(self) -> tuple[ProfileTermMapping, ...]:
        self.commit_pending_edits()
        return self.mapping_model.read_mappings()

    def commit_pending_edits(self) -> None:
        self._delegate.commit_pending()
