"""User-facing undo limits for registered write steps before execution."""

from transbridge.application.assistant_requests.undo_capabilities import undo_limitation

from .tool_registry import ToolRegistry


def undo_notices(steps):
    notices = []
    for name in dict.fromkeys(step.get("tool", "") for step in steps):
        spec = ToolRegistry.get(name)
        if spec is None or spec.permission == "read":
            continue
        limitation = undo_limitation(name)
        if limitation:
            notices.append(f"{spec.display_name}：{limitation}")
    return notices
