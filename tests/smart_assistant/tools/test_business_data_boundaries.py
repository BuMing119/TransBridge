"""Business text and executable references must survive actual tool boundaries."""

from dataclasses import asdict
import json
from types import SimpleNamespace

import pytest

from transbridge.application.security import ConfirmationToken, DiagnosticValue, SecretRedactor, SecretValue
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.graph_executor import GraphExecutor
from transbridge.smart_assistant.guardrails.input_validator import InputValidationGuard
from transbridge.smart_assistant.guardrails.output_validator import OutputValidationGuard, sanitize_for_storage
from transbridge.smart_assistant.mcp.adapter import MCPAdapter
from transbridge.smart_assistant.reflexion import RetryHandler, ToolRetryExecutor
from transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec
from transbridge.smart_assistant.tools.base import ToolResult, execute_with_guardrails
from transbridge.smart_assistant.tools.diagnostics import technical_error
from transbridge.smart_assistant.tools.tool_editor import EditorController


def test_legacy_output_guard_constructor_does_not_rewrite_business_text():
    with pytest.warns(DeprecationWarning, match="protect technical diagnostics"):
        guard = OutputValidationGuard(1024, SecretRedactor.default())
    result = ToolResult.ok("password: swordfish", {"token": "a game token"})
    assert guard.after_execute({}, result, None).allowed
    assert result.message == "password: swordfish"
    assert result.data == {"token": "a game token"}


@pytest.mark.parametrize(
    "text",
    ["password: swordfish", "token=questpassword", "sk-" + "A" * 24, 'secret: "quest-password"\nnext line'],
)
def test_query_guard_observation_edit_preserves_original_identity(text, current_project_factory):
    entries = [
        TranslationEntry("same", "same", source, "old", 1, "QUST:CNAM", requires_original_match=True)
        for source in (text, text + " other")
    ]
    collection = TranslationEntryCollection(entries)
    project = current_project_factory(collection)
    context = project.context
    editor = EditorController()
    query = ToolSpec(
        "boundary_query",
        "query",
        "query",
        {},
        execute=lambda args, ctx: editor.get_visible_entries(args, ctx, collection),
    )
    edit = ToolSpec(
        "boundary_edit", "edit", "edit", {}, execute=lambda args, ctx: editor.edit_translation(args, ctx, collection)
    )
    result = execute_with_guardrails(query, {}, context, middlewares=[InputValidationGuard(), OutputValidationGuard()])
    projected = result.to_structured_observation(query.name).result
    row = projected["data"]["entries"][0]
    assert row["key"] == entries[0].identity.serialize()
    assert row["original"] == text
    assert result["data"] is result.get("data")
    changed = execute_with_guardrails(
        edit, {"entry_id": row["key"], "new_translation": text}, context, middlewares=[OutputValidationGuard()]
    )
    assert changed.success
    assert context.collection.get(entries[0].identity).translation == text
    assert context.collection.get(entries[1].identity).translation == "old"
    assert {entry.entry_key: entry for entry in project.aggregate.snapshot().entries}[
        entries[0].identity
    ].translation == text
    assert changed.to_dict()["data"]["new_translation"] == text


def test_mcp_compatibility_preserves_business_values_and_explicit_secret_boundaries(monkeypatch):
    text = "password: swordfish"
    token = ConfirmationToken("token-id", "owner", "hash", 100.0, "signature")
    payload = {
        "key": '["plugin", "same", "password: swordfish"]',
        "password": text,
        "token": "game-token",
        "path": "D:/mods/password=swordfish/source.esp",
        "confirmation_token": asdict(token),
        "credential": SecretValue("actual-account-key"),
        "diagnostic": DiagnosticValue("Authorization: Bearer " + "A" * 24),
    }
    spec = ToolSpec("boundary_mcp", "query", "query", {}, execute=lambda args, ctx: ToolResult.ok(data=payload))
    registry = SimpleNamespace(get=lambda *args, **kwargs: spec)
    monkeypatch.setattr(ToolRegistry, "get", registry.get)
    adapter = MCPAdapter(registry=registry, ctx=SimpleNamespace())
    result = adapter.call_tool(spec.name, {})
    assert not result["isError"]
    wire = result["structuredContent"]
    encoded = json.dumps(wire, ensure_ascii=False)
    assert "actual-account-key" not in encoded
    assert "A" * 24 not in encoded
    assert text in encoded
    assert json.loads(result["content"][0]["text"]) == wire
    # OperationResult wraps the legacy ToolResult data as its value.
    business = wire["value"]["data"]
    assert business["confirmation_token"] == asdict(token)
    assert business["key"] == payload["key"]
    assert business["path"] == payload["path"]
    assert business["password"] == text
    assert business["token"] == "game-token"


class _RepairField:
    def __init__(self):
        self.prompt = ""

    def chat(self, messages, max_tokens):
        self.prompt = messages[0]["content"]
        raw = self.prompt.split("Current arguments: ", 1)[1].split("\nArgument schema:", 1)[0]
        args = json.loads(raw)
        args["search_field"] = "original"
        return json.dumps({"retry": True, "adjusted_args": args})


