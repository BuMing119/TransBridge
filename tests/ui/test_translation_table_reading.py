"""Long translations stay readable and intact through editing and projection refreshes."""

from dataclasses import replace
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPersistentModelIndex, Qt
from PyQt6.QtGui import QTextDocument
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit
import pytest

from transbridge.application.terminology_profiles.projection import ProjectedTranslation, ProjectionDiagnostic
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.workbench.table_presenter import RenderSession
from transbridge.ui.workbench.translation_table import TranslationTable
from transbridge.ui.workbench.translation_table_columns import COL_KEY, COL_ORIGINAL, COL_TRANSLATION


@pytest.fixture
def reading_table():
    app = QApplication.instance() or QApplication([])
    table = TranslationTable(on_progress=lambda *_: None, on_batch=lambda: None)
    table.resize(950, 450)
    table.show()
    app.processEvents()
    yield table
    table.close_rendering()
    table.close()
    table.deleteLater()
    app.processEvents()


def _entry() -> TranslationEntry:
    return TranslationEntry(
        "long-entry", "long-key", "Original <Alias=Player> & " * 12, "完整译文 <b>标签</b> & " * 15, 1, "INFO:NAM1"
    )


def _tooltip_text(index) -> str:
    document = QTextDocument()
    document.setHtml(index.data(Qt.ItemDataRole.ToolTipRole))
    return document.toPlainText()


def test_full_text_is_available_in_cells_tooltips_and_inline_editor(reading_table):
    table = reading_table
    entry = _entry()
    table.start_render(RenderSession(1, None, (entry,)), {}, {})
    original, translation = table.model().index(0, COL_ORIGINAL), table.model().index(0, COL_TRANSLATION)

    assert original.data(Qt.ItemDataRole.DisplayRole) == entry.original
    assert translation.data(Qt.ItemDataRole.DisplayRole) == entry.translation
    assert _tooltip_text(original) == entry.original
    assert _tooltip_text(translation) == entry.translation
    table.setCurrentIndex(translation)
    table.edit(translation)
    editor = table.findChild(QLineEdit)
    assert editor is not None and editor.text() == entry.translation
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    assert translation.data(Qt.ItemDataRole.DisplayRole) == entry.translation


def test_text_columns_receive_more_space_than_key_without_horizontal_scrolling(reading_table):
    table = reading_table
    table.start_render(RenderSession(1, None, (_entry(),)), {}, {})
    QApplication.processEvents()
    assert table.columnWidth(COL_ORIGINAL) > table.columnWidth(COL_KEY)
    assert table.columnWidth(COL_TRANSLATION) > table.columnWidth(COL_KEY)
    assert table.horizontalScrollBar().maximum() == 0


def test_incremental_update_refreshes_full_translation_and_tooltip(reading_table):
    entry = _entry()
    reading_table.start_render(RenderSession(1, None, (entry,)), {}, {})
    item = QPersistentModelIndex(reading_table.model().index(0, COL_TRANSLATION))
    resets = []
    reading_table.model().modelReset.connect(lambda: resets.append(True))
    updated = replace(entry, translation="更新后的长译文 " * 30)

    assert reading_table.update_rendered_entry(updated) == 0
    assert item.isValid()
    assert reading_table.model().index(0, COL_TRANSLATION) == item
    assert resets == []
    assert item.data(Qt.ItemDataRole.DisplayRole) == updated.translation
    assert _tooltip_text(item) == updated.translation


def test_full_text_tooltips_preserve_whitespace_and_literal_markup(reading_table):
    text = "  A   B\t<Alias=Player>\n  D  "
    entry = replace(_entry(), original=text, translation=text)
    reading_table.start_render(RenderSession(1, None, (entry,)), {}, {})
    for column in (COL_ORIGINAL, COL_TRANSLATION):
        assert _tooltip_text(reading_table.model().index(0, column)) == text


def test_profile_preview_keeps_full_text_and_diagnostics_after_rejecting_edit(reading_table):
    entry = _entry()
    projection = ProjectedTranslation(
        entry.translation,
        "方案长译文 <b>保留标签</b> " * 20,
        diagnostics=(ProjectionDiagnostic("ambiguous", "请检查 <Alias=Player>", entry.key),),
    )
    reading_table.set_terminology_projection({entry.identity.serialize(): projection}, profile_label="测试方案")
    reading_table.start_render(RenderSession(1, None, (entry,)), {}, {})
    item = reading_table.model().index(0, COL_TRANSLATION)
    assert item.data(Qt.ItemDataRole.DisplayRole) == projection.translation
    assert not item.flags() & Qt.ItemFlag.ItemIsEditable
    tooltip = _tooltip_text(item)
    assert projection.translation in tooltip
    assert "测试方案" in tooltip and "请检查 <Alias=Player>" in tooltip

    assert not reading_table.model().setData(item, "out-of-band edit", Qt.ItemDataRole.EditRole)
    assert item.data(Qt.ItemDataRole.DisplayRole) == projection.translation
    assert _tooltip_text(item) == tooltip
    assert entry.translation != projection.translation
