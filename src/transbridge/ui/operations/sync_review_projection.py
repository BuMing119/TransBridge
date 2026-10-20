"""Decode review intents and project synchronization decisions for the GUI."""

from __future__ import annotations

import json

from transbridge.application.sync import SyncAction, SyncOperation

from .sync_review_view import SyncReviewCandidate, SyncReviewRow

_REASONS = {
    "duplicate_local_key": "本地有重复条目，无法确定应该更新哪一条。",
    "duplicate_remote_key": "ParaTranz 中有几条可能对应这句原文的翻译。",
    "duplicate_remote_id": "ParaTranz 返回了重复的记录编号，暂时无法区分这些翻译。",
    "original_match_required": "还不能确定 ParaTranz 中哪条翻译对应这句原文。",
    "ambiguous_remote_match": "ParaTranz 中有几条可能对应这句原文的翻译。",
    "remote_reference_owned": "这条云端翻译已经用于另一本地条目。",
    "remote_reference_collision": "几条本地内容都对应同一条云端翻译，不能同时采用。",
    "remote_id_missing": "这条云端翻译缺少记录编号，暂时无法使用。",
    "content_changed": "你的译文或翻译状态与 ParaTranz 不同。",
    "original_mismatch": "本地和 ParaTranz 的原文不同，可能不是同一句内容。",
    "remote_already_linked": "这条云端翻译已经用于另一本地条目。",
    "shared_remote_candidate": "几条本地内容都选中了同一条云端翻译，只能为其中一条采用。",
}

_UPLOAD_REASONS = {
    "duplicate_local_key": "本地有重复条目，无法确定应该上传哪一条。",
    "duplicate_remote_key": "ParaTranz 中有几条可能对应这句原文的翻译。",
    "duplicate_remote_id": "ParaTranz 返回了重复的记录编号，暂时无法区分这些翻译。",
    "original_match_required": "还不能确定这句译文应上传到 ParaTranz 中的哪一条。",
    "ambiguous_remote_match": "ParaTranz 中有几条可能对应这句原文的翻译。",
    "original_mismatch": "本地和 ParaTranz 的原文不同，可能不是同一句内容。",
    "remote_id_missing": "这条云端翻译缺少记录编号，暂时无法上传。",
    "remote_reference_missing": "找不到上次使用的云端翻译，它可能已被删除。",
    "remote_reference_stale": "找不到上次使用的云端翻译，它可能已被删除。",
    "stale_remote_reference": "找不到上次使用的云端翻译，它可能已被删除。",
    "remote_deleted": "这条云端翻译已被删除，不能上传到这里。",
    "remote_already_linked": "这条云端翻译正在供另一本地条目使用，不能覆盖。",
    "shared_remote_candidate": "几条本地译文都选中了同一条云端翻译，只能上传其中一条。",
    "content_changed": "你的译文或翻译状态与 ParaTranz 不同。",
    "local_key_collision": "几条本地内容会使用同一个云端编号，无法分别新增。",
}


def decode_decisions(value: object) -> tuple[tuple[str, str], ...]:
    """Freeze selections before they participate in the request digest."""
    if value in (None, ""):
        return ()
    try:
        decoded = json.loads(value) if isinstance(value, str) else dict(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("无法读取翻译选择，请重新打开翻译确认窗口。") from exc
    if not isinstance(decoded, dict) or any(
        not isinstance(key, str) or not key or not isinstance(choice, str) or not choice
        for key, choice in decoded.items()
    ):
        raise ValueError("无法读取翻译选择，请重新打开翻译确认窗口。")
    return tuple(sorted(decoded.items()))


def encode_decisions(decisions: tuple[tuple[str, str], ...]) -> str:
    return json.dumps(dict(decisions), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def review_rows(plan) -> tuple[SyncReviewRow, ...]:
    uploading = plan.operation is SyncOperation.UPLOAD
    reasons = _UPLOAD_REASONS if uploading else _REASONS
    rows = []
    for row in plan.upload_review if uploading else plan.download_review:
        candidates = tuple(
            SyncReviewCandidate(
                candidate.token,
                _candidate_label(candidate.entry),
                candidate.entry.original,
                candidate.entry.translation,
                candidate.entry.context,
                candidate.selectable,
                reasons.get(candidate.reason, "暂时无法使用这条云端翻译。") if not candidate.selectable else "",
            )
            for candidate in row.candidates
        )
        reason = reasons.get(row.reason, "还不能确定这条翻译该如何更新。")
        if row.allow_remote_choice and any(candidate.selectable for candidate in candidates):
            reason += (
                "请对照原文和使用场景，选择你要覆盖的云端译文；不确定时可跳过。"
                if uploading
                else "请对照原文和使用场景，选择你要采用的云端译文；不确定时可保留自己的译文。"
            )
        else:
            reason += "这条先不上传，其他翻译可继续。" if uploading else "这条先保留你的译文，其他翻译可继续更新。"
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
                preserves_local_source=row.local.entry_key.original is not None,
            )
        )
    return tuple(rows)


def download_impact(plan, local_entries) -> tuple[tuple[str, int], ...]:
    counts = dict(plan.counts)
    local_keys = {entry.entry_key for entry in local_entries}
    counts["keep_local"] = sum(
        item.action is SyncAction.SKIP
        and item.entry_key in local_keys
        and item.reason not in {"outside_local_source", "remote_only"}
        for item in plan.items
    )
    counts["remote_only"] = counts["skip"] - counts["keep_local"]
    counts["deferred"] = plan.conflicts
    return tuple(sorted(counts.items()))


def upload_impact(plan) -> tuple[tuple[str, int], ...]:
    counts = dict(plan.counts)
    counts["keep_remote"] = counts["skip"]
    counts["deferred"] = plan.conflicts
    return tuple(sorted(counts.items()))


def _candidate_label(entry) -> str:
    reference = entry.external_ref
    identity = "无远端编号" if reference is None else f"云端记录 #{reference.opaque_id}"
    return f"{entry.entry_key.local_key} · {identity}"
