"""Bounded response projections that omit translation payloads and credentials."""

from collections.abc import Mapping
import json

from transbridge.application.security.redaction import SecretRedactor

_DIAGNOSTIC_FIELDS = ("status", "code", "message", "error", "detail", "reason", "success", "result")


def error_response_summary(exc, *, redact) -> dict:
    text = dict(getattr(exc, "safe_context", ())).get("response", "")
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        # Non-JSON/truncated bodies may include HTML or echoed translation data.
        return {"type": "unparsed", "length": len(text)}
    return response_summary(value, redact=redact)


def response_summary(value, *, redact, depth: int = 0) -> dict:
    summary = {"type": type(value).__name__}
    if isinstance(value, Mapping):
        summary["keys"] = [str(redact(str(key)))[:80] for key in list(value)[:24]]
        summary["field_count"] = len(value)
        for key in _DIAGNOSTIC_FIELDS:
            item = value.get(key)
            if isinstance(item, str):
                summary[key] = str(redact(item))[:512]
            elif item is None or isinstance(item, (bool, int, float)):
                if key in value:
                    summary[key] = item
            elif isinstance(item, Mapping) and depth < 1:
                summary[key] = response_summary(item, redact=redact, depth=depth + 1)
        for key in ("id", "fileId"):
            if type(value.get(key)) is int:
                summary[key] = value[key]
        file = value.get("file")
        if isinstance(file, Mapping):
            summary["file"] = {}
            for key in ("id", "name", "path", "folder"):
                if isinstance(file.get(key), str):
                    summary["file"][key] = str(redact(file[key]))[:512]
                elif type(file.get(key)) is int:
                    summary["file"][key] = file[key]
        for key in ("results", "strings", "files"):
            if isinstance(value.get(key), list):
                summary[f"{key}_count"] = len(value[key])
    elif isinstance(value, list):
        summary["count"] = len(value)
    return SecretRedactor.default().redact(summary)
