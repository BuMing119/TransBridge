"""User result review and navigation backed by persisted request evidence."""

from dataclasses import replace
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from transbridge.application.assistant_requests.result_review import read_request_review
from transbridge.converter.translation_entry import STAGE_LABELS

from .change_preview_view import ChangeReviewView
from .operation_preview import operation_name

_STATUS = {
    "succeeded": "已完成",
    "completed": "已完成",
    "failed": "失败",
    "partial": "部分完成",
    "cancelled": "已取消",
    "aborted": "未执行",
    "outcome_unknown": "待核对",
}


class RequestReviewDialog(QDialog):
    def __init__(self, review, parent=None, *, failures_only=False, locate=None, open_directory=None, undo=None):
        super().__init__(parent)
        self.review = review
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("任务结果验收")
        self.resize(900, 650)
        layout = QVBoxLayout(self)
        goal = QLabel(review.goal)
        goal.setWordWrap(True)
        goal.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(goal)
        self.summary = QLabel(
            f"已保存 {len(review.entry_ids)} 条词条的修改凭据；{len(review.exported_files)} 个导出文件。"
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.filter = QCheckBox("仅查看失败或待核对记录")
        self.filter.setChecked(failures_only)
        layout.addWidget(self.filter)
        rows = tuple(
            replace(
                row,
                detail="；".join(
                    filter(
                        None,
                        (
                            row.detail,
                            f"状态：{STAGE_LABELS.get(row.before_stage, row.before_stage)} → "
                            f"{STAGE_LABELS.get(row.after_stage, row.after_stage)}"
                            if row.before_stage != row.after_stage
                            else "",
                        ),
                    )
                ),
            )
            for row in review.changes
        )
        self.changes = ChangeReviewView(rows)
        layout.addWidget(self.changes, 1)
        self.outcomes = QPlainTextEdit()
        self.outcomes.setReadOnly(True)
        self.outcomes.setMaximumHeight(160)
        self.outcomes.setAccessibleName("已保存的操作结果")
        layout.addWidget(self.outcomes)
        self.notice = QLabel("\n".join(review.notices))
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.notice)
        self.locate = QPushButton("在工作台定位第一条选中词条")
        self.locate.setVisible(
            locate is not None and bool(review.entry_ids or any(row.entry_id for row in review.failures))
        )
        self.locate.clicked.connect(lambda: locate(self._selected_ids()))
        layout.addWidget(self.locate)
        files = QHBoxLayout()
        self.files = QComboBox()
        self.files.addItems(review.exported_files)
        self.files.setVisible(bool(review.exported_files))
        self.open_folder = QPushButton("打开导出位置")
        self.open_folder.setVisible(bool(review.exported_files) and open_directory is not None)
        self.open_folder.clicked.connect(lambda: open_directory(self.files.currentText()))
        files.addWidget(self.files, 1)
        files.addWidget(self.open_folder)
        layout.addLayout(files)
        self.undo_summary = QLabel(review.undo_summary)
        self.undo_summary.setWordWrap(True)
        self.undo_summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.undo_summary)
        self.undo = QPushButton("撤销这次输入产生的修改")
        self.undo.setVisible(review.undo_available and undo is not None)
        self.undo.clicked.connect(lambda: undo())
        layout.addWidget(self.undo)
        self.filter.toggled.connect(self._filter)
        self._filter(failures_only)

    def _selected_ids(self):
        if self.filter.isChecked():
            return tuple(dict.fromkeys(row.entry_id for row in self.review.failures if row.entry_id))
        return self.changes.selected_ids()

    def _filter(self, only_failures):
        self.changes.setVisible(not only_failures and bool(self.review.changes))
        rows = self.review.failures if only_failures else self.review.outcomes
        text = "\n\n".join(
            f"{operation_name({'tool': row.tool})} · {_STATUS.get(row.status, row.status)}"
            + (f" · {row.entry_id}" if row.entry_id else "")
            + f"\n{row.message}"
            for row in rows
        )
        self.outcomes.setPlainText(
            text or "没有保存失败明细；这不代表所有词条均已成功。"
            if only_failures
            else text or "没有保存操作结果明细。"
        )
        self.locate.setEnabled(bool(self._selected_ids()))


class RequestResultReviewBinding:
    def __init__(self, binding, *, locate_entries=None, open_directory=None):
        self.binding = binding
        self.locate_entries = locate_entries
        self.open_directory = open_directory or self._open_directory
        self._closed = False
        self._sequence = 0
        self.dialog = None

    def _current(self, context, generation, sequence):
        return (
            not self._closed
            and not self.binding._closed
            and self.binding.context == context
            and self.binding._turn_generation == generation
            and self._sequence == sequence
        )

    def show(self, request_id, failures_only=False):
        if self._closed or self.binding._closed:
            return
        self.invalidate()
        context, generation, sequence = self.binding.context, self.binding._turn_generation, self._sequence

        def received(review):
            if not self._current(context, generation, sequence):
                return
            scope = dict(review.scope)
            target = replace(context, project_id=scope.get("project_id"), variant_id=scope.get("variant_id"))

            def action(callback, *args):
                if not self._current(context, generation, sequence):
                    self.invalidate()
                    return
                try:
                    callback(*args)
                except Exception as exc:
                    if self.dialog is not None and not sip.isdeleted(self.dialog):
                        self.dialog.notice.setText(f"操作未完成：{exc}")

            def undo():
                self.binding.undo.request(request_id)
                self.invalidate()

            dialog = RequestReviewDialog(
                review,
                self.binding.facade,
                failures_only=failures_only,
                locate=(lambda ids: action(self.locate_entries, target, ids))
                if self.locate_entries is not None
                else None,
                open_directory=lambda path: action(self.open_directory, str(Path(path).parent)),
                undo=lambda: action(undo),
            )
            self.dialog = dialog
            dialog.show()

        self.binding.background.submit(
            lambda: read_request_review(self.binding.service, context, request_id),
            received,
            context=context,
            failed=lambda exc: self._failed(context, generation, sequence, exc),
        )

    def _failed(self, context, generation, sequence, exc):
        if self._current(context, generation, sequence):
            self.binding.facade.add_system_message(f"无法读取该任务的已保存结果：{exc}")

    @staticmethod
    def _open_directory(directory):
        path = Path(directory)
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("已记录的导出目录不存在，请核对文件是否已移动。")
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            raise OSError("系统未能打开导出目录。")

    def invalidate(self):
        self._sequence += 1
        if self.dialog is not None and not sip.isdeleted(self.dialog):
            self.dialog.close()
        self.dialog = None

    def close(self):
        self._closed = True
        self.invalidate()
