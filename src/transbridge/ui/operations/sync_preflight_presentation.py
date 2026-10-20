"""Translate sync plan counts and checks into truthful confirmation feedback."""

from dataclasses import dataclass

from transbridge.ui.foundation.components import SemanticState

from .plan_view import OperationKind
from .preflight_view import OperationPreflightResult, PreflightCheckStatus
from .sync_review_direction import sync_review_direction


@dataclass(frozen=True, slots=True)
class SyncPreflightPresentation:
    impact: str
    status: str
    semantic: SemanticState
    can_confirm: bool
    can_retry: bool


def present_sync_preflight(result: OperationPreflightResult) -> SyncPreflightPresentation:
    impact = dict(result.estimated_impact)
    download = result.kind is OperationKind.DOWNLOAD
    direction = sync_review_direction(result.kind)
    side = "local" if download else "remote"
    parts = [
        ("更新", impact.get(f"update_{side}", 0)),
        ("新增", impact.get(f"create_{side}", 0)),
        ("删除", impact.get(f"delete_{side}", 0)),
    ]
    keep_key, unused_key = ("keep_local", "remote_only") if download else ("keep_remote", "local_only")
    if any(key in impact for key in (keep_key, unused_key, "deferred")):
        parts.extend((
            (f"保留{direction.retained_side}", impact.get(keep_key, 0)),
            ("不下载" if download else "不上传", impact.get(unused_key, 0)),
            ("待确认", impact.get("deferred", impact.get("conflict", 0))),
        ))
    else:
        parts.extend((("保留", impact.get("skip", 0)), ("待确认", impact.get("conflict", 0))))
    summary = "  ·  ".join(f"{label} {value:,}" for label, value in parts if value) or "没有内容变化"
    blocked = [item.reason for item in result.checks if item.status is PreflightCheckStatus.BLOCKED]
    warnings = [
        item.reason
        for item in result.checks
        if item.status is PreflightCheckStatus.WARNING and item.reason and item.check_id != "PARATRANZ_PLAN_FRESH"
    ]
    actionable = sum(impact.get(f"{action}_{side}", 0) for action in ("create", "update", "delete"))
    deferred = impact.get("deferred", impact.get("conflict", 0))
    if blocked:
        message, semantic = blocked[0], SemanticState.ERROR
    elif deferred:
        message = f"有 {deferred:,} 条翻译需要你确认，暂时保持{direction.retained_side}不变。" + (
            f"可以先{direction.verb}其余翻译，再点击下方按钮逐条确认。"
            if actionable
            else "请点击下方按钮查看；不确定的翻译可以跳过。"
        )
        if warnings:
            message += f"\n{warnings[0]}"
        semantic = SemanticState.WARNING
    elif actionable == 0:
        if impact.get(keep_key, 0) or impact.get(unused_key, 0):
            message = f"按当前选择无需{direction.verb}；跳过的翻译保持原样。"
        else:
            message = (
                "本地内容与 ParaTranz 已一致，无需更新。" if download else "ParaTranz 内容与本地已一致，无需上传。"
            )
        semantic = SemanticState.SUCCESS
    elif warnings:
        message, semantic = warnings[0], SemanticState.WARNING
    else:
        message = "已核对，可以更新本地翻译。" if download else "已核对，可以上传译文。"
        semantic = SemanticState.SUCCESS
    return SyncPreflightPresentation(
        summary,
        message,
        semantic,
        result.ready and result.confirmation_token is not None and actionable > 0,
        bool(blocked),
    )
