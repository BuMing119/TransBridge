from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from types import MappingProxyType, SimpleNamespace
from uuid import uuid4

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.security import (
    ConfirmationAuthority,
    PathAuthorizationPolicy,
    PathGrant,
    SecretRedactor,
)
from transbridge.application.tools import ToolInvocation
from transbridge.application.tools.schema import (
    ToolSchemaError,
    canonicalize_parameters,
    validate_arguments,
)
from transbridge.smart_assistant.guardrails import (
    InputValidationGuard,
    OutputValidationGuard,
    PermissionGuard,
)
from transbridge.smart_assistant.tool_registry import (
    DuplicateToolError,
    ToolRegistry,
    ToolSpec,
)
from transbridge.smart_assistant.tools import ExecutionContext, ToolResult, execute_with_guardrails


def test_legacy_parameters_become_canonical_json_schema() -> None:
    schema = canonicalize_parameters({
        "entry_id": {"type": "str", "required": True, "description": "entry"},
        "limit": {"type": "int", "required": False},
    })

    assert schema["type"] == "object"
    assert schema["properties"]["entry_id"]["type"] == "string"
    assert schema["required"] == ["entry_id"]
    assert schema["additionalProperties"] is False


def test_legacy_parameter_named_type_is_not_misclassified_as_root_schema() -> None:
    schema = canonicalize_parameters({
        "type": {"type": "str", "required": True},
        "description": {"type": "str", "required": False},
    })

    assert schema["type"] == "object"
    assert schema["properties"]["type"]["type"] == "string"
    assert schema["properties"]["description"]["type"] == "string"
    assert schema["required"] == ["type"]


def test_invalid_canonical_schema_fails_startup() -> None:
    with pytest.raises(ToolSchemaError):
        ToolSpec(
            name="invalid_schema",
            display_name="invalid",
            description="invalid",
            parameters={"type": "not-a-json-schema-type"},
        )

    with pytest.raises(ToolSchemaError, match="root type must be object"):
        ToolSpec(
            name="valid_but_not_parameters",
            display_name="invalid",
            description="invalid",
            parameters={"type": "string"},
        )


def test_unconvertible_legacy_schema_marks_capability_unavailable() -> None:
    spec = ToolSpec(
        name="legacy_unknown",
        display_name="legacy",
        description="legacy",
        parameters={"value": {"type": "custom-python-object"}},
    )

    assert spec.available is False
    assert "unsupported legacy type" in spec.unavailable_reason
    assert spec.parameters["type"] == "object"


def test_duplicate_tool_name_is_rejected() -> None:
    name = f"contract_duplicate_{uuid4().hex}"
    first = ToolSpec(name=name, display_name=name, description=name, parameters={})
    second = ToolSpec(name=name, display_name=name, description=name, parameters={})
    ToolRegistry.register(first, namespace="contract_test")
    try:
        with pytest.raises(DuplicateToolError):
            ToolRegistry.register(second, namespace="other_contract_test")
    finally:
        ToolRegistry._namespaced_tools["contract_test"].pop(name, None)


def test_json_schema_diagnostic_contains_json_pointer() -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                    "required": ["count"],
                },
            }
        },
        "required": ["items"],
    }

    errors = validate_arguments(schema, {"items": [{"count": "wrong"}]})

    assert errors[0].pointer == "/items/0/count"


def test_registered_agent_wildcards_resolve_only_after_tools_are_loaded() -> None:
    from transbridge.smart_assistant.agents.agent_registry import AgentRegistry
    from transbridge.smart_assistant.tools import register_all

    AgentRegistry.init_presets()
    register_all()

    editor = AgentRegistry.get("editor")
    assert editor is not None
    assert isinstance(editor.tools, tuple)
    assert editor.tools
    assert not any(name.endswith(":*") for name in editor.tools)
    assert set(editor.tools) == {
        spec.name for spec in ToolRegistry.list_namespace("editor") if spec.available and not spec.deprecated
    }


def test_register_all_is_idempotent_after_agent_and_tool_freeze() -> None:
    from transbridge.smart_assistant.agents.agent_registry import AgentRegistry
    from transbridge.smart_assistant.tools import register_all

    register_all()
    before_tools = {
        namespace: tuple(spec.name for spec in specs) for namespace, specs in ToolRegistry.list_all_namespaces().items()
    }
    before_agents = tuple(agent.agent_id for agent in AgentRegistry.list_all())

    register_all()

    after_tools = {
        namespace: tuple(spec.name for spec in specs) for namespace, specs in ToolRegistry.list_all_namespaces().items()
    }
    assert after_tools == before_tools
    assert tuple(agent.agent_id for agent in AgentRegistry.list_all()) == before_agents


