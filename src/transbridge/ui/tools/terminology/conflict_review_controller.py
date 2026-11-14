# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Scoped asynchronous review, save-and-next, and guarded one-step undo."""

from dataclasses import replace

from PyQt6.QtCore import QSignalBlocker, Qt
from PyQt6.QtWidgets import QMessageBox

from transbridge.application.terminology.conflicts import ConflictResolutionOperation

from .async_actions import AsyncActionRunner


class ConflictReviewController:
    def __init__(self, window):
        self.window = window
        self.view = window.conflicts_view
        self.pane = self.view.pane
        self._selected = None
        self._undo = None
        self._busy = False
        self._saved_ref = None
        self._runner = AsyncActionRunner(self.view, on_error=self.pane.set_error)
        self.view.review_requested.connect(self.select)
        self.pane.save_requested.connect(self.save)
        self.pane.skip_requested.connect(self.skip)
        self.view.undo_button.clicked.connect(self.undo)
        window.closed.connect(self._runner.close)
        window.context_invalidated.connect(self.invalidate)
        window.terms_view.conflict_dialog.close_guard = self.allow_leave

    def allow_leave(self):
        if self._busy:
            return False
        if not self.pane.has_unsaved_inputs:
            return True
        answer = QMessageBox.question(
            self.view,
            "尚未保存译名",
            "当前选择尚未保存，放弃这次选择并继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.pane.saved()
            return True
        return False

    def select(self, conflict):
        if not self.window.context_is_current:
            return
        if not self.allow_leave():
            selection = self.view.table.selectionModel()
            with QSignalBlocker(selection):
                for row in range(self.view.model.rowCount()):
                    item = self.view.model.index(row, 0).data(Qt.ItemDataRole.UserRole)
                    if self._selected and item.conflict_group_id == self._selected.conflict_group_id:
                        self.view.table.selectRow(row)
                        self.view._selected_id = item.conflict_group_id
                        break
            return
        self._selected = conflict
        self.pane.set_loading()
        if conflict is None:
            self.pane.title.setText("当前没有需要处理的术语")
            return
        if not self.view.isVisible():
            return

        def read(token):
            token.raise_if_cancelled()
            result = self.window.presenter.conflict_details(conflict, cancellation=token)
            token.raise_if_cancelled()
            return result

        def ready(details):
            if self._selected == conflict:
                self.pane.set_details(details)

        self._runner.run_read(read, ready, key="review")

    def skip(self):
        if self.allow_leave():
            self.view.select_next()

    def save(self, choice):
        details = self.pane.details
        if self._busy or details is None:
            return
        self._set_busy(True)
        advance = self.view.review_state != "resolved" or choice.operation is ConflictResolutionOperation.IGNORE
        following = self.view.next_conflict() if advance else None
        anchor = (following or details.conflict).conflict_group_id

        def saved(result):
            self._undo = (details, result.draft.ref)
            self._saved_ref = result.draft.ref
            self.pane.saved(result)
            self._set_busy(False)
            self._resume(anchor)

        self.window._run_draft_command(
            lambda: self.window.presenter.resolve_review(
                details, choice.operation, translation=choice.translation, plugin_id=choice.plugin_id
            ),
            on_saved=saved,
            on_error=self._failed,
        )

    def _resume(self, anchor):
        # A new draft revision invalidates cursors. Resume by stable ID, not by
        # replaying pages or accepting a cursor from a different snapshot.
        self.window._conflict_filter = replace(self.window._conflict_filter, start_at=anchor)
        self.view._selected_id = anchor
        self.view.reset_button.setEnabled(True)
        self.view.reset_button.setText("完整队列")
        self.pane.set_loading()
        self.view.status.setText("已保存，继续当前位置；点击重置可查看完整队列。")
        query = self.view.model._query
        if query is not None:
            self.window.bind_page("conflicts", query.snapshot_ref.build_ref)

    def undo(self):
        if self._busy or self._undo is None or not self.allow_leave():
            return
        details, expected = self._undo
        self._set_busy(True)
        self.pane.set_saving(True)

        def saved(result):
            self._undo = None
            self._saved_ref = result.draft.ref
            self.pane.saved(result)
            self._set_busy(False)
            self.view.tabs.setCurrentIndex(
                0 if details.conflict.status == "unresolved" else 2 if details.conflict.status == "equivalent" else 1
            )
            self._resume(details.conflict.conflict_group_id)

        self.window._run_draft_command(
            lambda: self.window.presenter.undo_review(details, expected), on_saved=saved, on_error=self._failed
        )

    def _failed(self, error):
        self._set_busy(False)
        self.pane.set_error(error)

    def bind_assets(self, assets):
        ref = assets.draft.ref if assets.draft is not None else None
        if self._saved_ref is not None and ref == self._saved_ref:
            self._saved_ref = None
            return
        query = self.view.model._query
        if query is not None and query.snapshot_ref.build_ref != assets.build_ref:
            self.window._conflict_filter = replace(self.window._conflict_filter, start_at=None)
            self._undo = None
            self.view.undo_button.setEnabled(False)
        self.window.bind_page("conflicts", assets.build_ref)

    def _set_busy(self, busy):
        self._busy = busy
        self.view.queue.setEnabled(not busy)
        self.view.undo_button.setEnabled(not busy and self._undo is not None)

    def invalidate(self):
        self._runner.close()
        self._undo = None
        self.view.undo_button.setEnabled(False)
        self.pane.invalidate_context()
