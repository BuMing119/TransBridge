"""Canonical identities for project terminology synchronization facts."""

from __future__ import annotations

from typing import Any

from transbridge.application.projects.remote_binding import normalize_paratranz_endpoint
from transbridge.application.terminology.identity import canonical_digest

SYNC_IDENTITY_SCHEMA = "terminology-sync.identity.v1"


def target_payload(*, endpoint: str, account_user_id: int | None, remote_project_id: int) -> dict[str, Any]:
    """Return the secret-free canonical identity of a ParaTranz target."""

    if account_user_id is not None:
        _positive_integer(account_user_id, "account user ID")
    _positive_integer(remote_project_id, "remote project ID")
    return {
        "account_user_id": account_user_id,
        "endpoint": normalize_paratranz_endpoint(endpoint),
        "remote_project_id": remote_project_id,
    }


def target_id(*, endpoint: str, account_user_id: int | None, remote_project_id: int) -> str:
    return canonical_digest(
        target_payload(
            endpoint=endpoint,
            account_user_id=account_user_id,
            remote_project_id=remote_project_id,
        ),
        namespace=f"{SYNC_IDENTITY_SCHEMA}.target",
    )


def line_payload(
    *,
    project_id: str,
    variant_id: str,
    target_identity: str,
    profile_revision: int,
) -> dict[str, Any]:
    return {
        "profile_revision": _revision(profile_revision, "profile revision"),
        "project_id": _required(project_id, "project ID"),
        "target_id": _required(target_identity, "target identity"),
        "variant_id": _required(variant_id, "variant ID"),
    }


def sync_line_id(
    *,
    project_id: str,
    variant_id: str,
    target_identity: str,
    profile_revision: int,
) -> str:
    return canonical_digest(
        line_payload(
            project_id=project_id,
            variant_id=variant_id,
            target_identity=target_identity,
            profile_revision=profile_revision,
        ),
        namespace=f"{SYNC_IDENTITY_SCHEMA}.line",
    )


def item_payload(*, line_id: str, local_term_id: str | None = None, remote_id: int | None = None) -> dict[str, Any]:
    """Build a stable item anchor.

    Local terms keep their local ID when a remote ID is assigned later. A
    remote-only item is anchored by its remote integer ID until it is linked.
    """

    if local_term_id is not None:
        return {
            "anchor": "local",
            "anchor_id": _required(local_term_id, "local term ID"),
            "line_id": _required(line_id, "line ID"),
        }
    if remote_id is None:
        raise ValueError("sync item identity requires a local term ID or remote ID")
    return {
        "anchor": "remote",
        "anchor_id": _positive_integer(remote_id, "remote ID"),
        "line_id": _required(line_id, "line ID"),
    }


def sync_item_id(*, line_id: str, local_term_id: str | None = None, remote_id: int | None = None) -> str:
    return canonical_digest(
        item_payload(line_id=line_id, local_term_id=local_term_id, remote_id=remote_id),
        namespace=f"{SYNC_IDENTITY_SCHEMA}.item",
    )


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value.strip()


def _positive_integer(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _revision(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


__all__ = [
    "SYNC_IDENTITY_SCHEMA",
    "item_payload",
    "line_payload",
    "sync_item_id",
    "sync_line_id",
    "target_id",
    "target_payload",
]