def test_absolute_path_under_grant_is_allowed(tmp_path: Path) -> None:
    target = tmp_path / "Unicode-数据.txt"
    target.write_text("safe", encoding="utf-8")
    request = RequestContext(owner_id="gui", authorized_roots=(str(tmp_path),))
    ctx = ExecutionContext(request_context=request, owner_id=request.owner_id)

    result = InputValidationGuard().before_execute(
        {"tool": "unknown_contract_tool", "args": {"input_path": str(target)}},
        ctx,
    )

    assert result.allowed


def test_relative_path_under_grant_is_allowed_without_process_cwd(tmp_path: Path) -> None:
    target = tmp_path / "relative.json"
    target.write_text("{}", encoding="utf-8")
    request = RequestContext(owner_id="gui", authorized_roots=(str(tmp_path),))
    ctx = ExecutionContext(request_context=request, owner_id=request.owner_id)

    result = InputValidationGuard().before_execute(
        {"tool": "unknown_contract_tool", "args": {"input_path": target.name}},
        ctx,
    )

    assert result.allowed


def test_relative_path_without_authorized_root_is_denied() -> None:
    result = InputValidationGuard().before_execute(
        {"tool": "unknown_contract_tool", "args": {"input_path": "relative.json"}},
        ExecutionContext(owner_id="gui"),
    )

    assert not result.allowed
    assert result.code == "PATH_GRANT_REQUIRED"


