from __future__ import annotations

import pytest

from transbridge.application.projects import (
    ParaTranzProjectBinding,
    ParaTranzTargetResolver,
    ParaTranzTargetSource,
    ParaTranzTargetStatus,
    normalize_paratranz_endpoint,
    project_paratranz_binding,
    project_with_paratranz_binding,
)
from transbridge.persistence.v2 import ProjectDto, ProjectId, ProjectRef, SchemaEnvelope


def _project(revision: int = 3) -> ProjectDto:
    ref = ProjectRef(ProjectId("project-1"))
    return ProjectDto(
        SchemaEnvelope(
            2,
            ref.kind,
            ref.identity.value,
            revision,
            {
                "name": "Local",
                "sources": [],
                "variant_ids": ["main"],
                "active_variant_id": "main",
            },
        )
    )


def _binding() -> ParaTranzProjectBinding:
    return ParaTranzProjectBinding(
        42,
        "Cloud",
        "HTTPS://PARATRANZ.CN/api/",
        7,
        "2026-08-24T10:00:00+08:00",
        "2026-08-24T10:01:00+08:00",
    )


def test_binding_round_trip_is_project_owned_and_secret_free() -> None:
    project = _project()

    updated = project_with_paratranz_binding(project, _binding(), expected_revision=3)
    restored = project_paratranz_binding(updated)

    assert updated.envelope.revision == 4
    assert restored == ParaTranzProjectBinding(
        42,
        "Cloud",
        "https://paratranz.cn",
        7,
        "2026-08-24T10:00:00+08:00",
        "2026-08-24T10:01:00+08:00",
    )
    serialized = repr(updated.envelope.data).lower()
    assert "token" not in serialized
    assert "api_key" not in serialized


def test_clear_binding_preserves_unknown_remote_providers() -> None:
    bound = project_with_paratranz_binding(_project(), _binding(), expected_revision=3)
    data = dict(bound.envelope.data)
    data["remote_bindings"] = {**data["remote_bindings"], "other": {"id": "kept"}}
    extended = ProjectDto(SchemaEnvelope(2, bound.envelope.entity_type, bound.envelope.identity, 4, data))

    cleared = project_with_paratranz_binding(extended, None, expected_revision=4)

    assert project_paratranz_binding(cleared) is None
    assert cleared.envelope.data["remote_bindings"] == {"other": {"id": "kept"}}


def test_resolver_never_uses_browse_state_and_detects_account_endpoint_mismatch() -> None:
    resolver = ParaTranzTargetResolver()
    binding = _binding()

    unbound = resolver.resolve(binding=None, endpoint="https://paratranz.cn", account_user_id=7)
    endpoint = resolver.resolve(binding=binding, endpoint="https://example.invalid", account_user_id=7)
    account = resolver.resolve(binding=binding, endpoint="https://paratranz.cn/api", account_user_id=8)
    resolved = resolver.resolve(binding=binding, binding_revision=4, endpoint="https://paratranz.cn", account_user_id=7)

    assert unbound.source is ParaTranzTargetSource.UNBOUND
    assert unbound.status is ParaTranzTargetStatus.UNBOUND
    assert endpoint.status is ParaTranzTargetStatus.ENDPOINT_MISMATCH
    assert account.status is ParaTranzTargetStatus.ACCOUNT_MISMATCH
    assert resolved.source is ParaTranzTargetSource.PROJECT_BINDING
    assert resolved.status is ParaTranzTargetStatus.AVAILABLE
    assert resolved.binding_revision == 4


def test_explicit_operation_target_has_priority_without_becoming_binding() -> None:
    resolved = ParaTranzTargetResolver().resolve(
        binding=_binding(),
        binding_revision=4,
        explicit_project_id=99,
        explicit_project_name="Temporary",
        endpoint="https://paratranz.cn",
        account_user_id=7,
        explicit_verified=True,
    )

    assert resolved.project_id == 99
    assert resolved.project_name == "Temporary"
    assert resolved.source is ParaTranzTargetSource.EXPLICIT
    assert resolved.status is ParaTranzTargetStatus.AVAILABLE
    assert resolved.binding_revision is None


@pytest.mark.parametrize(
    "value",
    (
        "",
        "paratranz.cn",
        "ftp://paratranz.cn",
        "https://x.test/?q=1",
        "https://user:secret@paratranz.cn/api",
    ),
)
def test_endpoint_normalization_rejects_ambiguous_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_paratranz_endpoint(value)
