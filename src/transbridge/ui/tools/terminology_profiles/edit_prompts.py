# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
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
    dialog.setWindowTitle("删除方案（可恢复）")
    dialog.resize(500, 330)
    layout = QVBoxLayout(dialog)
    text = QLabel(
        f"删除“{name}”后，以下 {len(selections)} 个翻译版本保留原方案关联和正文。\n"
        "方案及其草稿将移入已删除列表，不再用于新的方案选择。\n"
        "这不是永久清除；勾选“显示已删除方案（可恢复）”可恢复方案和草稿。",
        dialog,
    )
    text.setWordWrap(True)
    layout.addWidget(text)
    affected = QListWidget(dialog)
    affected.setAccessibleName("删除方案后保留关联的翻译版本")
    for selection in selections:
        name = variant_names.get(selection.variant_id, selection.variant_id)
        label = name if name == selection.variant_id else f"{name}（{selection.variant_id}）"
        affected.addItem(label)
    if not selections:
        affected.addItem("当前没有翻译版本使用此方案。")
    layout.addWidget(affected)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("删除方案")
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return dialog.exec() == QDialog.DialogCode.Accepted
