from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QShortcut
from PyQt6.QtWidgets import QApplication, QMainWindow

from transbridge.ui.shell.action_catalog import (
    DEFAULT_ACTION_CATALOG,
    ActionSection,
    IntentId,
)
from transbridge.ui.shell.menu_builder import MenuBuilder, MenuCallbacks
from transbridge.ui.shell.status_presenter import StatusPresenter

_APP = QApplication.instance() or QApplication([])


def _callbacks(calls: list[str]) -> MenuCallbacks:
    def callback(name: str):
        return lambda: calls.append(name)

    return MenuCallbacks(
        new_project=callback("new_project"),
        open_project=callback("open_project"),
        parse=callback("parse"),
        migrate=callback("migrate"),
        upload=callback("upload"),
        batch_upload=callback("batch_upload"),
        download=callback("download"),
        batch_download=callback("batch_download"),
        write=callback("write"),
        batch_write=callback("batch_write"),
        new_variant=callback("new_variant"),
        copy_variant=callback("copy_variant"),
        save_snapshot=callback("save_snapshot"),
        load_snapshot=callback("load_snapshot"),
        export_transbridge=callback("export"),
        import_transbridge=callback("import"),
        refresh_projects=callback("refresh"),
        show_config=callback("config"),
        open_ai_translator=callback("ai"),
        toggle_smart_assistant=callback("assistant"),
        open_dictionary=callback("dictionary"),
        open_fomod=callback("fomod"),
        show_user=callback("user"),
        show_mails=callback("mails"),
        show_about=callback("about"),
        manual_save=callback("save"),
    )


def test_menu_builder_connects_each_intent_once() -> None:
    calls: list[str] = []
    window = QMainWindow()
    handles = MenuBuilder(window, _callbacks(calls)).build()

    handles.parse.trigger()
    handles.upload.trigger()
    handles.smart_assistant.trigger()

    assert calls == ["parse", "upload", "assistant"]
    assert handles.parse.shortcut().toString() == "Ctrl+O"
    assert handles.smart_assistant is handles.view_assistant
    assert handles.smart_assistant.shortcut().toString() == "Ctrl+Shift+I"
    assert handles.smart_assistant.data() == IntentId.VIEW_SMART_ASSISTANT.value
    assert not any(shortcut.key().toString() == "Ctrl+K" for shortcut in window.findChildren(QShortcut))
    window.close()


def test_menu_builder_uses_task_oriented_top_level_navigation() -> None:
    window = QMainWindow()
    MenuBuilder(window, _callbacks([])).build()

    labels = [action.text() for action in window.menuBar().actions()]

    assert labels == ["文件", "项目", "翻译", "同步与发布", "视图", "设置", "帮助"]
    assert "小工具" not in labels
    assert "账户" not in labels
    window.close()


def test_action_catalog_has_unique_intents_shortcuts_and_disabled_reasons() -> None:
    descriptors = DEFAULT_ACTION_CATALOG.all()

    assert len({item.intent_id for item in descriptors}) == len(descriptors)
    assert len({item.shortcut.casefold() for item in descriptors if item.shortcut}) == len([
        item for item in descriptors if item.shortcut
    ])
    assert DEFAULT_ACTION_CATALOG.get(IntentId.TRANSLATION_AI).section is ActionSection.TRANSLATION
    unavailable = DEFAULT_ACTION_CATALOG.availability(
        IntentId.TRANSLATION_AI,
        enabled=False,
        reason="当前没有可翻译内容",
    )
    assert not unavailable.enabled
    assert unavailable.reason == "当前没有可翻译内容"


class _Context(QObject):
    user_changed = pyqtSignal(object)
    project_selected = pyqtSignal(object)


def test_status_presenter_detaches_bindings_and_stops_timer_for_100_lifecycles() -> None:
    context = _Context()
    for _ in range(100):
        window = QMainWindow()
        presenter = StatusPresenter(window, context)
        presenter.start()
        context.user_changed.emit({"nickname": "tester"})
        context.project_selected.emit({"name": "Demo", "id": 7})
        assert presenter.user_label.text() == "用户: tester"
        assert presenter.project_label.text() == "项目: Demo (id=7)"

        presenter.close()
        assert not presenter.api_indicator._timer.isActive()
        window.close()
