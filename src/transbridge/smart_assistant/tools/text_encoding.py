# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Resolve encoding settings from an Assistant operation's project binding."""

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.contracts import RequestContext
from transbridge.config.text_encoding import TextEncodingDefaults, TextEncodings


def effective_text_encodings(app, request: RequestContext) -> TextEncodings:
    service = getattr(app, "project_text_encodings", None)
    path = service.active_project_path(request) if service is not None else None
    if path is None:
        if request.project_id is not None:
            if service is not None:
                raise RequestError("TEXT_ENCODING_READ_FAILED", "当前工程已变化，请重新发起操作。")
            return TextEncodings()
        return (getattr(app, "text_encoding_defaults", None) or TextEncodingDefaults()).load()
    result = service.read(path, request)
    if result.value is None:
        message = "；".join(item.message for item in result.diagnostics)
        raise RequestError("TEXT_ENCODING_READ_FAILED", message or "无法读取当前项目编码。")
    return result.value