def test_real_read_tool_retry_preserves_query_and_schema():
    client = _RepairField()
    context = SimpleNamespace(filter_state={})
    context.set_filter = lambda **args: context.filter_state.update(args)
    editor = EditorController()
    spec = ToolRegistry.get("set_filters")
    step = {"tool": "set_filters", "args": {"search_query": "password: swordfish", "search_field": "wrong"}}
    assert GraphExecutor._is_retry_allowed(spec, step)
    result = ToolRetryExecutor(RetryHandler(client)).execute(
        step,
        lambda args: editor.set_filters(args, context),
        retry_allowed=True,
        tool_schema=spec.parameters,
    )
    assert result.result.success
    assert result.attempts == 2
    assert context.filter_state == {"search_query": "password: swordfish", "search_field": "original"}
    assert "password: swordfish" in client.prompt


def test_retry_distinguishes_business_password_token_and_explicit_credentials():
    secret = SecretValue("actual-account-key")
    schema = {
        "type": "object",
        "properties": {
            "password": {"type": "string", "description": "Game password: swordfish"},
            "token": {"type": "string"},
            "account": {"type": "string", "writeOnly": True},
            "typed": {"type": "string"},
        },
        "additionalProperties": False,
    }
    original = {"password": "swordfish", "token": "game-token", "account": "account-key", "typed": secret}
    projected = RetryHandler._sanitize_args(original, schema)
    assert projected == {
        "password": "swordfish",
        "token": "game-token",
        "account": SecretRedactor.REDACTED,
        "typed": SecretRedactor.REDACTED,
    }
    restored = RetryHandler._restore_sensitive_values(original, {**projected, "unexpected": "extra"}, schema)
    assert restored["password"] == "swordfish"
    assert restored["token"] == "game-token"
    assert restored["account"] == "account-key"
    assert restored["typed"]._reveal_for_request() == "actual-account-key"
    assert "unexpected" not in restored
    assert schema["properties"]["password"]["description"] == "Game password: swordfish"


def test_storage_projection_keeps_business_evidence_and_hides_typed_diagnostics():
    payload = {
        "query": "password: swordfish",
        "path": "D:/mods/source.esp",
        "token": "game-token",
        "secret": SecretValue("real-secret"),
        "error": DiagnosticValue("password: actual-account-key"),
    }
    safe = sanitize_for_storage(payload)
    assert safe["query"] == payload["query"]
    assert safe["path"] == payload["path"]
    assert safe["token"] == payload["token"]
    assert safe["secret"] == SecretRedactor.REDACTED
    assert safe["error"] == SecretRedactor.REDACTED
    assert isinstance(payload["secret"], SecretValue)


def test_business_failure_message_is_not_a_technical_exception():
    text = 'Missing entry: ["plugin", "key", "password: swordfish"]'
    assert ToolResult.fail(text).to_dict()["message"] == text
    diagnostic = technical_error(RuntimeError("provider rejected Authorization: Bearer " + "A" * 24))
    assert "A" * 24 not in diagnostic
    assert SecretRedactor.REDACTED in diagnostic


def test_provider_exception_keeps_opaque_credential_protection_after_request_scope():
    from transbridge.application.security.diagnostic_logging import credential_diagnostics
    from transbridge.smart_assistant.chat_worker import ChatWorker

    class Provider:
        def __init__(self):
            self._api_key = "opaque-provider-credential"

        @credential_diagnostics
        def fail(self):
            raise RuntimeError(f"SDK failed using {self._api_key}")

        def chat_stream(self, messages, max_tokens, callback):
            self.fail()

    provider = Provider()
    with pytest.raises(RuntimeError) as caught:
        provider.fail()
    assert "opaque-provider-credential" in str(caught.value)  # Retry/type semantics are intact.
    assert "opaque-provider-credential" not in technical_error(caught.value)
    outcome = ToolRetryExecutor().execute({"tool": "provider"}, lambda _args: provider.fail(), retry_allowed=False)
    assert "opaque-provider-credential" not in outcome.result.message
    assert SecretRedactor.REDACTED in outcome.result.message
    errors = []
    worker = ChatWorker(provider, [])
    worker.on_error = errors.append
    worker.run()
    assert len(errors) == 1
    assert "opaque-provider-credential" not in errors[0]


def test_retry_prompt_preserves_schema_property_names_and_operation_tokens():
    class Repair:
        def chat(self, messages, max_tokens):
            self.prompt = messages[0]["content"]
            args = json.loads(self.prompt.split("Current arguments: ", 1)[1].split("\nArgument schema:", 1)[0])
            self.schema = json.loads(self.prompt.split("Argument schema: ", 1)[1].split("\nStructured failure:", 1)[0])
            return json.dumps({"retry": True, "adjusted_args": args})

    client = Repair()
    schema = {
        "type": "object",
        "properties": {
            "password": {"type": "string", "description": "Game password: swordfish"},
            "confirmation_token": {"type": "object"},
            "api_key": {"type": "string", "writeOnly": True},
        },
        "additionalProperties": False,
    }
    token = asdict(ConfirmationToken("opaque-id", "owner", "hash", 100.0, "signature"))
    args = {"password": "password: swordfish", "confirmation_token": token, "api_key": "actual-account-key"}
    result = RetryHandler(client).analyze_and_adjust(
        {"tool": "test", "args": args}, ToolResult.fail("input invalid"), 0, tool_schema=schema
    )
    assert result["args"] == args
    assert client.schema == schema
    assert "actual-account-key" not in client.prompt
    assert "password: swordfish" in client.prompt
    assert "opaque-id" in client.prompt
