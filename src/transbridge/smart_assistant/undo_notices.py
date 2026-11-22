# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""User-facing undo limits for registered write steps before execution."""

from transbridge.application.assistant_requests.undo_capabilities import undo_limitation

from .tool_registry import ToolRegistry


def undo_notices(steps):
    notices = []
    for step in steps:
        name = step.get("tool", "")
        spec = ToolRegistry.get(name)
        if spec is None or spec.permission == "read":
            continue
        limitation = undo_limitation(name, step.get("args") or {})
        if limitation:
            notices.append(f"{spec.display_name}：{limitation}")
    return list(dict.fromkeys(notices))
