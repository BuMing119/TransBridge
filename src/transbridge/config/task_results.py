"""Persist conflict defaults independently from model and service settings."""

from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.translation.result_merge import ResultConflictPolicy

from .repository import ConfigRepository


@dataclass(frozen=True, slots=True)
class TaskResultPreferences:
    ai_policy: ResultConflictPolicy = ResultConflictPolicy.KEEP_CURRENT
    download_policy: ResultConflictPolicy = ResultConflictPolicy.KEEP_CURRENT


class TaskResultPreferenceRepository:
    def __init__(self, repository: ConfigRepository) -> None:
        self._repository = repository

    def load(self) -> TaskResultPreferences:
        snapshot = self._repository.load()
        return TaskResultPreferences(
            _policy(snapshot.value("task_results", "ai_policy")),
            _policy(snapshot.value("task_results", "download_policy")),
        )

    def save(self, preferences: TaskResultPreferences) -> None:
        # Update only this section so an unrelated settings change is preserved.
        self._repository.update_sections({
            "task_results": {
                "ai_policy": ResultConflictPolicy(preferences.ai_policy).value,
                "download_policy": ResultConflictPolicy(preferences.download_policy).value,
            }
        })


def _policy(value: str | None) -> ResultConflictPolicy:
    try:
        return ResultConflictPolicy(value)
    except (ValueError, TypeError):
        return ResultConflictPolicy.KEEP_CURRENT
