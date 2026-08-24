"""Qt-free presentation state for the AI quick-run surface."""

from __future__ import annotations

from dataclasses import dataclass

from .run_spec import AiPreflightResult, AiRunMode


@dataclass(frozen=True, slots=True)
class AiQuickRunState:
    mode: AiRunMode
    scope_summary: str
    entry_count: int
    estimate_text: str
    overwrite: bool
    enabled: bool
    enabled_reason: str | None
    active_run_id: str | None = None

    def __post_init__(self) -> None:
        if self.entry_count < 0:
            raise ValueError("entry count must not be negative")
        if self.enabled and self.enabled_reason is not None:
            raise ValueError("enabled quick run cannot carry a disabled reason")
        if not self.enabled and not (self.enabled_reason and self.enabled_reason.strip()):
            raise ValueError("disabled quick run requires a reason")


class AiQuickRunPresenter:
    def present(
        self,
        *,
        mode: AiRunMode,
        entry_count: int,
        estimate_text: str,
        overwrite: bool,
        preflight: AiPreflightResult,
        active_run_id: str | None = None,
    ) -> AiQuickRunState:
        active = active_run_id is not None
        reason = "已有 AI 任务正在运行" if active else preflight.reason
        return AiQuickRunState(
            mode=mode,
            scope_summary=f"本次处理 {entry_count} 条",
            entry_count=entry_count,
            estimate_text=estimate_text,
            overwrite=overwrite,
            enabled=not active and preflight.ready,
            enabled_reason=reason,
            active_run_id=active_run_id,
        )


__all__ = ["AiQuickRunPresenter", "AiQuickRunState"]
