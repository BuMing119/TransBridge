"""Small Qt dialogs and one-shot rendering for translator configuration."""

from __future__ import annotations

from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import QListWidget, QMessageBox, QWidget

from transbridge.ui.tools.ai_translator.config_presenter import ConnectionTestResult


def render_paratranz_source(priority_list: QListWidget, current_project: object | None) -> None:
    for index in range(priority_list.count()):
        item = priority_list.item(index)
        if "paratranz" not in item.text():
            continue
        if current_project is None:
            item.setForeground(QBrush(QColor("#999999")))
            item.setToolTip("未选择 ParaTranz 项目，此来源将被跳过")
        else:
            item.setForeground(QBrush(QColor()))
            item.setToolTip("")
        break


def show_connection_test(parent: QWidget, result: ConnectionTestResult) -> None:
    if result.level == "critical":
        QMessageBox.critical(parent, result.title, result.message)
    elif result.level == "warning":
        QMessageBox.warning(parent, result.title, result.message)
    else:
        QMessageBox.information(parent, result.title, result.message)


def open_term_editor(parent: QWidget, esp_path: str | None) -> None:
    if not esp_path:
        QMessageBox.warning(parent, "术语库", "尚未加载 ESP 文件。")
        return
    from transbridge.ai_translator.term_database import DynamicTermDatabase

    from ._term_editor_dialog import _TermEditorDialog

    database = DynamicTermDatabase(esp_path)
    database.load()
    _TermEditorDialog(database, parent=parent).exec()
