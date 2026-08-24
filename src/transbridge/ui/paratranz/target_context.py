"""Compatibility helpers that resolve Project-owned ParaTranz targets."""

from __future__ import annotations

from transbridge.application.projects import (
    ParaTranzTargetResolver,
    ParaTranzTargetStatus,
)


def resolved_paratranz_target(context):
    resolve = getattr(context, "resolve_paratranz_target", None)
    if callable(resolve):
        return resolve()
    config = getattr(context, "config", None)
    endpoint = getattr(config, "base_url", None)
    if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
        endpoint = "https://paratranz.cn"
    user = getattr(context, "current_user", None)
    account_id = user.get("id") if isinstance(user, dict) else getattr(config, "user_id", None)
    if not isinstance(account_id, int) or isinstance(account_id, bool) or account_id <= 0:
        account_id = None
    return ParaTranzTargetResolver().resolve(
        binding=getattr(context, "paratranz_binding", None),
        binding_revision=getattr(context, "project_revision", None),
        endpoint=endpoint,
        account_user_id=account_id,
    )


def bound_paratranz_project(context) -> dict | None:
    target = resolved_paratranz_target(context)
    if target.project_id is None or target.status not in {
        ParaTranzTargetStatus.UNVERIFIED,
        ParaTranzTargetStatus.AVAILABLE,
    }:
        return None
    return {"id": target.project_id, "name": target.project_name}


__all__ = ["bound_paratranz_project", "resolved_paratranz_target"]
