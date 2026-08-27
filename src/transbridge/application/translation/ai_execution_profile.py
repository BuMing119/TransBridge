"""Effective AI workflow profiles shared by UI and execution adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Literal

AiWorkflowPreset = Literal["translate", "polish", "mixed"]
PostProcessStrategy = Literal["proofread", "strict"]

_PROFILE_FIELDS = (
    "pp_strategy",
    "enable_post_process",
    "pp_enable_consistency_check",
    "pp_enable_format_validation",
    "pp_enable_quality_gate",
    "pp_quality_gate_batch_size",
    "pp_enable_refinement",
    "pp_refinement_batch_size",
    "pp_enable_polish",
    "pp_polish_scope",
    "pp_polish_level",
    "pp_polish_batch_size",
    "polish_preview_enabled",
    "pp_enable_arbitration",
    "pp_strict_arbitration",
    "pp_arbitration_batch_size",
)
_DEFAULT_SETTINGS: dict[str, object] = {
    "pp_strategy": "proofread",
    "enable_post_process": True,
    "pp_enable_consistency_check": True,
    "pp_enable_format_validation": True,
    "pp_enable_quality_gate": True,
    "pp_quality_gate_batch_size": 10,
    "pp_enable_refinement": True,
    "pp_refinement_batch_size": 5,
    "pp_enable_polish": False,
    "pp_polish_scope": "all",
    "pp_polish_level": "moderate",
    "pp_polish_batch_size": 5,
    "polish_preview_enabled": False,
    "pp_enable_arbitration": True,
    "pp_strict_arbitration": False,
    "pp_arbitration_batch_size": 10,
}


@dataclass(frozen=True, slots=True)
class AiExecutionProfile:
    """One immutable, user-effective workflow definition for a run."""

    preset: AiWorkflowPreset
    game_profile: str
    target_lang: str
    enable_translation: bool
    enable_post_process: bool
    postprocess_strategy: PostProcessStrategy
    enable_consistency_check: bool
    enable_format_validation: bool
    enable_quality_gate: bool
    quality_gate_batch_size: int
    enable_refinement: bool
    refinement_batch_size: int
    enable_polish: bool
    polish_scope: str
    polish_level: str
    polish_batch_size: int
    preview_enabled: bool
    enable_arbitration: bool
    strict_arbitration: bool
    arbitration_batch_size: int

    @classmethod
    def from_config(cls, preset: AiWorkflowPreset, config: object) -> AiExecutionProfile:
        enabled = bool(getattr(config, "enable_post_process", True))
        strategy = normalize_postprocess_strategy(getattr(config, "pp_strategy", "proofread"))
        return cls(
            preset=preset,
            game_profile=str(getattr(config, "game_profile", "skyrim_se")),
            target_lang=str(getattr(config, "target_lang", "zh_CN")),
            enable_translation=preset in {"translate", "mixed"},
            enable_post_process=enabled,
            postprocess_strategy=strategy,
            enable_consistency_check=enabled and bool(getattr(config, "pp_enable_consistency_check", True)),
            enable_format_validation=enabled and bool(getattr(config, "pp_enable_format_validation", True)),
            enable_quality_gate=enabled and bool(getattr(config, "pp_enable_quality_gate", True)),
            quality_gate_batch_size=max(1, int(getattr(config, "pp_quality_gate_batch_size", 10))),
            enable_refinement=enabled and bool(getattr(config, "pp_enable_refinement", True)),
            refinement_batch_size=max(1, int(getattr(config, "pp_refinement_batch_size", 5))),
            enable_polish=enabled and bool(getattr(config, "pp_enable_polish", False)),
            polish_scope=str(getattr(config, "pp_polish_scope", "all")),
            polish_level=str(getattr(config, "pp_polish_level", "moderate")),
            polish_batch_size=max(1, int(getattr(config, "pp_polish_batch_size", 5))),
            preview_enabled=bool(getattr(config, "polish_preview_enabled", False)),
            enable_arbitration=enabled and bool(getattr(config, "pp_enable_arbitration", True)),
            strict_arbitration=bool(getattr(config, "pp_strict_arbitration", False)),
            arbitration_batch_size=max(1, int(getattr(config, "pp_arbitration_batch_size", 10))),
        )

    @property
    def stages(self) -> tuple[str, ...]:
        values: list[str] = []
        if self.enable_translation:
            values.append("翻译")
        if self.enable_proofread:
            values.append("校对")
            return tuple(values)
        if self.enable_consistency_check or self.enable_format_validation or self.enable_quality_gate:
            values.append("检测")
        if self.enable_refinement:
            values.append("修复")
        if self.enable_polish:
            values.append("润色")
        if self.enable_arbitration:
            values.append("裁决")
        return tuple(values)

    @property
    def summary(self) -> str:
        return " → ".join(self.stages) if self.stages else "未启用处理阶段"

    @property
    def has_proofread_work(self) -> bool:
        if self.enable_proofread:
            return True
        return any((
            self.enable_consistency_check,
            self.enable_format_validation,
            self.enable_quality_gate,
            self.enable_polish,
            self.enable_arbitration,
        ))

    @property
    def requires_llm(self) -> bool:
        if self.enable_proofread:
            return True
        return any((self.enable_quality_gate, self.enable_refinement, self.enable_polish, self.enable_arbitration))

    @property
    def enable_proofread(self) -> bool:
        return self.enable_post_process and self.postprocess_strategy == "proofread"

    @property
    def enable_combined_proofread(self) -> bool:
        """Compatibility alias for integrations compiled against the former name."""

        return self.enable_proofread

    @property
    def digest(self) -> str:
        payload = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()


def ensure_workflow_profiles(config: object) -> dict[str, dict[str, object]]:
    """Return validated per-preset settings, migrating the legacy flat fields."""

    existing = getattr(config, "workflow_profiles", {})
    profiles = {
        str(name): _validated_settings(settings, legacy_without_strategy="pp_strategy" not in settings)
        for name, settings in existing.items()
        if name in {"translate", "polish", "mixed"} and isinstance(settings, Mapping)
    }
    legacy = capture_profile_settings(config)
    profiles.setdefault("translate", _factory_settings(legacy, enable_polish=False))
    profiles.setdefault("polish", _factory_settings(legacy, enable_polish=True))
    profiles.setdefault("mixed", _factory_settings(legacy, enable_polish=True))
    setattr(config, "workflow_profiles", profiles)
    return profiles


def capture_profile_settings(config: object) -> dict[str, object]:
    settings = {field: getattr(config, field, _DEFAULT_SETTINGS[field]) for field in _PROFILE_FIELDS}
    settings["pp_strategy"] = normalize_postprocess_strategy(settings["pp_strategy"])
    return settings


def store_profile_settings(config: object, preset: AiWorkflowPreset) -> None:
    profiles = ensure_workflow_profiles(config)
    profiles[preset] = capture_profile_settings(config)


def apply_profile_settings(config: object, preset: AiWorkflowPreset) -> object:
    settings = ensure_workflow_profiles(config)[preset]
    for field, value in settings.items():
        setattr(config, field, value)
    return config


def _factory_settings(legacy: Mapping[str, object], *, enable_polish: bool) -> dict[str, object]:
    settings = dict(legacy)
    settings.update({
        "pp_strategy": "proofread",
        "enable_post_process": True,
        "pp_enable_consistency_check": True,
        "pp_enable_format_validation": True,
        "pp_enable_quality_gate": True,
        "pp_enable_refinement": True,
        "pp_enable_polish": enable_polish,
        "pp_enable_arbitration": True,
    })
    return settings


def _validated_settings(
    settings: Mapping[str, object],
    *,
    legacy_without_strategy: bool = False,
) -> dict[str, object]:
    validated = dict(_DEFAULT_SETTINGS)
    if legacy_without_strategy:
        validated["pp_strategy"] = "strict"
    for field in _PROFILE_FIELDS:
        if field not in settings:
            continue
        value = settings[field]
        default = _DEFAULT_SETTINGS[field]
        if isinstance(default, bool) and isinstance(value, bool):
            validated[field] = value
        elif isinstance(default, int) and isinstance(value, int) and not isinstance(value, bool) and value > 0:
            validated[field] = value
        elif field == "pp_polish_scope" and value in {"all", "passed", "has_issues"}:
            validated[field] = value
        elif field == "pp_polish_level" and value in {"light", "moderate", "aggressive"}:
            validated[field] = value
        elif field == "pp_strategy" and value in {"proofread", "combined", "strict"}:
            validated[field] = normalize_postprocess_strategy(value)
    return validated


def normalize_postprocess_strategy(value: object) -> PostProcessStrategy:
    """Normalize the former ``combined`` machine value at compatibility boundaries."""

    normalized = str(value).strip().casefold()
    if normalized == "strict":
        return "strict"
    return "proofread"


__all__ = [
    "AiExecutionProfile",
    "AiWorkflowPreset",
    "PostProcessStrategy",
    "apply_profile_settings",
    "capture_profile_settings",
    "ensure_workflow_profiles",
    "normalize_postprocess_strategy",
    "store_profile_settings",
]
