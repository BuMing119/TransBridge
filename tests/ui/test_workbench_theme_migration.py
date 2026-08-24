from __future__ import annotations

import os
from pathlib import Path
from time import perf_counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLineEdit, QStyleOptionViewItem
import pytest

from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.config.ui_preferences import ThemeMode, UiPreferenceRepository
from transbridge.converter.translation_entry import STAGE_QUESTIONABLE, TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.builtins import DEFAULT_THEME_ID, create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemePreference, ThemeService
from transbridge.ui.workbench.cards.download_card import DownloadCard
from transbridge.ui.workbench.cards.upload_card import UploadCard
from transbridge.ui.workbench.cards.write_card import WriteCard
from transbridge.ui.workbench.filters_presenter import FilterState
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.translation_table import COL_CONTEXT, COL_TRANSLATION
from transbridge.ui.workbench.widget import WorkbenchWidget


class _Config:
    token = ""


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _theme(qapp: QApplication, tmp_path: Path) -> tuple[ThemeService, ThemeView]:
    path = tmp_path / "ui.ini"
    preferences = UiPreferenceRepository(
        ConfigRepository(
            path,
            legacy_path=path,
            credential_store=UnavailableCredentialStore(),
        )
    )
    service = ThemeService(qapp, create_builtin_registry(), preferences)
    service.start()
    return service, ThemeView(service)


def _collection(count: int) -> TranslationEntryCollection:
    return TranslationEntryCollection(
        TranslationEntry(
            str(index),
            f"key-{index}",
            f"Original {index}",
            f"Translation {index}" if index % 3 == 0 else "",
            1 if index % 3 == 0 else 0,
            "NPC_:FULL",
        )
        for index in range(count)
    )


def _drain_render(qapp: QApplication, widget: Step2PreviewWidget, expected: int) -> None:
    deadline = perf_counter() + 8.0
    while widget._table.rowCount() < expected and perf_counter() < deadline:
        qapp.processEvents()
    assert widget._table.rowCount() == expected


def test_theme_switch_preserves_table_identity_selection_scroll_edit_and_generation(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    service, theme_view = _theme(qapp, tmp_path)
    widget = Step2PreviewWidget(context_module.AppContext(), theme_view=theme_view)
    widget.resize(900, 640)
    widget.show()
    widget.refresh(_collection(600))
    _drain_render(qapp, widget, 600)
    table = widget._table
    table.selectRow(320)
    table.verticalScrollBar().setValue(300)
    translation_item = table.item(320, COL_TRANSLATION)
    table.openPersistentEditor(translation_item)
    qapp.processEvents()
    editor = table.indexWidget(table.model().index(320, COL_TRANSLATION))
    assert isinstance(editor, QLineEdit)
    editor.setText("尚未提交的编辑草稿")

    item_ids = tuple(id(table.item(320, column)) for column in range(table.columnCount()))
    entry_ids = tuple(
        id(table.item(320, column).data(Qt.ItemDataRole.UserRole)) for column in range(table.columnCount())
    )
    selected = widget.selected_row_entry_ids()
    scroll = table.verticalScrollBar().value()
    generation = table.render_session.generation

    result = service.set_preference(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID), persist=False)
    qapp.processEvents()

    assert result.snapshot is not None
    assert table.theme_revision == result.snapshot.revision
    assert tuple(id(table.item(320, column)) for column in range(table.columnCount())) == item_ids
    assert (
        tuple(id(table.item(320, column).data(Qt.ItemDataRole.UserRole)) for column in range(table.columnCount()))
        == entry_ids
    )
    assert widget.selected_row_entry_ids() == selected
    assert table.verticalScrollBar().value() == scroll
    assert table.render_session.generation == generation
    assert table.indexWidget(table.model().index(320, COL_TRANSLATION)) is editor
    assert editor.text() == "尚未提交的编辑草稿"

    widget.close()
    theme_view.close()
    service.close()


def test_filter_and_stage_states_have_text_and_accessible_metadata(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    service, theme_view = _theme(qapp, tmp_path)
    widget = Step2PreviewWidget(context_module.AppContext(), theme_view=theme_view)
    widget.refresh(_collection(3))
    _drain_render(qapp, widget, 3)

    widget._filters_view.apply_state(FilterState(focus_labeled=True))
    assert widget._filters_view.focus_button.isChecked()
    assert widget._filters_view.focus_button.accessibleDescription() == "已启用"
    assert "已翻译" in widget._table.item(0, COL_CONTEXT).text()
    assert "未翻译" in widget._table.item(1, COL_CONTEXT).text()
    assert widget._table.accessibleDescription()

    widget.close()
    theme_view.close()
    service.close()


def test_status_cell_text_and_theme_visual_follow_the_same_stage(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    service, theme_view = _theme(qapp, tmp_path)
    collection = _collection(1)
    widget = Step2PreviewWidget(context_module.AppContext(), theme_view=theme_view)
    widget.refresh(collection)
    _drain_render(qapp, widget, 1)
    entry = collection.get_by_id("0")

    widget._on_stage_change(entry, STAGE_QUESTIONABLE)
    option = QStyleOptionViewItem()
    index = widget._table.model().index(0, COL_CONTEXT)
    widget._table.itemDelegate().initStyleOption(option, index)
    expected = theme_view.domain_brushes(service.snapshot()).stage(str(STAGE_QUESTIONABLE))

    assert "有疑问" in widget._table.item(0, COL_CONTEXT).text()
    assert option.backgroundBrush.color() == expected.background.color()
    assert option.palette.brush(option.palette.ColorRole.Text).color() == expected.foreground.color()

    widget.close()
    theme_view.close()
    service.close()


def test_10k_materialized_rows_switch_theme_without_model_rebuild(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    service, theme_view = _theme(qapp, tmp_path)
    widget = Step2PreviewWidget(context_module.AppContext(), theme_view=theme_view)
    widget.refresh(_collection(10_000))
    _drain_render(qapp, widget, 10_000)
    table = widget._table
    generation = table.render_session.generation
    sentinel = table.item(9_999, COL_TRANSLATION)
    stylesheet_applies = 0
    real_set_stylesheet = qapp.setStyleSheet

    def counted_set_stylesheet(stylesheet: str) -> None:
        nonlocal stylesheet_applies
        stylesheet_applies += 1
        real_set_stylesheet(stylesheet)

    monkeypatch.setattr(qapp, "setStyleSheet", counted_set_stylesheet)

    started = perf_counter()
    result = service.set_preference(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID), persist=False)
    qapp.processEvents()
    elapsed = perf_counter() - started

    assert result.snapshot is not None
    assert elapsed <= 0.250
    assert stylesheet_applies == 0
    assert table.render_session.generation == generation
    assert table.item(9_999, COL_TRANSLATION) is sentinel

    widget.close()
    theme_view.close()
    service.close()


def test_composition_roots_accept_the_shared_optional_theme_view(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    service, theme_view = _theme(qapp, tmp_path)
    context = context_module.AppContext()
    workbench = WorkbenchWidget(context, theme_view=theme_view)
    cards = (
        UploadCard(context, lambda **_kwargs: None, theme_view=theme_view),
        DownloadCard(context, lambda **_kwargs: None, theme_view=theme_view),
        WriteCard(context, lambda **_kwargs: None, theme_view=theme_view),
    )

    assert workbench.preview._theme_view is theme_view
    assert workbench.project_bar._domain is not None
    assert all(card._theme_view is theme_view for card in cards)

    for card in cards:
        card.close()
    workbench.close()
    theme_view.close()
    service.close()
