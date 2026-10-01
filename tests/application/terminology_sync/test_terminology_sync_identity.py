from __future__ import annotations

import pytest

from transbridge.application.terminology_sync.identity import sync_item_id, sync_line_id, target_id


def test_target_identity_normalizes_endpoint_but_keeps_account_and_project_boundaries() -> None:
    base = target_id(endpoint="HTTPS://EXAMPLE.COM/api/", account_user_id=7, remote_project_id=11)

    assert base == target_id(endpoint="https://example.com", account_user_id=7, remote_project_id=11)
    assert base != target_id(endpoint="https://other.example.com", account_user_id=7, remote_project_id=11)
    assert base != target_id(endpoint="https://example.com", account_user_id=8, remote_project_id=11)
    assert base != target_id(endpoint="https://example.com", account_user_id=7, remote_project_id=12)
    assert base != target_id(endpoint="https://example.com", account_user_id=None, remote_project_id=11)


def test_line_identity_isolated_by_project_variant_target_and_profile_revision() -> None:
    target = target_id(endpoint="https://example.com", account_user_id=7, remote_project_id=11)
    base = sync_line_id(project_id="p1", variant_id="v1", target_identity=target, profile_revision=0)

    assert base != sync_line_id(project_id="p2", variant_id="v1", target_identity=target, profile_revision=0)
    assert base != sync_line_id(project_id="p1", variant_id="v2", target_identity=target, profile_revision=0)
    assert base != sync_line_id(project_id="p1", variant_id="v1", target_identity=target, profile_revision=1)


def test_local_item_identity_does_not_change_after_remote_assignment() -> None:
    local = sync_item_id(line_id="line-1", local_term_id="term-1")

    assert local == sync_item_id(line_id="line-1", local_term_id="term-1", remote_id=42)
    assert local != sync_item_id(line_id="line-1", remote_id=42)
    with pytest.raises(ValueError, match="requires"):
        sync_item_id(line_id="line-1")
