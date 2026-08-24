"""Read model for the Workbench ParaTranz target component."""

from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.projects import ParaTranzTargetStatus, ResolvedParaTranzTarget


@dataclass(frozen=True, slots=True)
class RemoteTargetViewState:
    title: str
    detail: str
    action_text: str
    can_clear: bool
    semantic_state: str


class RemoteTargetPresenter:
    def present(self, target: ResolvedParaTranzTarget) -> RemoteTargetViewState:
        if target.project_id is None:
            return RemoteTargetViewState(
                "ParaTranz · 未绑定",
                target.reason or "选择一个云端项目作为当前工程的默认同步目标。",
                "选择…",
                False,
                "warning",
            )
        names = {
            ParaTranzTargetStatus.UNVERIFIED: "待验证",
            ParaTranzTargetStatus.AVAILABLE: "可用",
            ParaTranzTargetStatus.NOT_FOUND: "项目不存在",
            ParaTranzTargetStatus.NOT_MEMBER: "无成员权限",
            ParaTranzTargetStatus.ACCOUNT_MISMATCH: "账号不一致",
            ParaTranzTargetStatus.ENDPOINT_MISMATCH: "服务地址不一致",
            ParaTranzTargetStatus.AUTHENTICATION_FAILED: "认证失败",
            ParaTranzTargetStatus.TEMPORARILY_UNAVAILABLE: "暂时无法验证",
        }
        label = target.project_name or f"项目 #{target.project_id}"
        status = names.get(target.status, target.status.value)
        semantic = "success" if target.status is ParaTranzTargetStatus.AVAILABLE else "info"
        if target.status in {
            ParaTranzTargetStatus.NOT_FOUND,
            ParaTranzTargetStatus.NOT_MEMBER,
            ParaTranzTargetStatus.ACCOUNT_MISMATCH,
            ParaTranzTargetStatus.ENDPOINT_MISMATCH,
            ParaTranzTargetStatus.AUTHENTICATION_FAILED,
        }:
            semantic = "error"
        return RemoteTargetViewState(
            f"ParaTranz · {label}",
            target.reason or f"#{target.project_id} · {status}",
            "更换…",
            target.source.value == "project_binding",
            semantic,
        )


__all__ = ["RemoteTargetPresenter", "RemoteTargetViewState"]
