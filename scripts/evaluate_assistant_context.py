"""Offline synthetic context benchmark. Never reads user conversations or calls a model."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc

from transbridge.application.assistant_context.projection import append_context
from transbridge.application.assistant_requests.models import RequestItem, UserRequest
from transbridge.persistence.assistant_context_store import AssistantContextStore
from transbridge.persistence.assistant_transcript_store import AssistantTranscriptStore
from transbridge.smart_assistant.context_budget import ContextBudget


def benchmark(count):
    request = UserRequest(
        "request", "session", "Keep original ordering", (RequestItem("answer", "Explain"),), source_message_ids=("m0",)
    )
    history = [{"role": "system", "content": "Fixed rules", "message_id": "system"}] + [
        {
            "role": "user",
            "content": f"Synthetic message {n}: retain original ordering.",
            "message_id": f"m{n}",
            "request_ids": ["request"],
        }
        for n in range(count)
    ]
    state = {"request_id": "request", "goal": request.goal, "constraints": ["Do not write files"]}
    budget = ContextBudget(context_window=4_000_000)
    tracemalloc.start()
    begin = perf_counter()
    first = append_context(history, request, state, config_digest="fixed")
    budget.require(first.messages)
    initial_ms = (perf_counter() - begin) * 1000
    history.append({"role": "user", "content": "One more question", "message_id": "new", "request_ids": ["request"]})
    begin = perf_counter()
    second = append_context(history, request, state, config_digest="fixed", previous=first, verified_immutable=True)
    append_ms = (perf_counter() - begin) * 1000
    with TemporaryDirectory(prefix="transbridge-context-eval-") as directory:
        store = AssistantContextStore(AssistantTranscriptStore(directory))
        begin = perf_counter()
        old = store.stage(first)
        initial_store_ms = (perf_counter() - begin) * 1000
        begin = perf_counter()
        new = store.stage(second, old)
        store_append_ms = (perf_counter() - begin) * 1000
        begin = perf_counter()
        restored = store.read("session", "request", (), new.head)
        restore_ms = (perf_counter() - begin) * 1000
        assert restored.epoch.messages == second.messages
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "mode": "offline_synthetic",
        "messages": count,
        "initial_projection_ms": initial_ms,
        "append_projection_ms": append_ms,
        "initial_storage_ms": initial_store_ms,
        "append_storage_ms": store_append_ms,
        "restore_ms": restore_ms,
        "peak_traced_bytes": peak,
        "prefix_preserved": second.messages[: len(first.messages)] == first.messages,
        "append_artifact_bytes": new.references[-1].size_bytes,
        "new_projection_items": len(second.items) - len(first.items),
        "actual_usage": "not_run",
        "provider_cache": "not_run",
        "semantic_quality": "not_run",
        "qt_heartbeat": "not_run",
        "limitations": "Synthetic local projection/storage only; not UI or total request cost.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--messages", type=int, default=10000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.messages <= 100000:
        parser.error("--messages must be between 1 and 100000")
    result = json.dumps(benchmark(args.messages), ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(result + "\n", encoding="utf-8")
    print(result)


if __name__ == "__main__":
    main()
