"""Bound Qt text layout while retaining complete data in the original source."""

from __future__ import annotations

MAX_DISPLAY_CHARS = 64 * 1024
MAX_DISPLAY_LINES = 1000
MAX_DISPLAY_LINE_CHARS = 2048


def bounded_display_text(text: str, *, full_text_hint: str, tail: bool = False) -> str:
    """Prepare visible text in a reader, including an explicit truncation notice.

    A byte limit alone cannot bound QTextLayout: a huge unbroken paragraph can
    stall word wrapping for seconds. Cap the document and each logical line.
    Normal-sized text is returned byte-for-byte, including its final newline.
    """
    lines = text.split("\n")
    if (
        len(text) <= MAX_DISPLAY_CHARS
        and len(lines) <= MAX_DISPLAY_LINES
        and all(len(line) <= MAX_DISPLAY_LINE_CHARS for line in lines)
    ):
        return text
    notice = f"[显示内容已截断，以保持窗口响应；{full_text_hint}]\n\n"
    remaining = MAX_DISPLAY_CHARS - len(notice)
    selected = lines[-MAX_DISPLAY_LINES:] if tail else lines[:MAX_DISPLAY_LINES]
    visible = []
    for line in reversed(selected) if tail else selected:
        if len(line) > MAX_DISPLAY_LINE_CHARS:
            marker = "…[本行已截断]…"
            width = MAX_DISPLAY_LINE_CHARS - len(marker)
            line = marker + line[-width:] if tail else line[:width] + marker
        needed = len(line) + bool(visible)
        if needed > remaining:
            width = remaining - bool(visible)
            if width > 0:
                visible.append(line[-width:] if tail else line[:width])
            break
        visible.append(line)
        remaining -= needed
    if tail:
        visible.reverse()
    return notice + "\n".join(visible)
