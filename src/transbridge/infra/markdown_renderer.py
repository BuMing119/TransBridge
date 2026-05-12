"""Markdown → QWidget renderer. Zero PyQt-external dependencies, pure regex parser."""

from __future__ import annotations

import re
from typing import Sequence

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
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
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HR_RE = re.compile(r"^(?:---|\*\*\*|___)\s*$")
_TABLE_SEP_RE = re.compile(r"^\|?[\s:]*-{3,}[\s:]*(?:\|[\s:]*-{3,}[\s:]*)*\|?\s*$")
_ORDERED_LIST_RE = re.compile(r"^(\d+)\.\s+(.*)")


def _apply_inline(text: str) -> str:
    """Convert Markdown inline formatting to HTML (safe for QLabel rich text)."""
    # Escape HTML entities first
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Inline code (before other patterns so backticks inside bold aren't split)
    text = _INLINE_CODE_RE.sub(
        r'<code style="background-color:#f0f0f0; padding:1px 4px; '
        r'border-radius:3px; font-family:Consolas,monospace; font-size:12px;">\1</code>',
        text,
    )
    # Bold
    text = _BOLD_RE.sub(r"<b>\1\2</b>", text)
    # Italic (after bold to avoid `**` being partially matched)
    text = _ITALIC_RE.sub(r"<i>\1\2</i>", text)
    # Links
    text = _LINK_RE.sub(r'<a href="\2">\1</a>', text)

    return text


def _make_label(
    text: str,
    *,
    heading_level: int = 0,
    alignment: Qt.AlignmentFlag | None = None,
    word_wrap: bool = True,
    max_width: int | None = None,
) -> QLabel:
    """Create a QLabel with rich text and heading-appropriate styling."""
    html = _apply_inline(text)

    if heading_level == 1:
        html = f"<h1>{html}</h1>"
    elif heading_level == 2:
        html = f"<h2>{html}</h2>"
    elif heading_level == 3:
        html = f"<h3>{html}</h3>"
    elif heading_level == 4:
        html = f"<h4>{html}</h4>"
    elif heading_level == 5:
        html = f"<h5>{html}</h5>"
    elif heading_level == 6:
        html = f"<h6>{html}</h6>"

    label = QLabel(html)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setWordWrap(word_wrap)
    label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    label.setOpenExternalLinks(False)
    label.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))

    if max_width:
        label.setMaximumWidth(max_width)
    if alignment:
        label.setAlignment(alignment)

    return label


def _make_code_block(lines: Sequence[str], language: str = "") -> QTextEdit:
    """Create a read-only QTextEdit with dark background for code."""
    widget = QTextEdit()
    widget.setReadOnly(True)
    widget.setPlainText("\n".join(lines))
    widget.setFont(QFont("Consolas", 11))
    widget.setStyleSheet(
        "QTextEdit {"
        "  background-color: #1E1E1E;"
        "  color: #D4D4D4;"
        "  border: 1px solid #333;"
        "  border-radius: 6px;"
        "  padding: 8px;"
        "}"
    )
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    # Auto-height: fit to content
    widget.document().setDocumentMargin(4)
    doc_height = int(widget.document().size().height()) + 12
    widget.setMinimumHeight(min(doc_height, 400))
    widget.setMaximumHeight(500)
    return widget


def _make_hr() -> QFrame:
    """Create a horizontal rule."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("QFrame { color: #ddd; margin: 8px 0; }")
    return line


def _make_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> QTableWidget:
    """Create a read-only QTableWidget for Markdown tables."""
    ncols = len(headers)
    nrows = len(rows)
    table = QTableWidget(nrows + 1, ncols)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

    for j, h in enumerate(headers):
        item = QTableWidgetItem(_apply_inline(h.strip()))
        item.setFont(QFont("", -1, QFont.Weight.Bold))
        table.setHorizontalHeaderItem(j, item)

    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            if j < ncols:
                table.setItem(i, j, QTableWidgetItem(_apply_inline(cell.strip())))

    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setVisible(False)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    row_height = table.rowHeight(0) if nrows > 0 else 30
    table.setMaximumHeight(row_height * (nrows + 2) + table.horizontalHeader().height())
    return table


def _make_list(items: Sequence[str], ordered: bool) -> QWidget:
    """Create a bulleted or numbered list widget."""
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(20, 0, 0, 0)
    layout.setSpacing(2)

    for i, item_text in enumerate(items):
        prefix = f"{i + 1}." if ordered else "•"
        label = _make_label(f"{prefix} {item_text}")
        layout.addWidget(label)

    return container


# ── Block types ────────────────────────────────────────────────

class _Block:
    """Base block. Subclasses render themselves to QWidget."""
    def render(self) -> QWidget:
        raise NotImplementedError


class _HeadingBlock(_Block):
    def __init__(self, level: int, text: str):
        self.level = level
        self.text = text

    def render(self) -> QWidget:
        return _make_label(self.text, heading_level=self.level)


class _ParagraphBlock(_Block):
    def __init__(self, text: str):
        self.text = text

    def render(self) -> QWidget:
        return _make_label(self.text)


class _CodeBlock(_Block):
    def __init__(self, lines: Sequence[str], language: str = ""):
        self.lines = list(lines)
        self.language = language

    def render(self) -> QWidget:
        return _make_code_block(self.lines, self.language)


class _ListBlock(_Block):
    def __init__(self, items: Sequence[str], ordered: bool = False):
        self.items = list(items)
        self.ordered = ordered

    def render(self) -> QWidget:
        return _make_list(self.items, self.ordered)


class _TableBlock(_Block):
    def __init__(self, headers: Sequence[str], rows: Sequence[Sequence[str]]):
        self.headers = list(headers)
        self.rows = [list(r) for r in rows]

    def render(self) -> QWidget:
        return _make_table(self.headers, self.rows)


class _HRBlock(_Block):
    def render(self) -> QWidget:
        return _make_hr()


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
                rows.append(
                    [c.strip() for c in lines[i].strip().strip("|").split("|")]
                )
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

class MarkdownRenderer:
    """Render Markdown text to a QWidget using only PyQt6 components."""

    def render(self, text: str) -> QWidget:
        """Parse *text* and return a QWidget with the rendered content."""
        try:
            blocks = _tokenize(text)
        except Exception:
            # Fallback: plain text label
            return self._fallback(text)

        if not blocks:
            label = QLabel("")
            label.setTextFormat(Qt.TextFormat.PlainText)
            container = QWidget()
            layout = QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(label)
            return container

        try:
            container = QWidget()
            layout = QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)

            for block in blocks:
                widget = block.render()
                if widget:
                    layout.addWidget(widget)

            layout.addStretch()
            return container
        except Exception:
            return self._fallback(text)

    def _fallback(self, text: str) -> QWidget:
        """Degrade to plain text when parsing/rendering fails."""
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label)
        return container
