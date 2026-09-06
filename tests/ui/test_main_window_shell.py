from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QFocusEvent, QShortcut
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget

from transbridge.ui.shell.action_catalog import (
    DEFAULT_ACTION_CATALOG,
    ActionSection,
    IntentId,
)
from transbridge.ui.shell.intent_composition import ShellIntentComposition
from transbridge.ui.shell.menu_builder import MenuBuilder, MenuCallbacks
from transbridge.ui.shell.progressive_menu_bar import ProgressiveMenuBar
from transbridge.ui.shell.status_presenter import StatusPresenter

_APP = QApplication.instance() or QApplication([])


def _callbacks(calls: list[str]) -> MenuCallbacks:
    def callback(name: str):
        return lambda: calls.append(name)

    return MenuCallbacks(
        new_project=callback("new_project"),
        open_project=callback("open_project"),
        prepare_content=callback("prepare_content"),
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
        show_appearance=callback("appearance"),
        show_config=callback("config"),
        open_ai_translator=callback("ai"),
        toggle_smart_assistant=callback("assistant"),
        open_dictionary=callback("dictionary"),
        open_history_search=callback("history_search"),
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

    handles.prepare_content.trigger()
    handles.upload.trigger()
    handles.smart_assistant.trigger()
    handles.history_search.trigger()
    handles.appearance.trigger()

    assert calls == ["prepare_content", "upload", "assistant", "history_search", "appearance"]
    assert handles.prepare_content.data() == IntentId.WORKBENCH_CONTENT_PREPARE.value
    assert handles.prepare_content.text() == "为当前工程添加插件…"
    assert handles.smart_assistant is handles.view_assistant
    assert handles.smart_assistant.shortcut().toString() == "Ctrl+Shift+I"
    assert handles.smart_assistant.data() == IntentId.VIEW_SMART_ASSISTANT.value
    assert handles.history_search.data() == IntentId.TRANSLATION_HISTORY_SEARCH.value
    assert handles.appearance.data() == IntentId.SETTINGS_APPEARANCE.value
    assert handles.appearance.text() == "设置…"
    assert handles.appearance.statusTip() == "管理外观、AI 服务、Embedding、ParaTranz 与默认参数"
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


def test_progressive_menu_bar_reveals_existing_top_level_actions_in_place() -> None:
    window = QMainWindow()
    window.setCentralWidget(QWidget())
    bar = ProgressiveMenuBar(window, collapse_delay_ms=25)
    window.setMenuBar(bar)
    MenuBuilder(window, _callbacks([])).build()
    bar.bind_existing_menus()
    assert not bar.is_expanded
    assert bar.compact_action.isVisible()
    assert bar.compact_action.text() == "☰"
    assert "TransBridge" not in bar.compact_action.text()
    assert not any(action.isVisible() for action in bar.menu_actions)
    window.resize(900, 500)
    window.show()
    _APP.processEvents()
    QTest.mouseMove(window.centralWidget(), QPoint(10, 10))
    bar.collapse()
    assert bar.collapse_timer.isSingleShot()
    assert bar.accessibleName() == "TransBridge 主菜单"

    QTest.mouseMove(bar, bar.actionGeometry(bar.compact_action).center())
    _APP.processEvents()

    assert bar.is_expanded
    assert not bar.compact_action.isVisible()
    assert [action.text() for action in bar.menu_actions] == [
        "文件",
        "项目",
        "翻译",
        "同步与发布",
        "视图",
        "设置",
        "帮助",
    ]
    assert all(action.isVisible() for action in bar.menu_actions)

    bar.collapse()
    QApplication.sendEvent(
        bar,
        QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.MenuBarFocusReason),
    )
    assert bar.is_expanded
    window.close()


def test_progressive_menu_bar_keeps_one_account_action_and_collapses_after_pointer_leaves() -> None:
    calls: list[str] = []
    window = QMainWindow()
    central = QWidget()
    window.setCentralWidget(central)
    bar = ProgressiveMenuBar(window, collapse_delay_ms=20)
    window.setMenuBar(bar)
    MenuBuilder(window, _callbacks(calls)).build()
    bar.bind_existing_menus()
    window.resize(900, 500)
    window.show()
    _APP.processEvents()

    settings_menu = next(action.menu() for action in bar.menu_actions if action.text() == "设置")
    account_action = next(
        action for action in settings_menu.actions() if action.data() == IntentId.SETTINGS_ACCOUNT.value
    )
    bar.expand()
    account_action.trigger()
    assert calls == ["user"]

    bar.schedule_collapse()
    assert bar.collapse_timer.isActive()
    settings_menu.aboutToShow.emit()
    assert bar.is_expanded
    assert not bar.collapse_timer.isActive()

    bar.collapse()
    bar.expand()
    same_account_action = next(
        action for action in settings_menu.actions() if action.data() == IntentId.SETTINGS_ACCOUNT.value
    )
    assert same_account_action is account_action

    QTest.mouseMove(central, QPoint(10, 10))
    bar.schedule_collapse()
    QTest.qWait(40)
    assert not bar.is_expanded
    assert not bar.collapse_timer.isActive()
    window.close()


