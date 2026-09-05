"""Small shared helpers for settings-center pages."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtWidgets import QFormLayout, QLabel, QLineEdit, QWidget


class SettingsPage(QWidget):
    """Minimal page contract consumed by the settings composition root."""

    def apply_to_draft(self) -> None:
        raise NotImplementedError


def unavailable_page(message: str, parent: QWidget | None = None) -> QWidget:
    page = QWidget(parent)
    layout = QFormLayout(page)
    note = QLabel(message, page)
    note.setWordWrap(True)
    note.setProperty("tbSecondary", True)
    layout.addRow(note)
    return page


def password_editor(
    configured: bool,
    *,
    read_only: bool = False,
    parent: QWidget | None = None,
) -> QLineEdit:
    editor = QLineEdit(parent)
    editor.setEchoMode(QLineEdit.EchoMode.Password)
    if read_only:
        editor.setEnabled(False)
        editor.setProperty("environmentCredential", True)
        editor.setPlaceholderText("由环境变量提供（只读）")
    elif configured:
        editor.setPlaceholderText("已配置；留空保持不变")
    else:
        editor.setPlaceholderText("未配置")
    return editor


def apply_if_present(target: object, attr: str, value: object) -> None:
    if hasattr(target, attr):
        setattr(target, attr, value)


def signal_dirty(widget: QWidget, callback: Callable[[], None] | None) -> None:
    if callback is None:
        return
    for signal_name in ("textChanged", "valueChanged", "currentIndexChanged", "toggled"):
        signal = getattr(widget, signal_name, None)
        if signal is not None:
            signal.connect(callback)
            return


__all__ = ["SettingsPage", "apply_if_present", "password_editor", "signal_dirty", "unavailable_page"]
