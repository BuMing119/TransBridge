"""润色结果预览：后台准备数据，虚拟表逐条接受或拒绝。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState

from ._theme_support import AiThemeBinding
from .polish_preview_model import ACCEPTED, FAILED, PENDING, REJECTED, PolishPreviewModel, build_preview
from .read_projection import LatestReadController

if TYPE_CHECKING:
    from transbridge.ai_translator.post_processor.polisher import PolishResult
    from transbridge.converter.translation_entry import TranslationEntry


class _PolishPreviewDialog(QDialog):
    """Keep candidates and choices separate from their visible table cells."""

    def __init__(
        self,
        entries: list[TranslationEntry],
        results: dict[str, PolishResult],
        parent=None,
        *,
        theme_view: ThemeView | None = None,
    ):
        super().__init__(parent)
        self._entries, self._results = entries, results
        self._loading = True
        self._closed = False
        self._reader = LatestReadController(build_preview, self)
        self._reader.ready.connect(self._loaded)
        self._reader.failed.connect(self._failed)
        self.setWindowTitle("润色结果预览")
        self.resize(900, 550)
        self._init_ui()
        self._theme_binding = AiThemeBinding(self, theme_view, self._apply_theme)
        self._populate()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._table.hasFocus():
            event.accept()
            return
        super().keyPressEvent(event)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        toolbar = QHBoxLayout()
        self._accept_all_btn = QPushButton("全部采用候选")
        self._accept_all_btn.clicked.connect(self._on_accept_all)
        toolbar.addWidget(self._accept_all_btn)
        self._reject_all_btn = QPushButton("全部保留原译文")
        self._reject_all_btn.clicked.connect(self._on_reject_all)
        toolbar.addWidget(self._reject_all_btn)
        toolbar.addStretch()
        self._stats_lbl = QLabel()
        toolbar.addWidget(self._stats_lbl)
        layout.addLayout(toolbar)
        hint = QLabel("在“采用候选”列勾选采用、取消勾选保留原译文；确认应用前可随时更改。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._table = QTableView()
        self._model = PolishPreviewModel(self)
        self._table.setModel(self._model)
        self._model.choices_changed.connect(self._update_stats)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self._resize_choice_column()
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setTabKeyNavigation(False)
        self._table.verticalHeader().setVisible(False)
        self._table.clicked.connect(self._clicked)
        layout.addWidget(self._table)
        bottom = QHBoxLayout()
        bottom.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(cancel_btn)
        self._apply_btn = QPushButton("确认应用")
        ComponentStyle.apply_static(self._apply_btn, ComponentKind.BUTTON)
        ComponentStyle.apply_state(self._apply_btn, SemanticState.PRIMARY)
        self._apply_btn.clicked.connect(self._on_apply)
        bottom.addWidget(self._apply_btn)
        layout.addLayout(bottom)

    def _populate(self):
        self._loading = True
        self._stats_lbl.setText("正在整理润色结果…")
        self._set_actions_enabled(False)
        self._reader.submit((self._entries, self._results))

    @pyqtSlot(object, object)
    def _loaded(self, _request, projection):
        self._loading = False
        self._model.set_projection(projection)
        self._set_actions_enabled(True)

    @pyqtSlot(str)
    def _failed(self, error):
        self._loading = False
        self._stats_lbl.setText(f"无法整理润色结果：{error}")

    def _set_actions_enabled(self, enabled):
        for button in (self._accept_all_btn, self._reject_all_btn, self._apply_btn):
            button.setEnabled(enabled)

    def _clicked(self, index):
        self._on_cell_clicked(index.row(), index.column())

    def _on_cell_clicked(self, row, col):
        if col == 4 and 0 <= row < self._model.rowCount():
            self._model.set_status(row, REJECTED if self._model.status(row) == ACCEPTED else ACCEPTED)

    def _on_accept_all(self):
        if not self._loading:
            self._model.set_all(ACCEPTED)

    def _on_reject_all(self):
        if not self._loading:
            self._model.set_all(REJECTED)

    def _update_stats(self):
        counts = self._model.counts
        self._stats_lbl.setText(
            f"采用 {counts[ACCEPTED]} · 保留 {counts[REJECTED]} · 待选 {counts[PENDING]} · "
            f"失败 {counts[FAILED]} · 共 {self._model.rowCount()} 条"
        )
        self._stats_lbl.setAccessibleName("润色选择状态")
        self._stats_lbl.setAccessibleDescription(self._stats_lbl.text())

    def _apply_theme(self, binding):
        self._resize_choice_column()
        if binding.domain is not None:
            self._model.set_backgrounds({
                status: binding.domain.diff(key).background
                for status, key in (
                    (ACCEPTED, "added"),
                    (REJECTED, "removed"),
                    (PENDING, "changed"),
                    (FAILED, "unchanged"),
                )
            })

    def _resize_choice_column(self):
        self._table.setColumnWidth(6, self._table.fontMetrics().horizontalAdvance("保留原译文") + 48)

    @property
    def theme_revision(self):
        return self._theme_binding.revision

    @property
    def _row_status(self):
        return {row: self._model.status(row) for row in range(self._model.rowCount())}

    def _on_apply(self):
        if self._loading or not self._apply_btn.isEnabled():
            return
        pending = self._model.counts[PENDING]
        if pending:
            from PyQt6.QtWidgets import QMessageBox

            reply = QMessageBox.question(
                self,
                "待处理条目",
                f"还有 {pending} 条未处理，未处理的条目将保留原译文。\n\n确定继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.accept()

    def done(self, result):
        self._closed = True
        self._reader.invalidate()
        super().done(result)

    def showEvent(self, event):
        if self._closed and self._loading:
            self._populate()
        self._closed = False
        super().showEvent(event)

    def get_results(self) -> dict[str, str | None]:
        """Return candidate translations for accepted entries; otherwise None."""
        return self._model.results()
