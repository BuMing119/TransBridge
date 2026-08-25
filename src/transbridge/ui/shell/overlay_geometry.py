"""Shared geometry policy for workspace overlay windows."""

from __future__ import annotations

from PyQt6.QtCore import QRect

_OVERLAY_MARGIN = 24
_OVERLAY_WIDTH_RATIO = 0.78
_OVERLAY_HEIGHT_RATIO = 0.82
_OVERLAY_MIN_WIDTH = 900
_OVERLAY_MIN_HEIGHT = 600
_OVERLAY_MAX_WIDTH = 1280
_OVERLAY_MAX_HEIGHT = 820


def workspace_overlay_rect(host_rect: QRect) -> QRect:
    """Return a centred overlay rectangle that remains visible on small hosts."""
    available_width = max(1, host_rect.width() - 2 * _OVERLAY_MARGIN)
    available_height = max(1, host_rect.height() - 2 * _OVERLAY_MARGIN)
    preferred_width = max(_OVERLAY_MIN_WIDTH, round(host_rect.width() * _OVERLAY_WIDTH_RATIO))
    preferred_height = max(_OVERLAY_MIN_HEIGHT, round(host_rect.height() * _OVERLAY_HEIGHT_RATIO))
    width = min(available_width, _OVERLAY_MAX_WIDTH, preferred_width)
    height = min(available_height, _OVERLAY_MAX_HEIGHT, preferred_height)
    left = host_rect.left() + max(0, (host_rect.width() - width) // 2)
    top = host_rect.top() + max(0, (host_rect.height() - height) // 2)
    return QRect(left, top, width, height)


__all__ = ["workspace_overlay_rect"]
