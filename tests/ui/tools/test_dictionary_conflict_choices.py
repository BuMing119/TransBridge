"""Dictionary review preserves the automatic winner and keeps long lists reachable."""

from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMessageBox, QPushButton, QScrollArea
import pytest

from tests.ui.fixtures.current_editing import CurrentEditingProject
from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.translation_memory import TranslationMemoryManager
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemeApplyStatus, ThemePreference, ThemeService
from transbridge.ui.tools import dictionary_panel
from transbridge.ui.tools.conflict_dialog import DictionaryConflictDialog


@pytest.mark.parametrize("choice", ["automatic", "alternative", "cancel"])
def test_real_dictionary_arbitration_and_review_keep_the_automatic_choice(layout_app, tmp_path, monkeypatch, choice):
    manager = TranslationMemoryManager(base_dir=tmp_path)
    manager.add("one", "Dragon", "优先译名", mod_file_id="ProjectTerms", scope="project")
    manager.add("one", "Dragon", "其他译名", mod_file_id="GlobalTerms", scope="global")
    manager.save()
    monkeypatch.setattr(dictionary_panel, "TranslationMemoryManager", lambda **_kwargs: manager)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: QMessageBox.StandardButton.Ok)
    collection = TranslationEntryCollection([TranslationEntry("one", "one", "Dragon", "", 0, "NPC_:FULL")])
    monkeypatch.setattr("transbridge.ui.context.ParatranzConfig.create_or_load", lambda: SimpleNamespace(token=""))
    project = CurrentEditingProject(collection)
    context = project.context
    seen = []

    def review(dialog):
        dialog.show()
        settle_layout(layout_app)
        combo = dialog._combos[0][1]
        seen.append([combo.itemData(index) for index in range(combo.count())])
        if choice == "alternative":
            combo.setFocus()
            QTest.keyClick(combo, Qt.Key.Key_Down)
        buttons = dialog.findChild(QDialogButtonBox)
        button = buttons.button(
            QDialogButtonBox.StandardButton.Cancel if choice == "cancel" else QDialogButtonBox.StandardButton.Ok
        )
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        return QDialog.result(dialog)

    monkeypatch.setattr(DictionaryConflictDialog, "exec", review)
    panel = dictionary_panel.DictionaryPanel(context, base_dir=tmp_path)
    try:
        panel._apply_btn.click()
        assert seen == [["优先译名", "其他译名"]]
        expected = "其他译名" if choice == "alternative" else "优先译名"
        assert context.collection.get("one").translation == expected
        assert project.aggregate.snapshot().entries[0].translation == expected
        assert context.dirty
    finally:
        panel.close()
        project.close()


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_many_conflicts_fit_short_window_and_last_choice_remains_keyboard_reachable(layout_app, mode):
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=mode, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(layout_app, create_builtin_registry(), preferences)
    assert service.start().status is not ThemeApplyStatus.FAILED
    result = service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
    assert result.status is not ThemeApplyStatus.FAILED and result.snapshot.effective_scheme.value == mode.value
    conflicts = [
        {"entry_id": f"term-{index}", "translation": f"自动译名{index}", "mod_file_id": "Project", "wins": True}
        for index in range(45)
    ]
    dialog = DictionaryConflictDialog(conflicts)
    try:
        dialog.resize(640, 480)
        dialog.show()
        settle_layout(layout_app)
        assert dialog.height() == 480
        scroll = dialog.findChild(QScrollArea)
        assert scroll is not None and scroll.verticalScrollBar().maximum() > 0
        buttons = dialog.findChild(QDialogButtonBox)
        assert buttons.geometry().bottom() < dialog.height()
        dialog._combos[0][1].setFocus()
        for _ in range(44):
            QTest.keyClick(layout_app.focusWidget(), Qt.Key.Key_Tab)
        settle_layout(layout_app)
        last = dialog._combos[-1][1]
        assert last.hasFocus()
        assert scroll.verticalScrollBar().value() > 0
        assert dialog.result()[-1] == ("term-44", "自动译名44")
    finally:
        dialog.close()
        service.close()


def test_explicit_winner_and_reset_do_not_depend_on_candidate_order(layout_app):
    long_translation = "完整译文" * 70
    dialog = DictionaryConflictDialog([
        {"entry_id": "one", "translation": "其他译名", "wins": False},
        {"entry_id": "one", "translation": long_translation, "wins": True},
        {"entry_id": "one", "translation": "第三个译名", "wins": False},
    ])
    try:
        dialog.resize(640, 480)
        dialog.show()
        settle_layout(layout_app)
        assert dialog.width() == 640
        assert any("1 处词条" in label.text() for label in dialog.findChildren(QLabel))
        combo = dialog._combos[0][1]
        assert combo.currentData() == long_translation
        assert long_translation in combo.toolTip()
        QTest.keyClick(combo, Qt.Key.Key_Down)
        assert combo.currentData() == "其他译名"
        reset = next(button for button in dialog.findChildren(QPushButton) if "恢复全部" in button.text())
        QTest.mouseClick(reset, Qt.MouseButton.LeftButton)
        assert dialog.isVisible()
        assert combo.currentData() == long_translation
    finally:
        dialog.close()
