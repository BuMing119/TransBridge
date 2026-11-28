"""Verify dictionary result rendering, wrapping and lazy row sizing."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.ui.foundation.accessibility import contrast_ratio
from transbridge.ui.tools import dictionary_results
from transbridge.ui.tools.dictionary_results import DictionaryResultsTable


@pytest.fixture
def table(layout_app):
    view = DictionaryResultsTable()
    view.resize(600, 320)
    view.show()
    settle_layout(layout_app)
    try:
        yield view
    finally:
        view.close()
        view.deleteLater()


def _contains_highlight(image, rect, color):
    scale = image.devicePixelRatio()
    return any(
        image.pixelColor(x, y) == color
        for x in range(round(rect.left() * scale), round((rect.right() + 1) * scale))
        for y in range(round(rect.top() * scale), round((rect.bottom() + 1) * scale))
    )


def test_result_highlights_are_literal_bilingual_and_preserve_plain_text(table, layout_app):
    data = ("😀 <b>A.B</b> <axb>", "译文 a.b", "A.B.esp")
    table.replace_results((data, ("axb", "axb", "axb.esp")), " a.b ")
    settle_layout(layout_app)
    model = table.model()
    indexes = [model.index(0, column) for column in range(model.columnCount())]
    assert tuple(index.data() for index in indexes) == data
    assert tuple(index.data(Qt.ItemDataRole.ToolTipRole) for index in indexes) == data
    assert model.rowCount() == 2  # The presentation does not filter incoming results.
    assert all(not model.flags(index) & Qt.ItemFlag.ItemIsEditable for index in indexes)
    assert table.editTriggers() == table.EditTrigger.NoEditTriggers

    image = table.viewport().grab().toImage()
    color = QColor("#f9e9b0")
    assert [_contains_highlight(image, table.visualRect(index), color) for index in indexes] == [True, True, False]
    assert not _contains_highlight(image, table.visualRect(model.index(1, 0)), color)

    table.replace_results((data,), "")
    settle_layout(layout_app)
    image = table.viewport().grab().toImage()
    assert not _contains_highlight(image, table.visualRect(model.index(0, 0)), color)


@pytest.mark.parametrize(
    ("base", "text", "background"), [("#ffffff", "#20262e", "#f9e9b0"), ("#21252a", "#edf0f3", "#5c4c28")]
)
def test_match_keeps_readable_body_text_in_light_and_dark_palettes(table, base, text, background):
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Base, QColor(base))
    palette.setColor(QPalette.ColorRole.Text, QColor(text))
    table.replace_results((("Light", "", ""),), "light")
    document = dictionary_results._wrapped_document("Light", table.font(), 120)
    mark = table.itemDelegate()._highlight(document, "Light", palette)[0].format
    assert mark.foreground().color().name() == text
    assert mark.background().color().name() == background
    assert contrast_ratio(text, background) >= 4.5


def test_highlight_positions_account_for_casefold_expansion_and_qt_text_offsets(table):
    text = "😀\r\nStraße + STRASSE"
    table.replace_results(((text, "", ""),), "strasse")
    document = dictionary_results._wrapped_document(text, table.font(), 140)
    selections = table.itemDelegate()._highlight(document, text, table.palette())
    assert [selection.cursor.selectedText() for selection in selections] == ["Straße", "STRASSE"]


def test_unbroken_bilingual_text_wraps_when_columns_resize(table, layout_app):
    long_text = "ABCD123456" * 24
    table.replace_results((("短词", long_text, "fixture.esp"),), "123")
    settle_layout(layout_app)
    initial_widths = [table.columnWidth(column) for column in range(3)]
    initial_height = table.rowHeight(0)
    assert initial_widths[0] > initial_widths[2] and initial_widths[1] > initial_widths[2]
    assert initial_height >= 3 * table.fontMetrics().height()
    assert table.horizontalScrollBar().maximum() == 0

    table.resize(900, 320)
    settle_layout(layout_app)
    assert all(table.columnWidth(column) > width for column, width in enumerate(initial_widths))
    assert table.rowHeight(0) < initial_height

    table.replace_results((("短词", "短译", "fixture.esp"),), "")
    settle_layout(layout_app)
    assert table.rowHeight(0) < 3 * table.fontMetrics().height()


def test_large_result_replacement_and_scrolling_only_measure_visible_rows(table, layout_app, monkeypatch):
    wrapped_document = dictionary_results._wrapped_document
    seen = []

    def observe(text, font, width):
        seen.append(text)
        return wrapped_document(text, font, width)

    monkeypatch.setattr(dictionary_results, "_wrapped_document", observe)
    table.replace_results(tuple((f"row {row}", "译文", "fixture.esp") for row in range(40_000)), "")
    settle_layout(layout_app)
    model = table.model()
    assert model.rowCount() == 40_000
    assert "row 0" in seen
    assert "row 39000" not in seen
    assert len(seen) < 300

    seen.clear()
    far = model.index(39_000, 0)
    table.scrollTo(far, table.ScrollHint.PositionAtTop)
    settle_layout(layout_app)
    assert table.viewport().rect().intersects(table.visualRect(far))
    assert "row 39000" in seen
    assert len(seen) < 300
