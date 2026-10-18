"""Complete term form that stays open until the draft write has succeeded."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.terminology.models import DecisionStatus, ScopeKind, TermScope

from .draft_write_queue import DraftWriteDiscarded


@dataclass(frozen=True, slots=True)
class TermEditValues:
    original: str
    translation: str
    scope: TermScope
    variants: tuple[str, ...]
    notes: str


class TermEditDialog(QDialog):
    save_requested = pyqtSignal(object)

    def __init__(self, decision=None, parent=None, *, plugin_ids=()) -> None:
        super().__init__(parent)
        self._saving = False
        self._context_active = True
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle("新增术语" if decision is None else "编辑术语")
        self.resize(580, 540)
        layout = QVBoxLayout(self)
        note = "保存到草稿后，需发布才会用于后续翻译。"
        if getattr(decision, "status", None) is DecisionStatus.REVIEW_REQUIRED:
            note += " 保存也会确认当前译名并完成此条复核。"
        intro = QLabel(note, self)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.form_widget = QWidget(self)
        form = QFormLayout(self.form_widget)
        form.setContentsMargins(0, 0, 0, 0)
        self.original = QLineEdit(getattr(decision, "original", ""), self)
        self.original.setAccessibleName("术语原名")
        self.translation = QLineEdit(getattr(decision, "translation", ""), self)
        self.translation.setAccessibleName("术语译名")
        self.scope = QComboBox(self)
        self.scope.addItem("整个项目", ScopeKind.PROJECT)
        self.scope.addItem("指定插件", ScopeKind.PLUGIN)
        self.scope.setAccessibleName("术语使用范围")
        self.plugin = QComboBox(self)
        self.plugin.setEditable(True)
        self.plugin.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.plugin.addItems(sorted(set(plugin_ids)))
        self.plugin.setCurrentIndex(-1)
        self.plugin.setPlaceholderText("选择或输入插件名称，如 Skyrim.esm")
        self.plugin.setAccessibleName("指定插件名称")
        self.variants = QPlainTextEdit(self)
        self.variants.setPlainText("\n".join(getattr(decision, "variants", ())))
        self.variants.setPlaceholderText("每行填写一个原名别名；可留空")
        self.variants.setAccessibleName("术语别名，每行一个")
        self.variants.setMaximumHeight(100)
        self.notes = QPlainTextEdit(self)
        self.notes.setPlainText(getattr(decision, "notes", ""))
        self.notes.setPlaceholderText("说明选用此译名的原因或使用注意事项；可留空")
        self.notes.setAccessibleName("术语备注")
        self.notes.setMaximumHeight(100)
        for label, widget in (
            ("原名（必填）", self.original),
            ("译名（必填）", self.translation),
            ("使用范围", self.scope),
            ("插件", self.plugin),
            ("别名", self.variants),
            ("备注", self.notes),
        ):
            widget.setProperty("tbComponentKind", "input")
            form.addRow(label, widget)
        layout.addWidget(self.form_widget)
        current_scope = getattr(decision, "scope", TermScope.project())
        self.scope.setCurrentIndex(self.scope.findData(current_scope.kind))
        if current_scope.plugin_id:
            self.plugin.setCurrentText(current_scope.plugin_id)
        self.scope.currentIndexChanged.connect(self._scope_changed)
        self._scope_changed()
        self.error = QLabel(self)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        self.error.setAccessibleName("术语保存状态")
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("保存并确认" if decision is not None else "保存到草稿")
        self._save_label = self.save_button.text()
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._initial_inputs = self._inputs()
        context_invalidated = getattr(parent, "context_invalidated", None)
        if context_invalidated is not None:
            context_invalidated.connect(self._context_changed)

    @property
    def has_unsaved_inputs(self) -> bool:
        return self._inputs() != self._initial_inputs

    def _scope_changed(self, *_args) -> None:
        self.plugin.setEnabled(self._context_active and self.scope.currentData() is ScopeKind.PLUGIN)

    def _context_changed(self) -> None:
        self._context_active = False
        for field in (self.original, self.translation, self.variants, self.notes):
            field.setReadOnly(True)
        self.scope.setEnabled(False)
        self.plugin.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("关闭")
        self.set_saving(self._saving)

    def values(self) -> TermEditValues:
        original, translation = self.original.text().strip(), self.translation.text().strip()
        if not original:
            raise ValueError("请填写术语原名。")
        if not translation:
            raise ValueError("请填写术语译名。")
        scope = TermScope.project()
        if self.scope.currentData() is ScopeKind.PLUGIN:
            plugin_id = self.plugin.currentText().strip()
            if not plugin_id:
                raise ValueError("选择“指定插件”后，请填写插件名称。")
            scope = TermScope.plugin(plugin_id)
        variants = tuple(line.strip() for line in self.variants.toPlainText().splitlines() if line.strip())
        if len(set(variants)) != len(variants):
            raise ValueError("别名中有重复内容，请保留每个别名一次。")
        return TermEditValues(original, translation, scope, variants, self.notes.toPlainText())

    def _save(self) -> None:
        if self._saving or not self._context_active:
            return
        try:
            values = self.values()
        except ValueError as error:
            self.error.setText(str(error))
            return
        self.set_saving(True)
        self.save_requested.emit(values)

    def set_saving(self, saving: bool) -> None:
        self._saving = saving
        self.form_widget.setEnabled(not saving or not self._context_active)
        self.buttons.setEnabled(not saving)
        self.save_button.setEnabled(not saving and self._context_active)
        self.save_button.setText("正在保存…" if saving else self._save_label)
        if self._context_active:
            self.error.setText("正在保存草稿，请稍候…" if saving else "")
        else:
            self.error.setText(
                "工程或翻译版本已变化，正在完成原版本的保存。输入可复制，保存结束后可关闭。"
                if saving
                else "工程或翻译版本已变化，此窗口不再接受保存。输入已保留，可复制后关闭；"
                "请切回原版本并重新打开术语工作台。"
            )

    def save_failed(self, error: object) -> None:
        self.set_saving(False)
        if isinstance(error, DraftWriteDiscarded):
            # The workbench close confirmation already authorized discarding this input.
            super().reject()
            return
        recovery = (
            "输入已保留；若草稿已被其他操作修改，请复制输入后取消并重新打开。"
            if self._context_active
            else "工程或翻译版本已变化，输入未保存且已保留，可复制后关闭；请切回原版本并重新打开术语工作台。"
        )
        self.error.setText(f"未能保存：{error}\n{recovery}")

    def saved(self, _result=None) -> None:
        self.set_saving(False)
        self._initial_inputs = self._inputs()
        self.accept()

    def _inputs(self) -> tuple:
        return (
            self.original.text(),
            self.translation.text(),
            self.scope.currentData(),
            self.plugin.currentText(),
            self.variants.toPlainText(),
            self.notes.toPlainText(),
        )

    def reject(self) -> None:
        if self._saving:
            return
        if self.has_unsaved_inputs:
            answer = QMessageBox.question(
                self,
                "放弃未保存的修改？",
                "此词条的修改尚未保存到草稿。确定放弃这些输入吗？",
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
