from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import time

from transbridge.application.contracts import OperationOutcome
from transbridge.application.io.identity import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.translation import (
    ActionAssignment,
    ActionPlan,
    CandidateSet,
    ContextBatch,
    ContextPlan,
    InMemoryTranslationCheckpointPort,
    OpenAiTranslationHttpPort,
    TranslationAction,
    TranslationInput,
    TranslationWorkload,
    TranslationWorkloadRequest,
    build_run_spec,
)

NAMESPACE = SourceNamespace("test:http-translation-workload")


class TranslationHandler(BaseHTTPRequestHandler):
    retry_requests = 0
    received: list[dict] = []
    idempotency_keys: list[str] = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        self.received.append(payload)
        self.idempotency_keys.append(self.headers["Idempotency-Key"])
        if self.path.startswith("/retry/"):
            type(self).retry_requests += 1
            if self.retry_requests == 1:
                self.send_response(429)
                self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(b'{"error":"rate limited"}')
                return
        if self.path.startswith("/timeout/"):
            time.sleep(0.1)
        if self.path.startswith("/malformed/"):
            self._json_response({"not_translations": []})
            return
        user_payload = json.loads(payload["messages"][1]["content"])
        action = "polish" if "perform polish" in payload["messages"][0]["content"] else "translate"
        translations = []
        for entry in user_payload["entries"]:
            source = entry["translation"] if action == "polish" else entry["original"]
            translations.append({
                "entry_key": entry["entry_key"],
                "text": f"http:{action}:{source}",
            })
        self._json_response({"translations": translations})

    def _json_response(self, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def log_message(self, format: str, *args: object) -> None:
        return


def _key(name: str) -> EntryKey:
    return EntryKey(NAMESPACE, name)


def _entries() -> tuple[TranslationInput, ...]:
    return (
        TranslationInput(_key("translate"), EntryRevision(), "source-a", "", 0),
        TranslationInput(_key("polish"), EntryRevision(), "source-b", "draft-b", 1),
        TranslationInput(_key("both"), EntryRevision(), "source-c", "", 0),
    )


def _request(base_url: str) -> TranslationWorkloadRequest:
    entries = _entries()
    actions = (
        TranslationAction.TRANSLATE,
        TranslationAction.POLISH,
        TranslationAction.BOTH,
    )
    spec = build_run_spec(
        run_id="http-translation-run",
        config_revision=7,
        input_revision=5,
        source_locale="en",
        target_locale="zh-CN",
        prompt_profile="integration",
        provider="controlled-http",
        base_url=base_url,
        model="fixture-model",
        parameters={"temperature": 0},
        retrieval_enabled=False,
        retrieval_loader=None,
        scope=tuple(entry.entry_key for entry in entries),
    )
    action_plan = ActionPlan(
        spec.scope,
        tuple(
            ActionAssignment(entry.entry_key, action, "integration")
            for entry, action in zip(entries, actions, strict=True)
        ),
    )
    context_plan = ContextPlan((ContextBatch(1, "fixture", spec.scope),))
    return TranslationWorkloadRequest(
        spec,
        action_plan,
        context_plan,
        entries,
        "integration-owner",
        InMemoryTranslationCheckpointPort(),
        max_retries=1,
        retry_backoff_seconds=0,
    )


def test_controlled_http_mixed_workload_retries_429_and_preserves_run_config() -> None:
    TranslationHandler.retry_requests = 0
    TranslationHandler.received = []
    TranslationHandler.idempotency_keys = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), TranslationHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/retry/v1"
        result = TranslationWorkload(OpenAiTranslationHttpPort(timeout_seconds=1)).run(_request(base_url))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert result.outcome is OperationOutcome.COMPLETED
    candidates = {
        candidate.entry_key.local_key: candidate.text
        for candidate in CandidateSet.from_dict(result.value["candidate_set"]).candidates
    }
    assert candidates == {
        "translate": "http:translate:source-a",
        "polish": "http:polish:draft-b",
        "both": "http:polish:http:translate:source-c",
    }
    assert TranslationHandler.retry_requests == 3
    assert all(payload["model"] == "fixture-model" for payload in TranslationHandler.received)
    assert all("to zh-CN" in payload["messages"][0]["content"] for payload in TranslationHandler.received)
    assert TranslationHandler.idempotency_keys[0] == TranslationHandler.idempotency_keys[1]
    assert len(set(TranslationHandler.idempotency_keys)) == 2


def test_controlled_http_malformed_and_timeout_are_failed_without_candidates() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), TranslationHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        malformed = TranslationWorkload(OpenAiTranslationHttpPort(timeout_seconds=1)).run(
            _request(f"http://127.0.0.1:{server.server_port}/malformed/v1")
        )
        timed_out = TranslationWorkload(OpenAiTranslationHttpPort(timeout_seconds=0.01)).run(
            _request(f"http://127.0.0.1:{server.server_port}/timeout/v1")
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert malformed.outcome is OperationOutcome.FAILED
    assert "TRANSLATION_RESPONSE_MALFORMED" in {diagnostic.code for diagnostic in malformed.diagnostics}
    assert timed_out.outcome is OperationOutcome.FAILED
    assert "TRANSLATION_TRANSPORT_UNAVAILABLE" in {diagnostic.code for diagnostic in timed_out.diagnostics}
    assert malformed.value is None
    assert malformed.counts.succeeded == 0
    assert timed_out.value is None
    assert timed_out.counts.succeeded == 0
