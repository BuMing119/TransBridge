# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Owner editor for the values in a v1 project upload policy."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from transbridge.application.file_upload_modes import UploadOperation
from transbridge.application.upload_policy import (
    MemberSelectors,
    OperationRule,
    ProjectRole,
    TranslationMode,
    UploadLayout,
    UploadPolicy,
)
from transbridge.paratranz.project_policy import default_upload_policy
from transbridge.ui.foundation.components import configure_dialog

_ROLE_NAMES = {
    ProjectRole.OWNER: "所有者",
    ProjectRole.ADMIN: "管理员",
    ProjectRole.PROOFREADER: "校对者",
    ProjectRole.TRANSLATOR: "翻译者",
}
_MODES = {
    TranslationMode.FILL_EMPTY: "填补空译文",
    TranslationMode.SAFE_IMPORT: "安全导入",
    TranslationMode.FORCE: "强制导入",
}
_OPERATIONS = {
    UploadOperation.ORIGINAL: "仅更新原文",
    UploadOperation.FILL_EMPTY: "仅填补空译文",
    UploadOperation.SAFE: "仅安全导入译文",
    UploadOperation.BOTH: "更新原文并安全导入",
    UploadOperation.FORCE: "仅强制导入差异译文",
}


class MemberButton(QPushButton):
    def __init__(self, title, selectors, members, parent):
        super().__init__(parent)
        self.title, self.selectors, self.members = title, selectors, members
        self.setAccessibleName(title)
        self.clicked.connect(self._choose)
        self._refresh()

    def _refresh(self):
        s = self.selectors
        labels = ["项目成员"] if s.project_member else []
        labels.extend(_ROLE_NAMES[r] for r in sorted(s.roles))
        names = dict(self.members)
        labels.extend(names.get(uid, str(uid)) for uid in sorted(s.user_ids))
        self.setText("、".join(labels[:2]) + ("…" if len(labels) > 2 else "") if labels else "无人")
        self.setToolTip("、".join(labels) or "无人")

    def _choose(self):
        dialog = QDialog(self)
        configure_dialog(dialog)
        dialog.setWindowTitle(self.title)
        layout = QVBoxLayout(dialog)
        choices = QListWidget(dialog)
        layout.addWidget(choices)
        labels = [("项目成员", "all", self.selectors.project_member)]
        labels.extend((name, role, role in self.selectors.roles) for role, name in _ROLE_NAMES.items())
        members = dict(self.members)
        for uid in self.selectors.user_ids:
            members.setdefault(uid, str(uid))
        labels.extend((f"{name} ({uid})", uid, uid in self.selectors.user_ids) for uid, name in members.items())
        for label, value, checked in labels:
            item = QListWidgetItem(label, choices)
            item.setData(Qt.ItemDataRole.UserRole, value)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected = [
                value
                for index, (_, value, _) in enumerate(labels)
                if choices.item(index).checkState() == Qt.CheckState.Checked
            ]
            self.selectors = MemberSelectors(
                frozenset(v for v in selected if type(v) is int),
                frozenset(v for v in selected if isinstance(v, ProjectRole)),
                "all" in selected,
            )
            self._refresh()
        dialog.deleteLater()


class UploadPolicyEditor(QDialog):
    def __init__(self, access, parent=None):
        super().__init__(parent)
        configure_dialog(self)
        self.setWindowTitle("编辑项目上传规则")
        policy = access.policy or default_upload_policy()
        root = QVBoxLayout(self)
        form = QFormLayout()
        root.addLayout(form)
        self.layout_choice = QComboBox(self)
        for value, name in (
            (UploadLayout.USER_CHOICE, "成员自行选择"),
            (UploadLayout.FLAT, "普通上传"),
            (UploadLayout.CATEGORIZED, "分类上传"),
        ):
            self.layout_choice.addItem(name, value.value)
        self.layout_choice.setCurrentIndex(self.layout_choice.findData(policy.layout.value))
        form.addRow("上传布局", self.layout_choice)
        self.modes = {}
        for mode, name in _MODES.items():
            box = QCheckBox(name, self)
            box.setChecked(mode in policy.translation_modes)
            form.addRow("译文写入方式" if not self.modes else "", box)
            self.modes[mode] = box
        grid = QGridLayout()
        root.addLayout(grid)
        for col, name in enumerate(("上传操作", "允许", "排除")):
            grid.addWidget(QLabel(name), 0, col)
        self.rules = {}
        for row, (op, name) in enumerate(_OPERATIONS.items(), 1):
            rule = policy.rules[op]
            allow = MemberButton(f"{name} · 允许", rule.allow, access.members, self)
            deny = MemberButton(f"{name} · 排除", rule.deny, access.members, self)
            self.rules[op] = (allow, deny)
            for col, widget in enumerate((QLabel(name), allow, deny)):
                grid.addWidget(widget, row, col)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存到 ParaTranz")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.resize(max(620, self.sizeHint().width()), self.sizeHint().height())

    def policy(self):
        modes = frozenset(mode for mode, box in self.modes.items() if box.isChecked())
        rules = {op: OperationRule(a.selectors, d.selectors) for op, (a, d) in self.rules.items()}
        return UploadPolicy(UploadLayout(self.layout_choice.currentData()), modes, rules)

    def accept(self):
        try:
            self.policy()
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        super().accept()
