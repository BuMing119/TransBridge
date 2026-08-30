"""Shared Smart Assistant boundary for one-time terminology run capture."""

from __future__ import annotations


def freeze_terminology_binding(ctx: object):
    from transbridge.ai_translator.project_terminology_runtime import freeze_project_terminology
    from transbridge.application.translation.terminology_run_snapshot import TerminologyRunSnapshotError

    try:
        return freeze_project_terminology(ctx)
    except TerminologyRunSnapshotError as exc:
        raise ValueError(f"无法固定项目术语快照：{exc}") from exc


__all__ = ["freeze_terminology_binding"]
