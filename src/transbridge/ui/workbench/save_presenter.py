"""Qt-free Workbench save-state projection with stale-completion protection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from transbridge.application.projects import DirtyDecision
from transbridge.ui.shell.action_catalog import IntentId


class SavePhase(StrEnum):
    CLEAN = "clean"
    SAVING = "saving"
    DIRTY = "dirty"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SaveTarget:
    project_id: str
    project_name: str
    variant_id: str
    variant_name: str
    revision: int | None = None

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.project_id, self.project_name, self.variant_id, self.variant_name)):
            raise ValueError("save target identity and display names must not be empty")
        if self.revision is not None and self.revision < 0:
            raise ValueError("save target revision must not be negative")

    @property
    def display_name(self) -> str:
        return f"{self.project_name} / {self.variant_name}"

    @property
    def identity(self) -> tuple[str, str]:
        return self.project_id, self.variant_id


@dataclass(frozen=True, slots=True)
class SaveViewState:
    phase: SavePhase
    target: SaveTarget | None
    message: str
    saved_at: datetime | None = None
    diagnostic: str | None = None
    retry_intent: IntentId | None = None

    @property
    def requires_dirty_decision(self) -> bool:
        return self.phase in (SavePhase.SAVING, SavePhase.DIRTY, SavePhase.FAILED)

    def allows_transition(self, decision: DirtyDecision | None) -> bool:
        if not self.requires_dirty_decision:
            return True
        return decision in (DirtyDecision.SAVE, DirtyDecision.DISCARD)


class SaveStatePresenter:
    """Project save status; application lifecycle still owns dirty decisions."""

    def __init__(self) -> None:
        self._state = SaveViewState(SavePhase.CLEAN, None, "未打开工程")
        self._dirty_after_begin = False

    @property
    def state(self) -> SaveViewState:
        return self._state

    def show_target(self, target: SaveTarget, *, dirty: bool) -> SaveViewState:
        if self._state.target is None or self._state.target.identity != target.identity:
            self._dirty_after_begin = False
            self._state = self._dirty(target) if dirty else self._clean(target)
        elif dirty:
            self.mark_dirty(target)
        elif self._state.phase is not SavePhase.SAVING:
            self._state = self._clean(target, self._state.saved_at)
        return self._state

    def begin(self, target: SaveTarget) -> SaveViewState:
        self._dirty_after_begin = False
        self._state = SaveViewState(
            SavePhase.SAVING,
            target,
            f"正在保存 · {target.display_name}",
            self._state.saved_at if self._same_identity(target) else None,
        )
        return self._state

    def mark_dirty(self, target: SaveTarget) -> SaveViewState:
        if self._state.phase is SavePhase.SAVING and self._same_identity(target):
            self._dirty_after_begin = True
            return self._state
        self._state = self._dirty(target, self._state.saved_at if self._same_identity(target) else None)
        return self._state

    def succeed(self, target: SaveTarget, *, saved_at: datetime | None = None) -> SaveViewState:
        if not self._same_identity(target) or self._state.phase is not SavePhase.SAVING:
            return self._state
        timestamp = saved_at or datetime.now().astimezone()
        if self._dirty_after_begin:
            self._state = self._dirty(target, timestamp)
        else:
            self._state = self._clean(target, timestamp)
        self._dirty_after_begin = False
        return self._state

    def fail(self, target: SaveTarget, diagnostic: str) -> SaveViewState:
        if not self._same_identity(target) or self._state.phase is not SavePhase.SAVING:
            return self._state
        reason = diagnostic.strip() or "保存失败"
        self._state = SaveViewState(
            SavePhase.FAILED,
            target,
            f"保存失败，可重试 · {target.display_name}",
            self._state.saved_at,
            reason,
            IntentId.PROJECT_SAVE,
        )
        self._dirty_after_begin = False
        return self._state

    def clear(self) -> SaveViewState:
        self._dirty_after_begin = False
        self._state = SaveViewState(SavePhase.CLEAN, None, "未打开工程")
        return self._state

    def _same_identity(self, target: SaveTarget) -> bool:
        return self._state.target is not None and self._state.target.identity == target.identity

    @staticmethod
    def _clean(target: SaveTarget, saved_at: datetime | None = None) -> SaveViewState:
        if saved_at is None:
            return SaveViewState(SavePhase.CLEAN, target, f"已保存 · {target.display_name}")
        local_time = saved_at.astimezone().strftime("%H:%M:%S")
        return SaveViewState(
            SavePhase.CLEAN,
            target,
            f"已保存 {local_time} · {target.display_name}",
            saved_at,
        )

    @staticmethod
    def _dirty(target: SaveTarget, saved_at: datetime | None = None) -> SaveViewState:
        return SaveViewState(
            SavePhase.DIRTY,
            target,
            f"有未保存修改 · {target.display_name}",
            saved_at,
        )


__all__ = ["SavePhase", "SaveStatePresenter", "SaveTarget", "SaveViewState"]
