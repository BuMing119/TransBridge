from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication, QHeaderView, QLabel, QToolButton, QWidget

from transbridge.converter.translation_entry import STAGE_TRANSLATED, TranslationEntry
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.shell.navigation_rail import NavigationRail, WorkspaceShell
from transbridge.ui.workbench.filters_presenter import FiltersPresenter, FilterState
from transbridge.ui.workbench.table_presenter import RenderSession
from transbridge.ui.workbench.translation_table import (
    COL_CHECK,
    COL_CONTEXT,
    COL_INDEX,
    COL_KEY,
    COL_MARK,
    COL_ORIGINAL,
    COL_TRANSLATION,
    NUM_COLUMNS,
    TranslationTable,
)

_APP = QApplication.instance() or QApplication([])


def _entry(entry_id: str = "entry-1") -> TranslationEntry:
    return TranslationEntry(entry_id, "Tamriel:0000003C", "Skyrim", "天际", STAGE_TRANSLATED, "WRLD:FULL")


def test_workspace_shell_navigation_reuses_pages_and_emits_existing_intent() -> None:
    shell = WorkspaceShell()
    first = QWidget()
    second = QWidget()
    shell.addTab(first, "工作台")
    shell.addTab(second, "ParaTranz 管理")
    emitted: list[str] = []
    shell.intent_requested.connect(emitted.append)

    shell.navigation._page_buttons[1].click()
    settings = next(button for button in shell.navigation.findChildren(QToolButton) if button.text().endswith("设置"))
    settings.click()

    assert shell.currentIndex() == 1
    assert shell.widget(1) is second
    assert emitted == [IntentId.SETTINGS_APPEARANCE.value]
    assert all(not button.icon().isNull() for button in shell.navigation.findChildren(QToolButton))
    assert [button.text() for button in shell.navigation._page_buttons] == ["工作台", "ParaTranz 管理"]
    shell.close()


def test_navigation_utilities_have_hover_contract_and_user_presence_is_semantic(monkeypatch) -> None:
    shell = WorkspaceShell()
    navigation = shell.navigation
    avatar_payloads: list[bytes] = []
    monkeypatch.setattr(navigation, "_apply_avatar_payload", avatar_payloads.append)
    utility_buttons = [
        button for button in navigation.findChildren(QToolButton) if button.property("tbNavIntent") is True
    ]

    assert [button.text().strip().split()[-1] for button in utility_buttons] == ["设置", "帮助", "关于"]
    assert all(button.testAttribute(Qt.WidgetAttribute.WA_Hover) for button in utility_buttons)
    assert all(button.hasMouseTracking() for button in utility_buttons)

    navigation.set_user({
        "nickname": "望山",
        "avatar": "https://paratranz.cn/avatar.png",
        "_avatar_bytes": b"avatar-payload",
    })
    avatar = next(label for label in navigation.findChildren(QLabel) if label.property("tbAvatar") is True)
    presence = next(label for label in navigation.findChildren(QLabel) if label.accessibleName() == "用户连接状态")
    presence_dot = next(
        label for label in navigation.findChildren(QLabel) if label.property("tbConnectionState") is not None
    )
    assert avatar.text() == "望"
    assert presence.text() == "在线"
    assert presence_dot.text() == "●"
    assert presence_dot.property("tbConnectionState") == "online"
    assert "ParaTranz" in presence.accessibleDescription()
    assert avatar_payloads == [b"avatar-payload"]
    assert not hasattr(navigation, "_avatar_manager")

    navigation.set_user(None)
    assert avatar.text() == "本"
    assert presence.text() == "本地模式"
    assert presence_dot.property("tbConnectionState") == "local"
    shell.close()


def test_paratranz_avatar_is_center_cropped_to_a_circle() -> None:
    source = QPixmap(48, 32)
    source.fill(QColor(20, 160, 80))

    avatar = NavigationRail._circular_avatar(source)
    image = avatar.toImage()

    assert avatar.size().width() == avatar.size().height() == 32
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(16, 16).green() == 160


def test_translation_table_uses_dense_columns_without_persistent_cell_widgets() -> None:
    table = TranslationTable(on_progress=lambda *_args: None, on_batch=lambda: None)
    entry = _entry()
    table.start_render(RenderSession(1, None, (entry,)), {}, {})
    _APP.processEvents()

    assert table.columnCount() == NUM_COLUMNS == 7
    assert [
        table.horizontalHeaderItem(column).text()
        for column in (
            COL_CHECK,
            COL_INDEX,
            COL_MARK,
            COL_KEY,
            COL_ORIGINAL,
            COL_TRANSLATION,
            COL_CONTEXT,
        )
    ] == ["", "#", "标签", "Key", "原文", "译文", "类型 / 状态"]
    header = table.horizontalHeader()
    assert header.sectionResizeMode(COL_CHECK) is QHeaderView.ResizeMode.Fixed
    assert header.sectionResizeMode(COL_INDEX) is QHeaderView.ResizeMode.Interactive
    assert header.sectionResizeMode(COL_MARK) is QHeaderView.ResizeMode.Interactive
    assert all(table.cellWidget(0, column) is None for column in range(NUM_COLUMNS))
    assert table.item(0, COL_INDEX).text() == "1"
    assert "已翻译" in table.item(0, COL_CONTEXT).text()

    table.item(0, COL_CHECK).setCheckState(Qt.CheckState.Checked)
    assert table.selected_entry_ids() == (entry.id,)
    table.close()


def test_global_search_matches_key_original_translation_or_context() -> None:
    entries = (
        _entry("one"),
        TranslationEntry("two", "other", "Needle original", "另一个", STAGE_TRANSLATED, "NPC_:FULL"),
    )
    presenter = FiltersPresenter()
    presenter.update(FilterState(search_all="needle"))

    assert [entry.id for entry in presenter.apply(entries, {})] == ["two"]
    assert FilterState.from_mapping({"search_all": "skyrim"}).to_mapping()["search_all"] == "skyrim"
