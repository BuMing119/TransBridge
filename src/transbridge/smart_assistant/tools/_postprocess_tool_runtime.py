"""Preset-aware request resolution and execution for ``run_postprocess``."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Literal

from transbridge.application.translation.ai_execution_profile import AiWorkflowPreset

from ._workflow_llm_runtime import create_workflow_llm_runtime

PostprocessStrategy = Literal["proofread", "strict"]

_BUILTIN_PROFILES = frozenset({"translate", "polish", "mixed"})
_PHASE_ORDER = ("consistency", "format", "quality_gate", "refinement", "polish", "arbitration")
_PHASE_FIELDS = {
    "consistency": "pp_enable_consistency_check",
    "format": "pp_enable_format_validation",
    "quality_gate": "pp_enable_quality_gate",
    "refinement": "pp_enable_refinement",
    "polish": "pp_enable_polish",
    "arbitration": "pp_enable_arbitration",
}
_LIMITS = {
    "max_concurrent": (1, 128),
    "max_tokens_per_batch": (1, 1_000_000),
    "max_output_tokens": (0, 1_000_000),
    "max_terms_per_batch": (1, 10_000),
}
_INTENSITIES = {"light": "light", "medium": "moderate", "heavy": "aggressive"}
_SCOPES = frozenset({"configured", "set_scope", "all", "passed", "has_issues"})


class PostprocessToolArgumentError(ValueError):
    """Raised before task registration when the requested workflow is invalid."""


@dataclass(frozen=True, slots=True)
class PostprocessToolRequest:
    entries: tuple[object, ...]
    effective_config: object
    profile: str
    profile_id: str | None
    profile_base_mode: str
    strategy: PostprocessStrategy
    stages: tuple[str, ...]
    scope: str
    intensity: str
    limits: dict[str, int]
    terminology_binding: object

    @property
    def metadata(self) -> dict[str, object]:
        return {
            "type": "postprocess",
            "profile": self.profile,
            "profile_id": self.profile_id,
            "profile_base_mode": self.profile_base_mode,
            "strategy": self.strategy,
            "stages": list(self.stages),
            "scope": self.scope,
            "intensity": self.intensity,
            "limits": dict(self.limits),
        }


@dataclass(frozen=True, slots=True)
class PostprocessTaskResult:
    report_data: dict[str, object]
    completion_data: dict[str, object]
    cancelled: bool
    committed: bool


def resolve_postprocess_request(args: dict, ctx, collection) -> PostprocessToolRequest:
    """Resolve one immutable effective request from presets, custom profiles and overrides."""

    phases_supplied = "phases" in args
    requested_phases = _parse_phases(args.get("phases")) if phases_supplied else None
    strategy_arg = args.get("strategy")
    if strategy_arg is not None and strategy_arg not in {"proofread", "combined", "strict"}:
        raise PostprocessToolArgumentError("strategy 必须是 proofread 或 strict")
    normalized_arg = _normalize_strategy(strategy_arg) if strategy_arg is not None else None
    if phases_supplied and normalized_arg == "proofread":
        raise PostprocessToolArgumentError("proofread 策略不接受 phases；请移除 phases 或改用 strategy=strict")

    profile_arg_supplied = "profile" in args
    profile_ref = args.get("profile", "polish")
    if not isinstance(profile_ref, str) or not profile_ref.strip():
        raise PostprocessToolArgumentError("profile 必须是内置预设名、custom、具名配置名称或 UUID")
    effective_config, profile_name, profile_id, base_mode = _resolve_profile(profile_ref.strip())

    if strategy_arg is not None:
        strategy = normalized_arg
    elif phases_supplied:
        strategy = "strict"
    elif profile_arg_supplied:
        strategy = _normalize_strategy(getattr(effective_config, "pp_strategy", "proofread"))
    else:
        strategy = "proofread"
    if strategy not in {"proofread", "strict"}:  # defensive boundary for hand-edited legacy config
        strategy = "proofread"
    setattr(effective_config, "pp_strategy", strategy)
    setattr(effective_config, "enable_post_process", True)

    intensity = args.get("intensity", "configured")
    if intensity not in {"configured", *_INTENSITIES}:
        raise PostprocessToolArgumentError("intensity 必须是 configured、light、medium 或 heavy")
    if intensity != "configured":
        setattr(effective_config, "pp_polish_level", _INTENSITIES[intensity])
    effective_intensity = _external_intensity(str(getattr(effective_config, "pp_polish_level", "moderate")))

    limits = _apply_limit_overrides(args, effective_config)
    stages = ("proofread",) if strategy == "proofread" else _strict_stages(effective_config, requested_phases)
    entries, scope = _resolve_entries(args, ctx, collection, effective_config)
    from .terminology_run import freeze_terminology_binding

    return PostprocessToolRequest(
        entries=entries,
        effective_config=effective_config,
        profile=profile_name,
        profile_id=profile_id,
        profile_base_mode=base_mode,
        strategy=strategy,
        stages=stages,
        scope=scope,
        intensity=effective_intensity,
        limits=limits,
        terminology_binding=freeze_terminology_binding(ctx),
    )


def execute_postprocess_task(
    request: PostprocessToolRequest,
    *,
    ctx,
    collection,
    task_id: str,
    stop_event: object,
    pause_event: object,
    report_directory: Path,
) -> PostprocessTaskResult:
    """Run the resolved workload through shared budget/logging and render its canonical report."""

    from transbridge.ai_translator.post_processor.post_processor import PostProcessor, PostProcessorConfig
    from transbridge.ai_translator.term_database import TermDatabaseManager
    from transbridge.application.contracts import OperationOutcome, RequestContext
    from transbridge.application.io import StagePolicy
    from transbridge.application.io.publish import ImmediateCommitGuard
    from transbridge.application.translation import (
        FilesystemPostProcessCheckpointPort,
        FilesystemTranslationCheckpointPort,
        PostProcessExecutionService,
        PostProcessWorkload,
        TranslationInput,
        render_report_bundle,
    )
    from transbridge.paratranz.config_manager import ParatranzConfig

    config = request.effective_config
    if not getattr(config, "api_key", ""):
        raise ValueError("API Key 未配置，请在 AI 翻译设置中配置 API Key")
    term_manager = TermDatabaseManager(
        config=config,
        esp_path=getattr(ctx, "esp_path", None) or "",
        **request.terminology_binding.term_database_kwargs(),
    )
    term_manager.load_all()
    llm_runtime = create_workflow_llm_runtime(
        config,
        esp_path=getattr(ctx, "esp_path", None) or "",
        workflow="postprocess",
        stop_event=stop_event,
        pause_event=pause_event,
    )
    try:
        stages = _build_stages(request, llm_runtime.client, term_manager, PostProcessor, PostProcessorConfig)
        checkpoint_root = Path(ParatranzConfig.get_data_dir()) / "checkpoints"
        workload = PostProcessWorkload(
            stages,
            stage_policy=StagePolicy(),
            stage_names=request.stages,
            checkpoint_port=FilesystemPostProcessCheckpointPort(checkpoint_root / "postprocess"),
        )
        context = _request_context(ctx, task_id, RequestContext)
        from transbridge.ai_translator.project_terminology_adapter import plugin_id_from_entry

        inputs = tuple(
            TranslationInput(
                entry.identity,
                entry.revision,
                entry.original,
                entry.translation,
                entry.stage,
                entry.context or "",
                plugin_id_from_entry(entry),
            )
            for entry in request.entries
        )
        execution = PostProcessExecutionService(workload).execute(
            run_id=task_id,
            entries=inputs,
            collection=collection,
            context=context,
            commit_guard=ImmediateCommitGuard(task_id, active=lambda: not stop_event.is_set()),
            commit_checkpoint=FilesystemTranslationCheckpointPort(checkpoint_root / "translation"),
            is_cancelled=stop_event.is_set,
            run_spec_summary={**request.metadata, "model": str(getattr(config, "model", ""))},
        )
        report = execution.report_snapshot
        if report is None:
            raise RuntimeError("后处理未生成终态报告快照")
        rendered = render_report_bundle(report, base_dir=report_directory)
        report_data = _report_data(request, execution, report, rendered, llm_runtime.log_store.log_dir)
        completion_data = {
            key: report_data[key]
            for key in (
                "profile",
                "strategy",
                "stages",
                "scope",
                "limits",
                "total_checked",
                "issue_count",
                "auto_fixed",
                "verdict_stats",
                "outcome",
                "report_file",
                "report_files",
                "report_diagnostics",
                "log_dir",
            )
        }
        if execution.report_result.value is None and execution.report_result.outcome is not OperationOutcome.CANCELLED:
            codes = ", ".join(item.code for item in execution.report_result.diagnostics)
            raise RuntimeError(f"后处理候选阶段失败: {codes or 'POSTPROCESS_FAILED'}")
        if execution.commit_result is not None and execution.commit_result.outcome not in {
            OperationOutcome.COMPLETED,
            OperationOutcome.PARTIAL,
        }:
            codes = ", ".join(item.code for item in execution.commit_result.diagnostics)
            raise RuntimeError(f"后处理提交失败: {codes or 'POSTPROCESS_COMMIT_FAILED'}")
        return PostprocessTaskResult(
            report_data,
            completion_data,
            execution.report_result.outcome is OperationOutcome.CANCELLED,
            execution.commit_result is not None,
        )
    finally:
        llm_runtime.close()


def _resolve_profile(profile_ref: str) -> tuple[object, str, str | None, str]:
    from transbridge.application.translation.ai_execution_profile import apply_profile_settings
    from transbridge.config.ai_workflow_profiles import AiWorkflowProfileRepository

    from ._common import load_llm_config

    base_config = load_llm_config()
    copier = getattr(base_config, "copy_for_execution", None)
    detached = copier() if callable(copier) else copy.deepcopy(base_config)
    normalized = profile_ref.casefold()
    if normalized in _BUILTIN_PROFILES:
        preset: AiWorkflowPreset = normalized  # type: ignore[assignment]
        apply_profile_settings(detached, preset)
        return detached, f"builtin:{preset}", None, preset

    repository = AiWorkflowProfileRepository()
    document = repository.load()
    profile = (
        document.selected_profile
        if normalized == "custom"
        else next(
            (item for item in document.profiles if item.id == profile_ref or item.name.casefold() == normalized),
            None,
        )
    )
    if profile is None:
        raise PostprocessToolArgumentError(f"未找到工作流配置: {profile_ref}")
    return profile.apply_to(detached), f"custom:{profile.name}", profile.id, profile.base_mode


def _parse_phases(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise PostprocessToolArgumentError("phases 必须是非空阶段列表")
    if any(not isinstance(item, str) for item in value):
        raise PostprocessToolArgumentError("phases 中的阶段名必须是字符串")
    invalid = sorted(set(value) - set(_PHASE_ORDER))
    if invalid:
        raise PostprocessToolArgumentError(f"无效的阶段名: {invalid}，可选: {list(_PHASE_ORDER)}")
    return tuple(phase for phase in _PHASE_ORDER if phase in value)


def _strict_stages(config: object, requested: tuple[str, ...] | None) -> tuple[str, ...]:
    stages = requested or tuple(phase for phase in _PHASE_ORDER if bool(getattr(config, _PHASE_FIELDS[phase], False)))
    if not stages:
        raise PostprocessToolArgumentError("strict 策略至少需要启用一个 phases 阶段")
    for phase, field in _PHASE_FIELDS.items():
        setattr(config, field, phase in stages)
    return stages


def _normalize_strategy(value: object) -> PostprocessStrategy:
    return "strict" if value == "strict" else "proofread"


def _apply_limit_overrides(args: dict, config: object) -> dict[str, int]:
    if "max_workers" in args and "max_concurrent" in args:
        raise PostprocessToolArgumentError("max_workers 是 max_concurrent 的兼容别名，不能同时提供")
    if "max_workers" in args:
        legacy = args["max_workers"]
        if not isinstance(legacy, int) or isinstance(legacy, bool) or legacy < 1:
            legacy = 1
        setattr(config, "max_concurrent", min(legacy, 8))
    for field, (minimum, maximum) in _LIMITS.items():
        if field not in args:
            continue
        value = args[field]
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise PostprocessToolArgumentError(f"{field} 必须是 [{minimum}, {maximum}] 范围内的整数")
        setattr(config, field, value)
    return {field: int(getattr(config, field, minimum)) for field, (minimum, _maximum) in _LIMITS.items()}


def _resolve_entries(args: dict, ctx, collection, config: object) -> tuple[tuple[object, ...], str]:
    entry_ids = args.get("entry_ids")
    if entry_ids is not None:
        if not isinstance(entry_ids, list) or not entry_ids or any(not isinstance(item, str) for item in entry_ids):
            raise PostprocessToolArgumentError("entry_ids 必须是非空条目 key 列表")
        entries = _entries_by_ids(collection, entry_ids)
        if not entries:
            raise PostprocessToolArgumentError("所有指定的 entry_id 均无效，未找到匹配条目")
        scope = "entry_ids"
    else:
        scope_arg = args.get("scope")
        if scope_arg is not None and scope_arg not in _SCOPES:
            raise PostprocessToolArgumentError(f"scope 必须是 {sorted(_SCOPES)} 之一")
        from .base import resolve_scope_to_entry_ids

        scoped_ids = resolve_scope_to_entry_ids(ctx, collection)
        if scope_arg == "set_scope":
            if scoped_ids is None:
                raise PostprocessToolArgumentError("当前没有通过 set_scope 设置可用范围")
            entries = _entries_by_ids(collection, scoped_ids)
            scope = "set_scope"
        elif scope_arg is None and scoped_ids is not None:
            entries = _entries_by_ids(collection, scoped_ids)
            scope = "set_scope"
        else:
            configured = str(getattr(config, "pp_polish_scope", "all"))
            scope = configured if scope_arg in {None, "configured"} else scope_arg
            entries = _entries_for_scope(collection, scope)
    translated = tuple(entry for entry in entries if entry is not None and bool(getattr(entry, "translation", "")))
    if not translated:
        raise PostprocessToolArgumentError("没有可处理的条目：所选范围内不存在已有译文")
    return translated, scope


def _entries_by_ids(collection, entry_ids: list[str]) -> tuple[object, ...]:
    seen: set[object] = set()
    entries = []
    for entry_id in entry_ids:
        entry = collection.get(entry_id)
        identity = getattr(entry, "identity", None)
        if entry is not None and identity not in seen:
            entries.append(entry)
            seen.add(identity)
    return tuple(entries)


def _entries_for_scope(collection, scope: str) -> tuple[object, ...]:
    entries = tuple(collection)
    if scope == "all":
        return entries
    if scope == "passed":
        return tuple(entry for entry in entries if entry.stage == 1 or entry.stage >= 3)
    if scope == "has_issues":
        return tuple(entry for entry in entries if entry.stage == 2)
    raise PostprocessToolArgumentError(f"不支持的有效作用域: {scope}")


def _build_stages(request, client, term_manager, postprocessor_type, postprocessor_config_type) -> tuple[object, ...]:
    config = request.effective_config
    if request.strategy == "proofread":
        from transbridge.application.translation import ProofreadStage

        max_terms = request.limits["max_terms_per_batch"]

        def resolve_terms(candidate):
            if not candidate.original:
                return {}
            contextual = getattr(term_manager, "match_terms_for_entry", None)
            if callable(contextual):
                matches = contextual(candidate)
            else:
                lookup_context = getattr(term_manager, "lookup_context_for_entry", None)
                match_terms = getattr(term_manager, "match_terms", None)
                if not callable(lookup_context) or not callable(match_terms):
                    return {}
                matches = match_terms([candidate.original], context=lookup_context(candidate))
            return dict(list(matches.items())[:max_terms])

        return (
            ProofreadStage(
                client,
                term_resolver=resolve_terms,
                target_locale=str(getattr(config, "target_lang", "zh_CN")),
                game_profile=str(getattr(config, "game_profile", "general")),
                polish_level=str(getattr(config, "pp_polish_level", "moderate")),
                model=str(getattr(config, "model", "")),
                max_tokens_per_batch=request.limits["max_tokens_per_batch"],
                refinement_batch_size=max(1, int(getattr(config, "pp_refinement_batch_size", 5))),
                max_output_tokens=request.limits["max_output_tokens"],
                max_workers=request.limits["max_concurrent"],
            ),
        )

    from transbridge.application.translation import (
        CheckerStage,
        LlmClientPostProcessPort,
        LlmPostProcessStage,
        PostProcessLlmPhase,
    )

    pp_config = postprocessor_config_type.from_llm_config(config)
    processor = postprocessor_type(pp_config)
    processor.register_default_checkers(term_manager=term_manager, llm_client=client)
    stages: list[object] = []
    checker_phases = {
        "ConsistencyChecker": "consistency",
        "FormatValidator": "format",
        "QualityGateChecker": "quality_gate",
    }
    for checker in processor._checkers:
        phase = checker_phases.get(type(checker).__name__)
        if phase not in request.stages:
            continue
        options = (
            {"model": str(getattr(config, "model", "")), "max_tokens_per_batch": request.limits["max_tokens_per_batch"]}
            if phase == "quality_gate"
            else {}
        )
        stages.append(CheckerStage(phase, checker, **options))
    llm_port = LlmClientPostProcessPort(client, max_output_tokens=request.limits["max_output_tokens"])
    llm_phases = {
        "refinement": (PostProcessLlmPhase.REFINE, pp_config.refinement_batch_size),
        "polish": (PostProcessLlmPhase.POLISH, pp_config.polish_batch_size),
        "arbitration": (PostProcessLlmPhase.ARBITRATE, pp_config.arbitration_batch_size),
    }
    for phase_name, (phase, max_items) in llm_phases.items():
        if phase_name in request.stages:
            stages.append(
                LlmPostProcessStage(
                    phase,
                    llm_port,
                    target_locale=str(getattr(config, "target_lang", "zh_CN")),
                    game_profile=str(getattr(config, "game_profile", "general")),
                    base_url=str(getattr(config, "base_url", "")),
                    model=str(getattr(config, "model", "")),
                    max_tokens_per_batch=request.limits["max_tokens_per_batch"],
                    max_items=max_items,
                )
            )
    return tuple(stages)


def _request_context(ctx, task_id: str, request_context_type):
    current = getattr(ctx, "request_context", None)
    owner_id = getattr(current, "owner_id", "") or getattr(ctx, "owner_id", "") or "smart-assistant"
    return request_context_type(
        owner_id,
        run_id=task_id,
        project_id=getattr(current, "project_id", None),
        variant_id=getattr(current, "variant_id", None),
        session_id=getattr(current, "session_id", None),
        permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
    )


def _report_data(request, execution, report, rendered, log_dir: str) -> dict[str, object]:
    artifacts = rendered.value.artifacts if rendered.value is not None else ()
    artifact_paths = [artifact.artifact_path for artifact in artifacts if artifact.artifact_path]
    excel_path = next(
        (artifact.artifact_path for artifact in artifacts if artifact.renderer == "excel" and artifact.artifact_path),
        None,
    )
    return {
        "phase": "postprocess",
        **request.metadata,
        "total_checked": report.input_count,
        "issue_count": report.issue_count,
        "auto_fixed": _count_committed_fixes(report.candidates) if execution.report_result.value is not None else 0,
        "needs_review": [candidate.entry_key.serialize() for candidate in report.candidates if not candidate.accepted],
        "verdict_stats": {
            "passed": report.accepted_count,
            "rejected": len(report.candidates) - report.accepted_count,
            "pending": 0,
        },
        "issues": [diagnostic.to_dict() for diagnostic in report.diagnostics[:50]],
        "report_file": excel_path or (artifact_paths[0] if artifact_paths else None),
        "report_files": artifact_paths,
        "report_diagnostics": [diagnostic.to_dict() for diagnostic in rendered.diagnostics],
        "report_fingerprint": report.fingerprint,
        "outcome": execution.outcome.value,
        "log_dir": log_dir,
        "timestamp": time.time(),
    }


def _count_committed_fixes(candidates) -> int:
    return sum(1 for candidate in candidates if candidate.accepted and candidate.text != candidate.before_text)


def _external_intensity(value: str) -> str:
    return {"light": "light", "moderate": "medium", "aggressive": "heavy"}.get(value, "medium")
