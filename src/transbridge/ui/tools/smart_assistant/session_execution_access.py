"""Shared execution boundary for chat, request and confirmation entry points."""

from collections.abc import Callable


def execution_allowed(facade) -> bool:
    check = getattr(facade, "_navigation", {}).get("session_scope_ready")
    return check is None or bool(check())


def ensure_execution(facade, action: str, resume: Callable[[], None]) -> bool:
    ensure = getattr(facade, "_navigation", {}).get("ensure_session_scope")
    return ensure is None or bool(ensure(action, resume))


def invoke_when_ready(facade, action: str, callback: Callable[[], None]) -> None:
    if ensure_execution(facade, action, lambda: invoke_when_ready(facade, action, callback)):
        callback()
