"""Windows frame integration checks, restricted to isolated assistant test HWNDs.

Run with QT_QPA_PLATFORM=windows. No desktop pointer or existing app is operated.
The tests query native hit targets and issue system commands to the test window;
they do not replace a human check of dragging or multi-monitor DPI transitions.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.tools.smart_assistant import test_v2_chat_recovery as fixtures

chat_environment = fixtures.chat_environment
pytestmark = [pytest.mark.integration, pytest.mark.skipif(sys.platform != "win32", reason="Windows HWND contract")]


@pytest.fixture
def native_app():
    app = QApplication.instance() or QApplication([])
    if app.platformName() != "windows":
        pytest.skip("Requires QT_QPA_PLATFORM=windows; offscreen cannot verify native HWND behavior")
    return app


@pytest.fixture
def native_panel(native_app, chat_environment):
    panel = chat_environment.panel()
    fixtures._until(lambda: panel.chat.session_ready)
    panel.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    panel.resize(640, 480)
    panel.move(100, 100)
    panel.show()
    native_app.processEvents()
    yield panel
    panel.close()
    native_app.processEvents()


@pytest.fixture
def win32(native_app):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    return user32


def _frame_rect(win32, panel):
    rect = wintypes.RECT()
    assert win32.GetWindowRect(int(panel.winId()), ctypes.byref(rect))
    return rect


def _hit_test(win32, panel, x, y):
    return win32.SendMessageW(int(panel.winId()), 0x0084, 0, ((y & 0xFFFF) << 16) | (x & 0xFFFF))


def test_native_window_has_system_caption_resize_frame_and_window_buttons(native_panel, win32):
    style = win32.GetWindowLongPtrW(int(native_panel.winId()), -16)
    # WS_CAPTION, WS_THICKFRAME, WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX.
    for required in (0x00C00000, 0x00040000, 0x00080000, 0x00020000, 0x00010000):
        assert style & required == required
    assert native_panel.titleBarWidget() is None
    assert native_panel.mask().isEmpty()


@pytest.mark.parametrize(
    ("edge", "expected"),
    [
        ("left", 10),
        ("right", 11),
        ("top", 12),
        ("bottom", 15),
        ("top_left", 13),
        ("top_right", 14),
        ("bottom_left", 16),
        ("bottom_right", 17),
    ],
)
def test_native_frame_exposes_all_eight_resize_targets(native_panel, win32, edge, expected):
    rect = _frame_rect(win32, native_panel)
    x = rect.left + 2 if "left" in edge else rect.right - 3 if "right" in edge else (rect.left + rect.right) // 2
    y = rect.top + 2 if "top" in edge else rect.bottom - 3 if "bottom" in edge else (rect.top + rect.bottom) // 2
    assert _hit_test(win32, native_panel, x, y) == expected


def test_native_title_is_a_caption_drag_target_but_session_summary_is_client_area(native_panel, win32):
    rect = _frame_rect(win32, native_panel)
    client_origin = wintypes.POINT()
    assert win32.ClientToScreen(int(native_panel.winId()), ctypes.byref(client_origin))
    assert client_origin.y > rect.top
    assert _hit_test(win32, native_panel, (rect.left + rect.right) // 2, (rect.top + client_origin.y) // 2) == 2
    ratio = native_panel.devicePixelRatioF()
    assert (
        _hit_test(win32, native_panel, client_origin.x + round(100 * ratio), client_origin.y + round(20 * ratio)) == 1
    )


def test_native_system_commands_minimize_maximize_restore_and_reuse_on_close(native_panel, native_app, win32):
    panel = native_panel
    panel.chat.set_input("关闭后继续编辑")
    original_chat = panel.chat

    def command(value):
        win32.SendMessageW(int(panel.winId()), 0x0112, value, 0)
        native_app.processEvents()

    command(0xF020)  # SC_MINIMIZE
    assert panel.isMinimized()
    command(0xF120)  # SC_RESTORE
    assert not panel.isMinimized()
    command(0xF030)  # SC_MAXIMIZE
    assert panel.isMaximized()
    command(0xF120)
    assert not panel.isMaximized()
    command(0xF060)  # SC_CLOSE
    assert not panel.isVisible()
    assert not panel.is_disposed
    panel.show()
    native_app.processEvents()
    assert panel.isVisible()
    assert panel.chat is original_chat
    assert panel.chat._input_view.input.toPlainText() == "关闭后继续编辑"
    assert panel.mask().isEmpty()


def test_native_frame_retains_minimum_client_size_and_draft(native_panel, native_app, win32):
    panel = native_panel
    panel.chat.set_input("原生窗口缩放仍保留的草稿")
    panel.resize(100, 100)
    native_app.processEvents()
    assert panel.width() == panel.minimumWidth() == 480
    assert panel.height() == panel.minimumHeight() == 400
    margins = panel.windowHandle().frameMargins()
    # Windows can report zero visible side margins while retaining invisible
    # resize borders. Native hit tests above are the contract for those sides.
    assert margins.top() > 0
    rect = _frame_rect(win32, panel)
    assert rect.bottom - rect.top > round(panel.height() * panel.devicePixelRatioF())
    assert panel.chat._input_view.input.toPlainText() == "原生窗口缩放仍保留的草稿"
