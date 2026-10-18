"""Explicit user choices for leaving edits and reviewing archive impact."""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QMessageBox, QVBoxLayout


def choose_unsaved_action(parent, name: str) -> QMessageBox.StandardButton:
    box = QMessageBox(parent)
    box.setWindowTitle("未保存的修改")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(f"“{name}”有未保存的修改。")
    box.setInformativeText("保存只更新此工程的方案草稿；发布后才会生效。")
    buttons = QMessageBox.StandardButton
    box.setStandardButtons(buttons.Save | buttons.Discard | buttons.Cancel)
    box.button(buttons.Save).setText("保存")
    box.button(buttons.Discard).setText("放弃修改")
    box.button(buttons.Cancel).setText("取消")
    box.setDefaultButton(buttons.Cancel)
    return QMessageBox.StandardButton(box.exec())


def confirm_archive(parent, name: str, selections, variant_names: dict[str, str]) -> bool:
    dialog = QDialog(parent)
    dialog.setWindowTitle("归档影响确认")
    dialog.resize(500, 330)
    layout = QVBoxLayout(dialog)
    text = QLabel(
        f"归档“{name}”后，以下 {len(selections)} 个翻译版本将使用项目默认译名。\n"
        "方案内容和已应用历史会保留，可在“显示已归档”中恢复；恢复后需要重新选择使用。",
        dialog,
    )
    text.setWordWrap(True)
    layout.addWidget(text)
    affected = QListWidget(dialog)
    affected.setAccessibleName("归档受影响的翻译版本")
    for selection in selections:
        name = variant_names.get(selection.variant_id, selection.variant_id)
        label = name if name == selection.variant_id else f"{name}（{selection.variant_id}）"
        affected.addItem(label)
    if not selections:
        affected.addItem("当前没有翻译版本使用此方案。")
    layout.addWidget(affected)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("归档")
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return dialog.exec() == QDialog.DialogCode.Accepted
