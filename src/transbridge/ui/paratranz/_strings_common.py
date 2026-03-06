"""词条模块共享常量与工具函数。"""

from PyQt6.QtCore import Qt

_STAGE_LABELS = {
    -2: "全部",
    0: "未翻译",
    1: "已翻译",
    2: "有疑问",
    3: "已检查",
    5: "已审核",
    9: "已锁定",
    -1: "已隐藏",
}
_STAGE_COLORS = {
    0: "#9E9E9E", 1: "#2196F3", 2: "#FF9800",
    3: "#00BCD4", 5: "#4CAF50", 9: "#B71C1C", -1: "#757575",
}
_KEY_ROLE = Qt.ItemDataRole.UserRole + 1


def _extract_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("data", "results", "items"):
            if isinstance(data.get(k), list):
                return data[k]
    return []