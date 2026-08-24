"""Navigation rendering primitives for the ParaTranz string dialog."""

from __future__ import annotations

from collections.abc import Iterable

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QBrush, QFont, QPalette
from PyQt6.QtWidgets import QApplication, QStyle, QStyledItemDelegate

from transbridge.ui.foundation.adapters import DomainBrushes

from ._strings_common import _KEY_ROLE


class NavItemDelegate(QStyledItemDelegate):
    """Render an original string and a smaller, muted key on two rows."""

    def __init__(self, parent=None, *, domain_brushes: DomainBrushes | None = None) -> None:
        super().__init__(parent)
        self._normal_brush = QBrush()
        self._key_brush = QBrush()
        self._selected_brush = QBrush()
        self.apply_domain_brushes(domain_brushes)

    def apply_domain_brushes(self, domain_brushes: DomainBrushes | None) -> None:
        """Compile brushes outside paint; a theme revision only swaps this cache."""

        palette = QApplication.palette()
        self._selected_brush = QBrush(palette.brush(QPalette.ColorRole.HighlightedText))
        if domain_brushes is None:
            self._normal_brush = QBrush(palette.brush(QPalette.ColorRole.Text))
            self._key_brush = QBrush(palette.brush(QPalette.ColorRole.PlaceholderText))
        else:
            self._normal_brush = QBrush(domain_brushes.translation("source").foreground)
            self._key_brush = QBrush(domain_brushes.label("neutral").foreground)

    def paint(self, painter, option, index) -> None:
        display_option = option.__class__(option)
        self.initStyleOption(display_option, index)
        display_option.text = ""
        QApplication.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, display_option, painter)
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        rect = option.rect.adjusted(6, 4, -6, -4)
        original = index.data(Qt.ItemDataRole.DisplayRole) or ""
        key = index.data(_KEY_ROLE) or ""
        split = rect.height() * 6 // 10
        original_rect = QRect(rect.x(), rect.y(), rect.width(), split)
        key_rect = QRect(rect.x(), rect.y() + split, rect.width(), rect.height() - split)
        painter.setPen(self._selected_brush if selected else self._normal_brush)
        painter.setFont(option.font)
        painter.drawText(
            original_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(original, Qt.TextElideMode.ElideRight, original_rect.width()),
        )
        key_font = QFont(option.font)
        key_font.setPointSize(max(option.font.pointSize() - 1, 8))
        painter.setFont(key_font)
        painter.setPen(self._selected_brush if selected else self._key_brush)
        painter.drawText(
            key_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(key, Qt.TextElideMode.ElideRight, key_rect.width()),
        )
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width() if option.rect.width() > 0 else 200, 52)


def filtered_indices(
    strings: Iterable[dict],
    *,
    selected_stages: set[int],
    modifier_id: int,
    current_user_id: object,
) -> list[int]:
    """Return stable source indices matching the current navigation filter."""

    result: list[int] = []
    for index, item in enumerate(strings):
        if selected_stages and item.get("stage", 0) not in selected_stages:
            continue
        if modifier_id in (1, 2) and current_user_id is not None:
            user = item.get("user") or {}
            user_id = user.get("uid") or user.get("id")
            if modifier_id == 1 and user_id != current_user_id:
                continue
            if modifier_id == 2 and user_id == current_user_id:
                continue
        result.append(index)
    return result


def sync_candidates(strings: Iterable[dict], saved_string: dict, new_stage: int) -> list[dict]:
    """Find same-original strings whose stage may be safely raised."""

    current_id = saved_string.get("id")
    current_original = saved_string.get("original", "")
    return [
        item
        for item in strings
        if item.get("original") == current_original
        and item.get("id") != current_id
        and 0 <= item.get("stage", 0) < new_stage
    ]
