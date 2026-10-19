"""译文冲突仲裁对话框：逐条展示多词典命中的冲突候选，用户采纳/拒绝。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle


class DictionaryConflictDialog(QDialog):
    """冲突可视化仲裁。

    输入 conflicts: list[dict]，每条含 {entry_id, translation, mod_file_id, scope, wins}。
    用户为每条选择采纳的译文；确认后 result() 返回 [(entry_id, 选中译文), ...]。
    """

    def __init__(self, conflicts: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._conflicts = conflicts
        self.setWindowTitle("译文冲突仲裁")
        self.setMinimumWidth(520)
        self.resize(600, 480)

        self._combos: list[tuple[str, QComboBox]] = []  # (entry_id, combo)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        # 按 entry_id 分组冲突（同一条词条可能有多个候选）
        by_entry: dict[str, list[dict]] = {}
        for c in self._conflicts:
            eid = c.get("entry_id", "")
            by_entry.setdefault(eid, []).append(c)

        info = QLabel(
            f"以下 {len(by_entry)} 处词条在多个词典中存在不同译文。"
            "默认保留自动仲裁结果；确认后采用下方选择，关闭窗口则保留自动结果。"
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setAccessibleName("词典冲突选择列表")
        content = QWidget(scroll)
        form = QFormLayout(content)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        for eid, cands in by_entry.items():
            # 候选译文列表（含胜者 + 冲突的其它译文）
            options: list[tuple[str, str]] = []
            seen: set[str] = set()
            for c in sorted(cands, key=lambda item: not item.get("wins", False)):
                trans = c.get("translation", "")
                if trans and trans not in seen:
                    seen.add(trans)
                    scope = c.get("scope", "")
                    mod = c.get("mod_file_id", "")
                    source = "自动仲裁结果" if c.get("wins") else f"{mod} / {scope}"
                    label = f"{trans}  [{source}]"
                    options.append((label, trans))
            if not options:
                continue
            combo = QComboBox()
            ComponentStyle.apply_static(combo, ComponentKind.INPUT)
            combo.setAccessibleName(f"词条 {eid} 的采纳译文")
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(24)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            for label, trans in options:
                combo.addItem(label, trans)
                combo.setItemData(combo.count() - 1, label, Qt.ItemDataRole.ToolTipRole)
            # 默认选中第一个（自动仲裁胜者排最前）
            combo.setCurrentIndex(0)
            combo.setToolTip(combo.currentText())
            combo.currentTextChanged.connect(combo.setToolTip)
            self._combos.append((eid, combo))
            label = QLabel(f"词条 {eid[:40]}")
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setToolTip(eid)
            form.addRow(label, combo)

        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        # 快捷按钮：全部采用胜者
        accept_all = QPushButton("恢复全部自动仲裁选择")
        ComponentStyle.apply_static(accept_all, ComponentKind.BUTTON)
        accept_all.setAutoDefault(False)
        accept_all.clicked.connect(self._accept_defaults)
        layout.addWidget(accept_all)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认所选译文")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("保留自动结果")
        for button in buttons.buttons():
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_defaults(self) -> None:
        """恢复每条的默认胜者，仍需用户确认后才应用。"""
        for _eid, combo in self._combos:
            combo.setCurrentIndex(0)

    def result(self) -> list[tuple[str, str]]:
        """返回 [(entry_id, 采纳译文), ...]。"""
        return [(eid, combo.currentData()) for eid, combo in self._combos]
