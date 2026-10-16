"""User decisions for terminology drafts, separate from window lifecycle."""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QInputDialog, QMessageBox

from transbridge.application.terminology.models import DecisionStatus

from .conflict_review_dialog import ConflictReviewDialog


class DraftInteractionController:
    def __init__(self, parent, presenter, apply_command, run_read=None) -> None:
        self._parent = parent
        self.presenter = presenter
        self._run_draft_command = apply_command
        self._run_read = run_read

    def add(self) -> None:
        original, accepted = QInputDialog.getText(self._parent, "新增术语", "原名")
        if not accepted or not original.strip():
            return
        translation, accepted = QInputDialog.getText(self._parent, "新增术语", "推荐译名")
        if not accepted or not translation.strip():
            return
        self._run_draft_command(lambda: self.presenter.add_term(original.strip(), translation.strip()))

    def edit(self, decision: object) -> None:
        term_identity = str(getattr(decision, "term_id", ""))
        if not term_identity:
            return
        current = str(getattr(decision, "translation", ""))
        translation, accepted = QInputDialog.getText(self._parent, "调整译名", "推荐译名", text=current)
        if (
            accepted
            and translation.strip()
            and (translation.strip() != current or getattr(decision, "status", None) is DecisionStatus.REVIEW_REQUIRED)
        ):
            self._run_draft_command(lambda: self.presenter.change_translation(term_identity, translation.strip()))

    def toggle_suppression(self, decision: object) -> None:
        term_identity = str(getattr(decision, "term_id", ""))
        if not term_identity:
            return
        suppressed = bool(getattr(decision, "suppressed", False))
        action = "启用" if suppressed else "停用"
        answer = QMessageBox.question(
            self._parent,
            action,
            f"确定要{action}“{getattr(decision, 'original', '')}”吗？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_draft_command(lambda: self.presenter.set_suppressed(term_identity, suppressed=not suppressed))

    def review(self, conflict: object) -> None:
        if self._run_read is None:
            raise RuntimeError("冲突审核需要异步证据查询")
        self._run_read(
            lambda: self.presenter.conflict_details(conflict), self._review_ready, "正在载入全部候选和来源证据…"
        )

    def _review_ready(self, details) -> None:
        dialog = ConflictReviewDialog(details, self._parent)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.choice is None:
            return
        choice = dialog.choice
        self._run_draft_command(
            lambda: self.presenter.resolve_review(
                details, choice.operation, translation=choice.translation, plugin_id=choice.plugin_id
            )
        )


__all__ = ["DraftInteractionController"]
