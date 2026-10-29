"""Immutable AI run identity, preflight, and capability contracts.

The saved :class:`LLMConfig` is a long-lived preference object.  A run only
keeps a private deep copy for execution and exposes this immutable summary to
task/result presentation.  Secrets are never copied into the summary.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import logging
from pathlib import Path

from transbridge.application.io.identity import EntryKey
from transbridge.application.preflight.ai import (
    AiPreflightCode as AiPreflightCode,
    AiPreflightIssue as ApplicationAiPreflightIssue,
    AiPreflightResult as AiPreflightResult,
    AiRunMode as AiRunMode,
    preflight_ai_run as evaluate_ai_run,
)
from transbridge.application.tasks import JobCapabilities, OwnerRef
from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
from transbridge.application.translation.entry_alias import ai_entry_id, ai_entry_key
from transbridge.application.translation.terminology_run_snapshot import TerminologyRunSnapshotRef
from transbridge.ui.shell.action_catalog import IntentId

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AiPreflightIssue(ApplicationAiPreflightIssue):
    """UI repair action projected from an application preflight issue."""

    fix_intent: IntentId


@dataclass(frozen=True, slots=True)
class AiRunCapabilities:
    """Feature evidence, including actions TaskRuntime does not model yet."""

    task_controls: JobCapabilities
    recover: bool = False
    retry_failed: bool = False
    open_global_log: bool = False
    open_global_result: bool = False


@dataclass(frozen=True, slots=True)
class AiRunSpec:
    run_id: str
    generation: int
    mode: AiRunMode
    owner: OwnerRef
    entry_keys: tuple[str, ...]
    input_ref: str
    input_fingerprint: str
    config_digest: str
    execution_profile: AiExecutionProfile
    overwrite: bool
    capabilities: AiRunCapabilities
    terminology_snapshot: TerminologyRunSnapshotRef | None = None

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        if self.generation < 1:
            raise ValueError("generation must be positive")
        if not self.input_ref.strip() or not self.input_fingerprint.strip():
            raise ValueError("run input identity must not be empty")
        if not self.config_digest.strip():
            raise ValueError("config digest must not be empty")


class FrozenExecutionConfig:
    """A copy-on-read snapshot so later preference edits cannot change a run."""

    __slots__ = ("_value",)

    def __init__(self, value: object) -> None:
        self._value = _copy_execution_value(value)

    def copy(self) -> object:
        return _copy_execution_value(self._value)


def capabilities_for(mode: AiRunMode) -> AiRunCapabilities:
    """All task modes use AiTaskWorker's shared pause, resume and cancellation controls."""

    controls = JobCapabilities(
        supports_pause=True,
        supports_resume=True,
        supports_cancel=True,
        supports_checkpoint=False,
    )
    return AiRunCapabilities(task_controls=controls)


def preflight_ai_run(
    mode: AiRunMode,
    config: object,
    entries: Iterable[object],
    *,
    esp_path: str | None,
    dependency_available: Callable[[str], bool] | None = None,
    mixed_has_translation: bool | None = None,
) -> AiPreflightResult:
    """Project application prerequisites to the existing UI repair intents.

    ``esp_path`` remains accepted for existing callers. Project entries supply
    translation input even when their original external path is unavailable.
    """
    del esp_path
    checked = evaluate_ai_run(
        mode,
        config,
        entries,
        dependency_available=dependency_available,
        mixed_has_translation=mixed_has_translation,
        local_model_path=_installed_embedding_model_path,
    )
    return AiPreflightResult(
        tuple(AiPreflightIssue(issue.code, issue.message, IntentId(issue.fix_intent)) for issue in checked.issues)
    )


def _installed_embedding_model_path(model_id: str) -> Path | None:
    from transbridge.infra.embedding_model_store import EmbeddingModelStore

    try:
        return EmbeddingModelStore().installed_path(model_id)
    except (KeyError, OSError, ValueError):
        logger.warning("Cannot resolve installed embedding model %s", model_id, exc_info=True)
        return None


def build_run_spec(
    *,
    run_id: str,
    generation: int,
    mode: AiRunMode,
    owner: OwnerRef,
    config: object,
    entries: Iterable[object],
    esp_path: str | None,
    overwrite: bool,
    project_revision: int | None = None,
    terminology_snapshot: TerminologyRunSnapshotRef | None = None,
) -> AiRunSpec:
    del esp_path  # Optional external references do not identify saved task input.
    values = tuple(entries)
    entry_keys = tuple(_entry_key(value) for value in values)
    namespaces = tuple(
        sorted({
            identity.namespace.value
            for value in values
            if isinstance(identity := getattr(value, "identity", None), EntryKey)
        })
    )
    input_ref = f"sources:{_digest(namespaces)}" if namespaces else f"owner:{owner.owner_id}"
    input_payload = {
        "entry_keys": entry_keys,
        "input_ref": input_ref,
        "mode": mode,
        "overwrite": overwrite,
        "project_id": owner.project_id,
        "project_revision": project_revision,
        "variant_id": owner.variant_id,
    }
    return AiRunSpec(
        run_id=run_id,
        generation=generation,
        mode=mode,
        owner=owner,
        entry_keys=entry_keys,
        input_ref=input_ref,
        input_fingerprint=_digest(input_payload),
        config_digest=_config_digest(config),
        execution_profile=AiExecutionProfile.from_config(
            "translate" if mode == "batch" else mode,
            config,
        ),
        overwrite=overwrite,
        capabilities=capabilities_for(mode),
        terminology_snapshot=terminology_snapshot,
    )


def _entry_key(entry: object) -> str:
    for value in (ai_entry_id(entry), ai_entry_key(entry)):
        if value is not None and str(value).strip():
            return str(value)
    raise ValueError("AI run entries require a stable id or key")


def _copy_execution_value(value: object) -> object:
    copier = getattr(value, "copy_for_execution", None)
    return copier() if callable(copier) else deepcopy(value)


def _config_digest(config: object) -> str:
    return _digest(_safe_config_value(config))


def _safe_config_value(value: object, *, config_object: bool = True) -> object:
    """Fingerprint settings without interpreting business text or token budgets as secrets."""
    from transbridge.application.security import SecretValue

    if isinstance(value, SecretValue):
        return True
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): (
                bool(item)
                if config_object and str(key) in {"api_key", "mcp_auth_token"}
                else _safe_config_value(item, config_object=config_object and str(key) == "embedding")
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_config_value(item, config_object=False) for item in value]
    if hasattr(value, "__dict__"):
        return _safe_config_value(
            {
                str(key): item
                for key, item in vars(value).items()
                if not str(key).startswith("_") and str(key) != "config_revision"
            },
            config_object=config_object,
        )
    return repr(value)


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "AiPreflightCode",
    "AiPreflightIssue",
    "AiPreflightResult",
    "AiRunCapabilities",
    "AiRunMode",
    "AiRunSpec",
    "FrozenExecutionConfig",
    "build_run_spec",
    "capabilities_for",
    "preflight_ai_run",
]
