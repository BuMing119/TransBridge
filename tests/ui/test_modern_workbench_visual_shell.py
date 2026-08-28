from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QHeaderView, QLabel, QPushButton, QToolButton, QWidget

from transbridge.converter.translation_entry import STAGE_TRANSLATED, TranslationEntry
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.shell.navigation_rail import NavigationRail, WorkspaceShell, _ui_asset_path
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
    start = QWidget()
    shell.addTab(first, "工作台")
    shell.addTab(second, "ParaTranz 管理")
    shell.addTab(start, "开始")
    emitted: list[str] = []
    start_requests: list[bool] = []
    shell.intent_requested.connect(emitted.append)
    shell.start_requested.connect(lambda: start_requests.append(True))

    page_buttons = shell.navigation._page_buttons
    page_buttons[2].click()
    assert shell.currentIndex() == 1
    page_buttons[0].click()

    assert start_requests == [True]
    assert shell.currentIndex() == 1

    shell.setCurrentWidget(start)
    assert shell.currentIndex() == 2
    assert page_buttons[0].isChecked()

    page_buttons[1].click()
    settings = next(button for button in shell.navigation.findChildren(QToolButton) if button.text().endswith("设置"))
    settings.click()

    assert shell.currentIndex() == 0
    assert shell.widget(0) is first
    assert shell.widget(1) is second
    assert shell.widget(2) is start
    assert emitted == [IntentId.SETTINGS_APPEARANCE.value]
    assert all(not button.icon().isNull() for button in shell.navigation.findChildren(QToolButton))
    assert [button.text() for button in page_buttons] == ["开始", "工作台", "ParaTranz"]
    shell.close()


def test_navigation_current_page_uses_page_ids_instead_of_visual_order() -> None:
    navigation = NavigationRail()
    start, workbench, paratranz = navigation._page_buttons

    assert workbench.isChecked()

    navigation.set_current_page(2)
    assert start.isChecked()

    navigation.set_current_page(1)
    assert paratranz.isChecked()

    navigation.set_current_page(99)
    assert paratranz.isChecked()
    navigation.close()


def test_navigation_renders_visible_bordered_paratranz_brand_icon() -> None:
    navigation = NavigationRail()
    paratranz = navigation._page_buttons[2]
    image = paratranz.icon().pixmap(18, 18).toImage()

    assert _ui_asset_path("paratranz.png") is not None
    assert paratranz is navigation._paratranz_button
    assert not image.isNull()
    assert any(
        image.pixelColor(x, y).blue() > 180 and image.pixelColor(x, y).red() < 80
        for y in range(image.height())
        for x in range(image.width())
    )
    border_pixel = image.pixelColor(9, 0)
    assert border_pixel.alpha() > 0
    assert border_pixel.red() < 245
    navigation.close()


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


def test_navigation_account_entry_opens_menu_and_routes_truthful_provider_state() -> None:
    shell = WorkspaceShell()
    shell.resize(640, 520)
    for label in ("工作台", "ParaTranz", "开始"):
        shell.addTab(QWidget(), label)
    shell.setCurrentIndex(2)
    shell.show()
    _APP.processEvents()
    navigation = shell.navigation
    emitted: list[str] = []
    shell.intent_requested.connect(emitted.append)
    entry = navigation._user_panel

    assert isinstance(entry, QPushButton)
    assert entry.property("tbAccountEntry") is True
    assert entry.accessibleName() == "账户与服务"
    assert "服务连接" in entry.accessibleDescription()
    assert entry.focusPolicy() is Qt.FocusPolicy.StrongFocus
    assert navigation._account_hint.text() == "⋯"
    assert entry.height() >= 48
    for child in (
        navigation._avatar,
        navigation._user_name,
        navigation._presence_dot,
        navigation._user_state,
        navigation._account_hint,
    ):
        assert child.isVisibleTo(entry)
        assert entry.rect().contains(child.geometry().topLeft())
        assert entry.rect().contains(child.geometry().bottomRight())

    QTest.mouseClick(entry, Qt.MouseButton.LeftButton)
    _APP.processEvents()
    assert navigation._account_menu.isVisible()
    navigation._account_menu.close()
    _APP.processEvents()
    assert not entry.hasFocus()

    entry.setFocus()
    QTest.keyClick(entry, Qt.Key.Key_Space)
    _APP.processEvents()

    assert navigation._account_menu.isVisible()
    assert navigation._account_menu.accessibleName() == "账户与服务菜单"
    assert navigation._provider_action.text() == "ParaTranz · 未连接"
    assert navigation._manage_services_action.text() == "服务与 API 配置…"
    assert navigation._account_menu.pos().y() < entry.mapToGlobal(entry.rect().topLeft()).y()
    navigation._account_menu.close()
    _APP.processEvents()
    assert entry.hasFocus()
    navigation._provider_action.trigger()
    navigation._manage_services_action.trigger()
    navigation._appearance_action.trigger()

    assert emitted == [
        IntentId.SETTINGS_SERVICES.value,
        IntentId.SETTINGS_SERVICES.value,
        IntentId.SETTINGS_APPEARANCE.value,
    ]
    assert shell.currentIndex() == 2

    long_name = "望山的超长多站点账户显示名称" * 8
    navigation.set_user({"nickname": long_name})
    navigation._provider_action.trigger()

    assert navigation._provider_action.text().startswith("ParaTranz · 望山")
    assert navigation._provider_action.text().endswith("…")
    assert navigation._provider_action.toolTip() == "查看 ParaTranz 账户信息"
    assert navigation._user_name.toolTip() == long_name
    assert emitted[-1] == IntentId.SETTINGS_ACCOUNT.value
    assert shell.currentIndex() == 2
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
