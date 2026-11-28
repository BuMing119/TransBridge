# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Resolve the project's effective encoding for new GUI operations."""

from transbridge.application.projects.source_encoding import (
    TEXT_ENCODING_FORMATS as TEXT_FORMATS,
    context_text_encodings,
    effective_text_encodings,
    request_for_project,
)
from transbridge.config.text_encoding import TEXT_ENCODINGS, TEXT_LANGUAGES, TextEncodings

__all__ = [
    "context_text_encodings",
    "effective_text_encodings",
    "request_for_project",
    "text_encoding_label",
    "text_encoding_summary",
    "export_encoding_summary",
]

_LABELS = dict(TEXT_ENCODINGS)


def text_encoding_label(codec):
    return _LABELS[codec]


def text_encoding_summary(encodings):
    input_label = text_encoding_label(encodings.input_encoding)
    output_label = text_encoding_label(encodings.output_encoding)
    languages = dict(TEXT_LANGUAGES)
    return (
        f"插件及 Strings：{languages[encodings.source_language]} / 输入编码 {input_label} → "
        f"{languages[encodings.target_language]} / 输出编码 {output_label}"
    )


def export_encoding_summary(draft, *, native=False):
    if (draft.source_snapshot.format_id if native else draft.format_id) not in TEXT_FORMATS:
        return ""
    options = dict(draft.options)
    summary = text_encoding_summary(
        TextEncodings(
            options.get("input_encoding", "utf-8"),
            options.get("output_encoding", "utf-8"),
            options.get("source_language", "english"),
            options.get("target_language", "chinese"),
        )
    )
    return f"项目设置：{summary}。已有来源按保存的读取记录核对。"
