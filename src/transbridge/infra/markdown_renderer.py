"""Markdown → QWidget renderer. Zero PyQt-external dependencies, pure regex parser."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import html
import re
from urllib.parse import urlparse

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHeaderView,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# ── Inline patterns (ordered: code first to protect backticks inside other patterns) ──
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(?=\S)(.+?)(?<=\S)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(?=\S)(.+?)(?<=\S)(?<!_)_(?!_)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HR_RE = re.compile(r"^(?:---|\*\*\*|___)\s*$")
_TABLE_SEP_RE = re.compile(r"^\|?[\s:]*-{3,20}[\s:]*(?:\|[\s:]*-{3,20}[\s:]*)*\|?\s*$")
_ORDERED_LIST_RE = re.compile(r"^(\d+)\.\s+(.*)")
_DEFAULT_INLINE_CODE_STYLE = "padding:1px 4px; border-radius:3px; font-family:Consolas,monospace; font-size:12px;"
_DEFAULT_CODE_BLOCK_STYLESHEET = "QTextEdit { border-radius: 6px; padding: 8px; }"
_DEFAULT_HORIZONTAL_RULE_STYLESHEET = "QFrame { margin: 8px 0; }"


@dataclass(frozen=True, slots=True)
class MarkdownRenderTheme:
    """Immutable CSS projection supplied by the UI layer, never ThemeService."""

    fingerprint: str
    stylesheet: str = ""
    inline_code_style: str = _DEFAULT_INLINE_CODE_STYLE
    code_block_stylesheet: str = _DEFAULT_CODE_BLOCK_STYLESHEET
    horizontal_rule_stylesheet: str = _DEFAULT_HORIZONTAL_RULE_STYLESHEET
    link_color: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.fingerprint, str) or not self.fingerprint.strip():
            raise ValueError("Markdown render theme requires a fingerprint")


def _apply_inline(text: str, theme: MarkdownRenderTheme | None = None) -> str:
    """Convert Markdown inline formatting to HTML (safe for QLabel rich text)."""
    # Escape HTML entities first
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Inline code (before other patterns so backticks inside bold aren't split)
    inline_style = _DEFAULT_INLINE_CODE_STYLE if theme is None else theme.inline_code_style
    text = _INLINE_CODE_RE.sub(
        rf'<code style="{inline_style}">\1</code>',
        text,
    )
    # Bold
    text = _BOLD_RE.sub(r"<b>\1\2</b>", text)
    # Italic (after bold to avoid `**` being partially matched)
    text = _ITALIC_RE.sub(r"<i>\1\2</i>", text)
    # Links — M9: 校验 URL 协议，仅允许 http/https 和内部锚点
    text = _LINK_RE.sub(_sanitize_link_url, text)
    if theme is not None and theme.link_color:
        text = text.replace('<a href="', f'<a style="color:{theme.link_color};" href="')

    return text


def _sanitize_link_url(match: re.Match) -> str:
    """M9: 校验 Markdown 链接 URL 协议。仅允许 http:/https:/#，其余替换为 about:blank。"""
    url = match.group(2)
    # M42: 去除所有空白符后再做协议检查，防止 "javascript :alert(1)" 绕过黑名单
    url_normalized = re.sub(r"\s+", "", url.strip())
    url_lower = url_normalized.lower()
    # 允许安全协议和内部锚点
    if url_lower.startswith(("http:", "https:", "#")):
        pass
    # 拦截危险协议（含 :// 的显式协议或已知危险前缀）
    elif "://" in url_lower or url_lower.startswith(("javascript:", "data:", "vbscript:", "file:")):
        url_normalized = "about:blank"
    # 无协议相对路径放行
    return f'<a href="{html.escape(url_normalized, quote=True)}">{match.group(1)}</a>'


