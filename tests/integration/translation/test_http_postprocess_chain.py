"""Controlled HTTP success chain for Story S06's post-process candidate pipeline."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

from transbridge.application.contracts import OperationOutcome
from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace, StagePolicy
from transbridge.application.translation import (
    InMemoryPostProcessCheckpointPort,
    OpenAiPostProcessHttpPort,
    PostProcessWorkload,
    TranslationInput,
    build_http_postprocess_stages,
)


class PostProcessHandler(BaseHTTPRequestHandler):
    phases: list[str] = []
    idempotency_keys: list[str] = []
    slow_phase: str | None = None

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        self.idempotency_keys.append(self.headers.get("Idempotency-Key", ""))
        user_payload = json.loads(payload["messages"][1]["content"])
        phase = user_payload["phase"]
        self.phases.append(phase)
        if self.slow_phase == phase:
            import time

            time.sleep(0.1)
        values = []
        for entry in user_payload["entries"]:
            current = entry["current"]
            if phase == "refine":
                value = f"refined:{current}"
            elif phase == "polish":
                value = f"polished:{current}"
            else:
                value = "pass"
            values.append({"entry_key": entry["entry_key"], "value": value})
        self._json_response({"results": values})

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


def _server():
    handler = PostProcessHandler
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server, thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=1)


def _entries() -> tuple[TranslationInput, ...]:
    namespace = SourceNamespace("test:http-postprocess")
    return (
        TranslationInput(EntryKey(namespace, "one"), EntryRevision(), "source-one", "draft-one", 2),
        TranslationInput(EntryKey(namespace, "two"), EntryRevision(), "source-two", "draft-two", 2),
    )


def test_controlled_http_chain_proves_refine_output_enters_polish_and_arbitration() -> None:
    PostProcessHandler.phases = []
    PostProcessHandler.idempotency_keys = []
    server, thread = _server()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        port = OpenAiPostProcessHttpPort(timeout_seconds=2)
        stages = build_http_postprocess_stages(port, base_url=base_url, model="fixture-model")
        workload = PostProcessWorkload(
            stages,
            stage_policy=StagePolicy(),
            stage_names=("refine", "polish", "arbitrate"),
            checkpoint_port=InMemoryPostProcessCheckpointPort(),
        )
        result = workload.run("http-pp-run", _entries(), owner_id="owner")
    finally:
        _stop(server, thread)

    assert result.outcome is OperationOutcome.COMPLETED
    assert PostProcessHandler.phases == ["refine", "polish", "arbitrate"]
    assert len(set(PostProcessHandler.idempotency_keys)) == 3
    assert result.value is not None
    snapshot = result.value
    assert snapshot.accepted_count == 2
    texts = {candidate.entry_key.local_key: candidate.text for candidate in snapshot.candidates}
    assert texts == {"one": "polished:refined:draft-one", "two": "polished:refined:draft-two"}
    assert all(candidate.accepted for candidate in snapshot.candidates)
    assert snapshot.run_spec_summary == {}
    rendered = snapshot.to_dict()
    assert rendered["counts"]["accepted"] == 2
    assert rendered["stages"][2]["phase"] == "arbitrate"


def test_controlled_http_transport_failure_is_partial_with_typed_diagnostic() -> None:
    PostProcessHandler.phases = []
    PostProcessHandler.slow_phase = "refine"
    server, thread = _server()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        port = OpenAiPostProcessHttpPort(timeout_seconds=0.01)
        stages = build_http_postprocess_stages(port, base_url=base_url)
        result = PostProcessWorkload(
            stages, stage_policy=StagePolicy(), stage_names=("refine", "polish", "arbitrate")
        ).run("http-pp-fail", _entries())
    finally:
        _stop(server, thread)

    assert result.outcome is OperationOutcome.PARTIAL
    assert any(
        d.code == "POSTPROCESS_TRANSPORT_UNAVAILABLE" for d in result.diagnostics
    )
    assert result.value is not None
    assert result.counts.failed == 1