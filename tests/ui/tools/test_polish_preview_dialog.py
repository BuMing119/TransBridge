from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.tools.ai_translator._polish_preview_dialog import _PolishPreviewDialog


def _entry(entry_id: str) -> TranslationEntry:
    return TranslationEntry(
        id=entry_id,
        key=f"key-{entry_id}",
        original=f"Original {entry_id}",
        translation=f"当前 {entry_id}",
        stage=1,
        context="DIAL:FULL",
    )


def test_preview_populates_all_columns_and_connects_click_once() -> None:
    app = QApplication.instance() or QApplication([])
    entries = [_entry("one"), _entry("two")]
    results = {
        entry.id: SimpleNamespace(
            confidence=0.9,
            issues=(SimpleNamespace(severity="warning", message="措辞问题"),),
            refined_translation=f"纠错 {entry.id}",
            polished_translation=f"最终 {entry.id}",
            verdict="pass",
            note="裁决通过",
        )
        for entry in entries
    }
    dialog = _PolishPreviewDialog(entries, results)

    assert [dialog._table.item(0, col).text() for col in range(6)] == [
        "Original one",
        "当前 one",
        "[warning] 措辞问题",
        "纠错 one",
        "最终 one",
        "pass (0.90)\n裁决通过",
    ]

    dialog._table.cellClicked.emit(0, 4)

    assert dialog.get_results() == {"one": "最终 one", "two": None}
    dialog.close()
    app.processEvents()