def _safe_open_url(url: str) -> None:
    """Open external URLs with user confirmation to prevent accidental navigation."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return
    reply = QMessageBox.question(
        None,
        "打开外部链接",
        f"即将在系统浏览器中打开：\n\n{url}\n\n是否继续？",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if reply == QMessageBox.StandardButton.Yes:
        QDesktopServices.openUrl(QUrl(url))


def _make_label(
    text: str,
    *,
    heading_level: int = 0,
    alignment: Qt.AlignmentFlag | None = None,
    word_wrap: bool = True,
    max_width: int | None = None,
    theme: MarkdownRenderTheme | None = None,
) -> QLabel:
    """Create a QLabel with rich text and heading-appropriate styling."""
    rich_text = _apply_inline(text, theme)

    if heading_level == 1:
        rich_text = f"<h1>{rich_text}</h1>"
    elif heading_level == 2:
        rich_text = f"<h2>{rich_text}</h2>"
    elif heading_level == 3:
        rich_text = f"<h3>{rich_text}</h3>"
    elif heading_level == 4:
        rich_text = f"<h4>{rich_text}</h4>"
    elif heading_level == 5:
        rich_text = f"<h5>{rich_text}</h5>"
    elif heading_level == 6:
        rich_text = f"<h6>{rich_text}</h6>"

    if theme is not None and theme.stylesheet:
        inline_theme = html.escape(theme.stylesheet, quote=True)
        rich_text = f'<div style="{inline_theme}">{rich_text}</div>'

    # Rich-text QLabel resolves document colours when setText() runs. Apply the
    # injected theme first so it cannot capture a stale application palette.
    label = QLabel()
    _apply_theme(label, theme)
    label.setText(rich_text)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setWordWrap(word_wrap)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
    label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    label.setOpenExternalLinks(False)
    label.linkActivated.connect(_safe_open_url)

    if max_width:
        label.setMaximumWidth(max_width)
    if alignment:
        label.setAlignment(alignment)

    return label


def _make_code_block(lines: Sequence[str], language: str = "", theme: MarkdownRenderTheme | None = None) -> QTextEdit:
    """Create a read-only QTextEdit using injected or palette-default styling."""
    widget = QTextEdit()
    widget.setReadOnly(True)
    widget.setPlainText("\n".join(lines))
    widget.setFont(QFont("Consolas", 11))
    widget.setStyleSheet(_DEFAULT_CODE_BLOCK_STYLESHEET if theme is None else theme.code_block_stylesheet)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    # Auto-height: fit to content
    widget.document().setDocumentMargin(4)
    doc_height = int(widget.document().size().height()) + 12
    # M43: doc_height may be 0 before layout completes — set a 20px floor
    widget.setMinimumHeight(max(min(doc_height, 400), 20))
    widget.setMaximumHeight(500)
    return widget


def _make_hr(theme: MarkdownRenderTheme | None = None) -> QFrame:
    """Create a horizontal rule."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(_DEFAULT_HORIZONTAL_RULE_STYLESHEET if theme is None else theme.horizontal_rule_stylesheet)
    return line


def _make_table(
    headers: Sequence[str], rows: Sequence[Sequence[str]], theme: MarkdownRenderTheme | None = None
) -> QTableWidget:
    """Create a read-only QTableWidget for Markdown tables."""
    ncols = len(headers)
    nrows = len(rows)
    table = QTableWidget(nrows + 1, ncols)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

    for j, h in enumerate(headers):
        item = QTableWidgetItem(_apply_inline(h.strip(), theme))
        item.setFont(QFont("", -1, QFont.Weight.Bold))
        table.setHorizontalHeaderItem(j, item)

    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            if j < ncols:
                table.setItem(i, j, QTableWidgetItem(_apply_inline(cell.strip(), theme)))

    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setVisible(False)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    row_height = table.rowHeight(0) if nrows > 0 else 30
    table.setMaximumHeight(row_height * (nrows + 2) + table.horizontalHeader().height())
    return table


def _make_list(items: Sequence[str], ordered: bool, theme: MarkdownRenderTheme | None = None) -> QWidget:
    """Create a bulleted or numbered list widget."""
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(20, 0, 0, 0)
    layout.setSpacing(2)

    for i, item_text in enumerate(items):
        prefix = f"{i + 1}." if ordered else "•"
        label = _make_label(f"{prefix} {item_text}", theme=theme)
        layout.addWidget(label)

    return container


# ── Block types ────────────────────────────────────────────────


class _Block:
    """Base block. Subclasses render themselves to QWidget."""

    def render(self, theme: MarkdownRenderTheme | None = None) -> QWidget:
        raise NotImplementedError


class _HeadingBlock(_Block):
    def __init__(self, level: int, text: str):
        self.level = level
        self.text = text

    def render(self, theme: MarkdownRenderTheme | None = None) -> QWidget:
        return _make_label(self.text, heading_level=self.level, theme=theme)


class _ParagraphBlock(_Block):
    def __init__(self, text: str):
        self.text = text

    def render(self, theme: MarkdownRenderTheme | None = None) -> QWidget:
        return _make_label(self.text, theme=theme)


class _CodeBlock(_Block):
    def __init__(self, lines: Sequence[str], language: str = ""):
        self.lines = list(lines)
        self.language = language

    def render(self, theme: MarkdownRenderTheme | None = None) -> QWidget:
        return _make_code_block(self.lines, self.language, theme)


class _ListBlock(_Block):
    def __init__(self, items: Sequence[str], ordered: bool = False):
        self.items = list(items)
        self.ordered = ordered

    def render(self, theme: MarkdownRenderTheme | None = None) -> QWidget:
        return _make_list(self.items, self.ordered, theme)


class _TableBlock(_Block):
    def __init__(self, headers: Sequence[str], rows: Sequence[Sequence[str]]):
        self.headers = list(headers)
        self.rows = [list(r) for r in rows]

    def render(self, theme: MarkdownRenderTheme | None = None) -> QWidget:
        return _make_table(self.headers, self.rows, theme)


class _HRBlock(_Block):
    def render(self, theme: MarkdownRenderTheme | None = None) -> QWidget:
        return _make_hr(theme)


# ── Tokenizer ───────────────────────────────────────────────────


