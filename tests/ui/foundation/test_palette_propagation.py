"""Theme switches update painted widget palettes without replacing live editors."""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent, QPoint
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
import pytest

from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle
from transbridge.ui.foundation.theme_service import ThemeApplyStatus, ThemePreference, ThemeService


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def palette_view(qapp):
    previous_palette = QPalette(qapp.palette())
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=ThemeMode.LIGHT, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(qapp, create_builtin_registry(), preferences)
    assert service.start().status is not ThemeApplyStatus.FAILED
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    body = QWidget()
    body_layout = QVBoxLayout(body)
    body_layout.addWidget(QLabel("Ordinary scroll content"))
    body_layout.addStretch()
    scroll.setWidget(body)
    layout.addWidget(scroll)
    sources = QListWidget()
    sources.setProperty("tbTaskList", True)
    sources.addItem("Project source")
    layout.addWidget(sources)
    table = QTableWidget(3, 2)
    ComponentStyle.apply_static(table, ComponentKind.TABLE)
    for row in range(3):
        for column in range(2):
            table.setItem(row, column, QTableWidgetItem(f"Entry {row} value {column}"))
    layout.addWidget(table)
    field = QLineEdit("Unsubmitted input")
    ComponentStyle.apply_static(field, ComponentKind.INPUT)
    layout.addWidget(field)
    dialog.resize(640, 680)
    dialog.show()
    for _ in range(5):
        qapp.processEvents()
    yield SimpleNamespace(
        app=qapp, service=service, dialog=dialog, body=body, sources=sources, table=table, field=field
    )
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    service.close()
    qapp.setPalette(previous_palette)


def _contains_text_colour(frame, widget, rect, expected):
    rect.translate(widget.mapTo(widget.window(), QPoint()))
    rect = rect.intersected(frame.rect())
    return any(
        frame.pixelColor(x, y) == expected
        for x in range(rect.left(), rect.right() + 1)
        for y in range(rect.top(), rect.bottom() + 1)
    )


def test_existing_scroll_body_list_table_and_input_follow_theme_pixels(palette_view):
    view = palette_view
    for mode in (ThemeMode.DARK, ThemeMode.LIGHT):
        result = view.service.set_preference(ThemePreference(mode), persist=False)
        assert result.status is ThemeApplyStatus.APPLIED
        for _ in range(5):
            view.app.processEvents()
        palette = result.snapshot.palette
        frame = view.dialog.grab().toImage()
        for widget, role in (
            (view.body, QPalette.ColorRole.Window),
            (view.sources.viewport(), QPalette.ColorRole.Base),
            (view.table.viewport(), QPalette.ColorRole.Base),
            (view.table.verticalHeader().viewport(), QPalette.ColorRole.Window),
        ):
            point = widget.mapTo(view.dialog, QPoint(3, widget.height() - 5))
            assert frame.pixelColor(point) == palette.color(role), (type(widget).__name__, role)
        expected = palette.color(QPalette.ColorRole.Text)
        assert _contains_text_colour(
            frame, view.sources.viewport(), view.sources.visualItemRect(view.sources.item(0)), expected
        )
        assert _contains_text_colour(
            frame, view.table.viewport(), view.table.visualItemRect(view.table.item(0, 0)), expected
        )
        assert _contains_text_colour(frame, view.field, view.field.rect(), expected)


def test_palette_refresh_preserves_explicit_overrides_and_live_editor_state(palette_view):
    view = palette_view
    custom = QWidget(view.dialog)
    custom_palette = QPalette()
    custom_palette.setColor(QPalette.ColorRole.Window, QColor("#884488"))
    custom.setPalette(custom_palette)
    custom.setAutoFillBackground(True)
    view.dialog.layout().addWidget(custom)
    custom.show()
    table = view.table
    item = table.item(1, 1)
    table.setCurrentItem(item)
    table.openPersistentEditor(item)
    view.app.processEvents()
    editor = table.indexWidget(table.model().index(1, 1))
    editor.setText("Keep this draft")
    editor.setSelection(2, 4)
    for _ in range(5):
        view.app.processEvents()
    geometry = {widget: widget.geometry() for widget in (view.body, view.sources, table, view.field)}
    changes = []
    view.service.theme_changed.connect(lambda revision, _snapshot: changes.append(revision))
    selected = table.selectionModel().selectedIndexes()
    model = table.model()
    result = view.service.set_preference(ThemePreference(ThemeMode.DARK), persist=False)
    view.app.processEvents()
    assert result.status is ThemeApplyStatus.APPLIED
    assert custom.palette().color(QPalette.ColorRole.Window) == QColor("#884488")
    assert table.model() is model and table.item(1, 1) is item
    assert table.selectionModel().selectedIndexes() == selected
    assert table.indexWidget(model.index(1, 1)) is editor
    assert editor.text() == "Keep this draft" and editor.selectedText() == "ep t"
    assert {widget: widget.geometry() for widget in geometry} == geometry
    assert editor.isVisible() and table.visualItemRect(item).contains(editor.geometry())
    assert changes == [result.snapshot.revision]
