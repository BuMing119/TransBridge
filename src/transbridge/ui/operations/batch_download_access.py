"""Reopen and reverse durable download results without remote requests."""

from hashlib import sha256

from transbridge.application.sync import ConflictPolicy
from transbridge.application.sync.batch_download import plan_batch_download
from transbridge.application.sync.download_review import DownloadDecision
from transbridge.application.translation.result_merge import ResultConflictPolicy
from transbridge.paratranz.batch_download_snapshot import BatchDownloadRead, BatchDownloadScope

from .sync_review_view import SyncReviewRow


def export_result(session, path):
    if session.record is None:
        raise ValueError("尚无可另存的下载成果。")
    session.result_store.export(
        session.record,
        path,
        source_overrides=tuple(
            (scope, read, session._bases[scope.source_id]) for scope, read in session._unsaved_reads.items()
        ),
    )


def restore_result(session, result_id, progress, cancel):
    record = next((row for row in session.saved_results() if row["result_id"] == result_id), None)
    if record is None:
        raise ValueError("找不到当前目标对应的下载成果。")
    session.record = record
    session.merge_policy = ResultConflictPolicy(record["policy"])
    session.default_policy = session.merge_policy.value
    session.decisions = tuple(tuple(row) for row in record.get("decisions", ()))
    session._reads, session._bases, session._scopes, session.review_states = {}, {}, {}, {}
    session._unsaved_reads = {}
    session._partial_reads = {}
    sources = []
    for scope, read, source in session.result_store.sources(
        record, scope_factory=BatchDownloadScope, read_factory=BatchDownloadRead
    ):
        session._bases[scope.source_id] = source
        session._scopes[scope.source_id] = scope
        if read.error:
            session._partial_reads[scope] = read
        elif read.paths:
            session._reads[scope] = read
            sources.append(source)
    session.source_ids = tuple(session._bases)
    session.plan = plan_batch_download(
        tuple(sources),
        policy=ConflictPolicy.PREFER_REMOTE,
        decisions=tuple(DownloadDecision(*row) for row in session.decisions if not row[0].startswith("merge:")),
    )
    session.snapshot = None
    session._retryable = bool(session._partial_reads)
    session.details = (
        "已重开独立保存的下载成果；合入时会逐条检查当前内容。",
        *(
            f"{scope.source_id}：已保留 {len(read.paths)} 个文件、{len(read.entries)} 条译文；{read.error}"
            for scope, read in session._partial_reads.items()
        ),
    )
    return session._preview()


def partial_review_rows(reads):
    return tuple(
        SyncReviewRow(
            "partial:" + sha256((scope.source_id + entry.entry_key.serialize() + str(index)).encode()).hexdigest(),
            entry.entry_key.local_key,
            entry.original,
            entry.translation,
            entry.context,
            "来源未完整读取，已下载内容可查看或另存，暂不合入。" + read.error,
            can_select_remote=False,
            selection_disabled_reason="请重试完成该来源后再合入。",
            stage=entry.stage,
        )
        for scope, read in reads.items()
        for index, entry in enumerate(read.entries)
    )


def restore_previous(session, progress, cancel):
    if session.record is None:
        raise ValueError("请先重开要恢复的下载成果。")
    session._override_changes = session.result_store.reverse_changes(session.record)
    previous_policy = session.merge_policy
    session.merge_policy = ResultConflictPolicy.DEFER
    try:
        return session.apply(progress, cancel)
    finally:
        session._override_changes = None
        session.merge_policy = previous_policy
