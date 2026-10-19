"""Keep a workbench open until accepted draft writes are handled explicitly."""

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QMessageBox

from .conflict_review_dialog import ConflictReviewDialog
from .term_edit_dialog import TermEditDialog


def choose_pending_save_action(parent, count):
    dialog = QMessageBox(parent)
    dialog.setWindowTitle("还有修改正在保存")
    dialog.setText(f"还有 {count} 项修改未完成保存。")
    dialog.setInformativeText("可以等待保存后关闭，或放弃尚未开始的修改。正在写入的修改会完成保存。")
    wait = dialog.addButton("保存完成后关闭", QMessageBox.ButtonRole.AcceptRole)
    discard = dialog.addButton("放弃排队修改并关闭", QMessageBox.ButtonRole.DestructiveRole)
    dialog.addButton("返回编辑", QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(wait)
    dialog.exec()
    return "wait" if dialog.clickedButton() is wait else "discard" if dialog.clickedButton() is discard else "cancel"


class DraftSaveCloseGuard(QObject):
    def __init__(self, window, queue):
        super().__init__(window)
        self._window = window
        self._queue = queue
        self._waiting = False
        self._context_changed = False
        self._previous_enabled = True
        queue.drained.connect(self._drained)

    def allow_close(self):
        if self._context_changed:
            if self._queue.pending_count:
                return False
            return self._close_unsaved_forms()
        if self._waiting:
            return False
        if not self._queue.pending_count:
            return self._close_unsaved_forms()
        action = choose_pending_save_action(self._window, self._queue.pending_count)
        if action == "cancel":
            return False
        if action == "discard":
            self._queue.discard_pending()
        # The nested confirmation loop may already have delivered the last save.
        if not self._queue.pending_count:
            if action == "wait" and self._queue.last_error is not None:
                return False
            return True
        self._waiting = True
        self._previous_enabled = self._window.workspace.isEnabled()
        self._window.workspace.setEnabled(False)
        self._window.operation_notice.show_message("正在完成保存，完成后将关闭窗口…")
        return False

    def context_changed(self):
        """Finish accepted writes in their original context without offering stale editing."""
        if self._context_changed:
            return
        self._context_changed = True
        self._window.workspace.setEnabled(False)
        if not self._queue.pending_count:
            if self._unsaved_forms():
                self._window.operation_notice.show_message(
                    "工程或翻译版本已切换，编辑窗口中的未保存输入已保留。请复制输入后关闭，并切回原版本重新编辑。"
                )
            else:
                self._window.close()
            return
        self._waiting = True
        self._window.operation_notice.show_message(
            "工程或翻译版本已切换，正在完成原版本的保存，完成后将关闭此窗口。可重新打开当前版本的术语工作台。"
        )

    def _unsaved_forms(self):
        return [
            dialog
            for kind in (TermEditDialog, ConflictReviewDialog)
            for dialog in self._window.findChildren(kind)
            if dialog.isVisible() and dialog.has_unsaved_inputs
        ]

    def _close_unsaved_forms(self):
        for dialog in self._unsaved_forms():
            dialog.close()
            if dialog.isVisible():
                return False
        return True

    def _drained(self, succeeded):
        if not self._waiting:
            return
        self._waiting = False
        if self._context_changed:
            if succeeded:
                QTimer.singleShot(0, self._window.close)
            else:
                self._window.operation_notice.show_message(
                    f"原工程或翻译版本有修改保存失败：{self._queue.last_error}。"
                    "请切回原版本并重新打开术语工作台，检查未保存的词条。此窗口可以关闭。"
                )
            return
        self._window.workspace.setEnabled(self._previous_enabled)
        if succeeded:
            QTimer.singleShot(0, self._window.close)
        else:
            self._window.operation_notice.show_message(
                f"部分修改保存失败：{self._queue.last_error}。窗口已保留，请重试后再关闭。"
            )
