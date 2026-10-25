"""Build reusable report search and diagnostic indexes away from the GUI."""

from __future__ import annotations

from dataclasses import dataclass
import json

from transbridge.application.translation.review_report import review_issue_text, review_label

from .bounded_read_text import bounded_display_text

RESULT_LABELS = {
    "applied": "已应用",
    "accepted": "已接受",
    "rejected": "未采纳",
    "failed": "失败",
    "pending": "待确认",
    "not_applied": "未应用",
    "not_started": "未处理",
    "cancelled": "已取消",
}


def result_label(entry):
    details = entry.get("report_details", {})
    return RESULT_LABELS.get(details.get("result_status"), "通过" if entry.get("accepted") else "未采用")


def entry_key_token(key):
    return json.dumps(key, sort_keys=True, ensure_ascii=False)


@dataclass(frozen=True)
class ResultIndex:
    entries: tuple
    search_texts: tuple[str, ...]
    statuses: tuple[str, ...]
    diagnostics: str
    details: dict[int, str]
    review_statuses: tuple[str, ...]


@dataclass(frozen=True)
class ResultProjection:
    index: ResultIndex
    visible: tuple


def build_result_index(source):
    snapshot = source.get("snapshot") or {}
    entries = tuple(snapshot.get("entries", ()))
    notes = [source.get("error", "")]
    entry_notes = {}
    for diagnostic in snapshot.get("diagnostics", ()):
        message = f"{diagnostic['code']}：{diagnostic['message']}"
        notes.append(message)
        details = diagnostic.get("details", {})
        keys = (details.get("entry_key"), *details.get("entry_keys", ()))
        for key in keys:
            if key is not None:
                entry_notes.setdefault(entry_key_token(key), []).append(message)
    details = {}
    for entry in entries:
        report = entry.get("report_details", {})
        reasons = [report.get("note", ""), report.get("review_reason", "")]
        reasons.extend(entry_notes.get(entry_key_token(entry.get("entry_key")), ()))
        reason = "\n".join(dict.fromkeys(note for note in reasons if note))
        full_detail = (
            f"原文\n{entry.get('original', '')}\n\n原译文\n{entry.get('before', '')}"
            f"\n\n候选译文\n{entry.get('candidate', '')}\n\n{result_label(entry)}\n{reason}"
        )
        if review_label(report):
            full_detail += (
                f"\n\n校对前译文\n{report.get('review_before_text', entry.get('before', ''))}"
                f"\n\nAI 校对：{review_label(report)}\n检测疑点（参考线索）\n{review_issue_text(report)}"
            )
        details[id(entry)] = bounded_display_text(
            full_detail, full_text_hint="完整条目和原因保留在任务记录和导出报告中。"
        )
    return ResultIndex(
        entries,
        tuple(
            " ".join(
                str(entry.get(key, "")) for key in ("entry_key", "original", "before", "candidate", "report_details")
            ).casefold()
            for entry in entries
        ),
        tuple(result_label(entry) for entry in entries),
        bounded_display_text(
            "\n".join(dict.fromkeys(note for note in notes if note)),
            full_text_hint="完整诊断保留在任务记录和导出报告中。",
        ),
        details,
        tuple(review_label(entry.get("report_details", {})) for entry in entries),
    )


class ResultProjector:
    """A serial reader owns its indexes; callers provide read-only report data."""

    def __init__(self):
        self._indexes = {}

    def __call__(self, request):
        source, query, status = request
        identity = id(source)
        cached = self._indexes.get(identity)
        if cached is None or cached[0] is not source:
            cached = (source, build_result_index(source))
            self._indexes[identity] = cached
        index = cached[1]
        visible = tuple(
            entry
            for entry, search, label, review in zip(
                index.entries, index.search_texts, index.statuses, index.review_statuses, strict=True
            )
            if (status == "全部" or label == status or review == status) and (not query or query in search)
        )
        return ResultProjection(index, visible)
