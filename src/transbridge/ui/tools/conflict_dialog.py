"""译文冲突仲裁对话框：逐条展示多词典命中的冲突候选，用户采纳/拒绝。"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


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

        info = QLabel(
            f"以下 {len(self._conflicts)} 处词条在多个词典中存在不同译文，"
            "请为每条选择采纳的译文（默认采用自动仲裁的胜者）。"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        # 按 entry_id 分组冲突（同一条词条可能有多个候选）
        by_entry: dict[str, list[dict]] = {}
        for c in self._conflicts:
            eid = c.get("entry_id", "")
            by_entry.setdefault(eid, []).append(c)

        for eid, cands in by_entry.items():
            # 候选译文列表（含胜者 + 冲突的其它译文）
            options: list[tuple[str, str]] = []
            seen: set[str] = set()
            for c in cands:
                trans = c.get("translation", "")
                if trans and trans not in seen:
                    seen.add(trans)
                    scope = c.get("scope", "")
                    mod = c.get("mod_file_id", "")
                    label = f"{trans}  [{mod} / {scope}]"
                    options.append((label, trans))
            if not options:
                continue
            combo = QComboBox()
            for label, trans in options:
                combo.addItem(label, trans)
            # 默认选中第一个（自动仲裁胜者排最前）
            combo.setCurrentIndex(0)
            self._combos.append((eid, combo))
            form.addRow(QLabel(f"词条 {eid[:40]}"), combo)

        layout.addLayout(form)

        # 快捷按钮：全部采用胜者
        accept_all = QPushButton("全部采用自动仲裁结果")
        accept_all.clicked.connect(self._accept_defaults)
        layout.addWidget(accept_all)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_defaults(self) -> None:
        """全部采用当前选中（即默认胜者）。"""
        for _eid, combo in self._combos:
            combo.setCurrentIndex(0)

    def result(self) -> list[tuple[str, str]]:
        """返回 [(entry_id, 采纳译文), ...]。"""
        return [(eid, combo.currentData()) for eid, combo in self._combos]
