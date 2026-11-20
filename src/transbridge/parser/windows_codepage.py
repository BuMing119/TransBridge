# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Match Windows ANSI-to-Unicode conversion without Python codec remapping."""

import ctypes
from ctypes import wintypes
import sys

if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _convert = _kernel32.MultiByteToWideChar
    _convert.argtypes = [wintypes.UINT, wintypes.DWORD, ctypes.c_char_p, ctypes.c_int, wintypes.LPWSTR, ctypes.c_int]
    _convert.restype = ctypes.c_int
else:
    _convert = None


def decode_windows_codepage(payload: bytes, encoding: str) -> str:
    """Use the same flags-zero Windows conversion as an ANSI string cast.

    The stdlib code_page_decode wrapper still applies Python error handling
    for some Greek and Japanese bytes, so it cannot substitute for this API.
    """
    if _convert is None:
        raise LookupError(f"XT-compatible {encoding} decoding requires the Windows code-page decoder")
    if not payload:
        return ""
    codepage = int(encoding.removeprefix("cp"))
    size = _convert(codepage, 0, payload, len(payload), None, 0)
    if not size:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(size)
    count = _convert(codepage, 0, payload, len(payload), buffer, size)
    if not count:
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer[:count]
