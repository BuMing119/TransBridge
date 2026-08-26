from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from transbridge.application.translation.custom_workflow_profile import (
    CustomWorkflowProfile,
    CustomWorkflowProfileDocument,
    WorkflowProfileValidationError,
)


def _limits() -> dict[str, int]:
    return {
        "max_concurrent": 4,
        "max_tokens_per_batch": 3000,
        "max_output_tokens": 0,
        "max_terms_per_batch": 60,
    }


def _profile(name: str = "标准校对", **overrides: object) -> CustomWorkflowProfile:
    values: dict[str, object] = {
        "name": name,
        "base_mode": "polish",
        "strategy": "combined",
        "workflow": {"enable_post_process": True, "pp_polish_level": "moderate"},
        "limits": _limits(),
    }
    values.update(overrides)
    return CustomWorkflowProfile.create(**values)  # type: ignore[arg-type]


def test_profile_round_trip_uses_versioned_secret_free_envelope() -> None:
    profile = _profile()
    document = CustomWorkflowProfileDocument(profile.id, (profile,))

    serialized = document.to_dict()
    restored = CustomWorkflowProfileDocument.from_dict(serialized)

    assert restored == document
    assert serialized["document_type"] == "transbridge.ai_workflow_profiles"
    assert serialized["schema_version"] == 1
    text = repr(serialized).casefold()
    for forbidden in ("provider", "base_url", "api_key", "credential", "embedding", "local_json_path"):
        assert forbidden not in text


@dataclass
class _GlobalConfig:
    provider: str = "openai_compatible"
    base_url: str = "https://example.invalid/v1"
    model: str = "global-model"
    api_key: str = "secret"
    local_json_path: str = "D:/private/terms.json"
    pp_strategy: str = "strict"
    enable_post_process: bool = False
    max_concurrent: int = 1
    action_rules: list[object] = field(default_factory=lambda: ["global"])


def test_apply_to_returns_detached_copy_and_only_overlays_whitelist() -> None:
    base = _GlobalConfig()
    applied = _profile().apply_to(base)

    assert applied is not base
    assert applied.pp_strategy == "combined"
    assert applied.enable_post_process is True
    assert applied.max_concurrent == 4
    assert applied.provider == base.provider
    assert applied.base_url == base.base_url
    assert applied.model == base.model
    assert applied.api_key == base.api_key
    assert applied.local_json_path == base.local_json_path
    assert base.pp_strategy == "strict"
    assert base.enable_post_process is False
    assert base.max_concurrent == 1


def test_from_config_captures_portable_fields_and_round_trips_without_service_settings() -> None:
    from transbridge.paratranz.config_manager import ActionRule

    config = _GlobalConfig(
        action_rules=[ActionRule(rule_id="untranslated", priority=0, status_filter={0}, action="translate")]
    )
    config.mixed_execution_order = "parallel"  # type: ignore[attr-defined]
    profile = CustomWorkflowProfile.from_config(" captured ", base_mode="mixed", config=config)

    restored = CustomWorkflowProfile.from_dict(profile.to_dict())

    assert restored == profile
    assert profile.name == "captured"
    assert profile.strategy == "strict"
    assert profile.workflow["enable_post_process"] is False
    assert profile.limits["max_concurrent"] == 1
    assert profile.mixed is not None
    assert profile.mixed["execution_order"] == "parallel"
    assert profile.mixed["action_rules"][0]["action"] == "translate"  # type: ignore[index]
    serialized = repr(profile.to_dict()).casefold()
    assert "global-model" not in serialized
    assert "https://example.invalid" not in serialized
    assert "secret" not in serialized
    assert "private/terms.json" not in serialized


def test_mixed_profile_validates_and_applies_execution_rules() -> None:
    mixed = {
        "execution_order": "parallel",
        "action_rules": [
            {
                "rule_id": "untranslated",
                "priority": 0,
                "status_filter": [0],
                "label_filter": None,
                "category_filter": None,
                "action": "translate",
            }
        ],
    }
    profile = _profile(base_mode="mixed", mixed=mixed)

    applied = profile.apply_to(_GlobalConfig())

    assert applied.mixed_execution_order == "parallel"
    assert applied.action_rules[0].action == "translate"
    applied.action_rules[0].action = "skip"
    assert profile.mixed is not None
    assert profile.mixed["action_rules"][0]["action"] == "translate"  # type: ignore[index]


def test_mixed_overlay_rules_are_directly_compatible_with_existing_planner() -> None:
    from transbridge.converter.translation_entry import TranslationEntry
    from transbridge.paratranz.config_manager import apply_rules

    profile = _profile(
        base_mode="mixed",
        mixed={
            "execution_order": "serial",
            "action_rules": [
                {
                    "rule_id": "untranslated",
                    "priority": 0,
                    "status_filter": [0],
                    "label_filter": None,
                    "category_filter": None,
                    "action": "translate",
                }
            ],
        },
    )
    applied = profile.apply_to(_GlobalConfig())
    entry = TranslationEntry("entry-1", "entry-1", "Hello", "", 0, "DIALOGUE")

    assert apply_rules(applied.action_rules, [entry]) == {"entry-1": "translate"}


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda payload: payload.update({"provider": "evil"}), "forbidden"),
        (lambda payload: payload["limits"].update({"api_key": "secret"}), "forbidden"),
        (lambda payload: payload["workflow"].update({"unknown": True}), "unknown"),
        (lambda payload: payload.update({"base_mode": "custom"}), "base_mode"),
        (lambda payload: payload["limits"].update({"max_concurrent": 0}), "max_concurrent"),
    ],
)
def test_profile_rejects_unknown_secret_enum_and_range_fields(mutation, match: str) -> None:
    payload = _profile().to_dict()
    mutation(payload)

    with pytest.raises(WorkflowProfileValidationError, match=match):
        CustomWorkflowProfile.from_dict(payload)


def test_document_rejects_duplicate_ids_and_trimmed_casefolded_names() -> None:
    first = _profile(" My Profile ")
    duplicate_id = _profile("another", profile_id=first.id)
    duplicate_name = _profile("my profile")

    with pytest.raises(WorkflowProfileValidationError, match="duplicate profile ids"):
        CustomWorkflowProfileDocument.from_dict({
            "document_type": "transbridge.ai_workflow_profiles",
            "schema_version": 1,
            "selected_profile_id": first.id,
            "profiles": [first.to_dict(), duplicate_id.to_dict()],
        })
    with pytest.raises(WorkflowProfileValidationError, match="duplicate profile names"):
        CustomWorkflowProfileDocument.from_dict({
            "document_type": "transbridge.ai_workflow_profiles",
            "schema_version": 1,
            "selected_profile_id": first.id,
            "profiles": [first.to_dict(), duplicate_name.to_dict()],
        })


def test_future_schema_and_missing_selected_profile_are_rejected() -> None:
    profile = _profile()
    payload = CustomWorkflowProfileDocument(profile.id, (profile,)).to_dict()
    payload["schema_version"] = 2
    with pytest.raises(WorkflowProfileValidationError, match="unsupported schema_version"):
        CustomWorkflowProfileDocument.from_dict(payload)

    payload["schema_version"] = 1
    payload["selected_profile_id"] = "08b6293c-47e9-491f-a66d-6a050703ff3f"
    with pytest.raises(WorkflowProfileValidationError, match="does not identify"):
        CustomWorkflowProfileDocument.from_dict(payload)
