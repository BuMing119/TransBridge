"""词条模块共享常量与工具函数。"""

from PyQt6.QtCore import Qt

from src.transbridge.converter.translation_entry import STAGE_LABELS, STAGE_COLORS

# 扩展：添加 ParaTranz UI 哨兵值 "全部"（-2）
_STAGE_LABELS = {-2: "全部", **STAGE_LABELS}
_STAGE_COLORS = STAGE_COLORS
_KEY_ROLE = Qt.ItemDataRole.UserRole + 1


def _extract_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("data", "results", "items"):
            if isinstance(data.get(k), list):
                return data[k]
    return []