"""Inline translation commits and recoverable drafts, independent of table rendering."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from PyQt6.QtCore import QObject, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from transbridge.application.io.mutation import EntrySnapshot
from transbridge.converter.translation_entry import STAGE_TRANSLATED, STAGE_UNTRANSLATED, TranslationEntry
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState


def _scope(context) -> tuple:
    return context.active_version_identity, context.active_key


@dataclass
class _FailedEdit:
    scope: tuple
    before: EntrySnapshot
    text: str
    label: str
    error: str = ""

    @property
    def key(self) -> tuple:
        return self.scope, self.before.entry_key


class InlineEditRecoveryView(QFrame):
    """Retain failed inputs visibly until applied or explicitly discarded."""

    def __init__(self, parent, *, retry, discard):
        super().__init__(parent)
        self.setAccessibleName("未提交的译文草稿")
        ComponentStyle.apply_static(self, ComponentKind.NOTIFICATION)
        ComponentStyle.apply_state(self, SemanticState.WARNING)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("未提交的译文草稿", self))
        self.drafts = QComboBox(self)
        self.drafts.setAccessibleName("选择需要恢复的译文草稿")
        self.drafts.setMinimumContentsLength(12)
        self.drafts.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        header.addWidget(self.drafts, 1)
        self.retry_button = QPushButton("重试提交", self)
        self.retry_button.clicked.connect(retry)
        header.addWidget(self.retry_button)
        self.copy_button = QPushButton("复制草稿", self)
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        header.addWidget(self.copy_button)
        self.discard_button = QPushButton("放弃此草稿", self)
        self.discard_button.clicked.connect(discard)
        header.addWidget(self.discard_button)
        layout.addLayout(header)
        self.message = QLabel(self)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.message)
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setAccessibleName("提交失败时保留的完整译文")
        self.text.setMaximumHeight(90)
        layout.addWidget(self.text)
        self.hide()


class InlineEditController(QObject):
    """Own the commit boundary without retaining view indices or mutable editor widgets."""

    def __init__(self, context, table, *, refresh_entry, parent):
        super().__init__(parent)
        self.context = context
        self.table = table
        self._refresh_entry = refresh_entry
        self._failed: dict[tuple, _FailedEdit] = {}
        self.view = InlineEditRecoveryView(parent, retry=self.retry, discard=self.discard)
        self.view.drafts.currentIndexChanged.connect(self._show_selected)
        context.collection_changed.connect(self._show_selected)
        context.variant_changed.connect(self._show_selected)
        context.project_changed.connect(self._show_selected)

    def edit_requested(self, entry: TranslationEntry, text: str, row: int) -> None:
        if self.table.terminology_profile_active:
            return
        if not isinstance(entry, TranslationEntry) or not entry.id:
            return
        # Capture before committing: projection notifications can replace the model session.
        draft = _FailedEdit(
            _scope(self.context),
            entry.snapshot(),
            text,
            f"{self.context.project_name or '当前内容'} · {entry.key} · {entry.original[:40]}",
        )
        self._commit(draft, entry, row)

    def _commit(self, draft: _FailedEdit, entry: TranslationEntry, row: int = -1) -> None:
        old_translation, old_stage = entry.translation, entry.stage
        translation = draft.text.strip()
        if translation == "（无译文）":
            translation = ""
        stage = STAGE_TRANSLATED if translation and entry.stage == STAGE_UNTRANSLATED else entry.stage
        try:
            if self.context.uses_authoritative_projection:
                result = self.context.update_projected_entry(entry.identity, translation=translation, stage=stage)
                if not result.is_success:
                    self._retain(draft, result.diagnostics[0].message if result.diagnostics else "工程提交失败")
                    return
        except Exception as exc:  # GUI boundary: retain the full draft and original diagnostic.
            logging.getLogger(__name__).exception("Inline translation commit failed")
            self._retain(draft, f"提交译文失败：{exc}")
            return
        if _scope(self.context) == draft.scope:
            entry.translation, entry.stage = translation, stage
            if not self.context.uses_authoritative_projection:
                self.context.mark_dirty()
            self._refresh_entry(
                entry,
                preferred_row=row,
                old_stage=old_stage,
                translation_filter_may_change=old_translation != translation,
            )
        self._failed.pop(draft.key, None)
        self._refresh_feedback()

    def _retain(self, draft: _FailedEdit, error: str) -> None:
        draft.error = error
        self._failed[draft.key] = draft
        if _scope(self.context) == draft.scope:
            collection = self.context.collection
            current = None if collection is None else collection.get(draft.before.entry_key)
            if current is not None:
                self.table.update_rendered_entry(current)
        self._refresh_feedback(selected=draft.key)

    def _selected(self) -> _FailedEdit | None:
        return self._failed.get(self.view.drafts.currentData())

    def _retry_reason(self, draft: _FailedEdit) -> str | None:
        if _scope(self.context) != draft.scope:
            return "工程、版本或翻译内容已切换；请返回原内容再重试，或复制草稿。"
        if self.table.terminology_profile_active:
            return "请先切换为“不应用方案”，再重试提交草稿。"
        collection = self.context.collection
        current = None if collection is None else collection.get(draft.before.entry_key)
        if current is None or current.snapshot() != draft.before:
            return "词条已变化或移除，不能覆盖新内容。请复制草稿，重新打开词条核对。"
        return None

    def retry(self) -> None:
        draft = self._selected()
        if draft is None:
            return
        reason = self._retry_reason(draft)
        if reason:
            self._show_selected()
            return
        self._commit(draft, self.context.collection.get(draft.before.entry_key))

    def discard(self) -> None:
        draft = self._selected()
        if draft is not None:
            self._failed.pop(draft.key)
            self._refresh_feedback()

    def confirm_close(self) -> bool:
        """Require an explicit decision before destroying in-memory failed inputs."""
        drafts = tuple(self._failed.values())
        if not drafts:
            return True
        answer = QMessageBox.question(
            self.table.window(),
            "译文草稿尚未提交",
            f"仍有 {len(drafts)} 条译文草稿提交失败，保存工程不会保存这些草稿。\n"
            "取消关闭后可重试提交或复制草稿；选择放弃将丢失这些未提交的译文。",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Discard:
            return False
        # A modal dialog runs an event loop. A newer draft must never inherit
        # the discard permission granted for the inputs shown before it opened.
        for draft in drafts:
            if self._failed.get(draft.key) is draft:
                self._failed.pop(draft.key)
        self._refresh_feedback()
        return not self._failed

    def _refresh_feedback(self, *, selected=None) -> None:
        selected = self.view.drafts.currentData() if selected is None else selected
        self.view.drafts.blockSignals(True)
        self.view.drafts.clear()
        for draft in self._failed.values():
            self.view.drafts.addItem(draft.label, draft.key)
        index = self.view.drafts.findData(selected)
        self.view.drafts.setCurrentIndex(max(0, index))
        self.view.drafts.blockSignals(False)
        self._show_selected()

    def _show_selected(self, *_args) -> None:
        draft = self._selected()
        self.view.setVisible(draft is not None)
        if draft is None:
            return
        reason = self._retry_reason(draft)
        self.view.message.setText(
            f"{draft.error}\n{reason or '草稿尚未提交；表格仍显示已提交的译文。'}"
            "\n草稿尚未保存；关闭窗口时需要重试、复制或明确放弃。"
        )
        self.view.text.setPlainText(draft.text)
        self.view.retry_button.setEnabled(reason is None)
        self.view.retry_button.setToolTip(reason or "重新提交这条保留的译文")
