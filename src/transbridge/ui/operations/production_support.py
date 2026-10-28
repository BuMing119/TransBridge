"""Qt-free helpers shared by write and FOMOD operation composition."""

from collections.abc import Callable
from dataclasses import replace

from transbridge.application.contracts import RequestContext

from .mappers import OperationPlanDraft
from .preflight_view import PreflightCheckState, PreflightCheckStatus


def context_factory(value: RequestContext | Callable[[object], RequestContext]):
    if isinstance(value, RequestContext):
        return lambda ui_context: replace(
            value,
            project_id=getattr(ui_context, "active_project_id", value.project_id),
            variant_id=getattr(ui_context, "active_variant_id", value.variant_id),
        )
    if not callable(value):
        raise TypeError("runtime_context must be a RequestContext or callable")
    return value


def operation_request(draft, expected_type):
    if not isinstance(draft, OperationPlanDraft) or not isinstance(draft.request, expected_type):
        raise TypeError(f"operation draft must contain {expected_type.__name__}")
    return draft.request


def blocked(code: str, reason: str) -> PreflightCheckState:
    return PreflightCheckState(code, code.replace("_", " "), PreflightCheckStatus.BLOCKED, reason)


def trim_cache(cache: dict, *, on_evict: Callable[[object], None] | None = None) -> None:
    while len(cache) > 100:
        value = cache.pop(next(iter(cache)))
        if on_evict is not None:
            on_evict(value)
