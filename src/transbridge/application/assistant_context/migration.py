"""Import old excerpts as historical material, never as coverage or current authority."""

from transbridge.application.assistant_requests.models import digest
from transbridge.application.assistant_requests.summaries import RequestSummary

from .models import CompactionSummary, PreparationWait
from .projection import authorized_records


def import_legacy_summary(raw, request, history, owners):
    try:
        summary = RequestSummary.from_dict(raw)
    except (KeyError, ValueError, TypeError) as exc:
        raise PreparationWait("CONTEXT_LEGACY_INVALID", "旧摘要格式无法验证，原记录已保留。") from exc
    allowed = {r["message_id"] for r in authorized_records(history, request, owners) if r["role"] != "system"}
    if (
        summary.session_id != request.session_id
        or summary.request_id != request.request_id
        or summary.request_revision > request.revision
        or not set(summary.source_ids) <= allowed
    ):
        raise PreparationWait("CONTEXT_LEGACY_INVALID", "旧摘要来源或访问范围无法验证，原记录已保留。")
    # A historical excerpt is expected to predate new turns/revisions. Re-running the old
    # rolling algorithm against today's request would incorrectly reject every stale excerpt.
    return CompactionSummary("legacy-" + digest(raw), summary.text, (), summary.source_ids, "legacy-excerpt")
