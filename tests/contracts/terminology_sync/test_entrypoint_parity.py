from __future__ import annotations

from transbridge.application.contracts import RequestContext
from transbridge.application.terminology.ports import Page
from transbridge.application.terminology_sync.models import TerminologySyncMode
from transbridge.application.terminology_sync.service import TerminologySyncPlanRef, TerminologySyncPlanSummary
from transbridge.smart_assistant.mcp.adapter import MCPAdapter
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import tool_terminology_sync  # noqa: F401


class _Service:
    def create_plan(self, context, mode):
        assert mode is TerminologySyncMode.BACKUP
        return TerminologySyncPlanSummary(
            TerminologySyncPlanRef("plan-1", "a" * 64),
            mode,
            context.project_id,
            context.variant_id,
            "target-1",
            (("create_remote", 2),),
            (),
            False,
            False,
            False,
            False,
            True,
        )

    def page_plan(self, ref, request):
        return Page((), ref.plan_hash, total=2)


class _Context:
    terminology_sync_service = _Service()
    request_context = RequestContext(
        owner_id="owner-1",
        project_id="project-1",
        variant_id="variant-1",
    )
    owner_id = "owner-1"


def test_agent_and_legacy_mcp_project_the_same_shared_plan_hash_and_counts() -> None:
    ctx = _Context()
    spec = ToolRegistry.get("terminology_sync_plan")

    agent = spec.execute({"mode": "backup", "limit": 20}, ctx)
    mcp = MCPAdapter(ctx=ctx).call_tool("terminology_sync_plan", {"mode": "backup", "limit": 20})
    mcp_data = mcp["structuredContent"]["value"]["data"]

    assert agent.success
    assert not mcp["isError"]
    assert mcp_data == agent.data
    assert mcp_data["ref"]["plan_hash"] == "a" * 64
    assert mcp_data["counts"] == [["create_remote", 2]]
