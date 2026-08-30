from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import tool_terminology_sync  # noqa: F401


def test_terminology_sync_tools_are_independent_and_registered_for_mcp_projection() -> None:
    names = {spec.name for spec in ToolRegistry.list_namespace("terminology_sync")}

    assert {
        "terminology_sync_preflight",
        "terminology_sync_plan",
        "terminology_sync_plan_page",
        "terminology_sync_authorize",
        "terminology_sync_execute",
        "terminology_sync_status",
        "terminology_sync_retry",
        "terminology_sync_reconcile",
    } <= names
    assert ToolRegistry.get("terminology_sync_execute").permission == "write"
