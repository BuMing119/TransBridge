"""Strict, portable definitions for named AI workflow profiles.

Profiles deliberately contain only execution policy.  Provider configuration,
credentials and local retrieval paths remain owned by the global LLM config.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

BaseMode = Literal["translate", "polish", "mixed"]
WorkflowStrategy = Literal["combined", "strict"]

DOCUMENT_TYPE = "transbridge.ai_workflow_profiles"
SCHEMA_VERSION = 1

_PROFILE_KEYS = frozenset({"id", "name", "description", "base_mode", "strategy", "workflow", "limits", "mixed"})
_DOCUMENT_KEYS = frozenset({"document_type", "schema_version", "selected_profile_id", "profiles"})
_WORKFLOW_BOOL_FIELDS = frozenset({
    "enable_post_process",
    "pp_enable_consistency_check",
    "pp_enable_format_validation",
    "pp_enable_quality_gate",
    "pp_enable_refinement",
    "pp_enable_polish",
    "polish_preview_enabled",
    "pp_enable_arbitration",
    "pp_strict_arbitration",
})
_WORKFLOW_POSITIVE_INT_FIELDS = frozenset({
    "pp_quality_gate_batch_size",
    "pp_refinement_batch_size",
    "pp_polish_batch_size",
    "pp_arbitration_batch_size",
})
_WORKFLOW_ENUM_FIELDS: dict[str, frozenset[str]] = {
    "pp_polish_scope": frozenset({"all", "passed", "has_issues"}),
    "pp_polish_level": frozenset({"light", "moderate", "aggressive"}),
}
_WORKFLOW_KEYS = _WORKFLOW_BOOL_FIELDS | _WORKFLOW_POSITIVE_INT_FIELDS | frozenset(_WORKFLOW_ENUM_FIELDS)
_WORKFLOW_DEFAULTS: dict[str, bool | int | str] = {
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
_LIMIT_RANGES: dict[str, tuple[int, int]] = {
    "max_concurrent": (1, 128),
    "max_tokens_per_batch": (1, 1_000_000),
    "max_output_tokens": (0, 1_000_000),
    "max_terms_per_batch": (1, 10_000),
}
_MIXED_KEYS = frozenset({"execution_order", "action_rules"})
_ACTION_RULE_KEYS = frozenset({
    "rule_id",
    "priority",
    "status_filter",
    "label_filter",
    "category_filter",
    "action",
})
_FORBIDDEN_KEY_MARKERS = (
    "api_key",
    "apikey",
    "auth_token",
    "base_url",
    "baseurl",
    "credential",
    "embedding",
    "local_json_path",
    "local_csv_path",
    "local_excel_path",
    "local_model_path",
    "password",
    "provider",
    "secret",
)
_ALLOWED_TOKEN_KEYS = frozenset({"max_tokens_per_batch", "max_output_tokens"})


class WorkflowProfileValidationError(ValueError):
    """Raised when a profile document fails its all-or-nothing contract."""


@dataclass(frozen=True, slots=True)
class CustomWorkflowProfile:
    """One named, portable overlay over a detached global LLM configuration."""

    id: str
    name: str
    description: str
    base_mode: BaseMode
    strategy: WorkflowStrategy
    workflow: dict[str, bool | int | str]
    limits: dict[str, int]
    mixed: dict[str, object] | None = None

    @classmethod
    def create(
        cls,
        name: str,
        *,
        base_mode: BaseMode,
        strategy: WorkflowStrategy = "combined",
        description: str = "",
        workflow: Mapping[str, object] | None = None,
        limits: Mapping[str, object] | None = None,
        mixed: Mapping[str, object] | None = None,
        profile_id: str | None = None,
    ) -> CustomWorkflowProfile:
        """Create a profile through the same strict parser used for imports."""

        payload: dict[str, object] = {
            "id": profile_id or str(uuid4()),
            "name": name,
            "description": description,
            "base_mode": base_mode,
            "strategy": strategy,
            "workflow": dict(workflow or {}),
            "limits": dict(limits or {}),
            "mixed": dict(mixed) if mixed is not None else None,
        }
        return cls.from_dict(payload)

    @classmethod
    def from_config(
        cls,
        name: str,
        base_mode: BaseMode,
        config: object,
        description: str = "",
        profile_id: str | None = None,
    ) -> CustomWorkflowProfile:
        """Capture only portable execution fields from a global configuration."""

        workflow = {field: deepcopy(getattr(config, field, default)) for field, default in _WORKFLOW_DEFAULTS.items()}
        limits = {
            field: deepcopy(getattr(config, field, default))
            for field, default in {
                "max_concurrent": 3,
                "max_tokens_per_batch": 2000,
                "max_output_tokens": 0,
                "max_terms_per_batch": 50,
            }.items()
        }
        mixed: dict[str, object] | None = None
        if base_mode == "mixed":
            from transbridge.paratranz.config_manager import ActionRule

            rules: list[dict[str, object]] = []
            for raw_rule in getattr(config, "action_rules", ()):
                if hasattr(raw_rule, "to_dict"):
                    rule = raw_rule.to_dict()
                elif isinstance(raw_rule, Mapping):
                    rule = ActionRule.from_dict(dict(raw_rule)).to_dict()
                else:
                    raise WorkflowProfileValidationError("config.action_rules contains an unsupported rule value")
                rules.append(rule)
            mixed = {
                "execution_order": getattr(config, "mixed_execution_order", "serial"),
                "action_rules": rules,
            }
        return cls.create(
            name,
            profile_id=profile_id,
            description=description,
            base_mode=base_mode,
            strategy=getattr(config, "pp_strategy", "combined"),
            workflow=workflow,
            limits=limits,
            mixed=mixed,
        )

    @classmethod
    def from_dict(cls, payload: object) -> CustomWorkflowProfile:
        data = _require_mapping(payload, "profile")
        _reject_forbidden_keys(data, "profile")
        _require_exact_keys(data, _PROFILE_KEYS, "profile")

        profile_id = _validate_uuid(data["id"], "profile.id")
        name = _bounded_text(data["name"], "profile.name", maximum=80, allow_empty=False)
        description = _bounded_text(data["description"], "profile.description", maximum=500, allow_empty=True)
        base_mode = _enum(data["base_mode"], {"translate", "polish", "mixed"}, "profile.base_mode")
        strategy = _enum(data["strategy"], {"combined", "strict"}, "profile.strategy")
        workflow = _validate_workflow(data["workflow"])
        limits = _validate_limits(data["limits"])
        mixed = _validate_mixed(data["mixed"], base_mode=base_mode)
        return cls(
            id=profile_id,
            name=name,
            description=description,
            base_mode=base_mode,  # type: ignore[arg-type]
            strategy=strategy,  # type: ignore[arg-type]
            workflow=workflow,
            limits=limits,
            mixed=mixed,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "base_mode": self.base_mode,
            "strategy": self.strategy,
            "workflow": deepcopy(self.workflow),
            "limits": dict(self.limits),
            "mixed": deepcopy(self.mixed),
        }

    def apply_to(self, base_config: object) -> object:
        """Apply this whitelist to a detached copy, preserving all service settings."""

        copier = getattr(base_config, "copy_for_execution", None)
        target = copier() if callable(copier) else deepcopy(base_config)
        setattr(target, "pp_strategy", self.strategy)
        for field, value in self.workflow.items():
            setattr(target, field, deepcopy(value))
        for field, value in self.limits.items():
            setattr(target, field, value)
        if self.base_mode == "mixed" and self.mixed is not None:
            from transbridge.paratranz.config_manager import ActionRule

            setattr(target, "mixed_execution_order", self.mixed["execution_order"])
            rules = [ActionRule.from_dict(deepcopy(rule)) for rule in self.mixed["action_rules"]]  # type: ignore[arg-type]
            setattr(target, "action_rules", rules)
        return target


@dataclass(frozen=True, slots=True)
class CustomWorkflowProfileDocument:
    """Validated repository aggregate and import/export envelope."""

    selected_profile_id: str | None = None
    profiles: tuple[CustomWorkflowProfile, ...] = ()

    @classmethod
    def empty(cls) -> CustomWorkflowProfileDocument:
        return cls()

    @classmethod
    def from_dict(cls, payload: object) -> CustomWorkflowProfileDocument:
        data = _require_mapping(payload, "document")
        _reject_forbidden_keys(data, "document")
        _require_exact_keys(data, _DOCUMENT_KEYS, "document")
        if data["document_type"] != DOCUMENT_TYPE:
            raise WorkflowProfileValidationError(f"document_type must be {DOCUMENT_TYPE!r}")
        if data["schema_version"] != SCHEMA_VERSION:
            raise WorkflowProfileValidationError(f"unsupported schema_version: {data['schema_version']!r}")
        raw_profiles = data["profiles"]
        if not isinstance(raw_profiles, list):
            raise WorkflowProfileValidationError("document.profiles must be a list")
        if len(raw_profiles) > 500:
            raise WorkflowProfileValidationError("document.profiles exceeds the 500 profile limit")
        profiles = tuple(CustomWorkflowProfile.from_dict(item) for item in raw_profiles)
        _reject_duplicate_profiles(profiles)
        selected = data["selected_profile_id"]
        if selected is not None:
            selected = _validate_uuid(selected, "document.selected_profile_id")
            if selected not in {profile.id for profile in profiles}:
                raise WorkflowProfileValidationError("selected_profile_id does not identify an imported profile")
        return cls(selected_profile_id=selected, profiles=profiles)

    @property
    def selected_profile(self) -> CustomWorkflowProfile | None:
        return self.get(self.selected_profile_id) if self.selected_profile_id is not None else None

    def get(self, profile_id: str | None) -> CustomWorkflowProfile | None:
        return next((profile for profile in self.profiles if profile.id == profile_id), None)

    def to_dict(self) -> dict[str, object]:
        return {
            "document_type": DOCUMENT_TYPE,
            "schema_version": SCHEMA_VERSION,
            "selected_profile_id": self.selected_profile_id,
            "profiles": [profile.to_dict() for profile in self.profiles],
        }


def apply_custom_workflow_profile(profile: CustomWorkflowProfile, base_config: object) -> object:
    """Function form for adapters that prefer an explicit overlay boundary."""

    return profile.apply_to(base_config)


def _validate_workflow(payload: object) -> dict[str, bool | int | str]:
    data = _require_mapping(payload, "profile.workflow")
    _reject_forbidden_keys(data, "profile.workflow")
    unknown = set(data) - _WORKFLOW_KEYS
    if unknown:
        raise WorkflowProfileValidationError(f"profile.workflow has unknown fields: {sorted(unknown)!r}")
    result: dict[str, bool | int | str] = {}
    for key, value in data.items():
        if key in _WORKFLOW_BOOL_FIELDS:
            if not isinstance(value, bool):
                raise WorkflowProfileValidationError(f"profile.workflow.{key} must be a boolean")
        elif key in _WORKFLOW_POSITIVE_INT_FIELDS:
            value = _bounded_int(value, f"profile.workflow.{key}", minimum=1, maximum=10_000)
        else:
            value = _enum(value, _WORKFLOW_ENUM_FIELDS[key], f"profile.workflow.{key}")
        result[key] = value
    return result


def _validate_limits(payload: object) -> dict[str, int]:
    data = _require_mapping(payload, "profile.limits")
    _reject_forbidden_keys(data, "profile.limits")
    _require_exact_keys(data, frozenset(_LIMIT_RANGES), "profile.limits")
    return {
        key: _bounded_int(data[key], f"profile.limits.{key}", minimum=bounds[0], maximum=bounds[1])
        for key, bounds in _LIMIT_RANGES.items()
    }


def _validate_mixed(payload: object, *, base_mode: str) -> dict[str, object] | None:
    if base_mode != "mixed":
        if payload is not None:
            raise WorkflowProfileValidationError("profile.mixed must be null unless base_mode is 'mixed'")
        return None
    data = _require_mapping(payload, "profile.mixed")
    _reject_forbidden_keys(data, "profile.mixed")
    _require_exact_keys(data, _MIXED_KEYS, "profile.mixed")
    order = _enum(data["execution_order"], {"serial", "parallel"}, "profile.mixed.execution_order")
    raw_rules = data["action_rules"]
    if not isinstance(raw_rules, list):
        raise WorkflowProfileValidationError("profile.mixed.action_rules must be a list")
    if len(raw_rules) > 500:
        raise WorkflowProfileValidationError("profile.mixed.action_rules exceeds the 500 rule limit")
    rules = [_validate_action_rule(rule, index) for index, rule in enumerate(raw_rules)]
    rule_ids = [str(rule["rule_id"]) for rule in rules]
    if len(rule_ids) != len(set(rule_ids)):
        raise WorkflowProfileValidationError("profile.mixed.action_rules contains duplicate rule_id values")
    return {"execution_order": order, "action_rules": rules}


def _validate_action_rule(payload: object, index: int) -> dict[str, object]:
    path = f"profile.mixed.action_rules[{index}]"
    data = _require_mapping(payload, path)
    _reject_forbidden_keys(data, path)
    _require_exact_keys(data, _ACTION_RULE_KEYS, path)
    return {
        "rule_id": _bounded_text(data["rule_id"], f"{path}.rule_id", maximum=128, allow_empty=False),
        "priority": _bounded_int(data["priority"], f"{path}.priority", minimum=0, maximum=1_000_000),
        "status_filter": _validate_int_filter(data["status_filter"], f"{path}.status_filter"),
        "label_filter": _validate_text_filter(data["label_filter"], f"{path}.label_filter"),
        "category_filter": _validate_text_filter(data["category_filter"], f"{path}.category_filter"),
        "action": _enum(data["action"], {"translate", "polish", "skip"}, f"{path}.action"),
    }


def _validate_int_filter(value: object, path: str) -> list[int] | None:
    if value is None:
        return None
    values = _require_sequence(value, path)
    result = [_bounded_int(item, path, minimum=-100, maximum=100) for item in values]
    return sorted(set(result)) or None


def _validate_text_filter(value: object, path: str) -> list[str] | None:
    if value is None:
        return None
    values = _require_sequence(value, path)
    result = [_bounded_text(item, path, maximum=128, allow_empty=False) for item in values]
    return sorted(set(result)) or None


def _require_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise WorkflowProfileValidationError(f"{path} must be a JSON object with string keys")
    return value


def _require_sequence(value: object, path: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise WorkflowProfileValidationError(f"{path} must be a list")
    if len(value) > 500:
        raise WorkflowProfileValidationError(f"{path} exceeds the 500 item limit")
    return value


def _require_exact_keys(data: Mapping[str, object], expected: frozenset[str], path: str) -> None:
    missing = expected - set(data)
    unknown = set(data) - expected
    if missing or unknown:
        raise WorkflowProfileValidationError(
            f"{path} fields are invalid; missing={sorted(missing)!r}, unknown={sorted(unknown)!r}"
        )


def _reject_forbidden_keys(value: object, path: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().casefold()
            if (
                any(marker in normalized for marker in _FORBIDDEN_KEY_MARKERS)
                or ("token" in normalized and normalized not in _ALLOWED_TOKEN_KEYS)
                or normalized == "model"
            ):
                raise WorkflowProfileValidationError(f"{path} contains forbidden service or secret field {key!r}")
            _reject_forbidden_keys(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_forbidden_keys(nested, f"{path}[{index}]")


def _bounded_text(value: object, path: str, *, maximum: int, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise WorkflowProfileValidationError(f"{path} must be a string")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise WorkflowProfileValidationError(f"{path} must not be empty")
    if len(normalized) > maximum:
        raise WorkflowProfileValidationError(f"{path} exceeds {maximum} characters")
    return normalized


def _bounded_int(value: object, path: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise WorkflowProfileValidationError(f"{path} must be an integer in [{minimum}, {maximum}]")
    return value


def _enum(value: object, allowed: set[str] | frozenset[str], path: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise WorkflowProfileValidationError(f"{path} must be one of {sorted(allowed)!r}")
    return value


def _validate_uuid(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise WorkflowProfileValidationError(f"{path} must be a UUID string")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise WorkflowProfileValidationError(f"{path} must be a UUID string") from exc
    return str(parsed)


def _reject_duplicate_profiles(profiles: tuple[CustomWorkflowProfile, ...]) -> None:
    ids = [profile.id for profile in profiles]
    if len(ids) != len(set(ids)):
        raise WorkflowProfileValidationError("document.profiles contains duplicate profile ids")
    names = [profile.name.casefold() for profile in profiles]
    if len(names) != len(set(names)):
        raise WorkflowProfileValidationError("document.profiles contains duplicate profile names")


__all__ = [
    "BaseMode",
    "CustomWorkflowProfile",
    "CustomWorkflowProfileDocument",
    "DOCUMENT_TYPE",
    "SCHEMA_VERSION",
    "WorkflowProfileValidationError",
    "WorkflowStrategy",
    "apply_custom_workflow_profile",
]
