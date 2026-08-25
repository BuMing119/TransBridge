from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from transbridge.ui import windows_taskbar

_APP = QApplication.instance() or QApplication([])


def test_window_app_user_model_id_delegates_to_native_hwnd(monkeypatch) -> None:
    calls: list[tuple[int, str | None]] = []
    qualified_hwnds: list[int] = []
    window = QWidget()
    monkeypatch.setattr(windows_taskbar, "_uses_native_windows_qt", lambda: True)
    monkeypatch.setattr(windows_taskbar, "_ensure_hwnd_taskbar_button", qualified_hwnds.append)
    monkeypatch.setattr(
        windows_taskbar,
        "_set_hwnd_app_user_model_id",
        lambda hwnd, app_id: calls.append((hwnd, app_id)),
    )

    assert windows_taskbar.set_window_app_user_model_id(window, "TransBridge.SmartAssistant") is True
    assert windows_taskbar.clear_window_app_user_model_id(window) is True
    assert qualified_hwnds == [int(window.winId())]
    assert calls == [
        (int(window.winId()), "TransBridge.SmartAssistant"),
        (int(window.winId()), None),
    ]
    window.close()


def test_window_app_user_model_id_is_noop_off_windows(monkeypatch) -> None:
    window = QWidget()
    monkeypatch.setattr(windows_taskbar, "_uses_native_windows_qt", lambda: False)

    assert windows_taskbar.set_window_app_user_model_id(window, "TransBridge.SmartAssistant") is False
    assert windows_taskbar.clear_window_app_user_model_id(window) is False
    window.close()


@pytest.mark.parametrize("app_id", ["", "x" * 129])
def test_window_app_user_model_id_rejects_invalid_identifiers(app_id: str) -> None:
    with pytest.raises(ValueError):
        windows_taskbar.set_window_app_user_model_id(QWidget(), app_id)
