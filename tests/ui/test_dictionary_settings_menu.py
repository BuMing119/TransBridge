"""Exercise the desktop dictionary preference and main-library menu."""

from threading import Event

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMainWindow
import pytest

from tests.ui.test_main_window_shell import _callbacks
from tests.ui.test_project_creation_reuse import _until, shared_plugin_window as window_fixture
from transbridge.application.projects.dictionary_apply import dictionary_library
from transbridge.config.dictionary_preferences import DictionaryPreferences
from transbridge.config.repository import ConfigRepository
from transbridge.translation_memory.library import DictionaryLibrary
from transbridge.ui.shell.menu_builder import MenuBuilder

shared_plugin_window = window_fixture
pytestmark = pytest.mark.parametrize("shared_plugin_window", [False], indirect=True)


@pytest.fixture
def desktop(shared_plugin_window, tmp_path, monkeypatch):
    monkeypatch.setattr("transbridge.translation_memory.library.default_library_dir", lambda: tmp_path / "dictionary")
    window = shared_plugin_window.window
    menu = window._menu.dictionary_settings
    yield window, menu
    menu.hide()
    _until(lambda: menu._worker is None)


def _action(menu, text):
    return next(action for action in menu.actions() if action.text() == text)


def _main_actions(menu):
    return {action.data(): action for action in _action(menu, "主库").menu().actions()}


def _open_submenu(menu, action):
    menu.setActiveAction(action)
    QTest.keyClick(menu, Qt.Key.Key_Right)
    _until(action.menu().isVisible)


def test_preferences_sync_across_menus_and_are_persisted(desktop, tmp_path, monkeypatch, caplog):
    window, menu = desktop
    other_window = QMainWindow()
    other = MenuBuilder(other_window, _callbacks([])).build().dictionary_settings
    other.bind_context(window.context)
    save_label = "保存工程时将译文存入词典"
    apply_label = "打开插件时自动套用词典"
    save = _action(menu, save_label)
    apply = _action(menu, apply_label)
    try:
        menu.aboutToShow.emit()
        assert save.isCheckable() and save.isChecked()
        assert apply.isCheckable() and apply.isChecked()
        assert not _action(menu, "主库").isVisible()

        save.trigger()
        assert not save.isChecked() and not _action(other, save_label).isChecked()
        _until(save.isEnabled)
        _action(other, apply_label).trigger()
        assert not apply.isChecked()
        _until(apply.isEnabled)
        restarted = DictionaryPreferences(ConfigRepository(tmp_path / "config.ini", legacy_path=tmp_path / "legacy"))
        assert not restarted.save_on_project_save and not restarted.apply_on_plugin_open

        window.context.dictionary_preferences.set_save_on_project_save(True)
        menu.aboutToShow.emit()
        assert save.isChecked() and not apply.isChecked()

        def fail(_updates):
            raise OSError("test config write failure")

        monkeypatch.setattr(window.context.dictionary_preferences._repository, "update_sections", fail)
        save.trigger()
        _until(save.isEnabled)
        assert save.isChecked() and _action(other, save_label).isChecked()
        assert "test config write failure" in caplog.text
    finally:
        other_window.close()
        other_window.deleteLater()


def test_preference_applies_immediately_during_background_write(desktop, monkeypatch):
    window, menu = desktop
    preferences = window.context.dictionary_preferences
    entered, release = Event(), Event()
    update_sections = preferences._repository.update_sections

    def blocked_write(updates):
        entered.set()
        assert release.wait(5)
        return update_sections(updates)

    monkeypatch.setattr(preferences._repository, "update_sections", blocked_write)
    menu.aboutToShow.emit()
    save = _action(menu, "保存工程时将译文存入词典")
    try:
        save.trigger()
        _until(entered.is_set)
        menu.aboutToShow.emit()
        assert not save.isEnabled() and not save.isChecked()
        assert not preferences.save_on_project_save
    finally:
        release.set()
    _until(save.isEnabled)
    assert not preferences.save_on_project_save


def test_progressive_menu_opens_named_main_choices_and_allows_clearing(desktop):
    window, dictionary_menu = desktop
    library = dictionary_library(window.context)
    library.save((), "Example", main="A & B", create_main=True)
    library.save((), "Example", main="Other", create_main=True)
    library.select_main("A & B")
    bar = window.menuBar()
    settings = next(action.menu() for action in bar.menu_actions if action.text() == "设置")
    main = _action(dictionary_menu, "主库")

    def open_main():
        bar.expand()
        QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.actionGeometry(settings.menuAction()).center())
        _until(settings.isVisible)
        _open_submenu(settings, dictionary_menu.menuAction())
        _until(lambda: main.isVisible() and main.isEnabled())
        _open_submenu(dictionary_menu, main)
        bar.schedule_collapse()
        assert not bar.collapse_timer.isActive()
        bar.collapse()
        assert bar.is_expanded
        return _main_actions(dictionary_menu)

    choices = open_main()
    assert tuple(choices) == ("", "A & B", "Other")
    assert choices[""].text() == "不选择主库"
    assert choices["A & B"].text() == "A && B"
    assert [name for name, action in choices.items() if action.isChecked()] == ["A & B"]
    main.menu().setActiveAction(choices["Other"])
    QTest.keyClick(main.menu(), Qt.Key.Key_Return)
    _until(lambda: choices["Other"].isChecked())
    assert library.active_main() == "Other"

    choices = open_main()
    main.menu().setActiveAction(choices[""])
    QTest.keyClick(main.menu(), Qt.Key.Key_Return)
    _until(lambda: choices[""].isChecked())
    assert library.active_main() == ""


def test_scope_change_rejects_pending_main_selection(desktop, monkeypatch, caplog):
    window, menu = desktop
    context = window.context
    original = dictionary_library(context)
    original.save((), "Example", main="Original", create_main=True)
    next_scope = DictionaryLibrary(original.base_dir, game="fallout4")
    next_scope.save((), "Example", main="New scope", create_main=True)
    entered, release = Event(), Event()
    select_main = DictionaryLibrary.select_main

    def slow_select(library, name, *, commit):
        def paused_commit(publish):
            entered.set()
            assert release.wait(5)
            commit(publish)

        return select_main(library, name, commit=paused_commit)

    monkeypatch.setattr(DictionaryLibrary, "select_main", slow_select)
    menu.aboutToShow.emit()
    _until(lambda: "Original" in _main_actions(menu) and menu._worker is None)
    try:
        _main_actions(menu)["Original"].trigger()
        _until(entered.is_set)
        context.game_profile = "fallout4"
        context.project_changed.emit()
        assert not _action(menu, "主库").isVisible()
    finally:
        release.set()
    _until(lambda: menu._worker is None)
    assert "主库范围已变化，未保存所选主库" in caplog.text
    assert original.active_main() == next_scope.active_main() == ""

    menu.aboutToShow.emit()
    _until(lambda: "New scope" in _main_actions(menu))
    assert tuple(_main_actions(menu)) == ("", "New scope")


def test_main_read_failure_hides_cached_choices(desktop, monkeypatch, caplog):
    window, menu = desktop
    dictionary_library(window.context).save((), "Example", main="Existing", create_main=True)
    menu.aboutToShow.emit()
    _until(lambda: "Existing" in _main_actions(menu) and menu._worker is None)

    def fail(_library):
        raise OSError("test main read failure")

    monkeypatch.setattr(DictionaryLibrary, "list_mains", fail)
    menu.aboutToShow.emit()
    _until(lambda: menu._worker is None)
    assert not _action(menu, "主库").isVisible()
    assert "test main read failure" in caplog.text
