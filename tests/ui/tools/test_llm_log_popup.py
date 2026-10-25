import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QProxyStyle, QStyle, QStyleFactory
import pytest

from tests.ui.responsive_layout_support import themed_layout_app as themed_layout_app
from tests.ui.tools.ai_read_projection_support import wait_for
from transbridge.ui.tools.ai_translator._llm_log_viewer import _LLMLogViewer


class _MenuPopupStyle(QProxyStyle):
    """Exercise platforms whose menu-style combo popups ignore maxVisibleItems."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):  # noqa: N802
        if hint == QStyle.StyleHint.SH_ComboBox_Popup:
            return 1
        return super().styleHint(hint, option, widget, returnData)


@pytest.mark.parametrize("menu_style", [False, True])
@pytest.mark.parametrize("font_size,window_size", [(9, (560, 420)), (14, (900, 640))])
def test_log_popup_height_is_bounded_and_last_log_remains_selectable(
    layout_app, tmp_path, menu_style, font_size, window_size
):
    for index in range(78):
        (tmp_path / f"translate_call_{index:03}.log").write_text(f"response {index}", encoding="utf-8")
    viewer = _LLMLogViewer(str(tmp_path))
    selector = viewer._log_selector
    if menu_style:
        style = _MenuPopupStyle(QStyleFactory.create("Fusion"))
        style.setParent(selector)
        selector.setStyle(style)
    viewer.setStyleSheet(f"QWidget {{ font-size: {font_size}pt; }}")
    viewer.resize(*window_size)
    viewer.show()
    viewer.stop_auto_refresh()
    wait_for(lambda: selector.count() == 78)
    selector.setCurrentIndex(0)
    layout_app.processEvents()
    size_before = viewer.size()
    try:
        selector.showPopup()
        layout_app.processEvents()
        view = selector.view()
        row_height = view.sizeHintForRow(0)
        assert row_height > 0
        assert view.viewport().height() <= row_height * 12
        assert view.window().height() < viewer.height()
        assert selector.count() == 78
        assert view.verticalScrollBar().isVisible()
        assert view.verticalScrollBar().maximum() > 0
        assert viewer.size() == size_before

        QTest.keyClick(view, Qt.Key.Key_End)
        layout_app.processEvents()
        last = selector.model().index(77, 0)
        assert view.viewport().rect().contains(view.visualRect(last).center())
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(last).center())
        layout_app.processEvents()
        assert selector.currentIndex() == 77
        wait_for(lambda: viewer._text_edit.toPlainText() == "response 77")
        assert viewer._text_edit.toPlainText() == "response 77"

        selector.showPopup()
        layout_app.processEvents()
        assert view.viewport().height() <= row_height * 12
    finally:
        selector.hidePopup()
        viewer.close()
        viewer.deleteLater()
        layout_app.processEvents()