def _tokenize(text: str) -> list[_Block]:
    """Convert raw Markdown text into a list of renderable blocks."""
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[_Block] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # Blank line
        if not line.strip():
            i += 1
            continue

        # Code block fence
        if line.strip().startswith("```"):
            lang = line.strip()[3:].strip()
            code_lines: list[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing ```
            blocks.append(_CodeBlock(code_lines, lang))
            continue

        # Horizontal rule
        if _HR_RE.match(line.strip()):
            blocks.append(_HRBlock())
            i += 1
            continue

        # Heading
        heading_match = re.match(r"^(#{1,6})\s+(.*)", line)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2)
            blocks.append(_HeadingBlock(level, text))
            i += 1
            continue

        # Table detection: look ahead for header separator
        if "|" in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1].strip()):
            headers = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2  # skip header and separator
            rows: list[list[str]] = []
            while i < n and "|" in lines[i]:
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            # Pad row cells to match header count
            for row in rows:
                while len(row) < len(headers):
                    row.append("")
            blocks.append(_TableBlock(headers, rows))
            continue

        # Unordered list
        ul_match = re.match(r"^(\s*)[-*+]\s+(.*)", line)
        if ul_match and not _ORDERED_LIST_RE.match(line):
            items: list[str] = []
            while i < n:
                m = re.match(r"^(\s*)[-*+]\s+(.*)", lines[i])
                if not m:
                    break
                items.append(m.group(2))
                i += 1
            blocks.append(_ListBlock(items, ordered=False))
            continue

        # Ordered list
        ol_match = _ORDERED_LIST_RE.match(line)
        if ol_match:
            items = []
            expected = 1
            while i < n:
                m = _ORDERED_LIST_RE.match(lines[i])
                if not m:
                    break
                items.append(m.group(2))
                expected += 1
                i += 1
            blocks.append(_ListBlock(items, ordered=True))
            continue

        # Paragraph: collect consecutive non-empty, non-special lines
        para_lines = [line]
        i += 1
        while i < n and lines[i].strip() and not _is_special_line(lines[i]):
            para_lines.append(lines[i])
            i += 1
        blocks.append(_ParagraphBlock(" ".join(para_lines)))

    return blocks


def _is_special_line(line: str) -> bool:
    """Check if a line starts a special block (heading, list, code, table, hr)."""
    s = line.strip()
    if not s:
        return True
    if s.startswith("```"):
        return True
    if _HR_RE.match(s):
        return True
    if re.match(r"^(#{1,6})\s+", s):
        return True
    if re.match(r"^(\s*)[-*+]\s+", s):
        return True
    if _ORDERED_LIST_RE.match(s):
        return True
    if "|" in s and _TABLE_SEP_RE.match(s):
        return True
    return False


# ── Renderer ────────────────────────────────────────────────────

# C30: Input size limit to avoid O(n) full-text regex scanning + massive
# QWidget creation (QLabel/QTextEdit/QTableWidget) for very large responses.
# Above this threshold, fall back to a plain-text QLabel instantly.
_MAX_INPUT_LENGTH = 50000
# M20: Even within the char limit, many short blocks can still create excessive
# QWidgets. Guard with a maximum block count, degrading to plain text above it.
_MAX_BLOCK_COUNT = 300


class MarkdownRenderer:
    """Render Markdown text to a QWidget using only PyQt6 components."""

    def __init__(self, theme: MarkdownRenderTheme | None = None) -> None:
        self._theme = _validate_theme(theme)

    def render(self, text: str, *, theme: MarkdownRenderTheme | None = None) -> QWidget:
        """Parse *text* and return a QWidget with the rendered content."""
        effective_theme = self._theme if theme is None else _validate_theme(theme)
        # C30: Skip expensive tokenization + widget creation for huge inputs
        if len(text) > _MAX_INPUT_LENGTH:
            return self._fallback(text, effective_theme)

        try:
            blocks = _tokenize(text)
        except Exception:
            # Fallback: plain text label
            return self._fallback(text, effective_theme)

        # M20: Guard against excessive QWidget creation from many small blocks
        if len(blocks) > _MAX_BLOCK_COUNT:
            return self._fallback(text, effective_theme)

        if not blocks:
            label = QLabel("")
            label.setTextFormat(Qt.TextFormat.PlainText)
            container = QWidget()
            container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            layout = QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(label)
            _apply_theme(container, effective_theme)
            return container

        try:
            container = QWidget()
            container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            layout = QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)

            for block in blocks:
                widget = block.render(effective_theme)
                if widget:
                    layout.addWidget(widget)

            _apply_theme(container, effective_theme)
            return container
        except Exception:
            return self._fallback(text, effective_theme)

    def _fallback(self, text: str, theme: MarkdownRenderTheme | None = None) -> QWidget:
        """Degrade to plain text when parsing/rendering fails."""
        label = QLabel()
        _apply_theme(label, theme)
        label.setText(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label)
        _apply_theme(container, theme)
        return container


def _validate_theme(theme: MarkdownRenderTheme | None) -> MarkdownRenderTheme | None:
    if theme is not None and not isinstance(theme, MarkdownRenderTheme):
        raise TypeError("theme must be MarkdownRenderTheme or None")
    return theme


def _apply_theme(widget: QWidget, theme: MarkdownRenderTheme | None) -> None:
    if theme is not None and theme.stylesheet:
        widget.setStyleSheet(theme.stylesheet)
