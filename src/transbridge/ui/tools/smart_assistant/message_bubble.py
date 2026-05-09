from PyQt6.QtWidgets import QWidget, QLabel, QHBoxLayout
from PyQt6.QtCore import Qt


class MessageBubble(QWidget):
    """单条消息气泡，根据 role 渲染不同样式。"""

    _STYLES = {
        "user": {
            "bg": "#DCF8C6",
            "align": "right",
            "border-radius": "12px",
            "padding": "8px 12px",
            "margin-left": "60px",
        },
        "assistant": {
            "bg": "#FFFFFF",
            "align": "left",
            "border": "1px solid #E0E0E0",
            "border-radius": "12px",
            "padding": "8px 12px",
            "margin-right": "60px",
        },
        "system": {
            "bg": "#F5F5F5",
            "align": "center",
            "font-size": "11px",
            "color": "#888888",
            "padding": "4px 8px",
            "border-radius": "4px",
        },
    }

    def __init__(self, text: str, role: str, parent=None):
        super().__init__(parent)
        self.setObjectName(f"Bubble_{role}")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        label = QLabel(text)
        label.setWordWrap(True)
        label.setMaximumWidth(320)
        label.setTextFormat(Qt.TextFormat.PlainText)

        style_def = self._STYLES.get(role, self._STYLES["assistant"])

        if role == "user":
            layout.addStretch()
            layout.addWidget(label)
            label.setStyleSheet(self._build_qss(style_def))
        elif role == "system":
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
            label.setStyleSheet(self._build_qss(style_def))
        else:
            layout.addWidget(label)
            layout.addStretch()
            label.setStyleSheet(self._build_qss(style_def))

    def _build_qss(self, s: dict) -> str:
        parts = []
        if "bg" in s:
            parts.append(f"background-color: {s['bg']};")
        if "color" in s:
            parts.append(f"color: {s['color']};")
        if "font-size" in s:
            parts.append(f"font-size: {s['font-size']};")
        if "border" in s:
            parts.append(f"border: {s['border']};")
        if "border-radius" in s:
            parts.append(f"border-radius: {s['border-radius']};")
        if "padding" in s:
            parts.append(f"padding: {s['padding']};")
        if "margin-left" in s:
            parts.append(f"margin-left: {s['margin-left']};")
        if "margin-right" in s:
            parts.append(f"margin-right: {s['margin-right']};")
        return " ".join(parts)
