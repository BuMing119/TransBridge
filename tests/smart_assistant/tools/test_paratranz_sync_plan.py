from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.conftest import MockAppContext, make_test_collection
from tests.contracts.io.test_paratranz_transport_keys import qualified
from transbridge.application.io.paratranz_mapping import paratranz_record_from_entry
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.mcp.adapter import MCPAdapter
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools.tool_paratranz import _tool_plan_sync

_SERVICE_FACTORY = "transbridge.paratranz.service.ParaTranzService.from_config"


def _service() -> MagicMock:
    service = MagicMock()
    service.list_entries.return_value = (
        ParaTranzEntry(
            7,
            "entry_000",
            "Original text 0",
            "remote translation",
            "INFO:NAM1",
            1,
        ),
    )
    return service


@patch(_SERVICE_FACTORY)
def test_agent_sync_plan_is_read_only_and_keeps_full_hash_with_paginated_items(factory) -> None:
    service = _service()
    factory.return_value = service
    ctx = MockAppContext(make_test_collection(3))

    result = _tool_plan_sync(
        {
            "project_id": "7",
            "operation": "bidirectional",
            "conflict_policy": "abort",
            "page_size": 1,
        },
        ctx,
    )

    assert result.success
    assert result.data["total_items"] == 3
    assert len(result.data["items"]) == 1
    assert len(result.data["plan_hash"]) == 64
    assert result.data["has_more"] is True
    service.list_entries.assert_called_once_with(7, limit=100_001, cancellation=None)
    service.upsert_entry.assert_not_called()


@patch(_SERVICE_FACTORY)
def test_gui_agent_registry_and_legacy_mcp_use_the_same_plan_dto(factory) -> None:
    factory.return_value = _service()
    ctx = MockAppContext(make_test_collection(2))
    spec = ToolRegistry.get("plan_sync")

    agent_result = spec.execute(
        {"project_id": "7", "operation": "upload", "page_size": 20},
        ctx,
    )
    mcp_result = MCPAdapter(ctx=ctx).call_tool(
        "plan_sync",
        {"project_id": "7", "operation": "upload", "page_size": 20},
    )
    mcp_data = mcp_result["structuredContent"]["value"]["data"]

    assert spec.execute is _tool_plan_sync
    assert not mcp_result["isError"]
    assert mcp_data == agent_result.data
    assert mcp_data["plan_hash"] == agent_result.data["plan_hash"]


@patch(_SERVICE_FACTORY)
def test_invalid_namespace_or_multi_source_collection_fails_closed(factory) -> None:
    factory.return_value = _service()
    ctx = MockAppContext(make_test_collection(2))

    mismatch = _tool_plan_sync(
        {"project_id": "7", "source_namespace": "project:other"},
        ctx,
    )

    assert not mismatch.success
    assert mismatch.error_code == "SOURCE_NAMESPACE_MISMATCH"
    factory.return_value.list_entries.assert_not_called()


@patch(_SERVICE_FACTORY)
def test_agent_download_plan_restores_transport_keys_before_matching(factory) -> None:
    entries = (qualified("One"), qualified("Two"))
    service = _service()
    service.list_entries.return_value = tuple(
        ParaTranzEntry.from_mapping(dict(paratranz_record_from_entry(entry), id=index + 20, translation="Cloud"))
        for index, entry in enumerate(entries)
    )
    factory.return_value = service
    result = _tool_plan_sync(
        {"project_id": "7", "operation": "download", "conflict_policy": "prefer_remote"},
        MockAppContext(TranslationEntryCollection(entries)),
    )
    assert result.success, result.error
    assert result.data["total_items"] == 2
    assert all(item["action"] == "update_local" for item in result.data["items"])
    service.upsert_entry.assert_not_called()
