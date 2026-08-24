from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.ui.foundation.builtins import DEFAULT_THEME_ID, create_builtin_registry
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.qt_palette import compile_palette
from transbridge.ui.tools.fomod.fomod_panel import FomodPanel


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _Facade:
    def __init__(self) -> None:
        self.calls = []

    def begin_fomod(self, *args, **kwargs) -> None:
        self.calls.append((args, kwargs))


def test_palette_revision_preserves_archive_form_and_has_zero_plan_or_file_side_effect(qapp, tmp_path) -> None:
    original_palette = QPalette(qapp.palette())
    registry = create_builtin_registry()
    qapp.setPalette(compile_palette(registry.resolve(DEFAULT_THEME_ID, ThemeScheme.LIGHT)))
    facade = _Facade()
    panel = FomodPanel(object(), operation_plan_facade=facade)
    new_archive = str(tmp_path / "new.7z")
    old_archive = str(tmp_path / "old.zip")
    chosen_output = str(tmp_path / "manual-output.zip")
    panel._new_edit.setText(new_archive)
    panel._old_edit.setText(old_archive)
    panel._out_edit.setText(chosen_output)
    panel._keep_ext_edit.setText(".dds")
    before_window = panel.palette().color(QPalette.ColorRole.Window)

    qapp.setPalette(compile_palette(registry.resolve(DEFAULT_THEME_ID, ThemeScheme.DARK)))
    qapp.processEvents()

    try:
        assert panel.palette().color(QPalette.ColorRole.Window) != before_window
        assert panel._new_edit.text() == new_archive
        assert panel._old_edit.text() == old_archive
        assert panel._out_edit.text() == chosen_output
        assert panel._keep_ext_edit.text() == ".dds"
        assert panel._worker is None
        assert facade.calls == []
    finally:
        panel.close()
        qapp.setPalette(original_palette)


def test_result_status_is_visible_text_and_accessible_semantic_state(qapp) -> None:
    panel = FomodPanel()
    panel._on_done({"archive_path": "translated.zip", "extracted_count": 2})

    assert panel._result_text.property("tbStatusId") == "completed"
    assert panel._result_text.property("tbSemanticState") == "success"
    assert "翻译完成" in panel._result_text.toPlainText()
    assert "翻译完成" in panel._result_text.accessibleDescription()

    panel._on_failed("archive invalid")
    assert panel._result_text.property("tbStatusId") == "failed"
    assert panel._result_text.property("tbSemanticState") == "error"
    assert "执行失败" in panel._result_text.toPlainText()
    panel.close()
