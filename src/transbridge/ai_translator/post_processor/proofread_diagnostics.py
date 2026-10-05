"""Compact run logs while retaining entry-specific proofreading diagnostics."""

from collections import defaultdict
from collections.abc import Iterable

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity
from transbridge.application.io import EntryKey


def entry_diagnostics(diagnostics: Iterable[Diagnostic]) -> dict[EntryKey, list[Diagnostic]]:
    """Index only explicitly owned diagnostics; global messages stay at run level."""
    indexed: dict[EntryKey, list[Diagnostic]] = defaultdict(list)
    for diagnostic in diagnostics:
        details = dict(diagnostic.details)
        keys = details.get("entry_keys", ())
        if isinstance(details.get("entry_key"), dict):
            keys = (details["entry_key"],)
        if not isinstance(keys, (list, tuple)):
            continue
        for data in keys:
            try:
                key = EntryKey.from_dict(data)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue  # Retained unchanged in the run's structured diagnostics.
            indexed[key].append(diagnostic)
    return dict(indexed)


def diagnostic_note(diagnostics: Iterable[Diagnostic]) -> str:
    notes = []
    for diagnostic in diagnostics:
        if diagnostic.severity is DiagnosticSeverity.INFO:
            continue
        details = dict(diagnostic.details)
        reason = details.get("reason")
        message = diagnostic.message
        if diagnostic.code == "PROOFREAD_RESPONSE_SCHEMA_INVALID":
            message = _schema_note(details.get("validation_details", {})) or message
        note = f"[{diagnostic.code}] {message}" + (f" ({reason})" if reason else "")
        if note not in notes:
            notes.append(note)
    return "；".join(notes)


def _schema_note(details: dict) -> str:
    messages = []
    for error in details.get("errors", (details,)):
        missing = error.get("missing_fields", ())
        unexpected = error.get("unexpected_fields", ())
        if "final_translation" in missing:
            messages.append("未返回译文")
        other_missing = [field for field in missing if field != "final_translation"]
        if other_missing:
            messages.append(f"缺少字段：{'、'.join(other_missing)}")
        if error.get("validator") == "type":
            messages.append("返回字段类型不正确")
        if unexpected:
            messages.append(f"多返回字段：{'、'.join(unexpected)}")
    return "；".join(dict.fromkeys(messages))


_SUMMARIES = {
    "PROOFREAD_RESPONSE_SCHEMA_INVALID": "模型响应格式不符合要求，相关条目保留原译文",
    "PROOFREAD_SYNTAX_REVIEW_REQUIRED": "标记存在差异，已保留原译文并标为有疑问",
    "PROOFREAD_CONTENT_TOKEN_LIMIT": "内容超过单请求 Token 上限，相关条目保留原译文",
    "PROOFREAD_RECOVERY_SUCCEEDED": "先前失败的条目已自动恢复",
    "PROOFREAD_TERMINOLOGY_REFINEMENT_FAILED": "术语修复未通过检查，相关条目保留原译文",
    "PROOFREAD_REFINEMENT_CANCELLED": "术语修复已取消，结果未提交",
    "PROOFREAD_PROTECTED_SYNTAX_MISMATCH": "校对结果改变了占位符或程序标记，相关条目保留原译文",
}


def diagnostic_logs(diagnostics: Iterable[Diagnostic]) -> Iterable[str]:
    groups: dict[str, list[Diagnostic]] = defaultdict(list)
    for diagnostic in diagnostics:
        groups[diagnostic.code].append(diagnostic)
    for code, items in groups.items():
        summary = _SUMMARIES.get(code, items[0].message)
        examples = []
        for diagnostic in items[:3]:
            details = dict(diagnostic.details)
            key = details.get("entry_key")
            reason = details.get("reason", "")
            if isinstance(key, dict):
                examples.append(f"{key.get('local_key', '')}" + (f" ({reason})" if reason else ""))
        suffix = f"；示例：{'；'.join(examples)}" if examples else ""
        yield f"[{code}] {summary}（{len(items)} 条记录）{suffix}"
