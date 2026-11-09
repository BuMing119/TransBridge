# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Choose one batch policy for an explicitly selected import source."""

from PyQt6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from transbridge.application.terminology_profiles import TerminologyProfileImportPolicy
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle


class TerminologyImportPolicyDialog(QDialog):
    def __init__(self, selection, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("导入术语")
        self.setAccessibleName("术语导入批量处理方式")
        self.resize(520, 210)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.source_edit = QLineEdit(selection.label, self)
        self.source_edit.setReadOnly(True)
        self.source_edit.setAccessibleName("本次导入来源")
        self.source_edit.setToolTip(selection.request.file_path or selection.label)
        self.source_edit.setAccessibleDescription(selection.request.file_path or selection.label)
        self.source_edit.setCursorPosition(0)
        ComponentStyle.apply_static(self.source_edit, ComponentKind.INPUT)
        form.addRow("导入来源", self.source_edit)
        self.policy_combo = QComboBox(self)
        self.policy_combo.setAccessibleName("导入同名术语的批量处理方式")
        self.policy_combo.addItem("保留已有译名", TerminologyProfileImportPolicy.KEEP_EXISTING)
        self.policy_combo.addItem("采用导入译名", TerminologyProfileImportPolicy.USE_IMPORTED)
        ComponentStyle.apply_static(self.policy_combo, ComponentKind.INPUT)
        form.addRow("同名术语", self.policy_combo)
        layout.addLayout(form)
        hint = QLabel("仅影响新方案；来源内重复词条，保留时取首条，采用时取末条。下一步先预览，再确认创建。", self)
        hint.setWordWrap(True)
        hint.setProperty("tbSecondary", True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("继续预览")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def policy(self) -> TerminologyProfileImportPolicy:
        return self.policy_combo.currentData()


__all__ = ["TerminologyImportPolicyDialog"]
