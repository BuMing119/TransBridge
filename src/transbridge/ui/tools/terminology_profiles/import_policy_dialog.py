# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Choose one batch policy for an explicitly selected import source."""

from PyQt6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from transbridge.application.terminology_profiles import TerminologyProfileImportPolicy
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle


class TerminologyImportPolicyDialog(QDialog):
    def __init__(self, selection, parent: QWidget | None = None, *, target=None) -> None:
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
        self.target_combo = QComboBox(self)
        if target is not None:
            self.target_combo.addItem(f"导入当前方案：{target.name}", target.profile_id)
        self.target_combo.addItem("创建新方案", None)
        form.addRow("导入到", self.target_combo)
        self.name_edit = QLineEdit(getattr(selection, "default_name", "新术语方案"), self)
        self.name_edit.setAccessibleName("新方案名称")
        form.addRow("新方案名称", self.name_edit)
        self.target_combo.currentIndexChanged.connect(lambda: self.name_edit.setEnabled(self.target_profile_id is None))
        self.name_edit.setEnabled(self.target_profile_id is None)
        self.policy_combo = QComboBox(self)
        self.policy_combo.setAccessibleName("导入同名术语的批量处理方式")
        self.policy_combo.addItem("保留已有译名", TerminologyProfileImportPolicy.KEEP_EXISTING)
        self.policy_combo.addItem("采用导入译名", TerminologyProfileImportPolicy.USE_IMPORTED)
        ComponentStyle.apply_static(self.policy_combo, ComponentKind.INPUT)
        form.addRow("同名术语", self.policy_combo)
        layout.addLayout(form)
        hint = QLabel(
            "将导入到上方指定的方案。来源内重复词条，保留时取首条，采用时取末条。下一步预览后确认导入。", self
        )
        hint.setWordWrap(True)
        hint.setProperty("tbSecondary", True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("继续预览")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        def update_accept():
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
                self.target_profile_id is not None or bool(self.profile_name)
            )

        self.name_edit.textChanged.connect(update_accept)
        self.target_combo.currentIndexChanged.connect(update_accept)
        update_accept()
        layout.addWidget(buttons)

    @property
    def target_profile_id(self):
        return self.target_combo.currentData()

    @property
    def profile_name(self):
        return self.name_edit.text().strip()

    @property
    def policy(self) -> TerminologyProfileImportPolicy:
        return self.policy_combo.currentData()


__all__ = ["TerminologyImportPolicyDialog"]
