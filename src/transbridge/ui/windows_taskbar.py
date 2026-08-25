"""Windows taskbar identity helpers for independent top-level Qt windows."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import sys
from typing import TYPE_CHECKING
from uuid import UUID

from PyQt6.QtGui import QGuiApplication

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

_IID_IPROPERTY_STORE = "886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"
_PKEY_APP_USER_MODEL_ID = "9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"
_PKEY_APP_USER_MODEL_ID_PID = 5
_IPROPERTY_STORE_SET_VALUE_INDEX = 6
_IUNKNOWN_RELEASE_INDEX = 2
_HResult = ctypes.c_long
_VT_LPWSTR = 31
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_APPWINDOW = 0x00040000
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", wintypes.DWORD),
        ("data2", wintypes.WORD),
        ("data3", wintypes.WORD),
        ("data4", ctypes.c_ubyte * 8),
    ]


class _PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", _Guid), ("pid", wintypes.DWORD)]


class _PropVariantValue(ctypes.Union):
    # PROPVARIANT's value union is pointer-sized on 32-bit Windows and 16
    # bytes on 64-bit Windows. The raw field preserves the native alignment;
    # pwsz_val is the member used by InitPropVariantFromString.
    _fields_ = [
        ("pwsz_val", wintypes.LPWSTR),
        ("raw", ctypes.c_ubyte * (16 if ctypes.sizeof(ctypes.c_void_p) == 8 else 8)),
    ]


class _PropVariant(ctypes.Structure):
    _fields_ = [
        ("vt", wintypes.USHORT),
        ("reserved1", wintypes.WORD),
        ("reserved2", wintypes.WORD),
        ("reserved3", wintypes.WORD),
        ("value", _PropVariantValue),
    ]


def _guid(value: str) -> _Guid:
    result = _Guid()
    ctypes.memmove(ctypes.byref(result), UUID(value).bytes_le, ctypes.sizeof(result))
    return result


_APP_USER_MODEL_ID_KEY = _PropertyKey(_guid(_PKEY_APP_USER_MODEL_ID), _PKEY_APP_USER_MODEL_ID_PID)


def _failed(result: int) -> bool:
    return result < 0


def _raise_for_hresult(result: int, operation: str) -> None:
    if _failed(result):
        unsigned_result = ctypes.c_ulong(result).value
        raise OSError(f"{operation} failed with HRESULT 0x{unsigned_result:08X}")


def _set_hwnd_app_user_model_id(hwnd: int, app_id: str | None) -> None:
    """Set or clear ``System.AppUserModel.ID`` on one native window."""
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    get_property_store = shell32.SHGetPropertyStoreForWindow
    get_property_store.argtypes = [wintypes.HWND, ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p)]
    get_property_store.restype = _HResult

    store = ctypes.c_void_p()
    result = get_property_store(hwnd, ctypes.byref(_guid(_IID_IPROPERTY_STORE)), ctypes.byref(store))
    _raise_for_hresult(result, "SHGetPropertyStoreForWindow")

    variant = _PropVariant()
    value_buffer = None
    try:
        if app_id is not None:
            # IPropertyStore.SetValue copies this VT_LPWSTR synchronously, so
            # the Python-owned buffer only needs to live through the call.
            value_buffer = ctypes.create_unicode_buffer(app_id)
            variant.vt = _VT_LPWSTR
            variant.value.pwsz_val = ctypes.cast(value_buffer, wintypes.LPWSTR)

        vtable = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        set_value_type = ctypes.WINFUNCTYPE(
            _HResult,
            ctypes.c_void_p,
            ctypes.POINTER(_PropertyKey),
            ctypes.POINTER(_PropVariant),
        )
        set_value = set_value_type(vtable[_IPROPERTY_STORE_SET_VALUE_INDEX])
        result = set_value(store, ctypes.byref(_APP_USER_MODEL_ID_KEY), ctypes.byref(variant))
        _raise_for_hresult(result, "IPropertyStore.SetValue")
    finally:
        if store:
            vtable = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            release_type = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)
            release_type(vtable[_IUNKNOWN_RELEASE_INDEX])(store)


def _ensure_hwnd_taskbar_button(hwnd: int) -> None:
    """Force a native top-level window to qualify for its own taskbar button."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_window_long = user32.GetWindowLongPtrW
    get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
    get_window_long.restype = ctypes.c_ssize_t
    set_window_long = user32.SetWindowLongPtrW
    set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_window_long.restype = ctypes.c_ssize_t
    set_window_pos = user32.SetWindowPos
    set_window_pos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    set_window_pos.restype = wintypes.BOOL

    ctypes.set_last_error(0)
    extended_style = get_window_long(hwnd, _GWL_EXSTYLE)
    error = ctypes.get_last_error()
    if extended_style == 0 and error:
        raise OSError(error, "GetWindowLongPtrW failed")
    taskbar_style = (extended_style | _WS_EX_APPWINDOW) & ~_WS_EX_TOOLWINDOW
    if taskbar_style == extended_style:
        return

    ctypes.set_last_error(0)
    previous_style = set_window_long(hwnd, _GWL_EXSTYLE, taskbar_style)
    error = ctypes.get_last_error()
    if previous_style == 0 and error:
        raise OSError(error, "SetWindowLongPtrW failed")
    frame_flags = _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED
    if not set_window_pos(hwnd, None, 0, 0, 0, 0, frame_flags):
        raise ctypes.WinError(ctypes.get_last_error())


def _uses_native_windows_qt() -> bool:
    application = QGuiApplication.instance()
    return sys.platform == "win32" and application is not None and application.platformName().lower() == "windows"


def set_window_app_user_model_id(window: QWidget, app_id: str) -> bool:
    """Give one Qt window a taskbar identity distinct from the process default."""
    if not app_id or len(app_id) > 128:
        raise ValueError("Windows AppUserModelID must contain between 1 and 128 characters")
    if not _uses_native_windows_qt():
        return False
    try:
        hwnd = int(window.winId())
        _ensure_hwnd_taskbar_button(hwnd)
        _set_hwnd_app_user_model_id(hwnd, app_id)
    except (AttributeError, OSError):
        logger.warning("无法为窗口设置独立的 Windows 任务栏标识", exc_info=True)
        return False
    return True


def clear_window_app_user_model_id(window: QWidget) -> bool:
    """Remove an explicit taskbar identity before the native window is destroyed."""
    if not _uses_native_windows_qt():
        return False
    try:
        _set_hwnd_app_user_model_id(int(window.winId()), None)
    except (AttributeError, OSError):
        logger.warning("无法清除窗口的 Windows 任务栏标识", exc_info=True)
        return False
    return True
