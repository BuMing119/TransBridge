"""Owner-aware AI result references and failed-subset retry intent factory."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from transbridge.application.tasks import OwnerRef, TaskArtifactRef, TaskNavigationIntent

from .run_spec import AiPreflightResult, AiRunSpec, preflight_ai_run


class RetryRunController(Protocol):
    def begin(self, mode: str, config: object, entries: list[object], **identity: object) -> object: ...


@dataclass(frozen=True, slots=True)
class AiResultActionState:
    run_id: str
    owner: OwnerRef
    report: TaskArtifactRef | None
    locatable_entry_keys: tuple[str, ...]
    failed_entry_keys: tuple[str, ...]
    retry_available: bool


@dataclass(frozen=True, slots=True)
class AiRetryPreparation:
    preflight: AiPreflightResult
    request: object | None


class AiResultNavigator:
    """Resolve opaque refs only for the exact run owner scope."""

    def __init__(self) -> None:
        self._reports: dict[str, tuple[OwnerRef, str]] = {}

    def register_report(self, spec: AiRunSpec, report_path: str | None) -> TaskArtifactRef | None:
        if not report_path:
            return None
        verified_path = Path(report_path)
        if not verified_path.is_file():
            return None
        artifact_id = sha256(f"{spec.run_id}:report".encode()).hexdigest()
        self._reports[artifact_id] = (spec.owner, str(verified_path))
        return TaskArtifactRef(artifact_id=artifact_id, kind="ai-report", label="AI 运行报告")

    def report_path(self, artifact: TaskArtifactRef, actor: OwnerRef) -> str | None:
        value = self._reports.get(artifact.artifact_id)
        if value is None or not value[0].same_scope(actor):
            return None
        return value[1]

    @staticmethod
    def entry_navigation(spec: AiRunSpec, entry_key: str, actor: OwnerRef) -> TaskNavigationIntent | None:
        if not spec.owner.same_scope(actor) or entry_key not in spec.entry_keys:
            return None
        return TaskNavigationIntent("workbench.entry", (("entry_key", entry_key),))


class FailedSubsetRetryFactory:
    """Re-preflight current inputs and create a new immutable run request."""

    def prepare(
        self,
        *,
        previous: AiRunSpec,
        failed_entry_keys: tuple[str, ...],
        current_entries: list[object],
        current_config: object,
        esp_path: str | None,
        controller: RetryRunController,
        mixed_has_translation: bool | None = None,
    ) -> AiRetryPreparation:
        failed = frozenset(failed_entry_keys)
        selected = [entry for entry in current_entries if _entry_key(entry) in failed]
        preflight = preflight_ai_run(
            previous.mode,
            current_config,
            selected,
            esp_path=esp_path,
            mixed_has_translation=mixed_has_translation,
        )
        if not preflight.ready or not failed:
            return AiRetryPreparation(preflight, None)
        request = controller.begin(
            previous.mode,  # type: ignore[arg-type]
            current_config,
            selected,
            overwrite=previous.overwrite,
            esp_path=esp_path,
            project_id=previous.owner.project_id,
            variant_id=previous.owner.variant_id,
        )
        if request.run_id == previous.run_id:
            raise RuntimeError("retry must allocate a new run ID")
        return AiRetryPreparation(preflight, request)


def result_action_state(
    spec: AiRunSpec,
    *,
    result: object,
    report: TaskArtifactRef | None,
) -> AiResultActionState:
    failed = tuple(str(value) for value in getattr(result, "failed_entries", ()) if str(value))
    locatable = tuple(key for key in spec.entry_keys if key in set(failed)) or spec.entry_keys
    return AiResultActionState(
        run_id=spec.run_id,
        owner=spec.owner,
        report=report,
        locatable_entry_keys=locatable,
        failed_entry_keys=failed,
        retry_available=bool(failed),
    )


def _entry_key(entry: object) -> str:
    return str(getattr(entry, "id", getattr(entry, "key", "")))


__all__ = [
    "AiResultActionState",
    "AiResultNavigator",
    "AiRetryPreparation",
    "FailedSubsetRetryFactory",
    "result_action_state",
]