def test_nested_path_arguments_cannot_bypass_authorization(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    grant = tmp_path / "grant"
    outside.mkdir()
    grant.mkdir()
    output = outside / "result.json"
    request = RequestContext(owner_id="gui", authorized_roots=(str(grant),))
    ctx = ExecutionContext(request_context=request, owner_id=request.owner_id)

    result = InputValidationGuard().before_execute(
        {
            "tool": "unknown_contract_tool",
            "args": {"options": {"outputs": [{"output_path": str(output)}]}},
        },
        ctx,
    )

    assert not result.allowed
    assert result.code == "PATH_OUTSIDE_GRANT"


def test_authorized_absolute_path_reaches_parser_business_preflight(tmp_path: Path) -> None:
    from transbridge.smart_assistant.tools.tool_parser import _validate_path

    target = tmp_path / "authorized.esp"
    target.write_bytes(b"TES4")
    request = RequestContext(owner_id="gui", authorized_roots=(str(tmp_path),))
    ctx = ExecutionContext(request_context=request, owner_id=request.owner_id)
    step = {"tool": "parse_esp", "args": {"path": str(target)}}

    guard_result = InputValidationGuard().before_execute(step, ctx)

    assert guard_result.allowed
    assert _validate_path(str(target)) is None


def test_parser_absolute_path_without_grant_or_outside_grant_is_denied(tmp_path: Path) -> None:
    target = tmp_path / "outside.esp"
    target.write_bytes(b"TES4")
    no_grant = ExecutionContext(owner_id="gui")
    other_root = tmp_path / "other"
    other_root.mkdir()
    escaped = ExecutionContext(
        request_context=RequestContext(
            owner_id="gui",
            authorized_roots=(str(other_root),),
        ),
        owner_id="gui",
    )
    step = {"tool": "parse_esp", "args": {"path": str(target)}}

    no_grant_result = InputValidationGuard().before_execute(step, no_grant)
    escaped_result = InputValidationGuard().before_execute(step, escaped)

    assert not no_grant_result.allowed
    assert no_grant_result.code == "PATH_GRANT_REQUIRED"
    assert not escaped_result.allowed
    assert escaped_result.code == "PATH_OUTSIDE_GRANT"


def test_path_escape_and_symlink_escape_are_denied(tmp_path: Path) -> None:
    root = tmp_path / "grant"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    policy = PathAuthorizationPolicy((PathGrant(root),))

    assert not policy.authorize(secret).allowed
    link = root / "escape-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    decision = policy.authorize(link / "secret.txt")
    assert not decision.allowed
    assert decision.code == "PATH_OUTSIDE_GRANT"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_junction_escape_is_denied(tmp_path: Path) -> None:
    root = tmp_path / "grant"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    link = root / "escape-junction"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True,
        check=False,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation is unavailable: {created.stderr}")

    decision = PathAuthorizationPolicy((PathGrant(root),)).authorize(link / "secret.txt")

    assert not decision.allowed
    assert decision.code == "PATH_OUTSIDE_GRANT"


def test_creation_requires_create_grant_and_canonical_parent(tmp_path: Path) -> None:
    readonly = PathAuthorizationPolicy((PathGrant(tmp_path, allow_create=False),))
    writable = PathAuthorizationPolicy((PathGrant(tmp_path, allow_create=True),))
    target = tmp_path / "new.json"

    assert not readonly.authorize(target, for_creation=True).allowed
    assert writable.authorize(target, for_creation=True).allowed


def test_confirmation_token_is_owner_request_bound_and_one_use() -> None:
    authority = ConfirmationAuthority()
    invocation = ToolInvocation("write", {"value": 1}, "owner-a", "plan-a")
    token = authority.issue(owner_id="owner-a", request_hash=invocation.request_hash)

    assert (
        authority.consume(token, owner_id="owner-b", request_hash=invocation.request_hash).code
        == "CONFIRMATION_OWNER_CHANGED"
    )
    assert (
        authority.consume(token, owner_id="owner-a", request_hash=invocation.request_hash).code
        == "CONFIRMATION_REPLAYED"
    )

    changed = authority.issue(owner_id="owner-a", request_hash=invocation.request_hash)
    assert (
        authority.consume(changed, owner_id="owner-a", request_hash="different").code == "CONFIRMATION_REQUEST_CHANGED"
    )


def test_confirmation_timeout_is_fail_closed() -> None:
    now = [10.0]
    authority = ConfirmationAuthority(ttl_seconds=5, clock=lambda: now[0])
    token = authority.issue(owner_id="owner", request_hash="request")
    now[0] = 16.0

    decision = authority.consume(token, owner_id="owner", request_hash="request")

    assert not decision.allowed
    assert decision.code == "CONFIRMATION_EXPIRED"


def test_confirmation_replay_cannot_repeat_real_side_effect() -> None:
    name = f"contract_once_{uuid4().hex}"
    effects = {"count": 0}

    def execute(args, ctx):
        effects["count"] += 1
        return ToolResult.ok("done")

    spec = ToolSpec(
        name=name,
        display_name=name,
        description="write once",
        parameters={},
        execute=execute,
        permission="write",
        require_confirmation=True,
    )
    ToolRegistry.register(spec, namespace="contract_replay")
    authority = ConfirmationAuthority()
    invocation = ToolInvocation(name, {}, "owner", "plan")
    token = authority.issue(owner_id="owner", request_hash=invocation.request_hash)
    ctx = ExecutionContext(
        owner_id="owner",
        plan_hash="plan",
        confirmation_authority=authority,
        confirmation_token=token,
    )
    try:
        first = execute_with_guardrails(spec, {}, ctx, middlewares=[PermissionGuard()])
        ctx.confirmation_token = token
        replay = execute_with_guardrails(spec, {}, ctx, middlewares=[PermissionGuard()])
    finally:
        ToolRegistry._namespaced_tools["contract_replay"].pop(name, None)

    assert first.success
    assert not replay.success
    assert effects["count"] == 1


def test_redactor_covers_tool_result_and_structured_observation() -> None:
    canary = "sk-" + "A" * 24
    result = ToolResult.ok(canary, data={"nested": {"token": canary}})

    serialized = result.to_dict()
    observation = result.to_structured_observation("secret_tool", max_chars=80)

    assert canary not in repr(serialized)
    assert canary not in repr(observation.to_dict())
    assert SecretRedactor.REDACTED in repr(observation.to_dict())


def test_redactor_is_fail_closed_for_mapping_and_custom_dataclass() -> None:
    canary = "short-secret-canary"

    @dataclass(init=False)
    class PositionalSecret:
        token: str

        def __init__(self, token, /):
            self.token = token

    payload = MappingProxyType({"token": canary, "nested": PositionalSecret(canary)})

    redacted = SecretRedactor.default().redact(payload)

    assert canary not in repr(redacted)
    assert redacted["token"] == SecretRedactor.REDACTED
    assert redacted["nested"]["token"] == SecretRedactor.REDACTED


def test_display_truncation_does_not_destroy_structured_schema() -> None:
    schema = {"description": "x" * 4180, "type": "object", "properties": {}}
    result = ToolResult.ok("schema", data={"inputSchema": schema})

    observation = result.to_structured_observation("get_tool_help", max_chars=100)

    assert len(observation.display_summary) <= 100
    assert observation.result["data"]["inputSchema"] == schema


def test_output_guard_keeps_large_authoritative_structure() -> None:
    payload = {"schema": "x" * 4180}
    step_result = SimpleNamespace(message="ok", data=payload)

    guard = OutputValidationGuard(max_output_size=10)
    decision = guard.after_execute({}, step_result, None)

    assert decision.allowed
    assert step_result.data == payload
    assert step_result.display_truncated is True


def test_guarded_tool_result_object_is_redacted_before_return() -> None:
    name = f"contract_redaction_{uuid4().hex}"
    canary = "sk-" + "B" * 24

    def execute(args, ctx):
        return ToolResult.ok(canary, data={"token": canary}, warnings=[canary])

    spec = ToolSpec(
        name=name,
        display_name=name,
        description="redaction",
        parameters={},
        execute=execute,
    )
    ToolRegistry.register(spec, namespace="contract_redaction")
    try:
        result = execute_with_guardrails(
            spec,
            {},
            ExecutionContext(owner_id="owner"),
            middlewares=[OutputValidationGuard()],
        )
    finally:
        ToolRegistry._namespaced_tools["contract_redaction"].pop(name, None)

    assert canary not in repr(result)
    assert result.data["token"] == SecretRedactor.REDACTED
