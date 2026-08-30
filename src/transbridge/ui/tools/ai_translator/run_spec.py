"""Immutable AI run identity, preflight, and capability contracts.

The saved :class:`LLMConfig` is a long-lived preference object.  A run only
keeps a private deep copy for execution and exposes this immutable summary to
task/result presentation.  Secrets are never copied into the summary.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from typing import Literal

from transbridge.application.tasks import JobCapabilities, OwnerRef
from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
from transbridge.application.translation.terminology_run_snapshot import TerminologyRunSnapshotRef
from transbridge.ui.shell.action_catalog import IntentId

AiRunMode = Literal["translate", "polish", "mixed", "batch"]


class AiPreflightCode(StrEnum):
    MISSING_API_KEY = "missing_api_key"
    MISSING_MODEL = "missing_model"
    MISSING_DEPENDENCY = "missing_dependency"
    MISSING_EMBEDDING_CONFIGURATION = "missing_embedding_configuration"
    MISSING_EMBEDDING_DEPENDENCY = "missing_embedding_dependency"
    EMPTY_SCOPE = "empty_scope"
    EMPTY_WORKFLOW = "empty_workflow"
    MISSING_SOURCE = "missing_source"


@dataclass(frozen=True, slots=True)
class AiPreflightIssue:
    code: AiPreflightCode
    message: str
    fix_intent: IntentId


@dataclass(frozen=True, slots=True)
class AiPreflightResult:
    issues: tuple[AiPreflightIssue, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.issues

    @property
    def reason(self) -> str | None:
        return None if self.ready else "；".join(issue.message for issue in self.issues)


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
    """Return only controls proven by the S03 workload inventory."""

    if mode == "mixed":
        controls = JobCapabilities(
            supports_pause=False,
            supports_resume=False,
            supports_cancel=True,
            supports_checkpoint=False,
        )
    else:
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
) -> AiPreflightResult:
    """Validate cheap, side-effect-free blockers before importing runtime code."""

    available = dependency_available or (lambda name: importlib.util.find_spec(name) is not None)
    issues: list[AiPreflightIssue] = []
    profile = AiExecutionProfile.from_config("translate" if mode == "batch" else mode, config)
    needs_llm = mode != "polish" or profile.requires_llm
    if needs_llm and not str(getattr(config, "api_key", "")).strip():
        issues.append(
            AiPreflightIssue(
                AiPreflightCode.MISSING_API_KEY,
                "尚未配置 API Key",
                IntentId.SETTINGS_SERVICES,
            )
        )
    if needs_llm and not str(getattr(config, "model", "")).strip():
        issues.append(
            AiPreflightIssue(
                AiPreflightCode.MISSING_MODEL,
                "尚未配置模型",
                IntentId.SETTINGS_SERVICES,
            )
        )
    if not tuple(entries):
        issues.append(
            AiPreflightIssue(
                AiPreflightCode.EMPTY_SCOPE,
                "当前范围没有可处理词条",
                IntentId.TRANSLATION_AI,
            )
        )
    if mode == "polish":
        if not profile.has_proofread_work:
            issues.append(
                AiPreflightIssue(
                    AiPreflightCode.EMPTY_WORKFLOW,
                    "当前润色预设未启用任何质量处理阶段",
                    IntentId.SETTINGS_SERVICES,
                )
            )
    if mode in {"translate", "mixed", "batch"} and not esp_path:
        issues.append(
            AiPreflightIssue(
                AiPreflightCode.MISSING_SOURCE,
                "当前翻译内容缺少源文件",
                IntentId.SOURCE_PARSE,
            )
        )
    if mode in {"translate", "mixed", "batch"} and not available("tiktoken"):
        issues.append(
            AiPreflightIssue(
                AiPreflightCode.MISSING_DEPENDENCY,
                "缺少 AI 翻译依赖 tiktoken",
                IntentId.SETTINGS_SERVICES,
            )
        )
    if (
        mode in {"translate", "mixed", "batch"}
        and bool(getattr(config, "retrieval_enabled", True))
        and bool(getattr(config, "enable_semantic_match", True))
    ):
        embedding = getattr(config, "embedding", None)
        embedding_mode = str(getattr(embedding, "mode", "disabled") or "disabled").casefold()
        if embedding_mode in {"openai", "custom"}:
            embedding_mode = "api"
        if embedding_mode == "api":
            embedding_provider = str(getattr(embedding, "provider", "") or "").strip().casefold()
            embedding_key = str(getattr(embedding, "api_key", "")).strip()
            embedding_model = str(getattr(embedding, "model", "")).strip()
            embedding_url = str(getattr(embedding, "base_url", "")).strip()
            if embedding_provider not in {"openai", "custom", "api"}:
                issues.append(
                    AiPreflightIssue(
                        AiPreflightCode.MISSING_EMBEDDING_CONFIGURATION,
                        "语义检索缺少受支持的 Embedding API 服务商",
                        IntentId.SETTINGS_SERVICES,
                    )
                )
            if not embedding_key:
                issues.append(
                    AiPreflightIssue(
                        AiPreflightCode.MISSING_EMBEDDING_CONFIGURATION,
                        "语义检索缺少 Embedding API Key",
                        IntentId.SETTINGS_SERVICES,
                    )
                )
            if not embedding_model:
                issues.append(
                    AiPreflightIssue(
                        AiPreflightCode.MISSING_EMBEDDING_CONFIGURATION,
                        "语义检索缺少 Embedding 模型名",
                        IntentId.SETTINGS_SERVICES,
                    )
                )
            if not embedding_url:
                issues.append(
                    AiPreflightIssue(
                        AiPreflightCode.MISSING_EMBEDDING_CONFIGURATION,
                        "语义检索缺少 Embedding Base URL",
                        IntentId.SETTINGS_SERVICES,
                    )
                )
            for dependency, label in (("openai", "OpenAI 客户端"), ("faiss", "FAISS")):
                if not available(dependency):
                    issues.append(
                        AiPreflightIssue(
                            AiPreflightCode.MISSING_EMBEDDING_DEPENDENCY,
                            f"语义检索缺少 {label} 依赖",
                            IntentId.SETTINGS_SERVICES,
                        )
                    )
        elif embedding_mode == "local":
            model_id = str(getattr(embedding, "local_model_id", "") or "").strip()
            model_path = None
            if model_id:
                from transbridge.infra.embedding_model_store import EmbeddingModelStore

                try:
                    model_path = EmbeddingModelStore().installed_path(model_id)
                except (KeyError, OSError, ValueError):
                    model_path = None
            if model_path is None or not model_path.is_dir() or not (model_path / "modules.json").is_file():
                issues.append(
                    AiPreflightIssue(
                        AiPreflightCode.MISSING_EMBEDDING_CONFIGURATION,
                        "当前没有可用的本地向量模型",
                        IntentId.SETTINGS_SERVICES,
                    )
                )
            for dependency, label in (("sentence_transformers", "sentence-transformers"), ("faiss", "FAISS")):
                if not available(dependency):
                    issues.append(
                        AiPreflightIssue(
                            AiPreflightCode.MISSING_EMBEDDING_DEPENDENCY,
                            f"本地语义检索缺少 {label} 依赖",
                            IntentId.SETTINGS_SERVICES,
                        )
                    )
        elif embedding_mode != "disabled":
            issues.append(
                AiPreflightIssue(
                    AiPreflightCode.MISSING_EMBEDDING_CONFIGURATION,
                    f"不支持的 Embedding 模式：{embedding_mode}",
                    IntentId.SETTINGS_SERVICES,
                )
            )
    return AiPreflightResult(tuple(issues))


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
    values = tuple(entries)
    entry_keys = tuple(_entry_key(value) for value in values)
    input_ref = str(Path(esp_path).resolve()) if esp_path else f"owner:{owner.owner_id}"
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
    for field in ("id", "key"):
        value = getattr(entry, field, None)
        if value is not None and str(value).strip():
            return str(value)
    raise ValueError("AI run entries require a stable id or key")


def _copy_execution_value(value: object) -> object:
    copier = getattr(value, "copy_for_execution", None)
    return copier() if callable(copier) else deepcopy(value)


def _config_digest(config: object) -> str:
    return _digest(_safe_config_value(config))


def _safe_config_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): bool(item) if _is_secret_field(str(key)) else _safe_config_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_config_value(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            str(key): bool(item) if _is_secret_field(str(key)) else _safe_config_value(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_") and str(key) != "config_revision"
        }
    return repr(value)


def _is_secret_field(name: str) -> bool:
    normalized = name.casefold()
    return any(token in normalized for token in ("api_key", "secret", "token", "password", "credential"))


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
