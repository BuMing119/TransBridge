"""Present exactly the immutable scheme changes that publication will commit."""

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QListWidget,
    QTableView,
    QVBoxLayout,
)


class PublicationChangesModel(QAbstractTableModel):
    _headers = ("变更", "原文术语", "发布前译名", "发布后译名", "范围", "现有译名匹配")

    def __init__(self, changes, parent=None):
        super().__init__(parent)
        self._changes = changes

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._changes)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._headers[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        change = self._changes[index.row()]
        item = change.after or change.before
        previous_base = change.before.base_translation if change.before else "—"
        next_base = change.after.base_translation if change.after else "—"
        return (
            change.kind,
            item.original,
            "—" if change.before is None else change.before.translation,
            "—" if change.after is None else change.after.translation,
            item.plugin_id or "整个项目",
            previous_base if previous_base == next_base else f"{previous_base} → {next_base}",
        )[index.column()]


def confirm_publication(parent, preview, variant_names) -> bool:
    dialog = QDialog(parent)
    dialog.setProperty("tbTaskDialog", True)
    dialog.setWindowTitle("发布译名方案 · 确认影响")
    dialog.resize(960, 640)
    layout = QVBoxLayout(dialog)
    counts = {kind: sum(item.kind == kind for item in preview.changes) for kind in ("新增", "修改", "移除")}
    rename = (
        f"名称：{preview.previous_name} → {preview.name}。\n"
        if preview.previous_name is not None and preview.previous_name != preview.name
        else ""
    )
    summary = QLabel(
        f"“{preview.name}”：新增 {counts['新增']} 条，修改 {counts['修改']} 条，移除 {counts['移除']} 条。\n"
        + rename
        + (
            "首次发布。"
            if preview.published_revision is None
            else "更新已发布方案。"
            if preview.has_changes
            else "内容与已发布版本一致；将直接采用现有版本，不创建重复修订。"
        )
        + ("还包含条目特例或精确位置绑定变更。" if preview.advanced_changed else "")
        + "项目译文不会被修改；之后的翻译与导出将使用新的方案内容。",
        dialog,
    )
    summary.setWordWrap(True)
    layout.addWidget(summary)
    table = QTableView(dialog)
    table.setAccessibleName("方案发布条目差异")
    table.setModel(PublicationChangesModel(preview.changes, table))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    layout.addWidget(table, 1)
    layout.addWidget(QLabel("受影响的翻译版本", dialog))
    affected = QListWidget(dialog)
    affected.setAccessibleName("发布受影响的翻译版本")
    affected.setMaximumHeight(150)
    ids = {item.variant_id for item in preview.selections}
    if preview.target_variant_id is not None:
        ids.add(preview.target_variant_id)
    for variant_id in sorted(ids):
        suffix = "（本次采用此方案）" if variant_id == preview.target_variant_id else "（使用中的方案将更新）"
        affected.addItem(f"{variant_names.get(variant_id, variant_id)}{suffix}")
    if not ids:
        affected.addItem("目前没有翻译版本使用此方案；发布后仍需选择使用。")
    layout.addWidget(affected)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
        "用于当前翻译版本"
        if not preview.has_changes
        else "发布并用于当前翻译版本"
        if preview.target_variant_id is not None
        else "确认发布"
    )
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("返回编辑")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return dialog.exec() == QDialog.DialogCode.Accepted