def test_account_entry_depends_on_logged_in_user_instead_of_selected_cloud_project() -> None:
    composition = object.__new__(ShellIntentComposition)
    composition._host = SimpleNamespace(
        context=SimpleNamespace(current_user={"id": 7}, current_project=None),
    )

    assert composition._has_current_user() == (True, None)
    composition._host.context.current_user = None
    enabled, reason = composition._has_current_user()
    assert not enabled
    assert "API Token" in reason


def test_cloud_sync_remains_discoverable_without_browse_selection_and_requires_content() -> None:
    composition = object.__new__(ShellIntentComposition)
    composition._host = SimpleNamespace(
        context=SimpleNamespace(
            collection=object(),
            current_project=None,
            mine_project_ids={7},
        ),
    )

    assert composition._has_cloud_context() == (True, None)

    composition._host.context.current_project = {"id": 8}
    assert composition._has_cloud_context() == (True, None)

    composition._host.context.current_project = {"id": 7}
    assert composition._has_cloud_context() == (True, None)
    composition._host.context.collection = None
    assert composition._has_cloud_context() == (False, "请先选择可编辑的翻译内容")


def test_project_creation_and_workbench_content_have_distinct_routes() -> None:
    calls: list[str] = []

    class Noop:
        def __getattr__(self, _name):
            return self

        def __call__(self, *_args, **_kwargs):
            return None

    host = Noop()
    host.context = SimpleNamespace(project_name="HLIORemi")
    host.parse_coordinator = SimpleNamespace(parse_plugin=lambda: calls.append("parse-current-content"))
    host.start_center_controller = SimpleNamespace(
        begin_creation=lambda: calls.append("open-project-creation"),
        create_empty=lambda: calls.append("create-empty-project"),
        choose_source=lambda: calls.append("create-project-from-source"),
    )
    composition = ShellIntentComposition(host)

    creation = composition.dispatch(IntentId.PROJECT_CREATE)

    assert creation.accepted
    assert calls == ["open-project-creation"]
    calls.clear()

    result = composition.dispatch(IntentId.WORKBENCH_CONTENT_PREPARE)

    assert result.accepted
    assert calls == ["parse-current-content"]
    composition.close()


def test_action_catalog_has_unique_intents_shortcuts_and_disabled_reasons() -> None:
    descriptors = DEFAULT_ACTION_CATALOG.all()

    assert len({item.intent_id for item in descriptors}) == len(descriptors)
    assert len({item.shortcut.casefold() for item in descriptors if item.shortcut}) == len([
        item for item in descriptors if item.shortcut
    ])
    assert DEFAULT_ACTION_CATALOG.get(IntentId.TRANSLATION_AI).section is ActionSection.TRANSLATION
    assert IntentId.TRANSLATION_AI_BATCH not in {item.intent_id for item in descriptors}
    appearance = DEFAULT_ACTION_CATALOG.get(IntentId.SETTINGS_APPEARANCE)
    assert appearance.section is ActionSection.SETTINGS
    assert appearance.shortcut is None
    assert "主题" in appearance.aliases
    unavailable = DEFAULT_ACTION_CATALOG.availability(
        IntentId.TRANSLATION_AI,
        enabled=False,
        reason="当前没有可翻译内容",
    )
    assert not unavailable.enabled
    assert unavailable.reason == "当前没有可翻译内容"


def test_legacy_ai_intent_uses_unified_handler_with_another_loaded_content() -> None:
    calls: list[str] = []

    class Noop:
        def __getattr__(self, _name):
            return self

        def __call__(self, *_args, **_kwargs):
            return None

    host = Noop()
    host.context = SimpleNamespace(collection=None, slots={"other": SimpleNamespace(collection=[object()])})
    host.tool_windows = Noop()
    host.tool_windows.open_ai_translator = lambda: calls.append("unified-ai")
    composition = ShellIntentComposition(host)

    assert composition.dispatch(IntentId.TRANSLATION_AI).accepted
    assert composition.dispatch(IntentId.TRANSLATION_AI_BATCH.value).accepted
    assert calls == ["unified-ai", "unified-ai"]
    host.context.slots.clear()
    assert not composition.dispatch(IntentId.TRANSLATION_AI).accepted
    assert calls == ["unified-ai", "unified-ai"]
    composition.close()


def test_menu_uses_optional_foundation_locale_without_changing_intent_identity() -> None:
    window = QMainWindow()
    window.ui_foundation = type(
        "Foundation",
        (),
        {"locale": type("Locale", (), {"gettext": staticmethod(lambda value: f"译:{value}")})()},
    )()

    handles = MenuBuilder(window, _callbacks([])).build()

    assert window.menuBar().actions()[5].text() == "译:设置"
    assert handles.appearance.text() == "译:设置…"
    assert handles.appearance.statusTip() == "译:管理外观、AI 服务、Embedding、ParaTranz 与默认参数"
    assert handles.appearance.data() == IntentId.SETTINGS_APPEARANCE.value
    window.close()


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
