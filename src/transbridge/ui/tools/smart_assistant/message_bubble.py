from __future__ import annotations

# TODO: i18n — 用户可见字符串均为硬编码中文（头像字母 U/A 除外），待国际化改造
"""消息组件：文档流布局（FR7.16）。

统一左对齐排列，通过圆形文字头像（U/A）区分角色。
消息内容居中最大宽度 720px，用户消息淡灰背景，AI 消息无背景。

颜色面板 (无主题系统 — 所有值硬编码在各控件 StyleSheet 中):
  语义色:
    AI 头像背景:       #4CAF50  (绿色)
    用户头像背景:       #424242  (深灰)
    头像文字:           #FFFFFF  (白色)

  中性色:
    用户气泡背景:       #f7f7f7  (淡灰)
    消息文字:           #333     (深灰)
"""

import threading

from PyQt6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout
from PyQt6.QtCore import Qt

from transbridge.infra.markdown_renderer import MarkdownRenderer

_RENDERER: MarkdownRenderer | None = None
_renderer_lock = threading.Lock()


def _get_renderer() -> MarkdownRenderer:
    """QA-003: 双重检查锁定保证 Singleton 线程安全。"""
    global _RENDERER
    if _RENDERER is None:
        with _renderer_lock:
            if _RENDERER is None:
                _RENDERER = MarkdownRenderer()
    return _RENDERER


class AvatarLabel(QLabel):
    """圆形文字头像：24x24px，单字母居中。"""

    _AVATAR_SIZE = 24

    _ROLE_STYLES = {
        "user": (
            "QLabel {"
            "  background-color: #424242; color: #FFFFFF;"
            "  border-radius: 12px; font-size: 11px; font-weight: bold;"
            "  min-width: 24px; min-height: 24px; max-width: 24px; max-height: 24px;"
            "}"
        ),
        "assistant": (
            "QLabel {"
            "  background-color: #4CAF50; color: #FFFFFF;"
            "  border-radius: 12px; font-size: 11px; font-weight: bold;"
            "  min-width: 24px; min-height: 24px; max-width: 24px; max-height: 24px;"
            "}"
        ),
    }

    _ROLE_LETTERS = {"user": "U", "assistant": "A"}

    def __init__(self, role: str, parent=None):
        super().__init__(parent)
        letter = self._ROLE_LETTERS.get(role, "?")
        style = self._ROLE_STYLES.get(role, self._ROLE_STYLES["assistant"])
        self.setText(letter)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(style)


class MessageBubble(QWidget):
    """文档流消息组件。

    布局：[头像 24px] [间距 10px] [内容区 撑满面板 左对齐]
    用户消息内容区淡灰背景（#f7f7f7），AI 消息无背景。
    """


    _PLAINTEXT_FALLBACK_LENGTH = 10000

    def __init__(self, text: str, role: str, parent=None):
        super().__init__(parent)
        self.setObjectName(f"Message_{role}")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 8)
        outer.setSpacing(10)

        # 头像
        avatar = AvatarLabel(role)
        outer.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)

        # 内容容器
        content_wrapper = QWidget()
        wrapper_layout = QVBoxLayout(content_wrapper)
        wrapper_layout.setContentsMargins(0, 2, 0, 2)
        wrapper_layout.setSpacing(0)

        if role == "user":
            content_wrapper.setStyleSheet(
                "QWidget {"
                "  background-color: #f7f7f7;"
                "  border-radius: 8px;"
                "  padding: 8px 12px;"
                "}"
            )

        content = self._render_content(text)
        wrapper_layout.addWidget(content)

        outer.addWidget(content_wrapper, stretch=1)

        self._role = role
        self._content_wrapper = content_wrapper
        self._content = content

    def _render_content(self, text: str):
        """渲染消息内容。超长文本使用纯文本 QLabel 避免 Markdown 渲染卡顿。"""
        if len(text) > self._PLAINTEXT_FALLBACK_LENGTH:
            label = QLabel(text)
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setStyleSheet("font-size: 12px; color: #333;")
            return label
        return _get_renderer().render(text)

    def set_text(self, text: str) -> None:
        """就地更新文本内容，复用外壳避免 QWidget 重建。"""
        wrapper_layout = self._content_wrapper.layout()
        if wrapper_layout is None:
            return
        if self._content:
            wrapper_layout.removeWidget(self._content)
            self._content.deleteLater()
        self._content = self._render_content(text)
        wrapper_layout.addWidget(self._content)
