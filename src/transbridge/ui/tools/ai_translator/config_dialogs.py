"""Small Qt dialogs and one-shot rendering for translator configuration."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QListWidget, QMessageBox, QWidget

from transbridge.ui.tools.ai_translator.config_presenter import ConnectionTestResult


def render_paratranz_source(priority_list: QListWidget, current_project: object | None) -> None:
    for index in range(priority_list.count()):
        item = priority_list.item(index)
        if "paratranz" not in item.text():
            continue
        if current_project is None:
            item.setForeground(priority_list.palette().brush(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text))
            item.setToolTip("未选择 ParaTranz 项目，此来源将被跳过")
        else:
            item.setData(Qt.ItemDataRole.ForegroundRole, None)
            item.setToolTip("")
        break


def show_connection_test(parent: QWidget, result: ConnectionTestResult) -> None:
    if result.level == "critical":
        QMessageBox.critical(parent, result.title, result.message)
    elif result.level == "warning":
        QMessageBox.warning(parent, result.title, result.message)
    else:
        QMessageBox.information(parent, result.title, result.message)


def open_term_editor(parent: QWidget, esp_path: str | None, *, source_key: str | None = None) -> None:
    if not esp_path and not source_key:
        QMessageBox.warning(parent, "术语库", "尚未选择处理来源。")
        return
    from transbridge.ai_translator.term_database import DynamicTermDatabase

    from ._term_editor_dialog import _TermEditorDialog
    from .source_terms import source_terminology_path

    database = DynamicTermDatabase(source_terminology_path(esp_path, source_key))
    database.load()
    _TermEditorDialog(database, parent=parent).exec()
