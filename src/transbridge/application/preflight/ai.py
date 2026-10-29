"""AI run prerequisites independent of UI and source-file references."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
import importlib.util
from pathlib import Path
from typing import Literal

from transbridge.application.translation.ai_execution_profile import AiExecutionProfile

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
    fix_intent: str


@dataclass(frozen=True, slots=True)
class AiPreflightResult:
    issues: tuple[AiPreflightIssue, ...] = ()

    @property
    def optional_issues(self) -> tuple[AiPreflightIssue, ...]:
        return tuple(
            issue
            for issue in self.issues
            if issue.code
            in {AiPreflightCode.MISSING_EMBEDDING_CONFIGURATION, AiPreflightCode.MISSING_EMBEDDING_DEPENDENCY}
        )

    @property
    def blocking_issues(self) -> tuple[AiPreflightIssue, ...]:
        optional = self.optional_issues
        return tuple(issue for issue in self.issues if issue not in optional)

    @property
    def blocking_reason(self) -> str | None:
        return "；".join(issue.message for issue in self.blocking_issues) or None

    @property
    def ready(self) -> bool:
        return not self.issues

    @property
    def reason(self) -> str | None:
        return None if self.ready else "；".join(issue.message for issue in self.issues)


def preflight_ai_run(
    mode: AiRunMode,
    config: object,
    entries: Iterable[object],
    *,
    dependency_available: Callable[[str], bool] | None = None,
    mixed_has_translation: bool | None = None,
    local_model_path: Callable[[str], Path | None] | None = None,
) -> AiPreflightResult:
    """Validate blockers using stage presence from the caller's existing mixed partition.

    Mixed callers without stage information retain translation prerequisites.
    """

    available = dependency_available or (lambda name: importlib.util.find_spec(name) is not None)
    issues: list[AiPreflightIssue] = []
    profile = AiExecutionProfile.from_config("translate" if mode == "batch" else mode, config)
    has_translation = mode in {"translate", "batch"} or (mode == "mixed" and mixed_has_translation is not False)
    has_polish = mode in {"polish", "mixed"}
    needs_llm = has_translation or (has_polish and profile.requires_llm)
    if needs_llm and not str(getattr(config, "api_key", "")).strip():
        issues.append(
            AiPreflightIssue(
                AiPreflightCode.MISSING_API_KEY,
                "尚未配置 API Key",
                "settings.services",
            )
        )
    if needs_llm and not str(getattr(config, "model", "")).strip():
        issues.append(
            AiPreflightIssue(
                AiPreflightCode.MISSING_MODEL,
                "尚未配置模型",
                "settings.services",
            )
        )
    if not tuple(entries):
        issues.append(
            AiPreflightIssue(
                AiPreflightCode.EMPTY_SCOPE,
                "当前范围没有可处理词条",
                "translation.ai-run",
            )
        )
    if has_polish and not has_translation:
        if not profile.has_proofread_work:
            issues.append(
                AiPreflightIssue(
                    AiPreflightCode.EMPTY_WORKFLOW,
                    "当前润色预设未启用任何质量处理阶段",
                    "settings.services",
                )
            )
    if has_translation and not available("tiktoken"):
        issues.append(
            AiPreflightIssue(
                AiPreflightCode.MISSING_DEPENDENCY,
                "缺少 AI 翻译依赖 tiktoken",
                "settings.services",
            )
        )
    if (
        has_translation
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
                        "settings.services",
                    )
                )
            if not embedding_key:
                issues.append(
                    AiPreflightIssue(
                        AiPreflightCode.MISSING_EMBEDDING_CONFIGURATION,
                        "语义检索缺少 Embedding API Key",
                        "settings.services",
                    )
                )
            if not embedding_model:
                issues.append(
                    AiPreflightIssue(
                        AiPreflightCode.MISSING_EMBEDDING_CONFIGURATION,
                        "语义检索缺少 Embedding 模型名",
                        "settings.services",
                    )
                )
            if not embedding_url:
                issues.append(
                    AiPreflightIssue(
                        AiPreflightCode.MISSING_EMBEDDING_CONFIGURATION,
                        "语义检索缺少 Embedding Base URL",
                        "settings.services",
                    )
                )
            for dependency, label in (("openai", "OpenAI 客户端"), ("faiss", "FAISS")):
                if not available(dependency):
                    issues.append(
                        AiPreflightIssue(
                            AiPreflightCode.MISSING_EMBEDDING_DEPENDENCY,
                            f"语义检索缺少 {label} 依赖",
                            "settings.services",
                        )
                    )
        elif embedding_mode == "local":
            model_id = str(getattr(embedding, "local_model_id", "") or "").strip()
            model_path = None
            if model_id and local_model_path is not None:
                model_path = local_model_path(model_id)
            if model_path is None or not model_path.is_dir() or not (model_path / "modules.json").is_file():
                issues.append(
                    AiPreflightIssue(
                        AiPreflightCode.MISSING_EMBEDDING_CONFIGURATION,
                        "当前没有可用的本地向量模型",
                        "settings.services",
                    )
                )
            for dependency, label in (("sentence_transformers", "sentence-transformers"), ("faiss", "FAISS")):
                if not available(dependency):
                    issues.append(
                        AiPreflightIssue(
                            AiPreflightCode.MISSING_EMBEDDING_DEPENDENCY,
                            f"本地语义检索缺少 {label} 依赖",
                            "settings.services",
                        )
                    )
        elif embedding_mode != "disabled":
            issues.append(
                AiPreflightIssue(
                    AiPreflightCode.MISSING_EMBEDDING_CONFIGURATION,
                    f"不支持的 Embedding 模式：{embedding_mode}",
                    "settings.services",
                )
            )
    return AiPreflightResult(tuple(issues))
