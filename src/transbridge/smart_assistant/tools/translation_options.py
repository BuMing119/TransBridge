# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Resolve desktop task settings for assistant previews and launches."""

from transbridge.application.translation.ai_execution_profile import apply_profile_settings
from transbridge.config.ai_workflow_profiles import AiWorkflowProfileRepository
from transbridge.config.llm import LLMConfig

POLISH_LEVELS = {"light": "light", "medium": "moderate", "heavy": "aggressive"}


def external_intensity(level: str) -> str:
    return next((label for label, configured in POLISH_LEVELS.items() if configured == level), "medium")


def validate_polish_options(args):
    if "intensity" in args and args["intensity"] not in tuple(POLISH_LEVELS):
        raise ValueError(f"无效 intensity: {args['intensity']}，可选: light, medium, heavy")
    if "strategy" in args and args["strategy"] not in ("proofread", "strict"):
        raise ValueError(f"无效 strategy: {args['strategy']}，可选: proofread, strict")


def translation_config(mode, args):
    base = LLMConfig.load_from_file()
    if "profile" in args:
        reference = args["profile"]
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError("profile 必须是已保存的工作流名称或 ID")
        reference = reference.strip()
        document = AiWorkflowProfileRepository().load()
        profile = document.get(reference) or next(
            (item for item in document.profiles if item.name.casefold() == reference.casefold()), None
        )
        if profile is None:
            raise ValueError(f"找不到已保存的工作流：{reference}")
        if profile.base_mode != mode:
            raise ValueError(f"工作流“{profile.name}”适用于 {profile.base_mode}，当前任务模式为 {mode}")
        config = profile.apply_to(base)
    else:
        config = base.copy_for_execution()
        apply_profile_settings(config, mode)
    if mode == "polish":
        if "strategy" in args:
            config.pp_strategy = args["strategy"]
        if "intensity" in args:
            config.pp_polish_level = POLISH_LEVELS[args["intensity"]]
    return config
