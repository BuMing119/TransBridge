"""Review the actual alternatives and evidence before recording a draft decision."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from transbridge.application.terminology.conflicts import ConflictResolutionOperation

from .draft_write_queue import DraftWriteDiscarded


@dataclass(frozen=True, slots=True)
class ConflictReviewChoice:
    operation: ConflictResolutionOperation
    translation: str | None = None
    plugin_id: str | None = None


class _EvidenceModel(QAbstractTableModel):
    headers = ("原文", "译文", "插件", "来源", "条目", "上下文")

    def __init__(self, evidence, parent=None):
        super().__init__(parent)
        self._items = tuple(evidence)

    def rowCount(self, parent=QModelIndex()):  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802 - Qt API
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        item = self._items[index.row()]
        return (
            item.original,
            item.translation,
            item.plugin_scope or "整个工程",
            " → ".join(item.source_chain),
            item.entry_key,
            item.context,
        )[index.column()]


class ConflictReviewDialog(QDialog):
    save_requested = pyqtSignal(object)

    def __init__(self, details, parent=None):
        super().__init__(parent)
        self.details = details
        self.choice = None
        self._saving = False
        self._submitted = False
        self._context_active = True
        self.setWindowTitle("审核同名异译")
        self.resize(960, 620)
        layout = QVBoxLayout(self)
        title = QLabel(f"原名：{details.conflict.normalized_original}", self)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)
        current = (
            "；".join(
                f"{item.scope.plugin_id or '整个工程'}：{'已停用' if item.suppressed else item.translation}"
                for item in details.decisions
            )
            or "尚未决定"
        )
        self.current_label = QLabel(f"当前草稿决定：{current}", self)
        self.current_label.setTextFormat(Qt.TextFormat.PlainText)
        self.current_label.setWordWrap(True)
        layout.addWidget(self.current_label)
        self.candidates_label = QLabel(
            "\n".join(
                f"{variant.normalized_translation} · {len(variant.evidence_ids)} 条证据"
                for variant in details.conflict.variants
            ),
            self,
        )
        self.candidates_label.setTextFormat(Qt.TextFormat.PlainText)
        self.candidates_label.setWordWrap(True)
        layout.addWidget(self.candidates_label)
        layout.addWidget(QLabel(f"全部来源证据（{len(details.evidence)} 条；出现次数仅供参考）", self))
        self.evidence = QTableView(self)
        self.evidence.setModel(_EvidenceModel(details.evidence, self.evidence))
        self.evidence.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.evidence.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.evidence.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.evidence.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.evidence, 1)
        form = QFormLayout()
        self.operation = QComboBox(self)
        self.operation.addItem("统一采用译名", ConflictResolutionOperation.UNIFY)
        self.operation.addItem("仅为指定插件采用译名", ConflictResolutionOperation.PLUGIN_EXCEPTION)
        self.operation.addItem("排除此术语（发布后停用）", ConflictResolutionOperation.IGNORE)
        self.translation = QComboBox(self)
        self.translation.setEditable(True)
        self.translation.addItems([variant.normalized_translation for variant in details.conflict.variants])
        selected_plugin = getattr(details.conflict, "status", "") == "plugin_exception"
        effective = next(
            (
                item
                for item in details.decisions
                if not item.suppressed and bool(item.scope.plugin_id) == selected_plugin
            ),
            None,
        )
        if effective is not None:
            self.translation.setCurrentText(effective.translation)
        self.plugin = QComboBox(self)
        self.plugin.addItems(list(details.plugin_ids))
        if selected_plugin and effective is not None:
            self.operation.setCurrentIndex(1)
            self.plugin.setCurrentText(effective.scope.plugin_id)
        elif getattr(details.conflict, "status", "") == "ignored":
            self.operation.setCurrentIndex(2)
        form.addRow("处理方式", self.operation)
        form.addRow("采用译名（可输入）", self.translation)
        form.addRow("插件", self.plugin)
        layout.addLayout(form)
        self.keep_button = QPushButton("确认保留当前译名", self)
        self.keep_button.setVisible(effective is not None)
        self.keep_button.clicked.connect(lambda: self._keep(effective))
        layout.addWidget(self.keep_button)
        self.explanation = QLabel("保存只修改当前草稿；检查发布影响并发布后才会用于后续翻译。", self)
        self.explanation.setWordWrap(True)
        layout.addWidget(self.explanation)
        self.error = QLabel(self)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        self.error.setAccessibleName("审核决定保存状态")
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存审核决定")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("稍后处理")
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.operation.currentIndexChanged.connect(self._update_controls)
        self.translation.currentTextChanged.connect(self._update_controls)
        self._update_controls()
        self._initial_inputs = self._inputs()
        context_invalidated = getattr(parent, "context_invalidated", None)
        if context_invalidated is not None:
            context_invalidated.connect(self._context_changed)

    def _keep(self, decision):
        if decision is None or self._saving or not self._context_active:
            return
        self.operation.setCurrentIndex(1 if decision.scope.plugin_id else 0)
        self.translation.setCurrentText(decision.translation)
        if decision.scope.plugin_id:
            self.plugin.setCurrentText(decision.scope.plugin_id)
        self._save()

    def _update_controls(self, *_args):
        operation = self.operation.currentData()
        excluded = operation is ConflictResolutionOperation.IGNORE
        scoped = operation is ConflictResolutionOperation.PLUGIN_EXCEPTION
        editable = not self._saving and self._context_active
        self.operation.setEnabled(editable)
        self.translation.setEnabled(not excluded and (not self._saving or not self._context_active))
        self.plugin.setEnabled(editable and scoped)
        self.keep_button.setEnabled(editable)
        valid = excluded or (bool(self.translation.currentText().strip()) and (not scoped or self.plugin.count() > 0))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(editable and valid)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setEnabled(not self._saving)
        self.explanation.setText(
            "排除会在发布后停用此原名的项目范围决定；已有插件特例保持不变，可在草稿中分别停用。"
            if excluded
            else "保存只修改当前草稿；检查发布影响并发布后才会用于后续翻译。"
        )

    def _save(self):
        if not self.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled():
            return
        operation = self.operation.currentData()
        if operation is ConflictResolutionOperation.IGNORE:
            answer = QMessageBox.question(
                self,
                "排除此术语",
                "确认在下一次发布后停用此原名的项目范围决定吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.choice = ConflictReviewChoice(
            operation,
            None if operation is ConflictResolutionOperation.IGNORE else self.translation.currentText().strip(),
            self.plugin.currentText() if operation is ConflictResolutionOperation.PLUGIN_EXCEPTION else None,
        )
        self._submitted = True
        self.set_saving(True)
        self.save_requested.emit(self.choice)

    @property
    def has_unsaved_inputs(self) -> bool:
        return self._submitted or self._inputs() != self._initial_inputs

    def _inputs(self) -> tuple:
        return self.operation.currentData(), self.translation.currentText(), self.plugin.currentText()

    def _context_changed(self) -> None:
        self._context_active = False
        # Keep the submitted text available for selection and copying in the old context.
        text = self.translation.currentText()
        self.translation.clear()
        self.translation.setEditText(text)
        self.translation.lineEdit().setReadOnly(True)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("关闭")
        self.set_saving(self._saving)

    def set_saving(self, saving: bool) -> None:
        self._saving = saving
        self._update_controls()
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("正在保存…" if saving else "保存审核决定")
        if self._context_active:
            self.error.setText("正在保存草稿，请稍候…" if saving else "")
        else:
            self.error.setText(
                "工程或翻译版本已变化，正在完成原版本的保存。输入可复制，保存结束后可关闭。"
                if saving
                else "工程或翻译版本已变化，此窗口不再接受保存。输入已保留，可复制后关闭；请切回原版本并重新打开审核。"
            )

    def saved(self, _result=None) -> None:
        self.set_saving(False)
        self._submitted = False
        self._initial_inputs = self._inputs()
        self.accept()

    def save_failed(self, error: object) -> None:
        self.set_saving(False)
        if isinstance(error, DraftWriteDiscarded):
            # The workbench close confirmation already authorized discarding this choice.
            super().reject()
            return
        recovery = (
            "审核决定已保留，可重试；若草稿已变化，请复制输入后取消并重新打开审核。"
            if self._context_active
            else "工程或翻译版本已变化，输入未保存且已保留，可复制后关闭；请切回原版本并重新打开审核。"
        )
        self.error.setText(f"未能保存：{error}\n{recovery}")

    def reject(self) -> None:
        if self._saving:
            return
        if self.has_unsaved_inputs:
            answer = QMessageBox.question(
                self,
                "放弃未保存的审核决定？",
                "审核决定尚未保存到草稿。确定放弃这些输入吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._saving:
            event.ignore()
        else:
            super().closeEvent(event)
