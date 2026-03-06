"""词条相关小对话框：创建词条、批量设置状态、同步译文确认。"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QTextEdit, QComboBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
)
from PyQt6.QtCore import Qt

from ._strings_common import _STAGE_LABELS


class _CreateStringDialog(QDialog):
    def __init__(self, files: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("创建词条")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._key = QLineEdit()
        self._key.setPlaceholderText("必填，唯一键名")
        form.addRow("键名 *", self._key)

        self._original = QLineEdit()
        self._original.setPlaceholderText("必填，原文")
        form.addRow("原文 *", self._original)

        self._translation = QTextEdit()
        self._translation.setMaximumHeight(70)
        self._translation.setPlaceholderText("可选，译文")
        form.addRow("译文", self._translation)

        self._file_combo = QComboBox()
        self._file_combo.addItem("不指定文件", None)
        for f in files:
            self._file_combo.addItem(f.get("name", str(f.get("id"))), f.get("id"))
        form.addRow("所属文件", self._file_combo)

        self._stage_combo = QComboBox()
        for val, name in _STAGE_LABELS.items():
            if val != -2:
                self._stage_combo.addItem(name, val)
        form.addRow("状态", self._stage_combo)

        self._context = QLineEdit()
        self._context.setPlaceholderText("可选，上下文说明")
        form.addRow("上下文", self._context)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("创建")
        ok_btn.clicked.connect(self._validate)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _validate(self):
        if not self._key.text().strip():
            QMessageBox.warning(self, "提示", "键名不能为空")
            return
        if not self._original.text().strip():
            QMessageBox.warning(self, "提示", "原文不能为空")
            return
        self.accept()

    def get_data(self) -> dict:
        data = {
            "key": self._key.text().strip(),
            "original": self._original.text().strip(),
            "translation": self._translation.toPlainText().strip(),
            "stage": self._stage_combo.currentData(),
        }
        file_id = self._file_combo.currentData()
        if file_id is not None:
            data["file"] = file_id
        ctx = self._context.text().strip()
        if ctx:
            data["context"] = ctx
        return data


class _BatchStageDialog(QDialog):
    """批量设置状态对话框：选择目标 stage 后确认。"""

    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量设置状态")
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"将选中的 <b>{count}</b> 条词条的状态设置为："))

        self._stage_combo = QComboBox()
        for val, name in _STAGE_LABELS.items():
            if val != -2:
                self._stage_combo.addItem(name, val)
        layout.addWidget(self._stage_combo)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("确认")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def get_stage(self) -> int:
        return self._stage_combo.currentData()


class _SyncTranslationDialog(QDialog):
    """发现相同原文的低状态词条时，弹出此对话框询问是否批量同步。"""

    def __init__(self, matches: list, original: str, translation: str,
                 new_stage: int, parent=None):
        super().__init__(parent)
        self._matches = matches
        self.setWindowTitle("发现相同原文的词条")
        self.setMinimumWidth(580)
        self.resize(640, 460)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        stage_text = _STAGE_LABELS.get(new_stage, str(new_stage))

        info = QLabel(
            f"发现 <b>{len(matches)}</b> 条相同原文的词条（状态低于「{stage_text}」）<br>"
            f"勾选要同步的词条，将其译文和状态一并同步。"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        orig_short = original[:60] + "…" if len(original) > 60 else original
        trans_short = translation[:40] + "…" if len(translation) > 40 else translation
        detail = QLabel(
            f"原文：「{orig_short}」\n"
            f"同步为：「{trans_short}」  →  {stage_text}"
        )
        detail.setWordWrap(True)
        detail.setStyleSheet("color: #555; margin: 2px 0;")
        layout.addWidget(detail)

        # 全选 / 全不选
        sel_row = QHBoxLayout()
        sel_all_btn = QPushButton("全选")
        sel_none_btn = QPushButton("全不选")
        sel_all_btn.setFixedHeight(22)
        sel_none_btn.setFixedHeight(22)
        sel_all_btn.clicked.connect(lambda: self._set_all(True))
        sel_none_btn.clicked.connect(lambda: self._set_all(False))
        self._sel_label = QLabel()
        sel_row.addWidget(sel_all_btn)
        sel_row.addWidget(sel_none_btn)
        sel_row.addStretch()
        sel_row.addWidget(self._sel_label)
        layout.addLayout(sel_row)

        # 词条列表（带复选框，显示全部）
        self._table = QTableWidget(len(matches), 3)
        self._table.setHorizontalHeaderLabels(["键名", "当前译文", "状态变化"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

        for i, m in enumerate(matches):
            old_stage_text = _STAGE_LABELS.get(m.get("stage", 0), str(m.get("stage", 0)))

            key_item = QTableWidgetItem(m.get("key", "—"))
            key_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            key_item.setCheckState(Qt.CheckState.Checked)
            self._table.setItem(i, 0, key_item)

            cur_trans = m.get("translation") or ""
            trans_display = (cur_trans[:40] + "…") if len(cur_trans) > 40 else (cur_trans or "（空）")
            self._table.setItem(i, 1, QTableWidgetItem(trans_display))
            self._table.setItem(i, 2, QTableWidgetItem(f"{old_stage_text} → {stage_text}"))

        self._table.itemChanged.connect(self._on_check_changed)
        layout.addWidget(self._table, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        skip_btn = QPushButton("跳过")
        skip_btn.clicked.connect(self.reject)
        self._sync_btn = QPushButton()
        self._sync_btn.setDefault(True)
        self._sync_btn.clicked.connect(self.accept)
        btn_row.addWidget(skip_btn)
        btn_row.addWidget(self._sync_btn)
        layout.addLayout(btn_row)

        self._update_count()

    def _set_all(self, checked: bool):
        self._table.itemChanged.disconnect(self._on_check_changed)
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item:
                item.setCheckState(state)
        self._table.itemChanged.connect(self._on_check_changed)
        self._update_count()

    def _on_check_changed(self, item):
        if item.column() == 0:
            self._update_count()

    def _update_count(self):
        count = sum(
            1 for row in range(self._table.rowCount())
            if self._table.item(row, 0) and
               self._table.item(row, 0).checkState() == Qt.CheckState.Checked
        )
        total = self._table.rowCount()
        self._sync_btn.setText(f"同步 {count} 条")
        self._sync_btn.setEnabled(count > 0)
        self._sel_label.setText(f"已选 {count} / {total}")

    def get_selected_ids(self) -> list:
        return [
            self._matches[row].get("id")
            for row in range(self._table.rowCount())
            if self._table.item(row, 0) and
               self._table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]