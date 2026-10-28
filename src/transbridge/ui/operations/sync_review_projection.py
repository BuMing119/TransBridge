"""Decode review intents and project synchronization decisions for the GUI."""

from __future__ import annotations

from .sync_review_view import SyncReviewCandidate, SyncReviewRow

_REASONS = {
    "local_content_changed": "对照后你修改了这条本地译文，请选择保留新编辑或采用本轮云端译文。",
    "duplicate_local_key": "本地有重复条目，无法确定应该更新哪一条。",
    "duplicate_remote_key": "ParaTranz 中有几条可能对应这句原文的翻译。",
    "multiple_remote_translations": "同一句原文存在不同译文，按原文、现有译文和词条信息仍不能唯一确定。",
    "duplicate_remote_id": "ParaTranz 返回了重复的记录编号，暂时无法区分这些翻译。",
    "original_match_required": "还不能确定 ParaTranz 中哪条翻译对应这句原文。",
    "ambiguous_remote_match": "ParaTranz 中有几条可能对应这句原文的翻译。",
    "remote_reference_owned": "这条云端翻译已经用于另一本地条目。",
    "remote_reference_collision": "几条本地内容都对应同一条云端翻译，不能同时采用。",
    "remote_id_missing": "这条云端翻译缺少记录编号，暂时无法使用。",
    "content_changed": "你的译文或翻译状态与 ParaTranz 不同。",
    "original_mismatch": "本地和 ParaTranz 的原文不同，可能不是同一句内容。",
    "stale_remote_reference": "找不到上次使用的云端翻译，它可能已被删除；请重新确认要采用的内容。",
    "remote_already_linked": "这条云端翻译已经用于另一本地条目。",
    "shared_remote_candidate": "几条本地内容都选中了同一条云端翻译，只能为其中一条采用。",
}


def review_rows(plan) -> tuple[SyncReviewRow, ...]:
    reasons = _REASONS
    rows = []
    for row in plan.download_review:
        candidates = tuple(
            SyncReviewCandidate(
                candidate.token,
                _candidate_label(candidate.entry, source_count=candidate.source_count),
                candidate.entry.original,
                candidate.entry.translation,
                candidate.entry.context,
                candidate.selectable,
                reasons.get(candidate.reason, "暂时无法使用这条云端翻译。") if not candidate.selectable else "",
                stage=candidate.entry.stage,
                source_count=candidate.source_count,
            )
            for candidate in row.candidates
        )
        reason = reasons.get(row.reason, "还不能确定这条翻译该如何更新。")
        if row.allow_remote_choice and any(candidate.selectable for candidate in candidates):
            reason += "请对照原文和补充说明，选择你要采用的云端译文；不确定时可保留自己的译文。"
        else:
            reason += "这条先保留你的译文，其他翻译可继续更新。"
        rows.append(
            SyncReviewRow(
                row.row_id,
                row.local.entry_key.local_key,
                row.local.original,
                row.local.translation,
                row.local.context,
                reason,
                candidates,
                row.selected_choice if row.resolved else "",
                row.allow_remote_choice,
                "" if row.allow_remote_choice else reason,
                stage=row.local.stage,
            )
        )
    return tuple(rows)


def _candidate_label(entry, *, source_count: int = 1) -> str:
    if source_count > 1:
        return f"相同译文（{source_count:,} 条云端记录）"
    reference = entry.external_ref
    identity = "无远端编号" if reference is None else f"云端记录 #{reference.opaque_id}"
    return f"{entry.entry_key.local_key} · {identity}"
