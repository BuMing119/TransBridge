# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Apply the Project reading settings to an operation-local source request."""

from dataclasses import replace

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.io import FormatId
from transbridge.application.io.text_source_options import snapshot_read_options
from transbridge.config.text_encoding import TextEncodings

from .provisioning_models import ProjectSourceRequest

TEXT_ENCODING_FORMATS = frozenset({FormatId.PLUGIN_SSE, FormatId.STRINGS, FormatId.DLSTRINGS, FormatId.ILSTRINGS})
TEXT_READ_OPTION_KEYS = ("input_encoding", "source_language", "text_read_policy")


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
