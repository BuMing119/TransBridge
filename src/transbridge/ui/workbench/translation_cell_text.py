"""Full-text cell presentation, including non-destructive terminology previews."""

from html import escape

from transbridge.converter.translation_entry import TranslationEntry


def full_text_tooltip(text: str) -> str:
    """Keep translation markup literal while allowing long tooltips to wrap."""

    return f'<qt><div style="white-space: pre-wrap;">{escape(text)}</div></qt>' if text else ""


def translation_cell_text(
    entry: TranslationEntry,
    projected: object | None,
    *,
    profile_label: str | None,
    pending: bool = False,
    error: str = "",
) -> tuple[str, str]:
    text = (entry.translation if projected is None else str(getattr(projected, "translation", ""))) or ""
    tooltip = text
    if profile_label is not None:
        diagnostics = tuple(getattr(projected, "diagnostics", ()))
        status = (
            "正在生成方案预览，暂时显示项目译文"
            if pending
            else f"方案预览失败：{error}，已保留项目译文"
            if error
            else f"有 {len(diagnostics)} 项术语无法安全定位，已保留整条项目译文"
            if diagnostics
            else "已按方案调整"
            if bool(getattr(projected, "is_derived", False))
            else "此条无需调整"
        )
        note = f"{profile_label or '当前译名方案'} · {status}。如需编辑正文，请选择“使用项目默认译名”。"
        details = [str(item.message) for item in diagnostics[:3]]
        tooltip = "\n\n".join(part for part in (text, note, "\n".join(details)) if part)
    return text or "（无译文）", full_text_tooltip(tooltip)
