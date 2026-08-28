"""存为词典对话框：选择目标词典（mod 名 + scope）、粒度、词典标签。

支持根据当前上下文预填默认值：
- mod 名预填「从打开文件路径推断」的 mod 名（去扩展名）
- scope 默认「全局共享 (global)」，可切换为「项目专属 (project)」
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class SaveToDictionaryDialog(QDialog):
    """收集「存为词典」所需参数。

    返回：
        mod_file_id: str（词典 mod 名，必填）
        scope: str（"project" / "global"，单值）
        selected_only: bool（True=仅选中条目，False=整个集合）
        tags: list[str]（词典标签，逗号分隔）
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        source_path: str = "",
        mod_file_id: str = "",
    ) -> None:
        super().__init__(parent)
        self._source_path = source_path.strip()
        self._init_mod = mod_file_id.strip()

        self.setWindowTitle("存为词典")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # mod 名（从打开文件路径推断）
        self._mod_edit = QLineEdit()
        self._mod_edit.setPlaceholderText("词典 mod 名，如 LegacyPatch")
        self._mod_edit.setText(self._default_mod_id())
        form.addRow("mod 名:", self._mod_edit)

        # scope 选择（global 居首，作为默认）
        self._scope_combo = QComboBox()
        self._scope_combo.addItem("全局共享 (global)", "global")
        self._scope_combo.addItem("项目专属 (project)", "project")
        self._scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        form.addRow("词典范围:", self._scope_combo)

        layout.addLayout(form)

        # 粒度
        self._radio_all = QRadioButton("整个集合")
        self._radio_selected = QRadioButton("仅选中条目")
        self._radio_all.setChecked(True)
        layout.addWidget(QLabel("范围:"))
        layout.addWidget(self._radio_all)
        layout.addWidget(self._radio_selected)

        # 词典标签
        tag_layout = QVBoxLayout()
        tag_layout.addWidget(QLabel("词典标签（逗号分隔，可选）:"))
        self._tags_edit = QLineEdit()
        self._tags_edit.setPlaceholderText("如: 术语, 已校对")
        tag_layout.addWidget(self._tags_edit)
        layout.addLayout(tag_layout)

        # 按钮
        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self._validate_and_accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._scope_combo.setCurrentIndex(0)

    def _default_mod_id(self) -> str:
        """从打开文件路径推断 mod 名（去扩展名）。"""
        if self._source_path:
            return Path(self._source_path).stem
        return self._init_mod

    def _on_scope_changed(self) -> None:
        # scope 单值切换，无附加输入
        pass

    def _validate_and_accept(self) -> None:
        mod_id = self._mod_edit.text().strip()
        if not mod_id:
            self._mod_edit.setFocus()
            self._mod_edit.setStyleSheet("border: 1px solid red;")
            return
        self.accept()

    def result(self) -> tuple[str, str, bool, list[str]]:
        """返回 (mod_file_id, scope, selected_only, tags)。"""
        mod_id = self._mod_edit.text().strip()
        scope = self._scope_combo.currentData()
        selected_only = self._radio_selected.isChecked()
        tags = [t.strip() for t in self._tags_edit.text().split(",") if t.strip()]
        # 去重保序
        seen: set[str] = set()
        unique_tags: list[str] = []
        for t in tags:
            if t not in seen:
                seen.add(t)
                unique_tags.append(t)
        return mod_id, scope, selected_only, unique_tags
