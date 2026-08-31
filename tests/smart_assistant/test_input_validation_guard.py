"""Tool text is data; schema, resource limits, and path authorization still apply."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import MockAppContext, make_test_collection
from transbridge.application.contracts import RequestContext
from transbridge.smart_assistant.guardrails.input_validator import InputValidationGuard
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import tool_editor  # noqa: F401 - register the real editor tools
from transbridge.smart_assistant.tools.base import ExecutionContext, execute_with_guardrails


@pytest.mark.parametrize(
    "text",
    [
        'Are you "friend" or "foe"?',
        "Press `E` to interact.",
        "AliExpress",
        "The guide documents Invoke-Expression and WAITFOR DELAY.",
        "Example: '<script>alert(1)</script>' is literal documentation.",
        '<iframe src="example.html" onload="ready()">',
        "The javascript: protocol is mentioned here.",
    ],
)
def test_plain_translation_text_reaches_the_real_editor_unchanged(text):
    collection = make_test_collection(1)
    context = ExecutionContext(app_context=MockAppContext(collection))

    result = execute_with_guardrails(
        ToolRegistry.get("edit_translation"),
        {"entry_id": "entry_000", "new_translation": text},
        context,
    )

    assert result.success
    assert collection.get("entry_000").translation == text


def test_plain_search_text_reaches_the_real_filter_unchanged():
    app_context = MockAppContext(make_test_collection(1))
    text = 'Are you "friend" or "foe"?'

    result = execute_with_guardrails(
        ToolRegistry.get("set_filters"),
        {"search_query": text, "search_field": "original"},
        ExecutionContext(app_context=app_context),
    )

    assert result.success
    assert app_context.filter_state["search_query"] == text


def test_translation_schema_still_rejects_a_non_string_before_editing():
    collection = make_test_collection(1)
    entry = collection.get("entry_000")
    original = entry.translation

    result = execute_with_guardrails(
        ToolRegistry.get("edit_translation"),
        {"entry_id": "entry_000", "new_translation": 42},
        ExecutionContext(app_context=MockAppContext(collection)),
    )

    assert not result.success
    assert entry.translation == original


def test_input_size_is_still_limited_by_utf8_bytes():
    result = InputValidationGuard(max_input_size=4).before_execute(
        {"tool": "unregistered-test-tool", "args": {"text": "中文"}}, SimpleNamespace()
    )

    assert not result.allowed
    assert "大小限制" in result.reason


def test_nested_input_depth_is_still_limited():
    result = InputValidationGuard(max_depth=2).before_execute(
        {"tool": "unregistered-test-tool", "args": {"options": {"items": [{"text": "literal"}]}}},
        SimpleNamespace(),
    )

    assert not result.allowed
    assert "嵌套深度" in result.reason


def test_nested_path_outside_the_grant_is_still_denied(tmp_path: Path):
    grant = tmp_path / "grant"
    grant.mkdir()
    context = ExecutionContext(request_context=RequestContext("review", authorized_roots=(str(grant),)))

    result = InputValidationGuard().before_execute(
        {"tool": "unregistered-test-tool", "args": {"options": {"output_path": "../outside.json"}}},
        context,
    )

    assert not result.allowed
    assert result.code == "PATH_OUTSIDE_GRANT"
    assert not (tmp_path / "outside.json").exists()
