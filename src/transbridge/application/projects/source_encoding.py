# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Apply the Project reading settings to an operation-local source request."""

from dataclasses import replace

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.io import FormatId
from transbridge.application.io.text_source_options import snapshot_read_options
from transbridge.config.text_encoding import TextEncodingDefaults, TextEncodings

from .provisioning_models import ProjectSourceRequest

TEXT_ENCODING_FORMATS = frozenset({FormatId.PLUGIN_SSE, FormatId.STRINGS, FormatId.DLSTRINGS, FormatId.ILSTRINGS})
TEXT_READ_OPTION_KEYS = ("input_encoding", "source_language", "text_read_policy")


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


def with_source_read_options(request: ProjectSourceRequest, encodings: TextEncodings) -> ProjectSourceRequest:
    options = {**dict(request.options), **encodings.read_options()}
    return replace(request, options=tuple(options.items()))


def validate_prepared_input_encoding(prepared, project_data) -> None:
    """Reject stale or externally parsed text that bypassed the Project codec."""
    hydration = prepared.hydration
    if hydration is None or hydration.format_id not in TEXT_ENCODING_FORMATS:
        return
    expected = TextEncodings.from_project_data(project_data).read_options()
    snapshot = hydration.source_snapshot
    actual = snapshot_read_options(snapshot)
    options = dict(prepared.descriptor).get("format_options") or {}
    if actual != expected or any(options.get(key, value) != value for key, value in expected.items()):
        raise DomainError(
            ErrorCategory.CONFLICT,
            "PROJECT_SOURCE_ENCODING_CHANGED",
            "来源的读取语言、编码或规则与当前项目设置不一致。请按当前项目读取设置重新准备后再添加。",
        )
