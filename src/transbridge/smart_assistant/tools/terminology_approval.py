# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Carry a user's terminology review through one confirmed tool invocation."""

from copy import deepcopy

REVIEW_ACTIONS = frozenset({
    "terminology_sync_authorize",
    "terminology_sync_mapping_confirmation",
    "terminology_sync_inbound_commit",
})


def record_review(context, step):
    context.terminology_review = (context.request_context, step["tool"], deepcopy(step["args"]))


def require_review(context, name, arguments):
    expected = (getattr(context, "request_context", None), name, arguments)
    if getattr(context, "terminology_review", None) != expected:
        raise PermissionError("请先确认本次术语同步操作")
