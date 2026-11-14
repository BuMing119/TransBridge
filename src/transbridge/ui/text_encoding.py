# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Resolve the project's effective encoding for new GUI operations."""

from dataclasses import replace

from transbridge.application.contracts import DomainError
from transbridge.application.projects.source_encoding import TEXT_ENCODING_FORMATS as TEXT_FORMATS
from transbridge.config.text_encoding import TEXT_ENCODINGS, TEXT_LANGUAGES, TextEncodingDefaults, TextEncodings

_LABELS = dict(TEXT_ENCODINGS)


def request_for_project(context, request):
    return replace(
        request,
        project_id=getattr(context, "active_project_id", request.project_id),
        variant_id=getattr(context, "active_variant_id", request.variant_id),
    )


def effective_text_encodings(request, service=None, defaults=None):
    if request is None or request.project_id is None:
        return (defaults or TextEncodingDefaults()).load()
    if service is None:
        return TextEncodings()
    try:
        encodings = service.active_encodings(request)
    except (DomainError, ValueError) as exc:
        raise RuntimeError(f"无法读取项目文本编码：{exc}") from exc
    if encodings is None:
        raise RuntimeError("当前项目已变化，请重新发起操作。")
    return encodings


def context_text_encodings(context, request=None):
    request = getattr(context, "runtime_context", None) if request is None else request
    return effective_text_encodings(
        None if request is None else request_for_project(context, request),
        getattr(context, "project_text_encodings", None),
        getattr(context, "text_encoding_defaults", None),
    )


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
